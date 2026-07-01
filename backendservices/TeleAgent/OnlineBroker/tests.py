from django.contrib.auth.models import User
from django.contrib.auth.hashers import check_password
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth.hashers import make_password

from .models import (
    AgentClient, BrokerClientCredential, BrokerClientToken, Conversation, Task, TaskEvent,
)
from .views import _iter_task_events


class BrokerClientTokenAuthTests(TestCase):
    """FIX 2：client-login 返回每条凭证专属的 broker Token（最小授权），而非用户主 Token。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="bt@example.com", email="bt@example.com", password="secret123",
        )
        self.secret = "broker-secret-key"
        self.cred = BrokerClientCredential.objects.create(
            name="PC", user=self.user, secret_hash=make_password(self.secret),
        )

    def _login(self):
        return APIClient().post(
            "/api/auth/client-login/",
            {"client_id": str(self.cred.id), "secret_key": self.secret},
            format="json",
        )

    def test_client_login_issues_scoped_token_not_master_token(self):
        master = Token.objects.create(user=self.user)
        resp = self._login()
        self.assertEqual(resp.status_code, 200)
        broker_key = resp.json()["token"]
        # 返回的不是用户主 Token。
        self.assertNotEqual(broker_key, master.key)
        # 是一条与该凭证关联的 BrokerClientToken。
        bt = BrokerClientToken.objects.get(credential=self.cred)
        self.assertEqual(bt.key, broker_key)

    def test_broker_token_authenticates_broker_endpoints(self):
        broker_key = self._login().json()["token"]
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Token {broker_key}")
        # broker Token 能访问 broker 端点，且归属到凭证用户。
        resp = api.get("/api/broker/clients/")
        self.assertEqual(resp.status_code, 200)

    def test_master_user_token_still_works(self):
        master = Token.objects.create(user=self.user)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Token {master.key}")
        self.assertEqual(api.get("/api/broker/clients/").status_code, 200)

    def test_deleting_credential_revokes_token(self):
        broker_key = self._login().json()["token"]
        self.cred.delete()  # 级联吊销
        self.assertFalse(BrokerClientToken.objects.filter(key=broker_key).exists())
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Token {broker_key}")
        self.assertEqual(api.get("/api/broker/clients/").status_code, 401)


class BrokerAgentSelectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="user@example.com",
            email="user@example.com",
            password="secret123",
        )
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")

    def test_register_client_persists_supported_agents(self):
        response = self.client.post(
            "/api/broker/clients/",
            {
                "name": "Workstation",
                "hostname": "workstation.local",
                "supported_agents": ["codex", "cursor_agent"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json()["supported_agents"],
            ["codex", "cursor_agent"],
        )

        client = AgentClient.objects.get(owner=self.user, hostname="workstation.local")
        self.assertEqual(client.supported_agents, ["codex", "cursor_agent"])

    def test_create_conversation_requires_agent_type_supported_by_client(self):
        agent_client = AgentClient.objects.create(
            owner=self.user,
            name="Laptop",
            hostname="laptop.local",
            supported_agents=["cursor_agent"],
        )

        response = self.client.post(
            "/api/broker/conversations/",
            {
                "cwd": "/tmp/project",
                "title": "Fix bug",
                "client_id": str(agent_client.id),
                "agent_type": "codex",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("agent_type", response.json())

    def test_send_message_inherits_conversation_agent_type_to_task(self):
        agent_client = AgentClient.objects.create(
            owner=self.user,
            name="Laptop",
            hostname="laptop.local",
            supported_agents=["codex", "cursor_agent"],
        )
        conversation = Conversation.objects.create(
            owner=self.user,
            cwd="/tmp/project",
            title="Refactor",
            assigned_client=agent_client,
            agent_type="codex",
        )

        response = self.client.post(
            f"/api/broker/conversations/{conversation.id}/messages/",
            {"prompt": "Refactor tests", "force": False},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        task = Task.objects.get(pk=response.json()["task_id"])
        self.assertEqual(task.agent_type, "codex")

    def test_create_broker_credential_returns_generated_secret_once(self):
        response = self.client.post(
            "/api/broker/credentials/",
            {
                "name": "Office Mac mini",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["name"], "Office Mac mini")
        self.assertTrue(data["id"])
        self.assertTrue(data["secret_key"])

        credential = BrokerClientCredential.objects.get(pk=data["id"])
        self.assertEqual(credential.user, self.user)
        self.assertTrue(check_password(data["secret_key"], credential.secret_hash))

    def test_list_broker_credentials_does_not_expose_secret(self):
        """安全：列表接口不得返回 secret（明文仅在创建时一次性返回）。"""
        BrokerClientCredential.objects.create(
            name="Laptop",
            user=self.user,
            secret_hash="hashed-value",
        )

        response = self.client.get("/api/broker/credentials/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["name"], "Laptop")
        self.assertNotIn("secret_key", response.json()[0])
        self.assertNotIn("secret_value", response.json()[0])


class TaskStreamTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="stream@example.com",
            email="stream@example.com",
            password="secret123",
        )
        self.other_user = User.objects.create_user(
            username="other-stream@example.com",
            email="other-stream@example.com",
            password="secret123",
        )
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
        self.conversation = Conversation.objects.create(
            owner=self.user,
            cwd="/tmp/project",
            title="Stream test",
            agent_type="codex",
        )

    def test_iter_task_events_emits_incremental_events_and_end_marker(self):
        e1 = {"type": "assistant", "message": {"content": [{"text": "hello"}]}}
        e2 = {"type": "result", "result": "done"}
        # status: 先 RUNNING 再 SUCCESS
        statuses = [
            SimpleNamespace(status=Task.Status.RUNNING),
            SimpleNamespace(status=Task.Status.SUCCESS),
        ]

        def loader():
            return statuses.pop(0)

        # 模拟事件随时间增多；fetcher 返回 seq > after 的增量 (seq, payload)
        snapshots = [
            [(1, e1)],            # 第 1 次拉取（运行中）
            [(1, e1), (2, e2)],  # 第 2 次拉取（完成时）
            [(1, e1), (2, e2)],  # 结束前的排空拉取
        ]
        idx = {"i": 0}

        def fetcher(after):
            snap = snapshots[min(idx["i"], len(snapshots) - 1)]
            idx["i"] += 1
            return [(s, p) for (s, p) in snap if s > after]

        stream = _iter_task_events(loader, fetcher, sleep_fn=lambda _seconds: None)

        self.assertEqual(
            list(stream),
            [
                'data: {"type": "assistant", "message": {"content": [{"text": "hello"}]}}\n\n',
                'data: {"type": "result", "result": "done"}\n\n',
                'data: {"type": "system", "subtype": "end", "status": "success"}\n\n',
            ],
        )

    def test_task_stream_endpoint_accepts_token_auth(self):
        task = Task.objects.create(
            conversation=self.conversation,
            agent_type="codex",
            prompt="hello",
            status=Task.Status.SUCCESS,
            cwd="/tmp/project",
        )
        TaskEvent.objects.create(
            task=task, seq=1, payload={"type": "result", "result": "done"}
        )

        response = self.client.get(f"/api/broker/tasks/{task.id}/stream/")

        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content).decode("utf-8")
        self.assertIn('"type": "result"', body)
        self.assertIn('"subtype": "end"', body)

    def test_task_stream_endpoint_accepts_text_event_stream_header(self):
        task = Task.objects.create(
            conversation=self.conversation,
            agent_type="codex",
            prompt="hello",
            status=Task.Status.SUCCESS,
            cwd="/tmp/project",
        )
        TaskEvent.objects.create(
            task=task, seq=1, payload={"type": "result", "result": "done"}
        )

        response = self.client.get(
            f"/api/broker/tasks/{task.id}/stream/",
            HTTP_ACCEPT="text/event-stream",
        )

        self.assertEqual(response.status_code, 200)

    def test_task_stream_prefers_authorization_token_over_session_user(self):
        task = Task.objects.create(
            conversation=self.conversation,
            agent_type="codex",
            prompt="hello",
            status=Task.Status.SUCCESS,
            cwd="/tmp/project",
        )
        TaskEvent.objects.create(
            task=task, seq=1, payload={"type": "result", "result": "done"}
        )
        self.client.force_login(self.other_user)

        response = self.client.get(
            f"/api/broker/tasks/{task.id}/stream/",
            HTTP_ACCEPT="text/event-stream",
        )

        self.assertEqual(response.status_code, 200)


class TaskLeaseRecoveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="lease@example.com",
            email="lease@example.com",
            password="secret123",
        )
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
        self.agent_client = AgentClient.objects.create(
            owner=self.user,
            name="Lease Worker",
            hostname="lease-worker.local",
            supported_agents=["codex"],
        )
        self.conversation = Conversation.objects.create(
            owner=self.user,
            cwd="/tmp/project",
            title="Lease Test",
            assigned_client=self.agent_client,
            agent_type="codex",
        )

    def test_stale_running_task_is_failed_when_client_polls(self):
        # 行为变更（FIX 4）：失联（租约超时）的 RUNNING 任务被标记为 FAILED 终态，并保留事件行，
        # 而非重排队重跑（避免重复执行与输出丢失）。租约阈值已提高到 600s，故用 15 分钟构造超时。
        task = Task.objects.create(
            conversation=self.conversation,
            assigned_client=self.agent_client,
            agent_type="codex",
            prompt="Recover me",
            status=Task.Status.RUNNING,
            cwd="/tmp/project",
            started_at=timezone.now() - timedelta(minutes=15),
            heartbeat_at=timezone.now() - timedelta(minutes=15),
            result_text="partial output",
            exit_code=123,
        )
        TaskEvent.objects.create(
            task=task, seq=1,
            payload={"type": "assistant", "message": {"content": [{"text": "partial"}]}},
        )

        response = self.client.get(f"/api/broker/tasks/queued/?client_id={self.agent_client.id}")

        self.assertEqual(response.status_code, 200)
        # 任务已是 FAILED 终态，不再出现在可执行队列里。
        self.assertEqual(response.json(), [])

        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertIsNone(task.heartbeat_at)
        self.assertIsNotNone(task.finished_at)
        self.assertIn("partial output", task.result_text)  # 保留已产出内容
        self.assertIn("租约超时", task.result_text)          # 追加失联说明
        self.assertEqual(task.event_rows.count(), 1)        # 事件行被保留

    def test_recent_running_task_is_not_requeued(self):
        Task.objects.create(
            conversation=self.conversation,
            assigned_client=self.agent_client,
            agent_type="codex",
            prompt="Still alive",
            status=Task.Status.RUNNING,
            cwd="/tmp/project",
            started_at=timezone.now(),
            heartbeat_at=timezone.now(),
        )

        response = self.client.get(f"/api/broker/tasks/queued/?client_id={self.agent_client.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_task_patch_accepts_heartbeat_at_and_clears_on_success(self):
        task = Task.objects.create(
            conversation=self.conversation,
            assigned_client=self.agent_client,
            agent_type="codex",
            prompt="Heartbeat",
            status=Task.Status.QUEUED,
            cwd="/tmp/project",
        )
        heartbeat_at = timezone.now()

        running = self.client.patch(
            f"/api/broker/tasks/{task.id}/",
            {"status": "running", "heartbeat_at": heartbeat_at.isoformat()},
            format="json",
        )

        self.assertEqual(running.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.RUNNING)
        self.assertIsNotNone(task.heartbeat_at)

        done = self.client.patch(
            f"/api/broker/tasks/{task.id}/",
            {"status": "success", "finished_at": heartbeat_at.isoformat()},
            format="json",
        )

        self.assertEqual(done.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertIsNone(task.heartbeat_at)

    def test_reap_command_fails_stale_without_client_poll(self):
        """权威回收：管理命令不依赖任何 client 轮询即可把超时任务标记为 FAILED（保留事件）。"""
        from django.core.management import call_command

        stale = Task.objects.create(
            conversation=self.conversation,
            assigned_client=self.agent_client,
            agent_type="codex",
            prompt="Reap me",
            status=Task.Status.RUNNING,
            cwd="/tmp/project",
            started_at=timezone.now() - timedelta(minutes=15),
            heartbeat_at=timezone.now() - timedelta(minutes=15),
            result_text="partial",
            exit_code=7,
        )
        TaskEvent.objects.create(task=stale, seq=1, payload={"type": "assistant"})
        fresh = Task.objects.create(
            conversation=self.conversation,
            assigned_client=self.agent_client,
            agent_type="codex",
            prompt="Keep me",
            status=Task.Status.RUNNING,
            cwd="/tmp/project",
            started_at=timezone.now(),
            heartbeat_at=timezone.now(),
        )

        call_command("reap_stale_tasks")

        stale.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(stale.status, Task.Status.FAILED)
        self.assertIsNone(stale.heartbeat_at)
        self.assertIn("partial", stale.result_text)          # 保留已产出内容
        self.assertIn("租约超时", stale.result_text)           # 追加失联说明
        self.assertEqual(stale.event_rows.count(), 1)        # 事件行被保留
        self.assertEqual(fresh.status, Task.Status.RUNNING)  # 心跳新鲜，不回收


class BrokerCredentialAdminTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="adminpass123",
        )
        self.target_user = User.objects.create_user(
            username="target@example.com",
            email="target@example.com",
            password="targetpass123",
        )
        self.client.force_login(self.admin_user)

    def test_add_view_loads(self):
        response = self.client.get("/admin/OnlineBroker/brokerclientcredential/add/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Secret Key")

    def test_add_view_creates_hashed_secret(self):
        response = self.client.post(
            "/admin/OnlineBroker/brokerclientcredential/add/",
            {
                "name": "Office PC",
                "user": str(self.target_user.pk),
                "secret_key": "plain-secret",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        credential = BrokerClientCredential.objects.get(name="Office PC")
        self.assertTrue(check_password("plain-secret", credential.secret_hash))


class AuthCsrfExemptTests(TestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)

    def test_login_endpoint_allows_json_without_csrf_token(self):
        User.objects.create_user(
            username="auth@example.com",
            email="auth@example.com",
            password="secret12345",
        )

        response = self.client.post(
            "/api/auth/login/",
            {
                "email": "auth@example.com",
                "password": "secret12345",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.json())

    def test_register_endpoint_allows_json_without_csrf_token(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "new-auth@example.com",
                "password": "secret12345",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertIn("token", response.json())


class ConversationTitleAndListTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="owner@example.com", email="owner@example.com", password="secret123",
        )
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
        self.agent = AgentClient.objects.create(
            owner=self.user, name="PC", hostname="pc.local", supported_agents=["claude_code"],
        )

    def _conv(self, **kw):
        return Conversation.objects.create(owner=self.user, cwd="/tmp/p", agent_type="claude_code", **kw)

    def test_list_annotates_message_count_and_last_result(self):
        conv = self._conv()
        t1 = conv.tasks.create(prompt="a", cwd="/tmp/p", status="success", result_text="第一轮结果")
        conv.messages.create(prompt="a", task=t1)
        t2 = conv.tasks.create(prompt="b", cwd="/tmp/p", status="success", result_text="给登录加了双因子认证")
        conv.messages.create(prompt="b", task=t2)

        row = self.client.get("/api/broker/conversations/").json()[0]
        self.assertEqual(row["message_count"], 2)
        self.assertEqual(row["last_result"], "给登录加了双因子认证")  # 取最近一条有结果的任务

    def test_list_last_result_is_truncated_and_whitespace_collapsed(self):
        conv = self._conv()
        conv.tasks.create(prompt="x", cwd="/tmp/p", status="success", result_text="行一\n\n  行二   " + "y" * 300)
        row = self.client.get("/api/broker/conversations/").json()[0]
        self.assertLessEqual(len(row["last_result"]), 140)
        self.assertNotIn("\n", row["last_result"])

    def test_list_respects_limit(self):
        for _ in range(3):
            self._conv()
        rows = self.client.get("/api/broker/conversations/?limit=2").json()
        self.assertEqual(len(rows), 2)

    def test_user_rename_sets_custom_and_blocks_auto_overwrite(self):
        conv = self._conv()
        # 用户重命名
        r = self.client.patch(f"/api/broker/conversations/{conv.id}/", {"title": "我的标题"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["title_custom"])
        # 自动摘要不得覆盖用户标题
        r2 = self.client.patch(f"/api/broker/conversations/{conv.id}/", {"title": "AI 摘要", "auto": True}, format="json")
        self.assertEqual(r2.json()["title"], "我的标题")
        self.assertTrue(r2.json()["title_custom"])

    def test_auto_title_applies_when_not_custom(self):
        conv = self._conv()
        r = self.client.patch(f"/api/broker/conversations/{conv.id}/", {"title": "AI 摘要", "auto": True}, format="json")
        self.assertEqual(r.json()["title"], "AI 摘要")
        self.assertFalse(r.json()["title_custom"])

    def test_open_marks_active_and_close_clears(self):
        conv = self._conv(assigned_client=self.agent)
        self.client.post(f"/api/broker/conversations/{conv.id}/open/")
        active = self.client.get(f"/api/broker/conversations/active/?client_id={self.agent.id}").json()
        self.assertEqual([a["id"] for a in active], [str(conv.id)])
        self.client.post(f"/api/broker/conversations/{conv.id}/close/")
        active2 = self.client.get(f"/api/broker/conversations/active/?client_id={self.agent.id}").json()
        self.assertEqual(active2, [])


class TaskEventStorageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ev@example.com", email="ev@example.com", password="secret123",
        )
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
        self.conv = Conversation.objects.create(
            owner=self.user, cwd="/tmp/p", title="ev", agent_type="claude_code",
        )
        self.task = Task.objects.create(
            conversation=self.conv, agent_type="claude_code", prompt="x",
            status=Task.Status.RUNNING, cwd="/tmp/p",
        )

    def test_append_creates_sequenced_rows(self):
        r1 = self.client.post(
            f"/api/broker/tasks/{self.task.id}/events/",
            {"events": [{"type": "assistant"}, {"type": "tool_call"}]}, format="json",
        )
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json()["appended"], 2)
        r2 = self.client.post(
            f"/api/broker/tasks/{self.task.id}/events/",
            {"events": [{"type": "result"}]}, format="json",
        )
        self.assertEqual(r2.json()["appended"], 1)
        seqs = list(self.task.event_rows.order_by("seq").values_list("seq", flat=True))
        self.assertEqual(seqs, [1, 2, 3])  # 连续自增，跨多次请求

    def test_append_extracts_session_id(self):
        self.client.post(
            f"/api/broker/tasks/{self.task.id}/events/",
            {"events": [{"type": "system", "subtype": "init", "session_id": "sess-xyz"}]},
            format="json",
        )
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.session_id, "sess-xyz")

    def test_detail_events_capped_to_last_200(self):
        TaskEvent.objects.bulk_create([
            TaskEvent(task=self.task, seq=i, payload={"i": i}) for i in range(1, 251)
        ])
        data = self.client.get(f"/api/broker/tasks/{self.task.id}/").json()
        self.assertEqual(len(data["events"]), 200)          # 截到最近 200 条
        self.assertEqual(data["events"][0]["i"], 51)        # 时间顺序：从第 51 条起
        self.assertEqual(data["events"][-1]["i"], 250)


class ConversationForceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="force@example.com", email="force@example.com", password="secret123",
        )
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
        self.agent = AgentClient.objects.create(
            owner=self.user, name="PC", hostname="pc.local", supported_agents=["claude_code"],
        )

    def test_force_defaults_false_and_is_inherited_by_tasks(self):
        r = self.client.post("/api/broker/conversations/", {
            "cwd": "/tmp/p", "agent_type": "claude_code", "client_id": str(self.agent.id),
        }, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertFalse(r.json()["force"])
        conv_id = r.json()["id"]
        # 消息未显式 force，会话也未开 → 任务 force False
        m = self.client.post(f"/api/broker/conversations/{conv_id}/messages/",
                             {"prompt": "hi"}, format="json")
        task = Task.objects.get(pk=m.json()["task_id"])
        self.assertFalse(task.force)

    def test_force_conversation_forces_all_tasks_and_active_reports_it(self):
        r = self.client.post("/api/broker/conversations/", {
            "cwd": "/tmp/p", "agent_type": "claude_code", "client_id": str(self.agent.id),
            "force": True,
        }, format="json")
        self.assertTrue(r.json()["force"])
        conv_id = r.json()["id"]
        # 即使消息没带 force，会话级 force 也兜底
        m = self.client.post(f"/api/broker/conversations/{conv_id}/messages/",
                             {"prompt": "hi"}, format="json")
        task = Task.objects.get(pk=m.json()["task_id"])
        self.assertTrue(task.force)
        # active 接口把 force 透传给 LocalBroker
        self.client.post(f"/api/broker/conversations/{conv_id}/open/")
        active = self.client.get(f"/api/broker/conversations/active/?client_id={self.agent.id}").json()
        self.assertEqual(active[0]["force"], True)


class ConversationOptionsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="opt@example.com", email="opt@example.com", password="secret123",
        )
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
        self.agent = AgentClient.objects.create(
            owner=self.user, name="PC", hostname="pc.local", supported_agents=["claude_code"],
        )

    def test_options_stored_inherited_and_exposed(self):
        r = self.client.post("/api/broker/conversations/", {
            "cwd": "/tmp/p", "agent_type": "claude_code", "client_id": str(self.agent.id),
            "options": {"permission_mode": "plan", "model": "sonnet"},
        }, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["options"], {"permission_mode": "plan", "model": "sonnet"})
        conv_id = r.json()["id"]
        # 任务继承会话 options
        m = self.client.post(f"/api/broker/conversations/{conv_id}/messages/",
                             {"prompt": "hi"}, format="json")
        task = Task.objects.get(pk=m.json()["task_id"])
        self.assertEqual(task.options, {"permission_mode": "plan", "model": "sonnet"})
        # active 接口把 options 透传给 LocalBroker（用于常驻进程启动参数）
        self.client.post(f"/api/broker/conversations/{conv_id}/open/")
        active = self.client.get(f"/api/broker/conversations/active/?client_id={self.agent.id}").json()
        self.assertEqual(active[0]["options"], {"permission_mode": "plan", "model": "sonnet"})

    def test_options_default_empty(self):
        r = self.client.post("/api/broker/conversations/", {
            "cwd": "/tmp/p", "agent_type": "claude_code", "client_id": str(self.agent.id),
        }, format="json")
        self.assertEqual(r.json()["options"], {})


class ConversationControlTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ctl@example.com", email="ctl@example.com", password="secret123",
        )
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
        self.agent = AgentClient.objects.create(
            owner=self.user, name="PC", hostname="pc.local", supported_agents=["claude_code"],
        )
        self.conv = Conversation.objects.create(
            owner=self.user, cwd="/tmp/p", agent_type="claude_code", assigned_client=self.agent,
        )

    def test_set_permission_mode_enqueues_and_persists_options(self):
        r = self.client.post(f"/api/broker/conversations/{self.conv.id}/control/",
                             {"action": "set_permission_mode", "value": "plan"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["action"], "set_permission_mode")
        self.assertEqual(r.json()["status"], "pending")
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.options.get("permission_mode"), "plan")  # 持久化

    def test_bypass_sets_force(self):
        self.client.post(f"/api/broker/conversations/{self.conv.id}/control/",
                         {"action": "set_permission_mode", "value": "bypassPermissions"}, format="json")
        self.conv.refresh_from_db()
        self.assertTrue(self.conv.force)

    def test_unsupported_action_rejected(self):
        r = self.client.post(f"/api/broker/conversations/{self.conv.id}/control/",
                             {"action": "rm_rf", "value": "/"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_pending_pull_and_ack_roundtrip(self):
        self.client.post(f"/api/broker/conversations/{self.conv.id}/control/",
                         {"action": "interrupt"}, format="json")
        pend = self.client.get(f"/api/broker/controls/pending/?client_id={self.agent.id}").json()
        self.assertEqual(len(pend), 1)
        cid = pend[0]["id"]
        self.assertEqual(pend[0]["action"], "interrupt")
        # broker acks
        self.client.patch(f"/api/broker/controls/{cid}/",
                          {"status": "applied", "result": "ok"}, format="json")
        # no longer pending
        pend2 = self.client.get(f"/api/broker/controls/pending/?client_id={self.agent.id}").json()
        self.assertEqual(pend2, [])


class FsBrowseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="fs@example.com", email="fs@example.com", password="secret123",
        )
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
        self.agent = AgentClient.objects.create(
            owner=self.user, name="FS PC", hostname="fs-pc.local", supported_agents=["codex"],
        )

    def test_browse_create_pull_ack_roundtrip(self):
        # Web 建请求
        created = self.client.post(
            "/api/broker/fs/browse/", {"client_id": str(self.agent.id), "path": ""}, format="json",
        )
        self.assertEqual(created.status_code, 201)
        req_id = created.json()["id"]
        self.assertEqual(created.json()["status"], "pending")

        # broker 拉取待处理
        pend = self.client.get(f"/api/broker/fs/pending/?client_id={self.agent.id}").json()
        self.assertEqual([p["id"] for p in pend], [req_id])

        # broker 回传结果
        ack = self.client.patch(
            f"/api/broker/fs/requests/{req_id}/",
            {
                "status": "done",
                "listed_path": "/home/u",
                "parent_path": "/home",
                "entries": [{"name": "proj", "path": "/home/u/proj", "is_dir": True}],
                "error": "",
            },
            format="json",
        )
        self.assertEqual(ack.status_code, 200)

        # Web 取结果
        got = self.client.get(f"/api/broker/fs/browse/{req_id}/").json()
        self.assertEqual(got["status"], "done")
        self.assertEqual(got["listed_path"], "/home/u")
        self.assertEqual(got["entries"][0]["name"], "proj")

        # 不再 pending
        pend2 = self.client.get(f"/api/broker/fs/pending/?client_id={self.agent.id}").json()
        self.assertEqual(pend2, [])

    def test_browse_rejects_foreign_client(self):
        other = User.objects.create_user(
            username="other-fs@example.com", email="other-fs@example.com", password="secret123",
        )
        foreign = AgentClient.objects.create(owner=other, name="Theirs", hostname="theirs.local")
        r = self.client.post(
            "/api/broker/fs/browse/", {"client_id": str(foreign.id), "path": "/"}, format="json",
        )
        self.assertEqual(r.status_code, 404)

    def test_browse_result_isolated_between_users(self):
        from .models import FsBrowseRequest
        req = FsBrowseRequest.objects.create(client=self.agent, path="")
        other = User.objects.create_user(
            username="snoop@example.com", email="snoop@example.com", password="secret123",
        )
        other_token = Token.objects.create(user=other)
        snoop = APIClient()
        snoop.credentials(HTTP_AUTHORIZATION=f"Token {other_token.key}")
        r = snoop.get(f"/api/broker/fs/browse/{req.id}/")
        self.assertEqual(r.status_code, 404)


class PermissionRequestTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="perm@example.com", email="perm@example.com", password="secret123",
        )
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
        self.agent = AgentClient.objects.create(
            owner=self.user, name="Perm PC", hostname="perm-pc.local", supported_agents=["claude_code"],
        )
        self.conv = Conversation.objects.create(
            owner=self.user, cwd="/tmp/project", title="Perm", assigned_client=self.agent,
            agent_type="claude_code",
        )

    def test_create_poll_answer_roundtrip(self):
        created = self.client.post(
            "/api/broker/permissions/",
            {
                "conversation_id": str(self.conv.id),
                "request_id": "ctl_abc123",
                "tool_name": "Bash",
                "tool_input": {"command": "rm -rf build"},
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        pid = created.json()["id"]
        self.assertEqual(created.json()["status"], "pending")

        pend = self.client.get(f"/api/broker/conversations/{self.conv.id}/permissions/pending/").json()
        self.assertEqual([p["id"] for p in pend], [pid])

        ans = self.client.patch(
            f"/api/broker/permissions/{pid}/", {"decision": "allow", "remember": True}, format="json",
        )
        self.assertEqual(ans.status_code, 200)
        self.assertEqual(ans.json()["status"], "allowed")
        self.assertTrue(ans.json()["remember"])

        got = self.client.get(f"/api/broker/permissions/{pid}/").json()
        self.assertEqual(got["status"], "allowed")

        pend2 = self.client.get(f"/api/broker/conversations/{self.conv.id}/permissions/pending/").json()
        self.assertEqual(pend2, [])

    def test_deny_answer(self):
        created = self.client.post(
            "/api/broker/permissions/",
            {"conversation_id": str(self.conv.id), "tool_name": "Write", "tool_input": {"path": "/etc/x"}},
            format="json",
        )
        pid = created.json()["id"]
        ans = self.client.patch(f"/api/broker/permissions/{pid}/", {"decision": "deny"}, format="json")
        self.assertEqual(ans.json()["status"], "denied")

    def test_answer_is_idempotent(self):
        created = self.client.post(
            "/api/broker/permissions/",
            {"conversation_id": str(self.conv.id), "tool_name": "Bash", "tool_input": {}},
            format="json",
        )
        pid = created.json()["id"]
        self.client.patch(f"/api/broker/permissions/{pid}/", {"decision": "allow"}, format="json")
        second = self.client.patch(f"/api/broker/permissions/{pid}/", {"decision": "deny"}, format="json")
        self.assertEqual(second.json()["status"], "allowed")

    def test_foreign_conversation_rejected(self):
        other = User.objects.create_user(
            username="other-perm@example.com", email="other-perm@example.com", password="secret123",
        )
        other_conv = Conversation.objects.create(owner=other, cwd="/tmp/x", agent_type="claude_code")
        r = self.client.post(
            "/api/broker/permissions/",
            {"conversation_id": str(other_conv.id), "tool_name": "Bash", "tool_input": {}},
            format="json",
        )
        self.assertEqual(r.status_code, 404)


class FileTransferTests(TestCase):
    def setUp(self):
        import tempfile
        self.media = tempfile.mkdtemp()
        self.override = self.settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.user = User.objects.create_user(
            username="ft@example.com", email="ft@example.com", password="secret123",
        )
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
        self.agent = AgentClient.objects.create(
            owner=self.user, name="FT PC", hostname="ft-pc.local", supported_agents=["claude_code"],
        )
        self.conv = Conversation.objects.create(
            owner=self.user, cwd="/tmp/project", title="FT", assigned_client=self.agent,
            agent_type="claude_code",
        )

    def tearDown(self):
        self.override.disable()

    def test_web_initiated_request_pending_upload_download(self):
        import base64
        # web 发起：建 pending 传输
        created = self.client.post(
            "/api/broker/files/request/",
            {"client_id": str(self.agent.id), "conversation_id": str(self.conv.id),
             "path": "/home/u/report.pdf"},
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        tid = created.json()["id"]
        self.assertEqual(created.json()["status"], "pending")
        self.assertEqual(created.json()["filename"], "report.pdf")

        # broker 拉取 pending
        pend = self.client.get(f"/api/broker/files/pending/?client_id={self.agent.id}").json()
        self.assertEqual([p["id"] for p in pend], [tid])

        # broker 上传内容（base64）
        payload = b"hello remote file"
        up = self.client.post(
            f"/api/broker/files/{tid}/upload/",
            {"filename": "report.pdf", "content_type": "application/pdf",
             "content_b64": base64.b64encode(payload).decode()},
            format="json",
        )
        self.assertEqual(up.status_code, 200)
        self.assertEqual(up.json()["status"], "ready")
        self.assertEqual(up.json()["size"], len(payload))

        # 不再 pending
        pend2 = self.client.get(f"/api/broker/files/pending/?client_id={self.agent.id}").json()
        self.assertEqual(pend2, [])

        # web 下载，校验内容与附件头
        dl = self.client.get(f"/api/broker/files/{tid}/download/")
        self.assertEqual(dl.status_code, 200)
        self.assertEqual(b"".join(dl.streaming_content), payload)
        self.assertIn("attachment", dl["Content-Disposition"])

    def test_agent_initiated_appears_in_conversation_files(self):
        import base64
        created = self.client.post(
            "/api/broker/files/request/",
            {"client_id": str(self.agent.id), "conversation_id": str(self.conv.id),
             "path": "/home/u/out.txt", "agent_initiated": True},
            format="json",
        )
        tid = created.json()["id"]
        self.assertTrue(created.json()["agent_initiated"])
        self.client.post(
            f"/api/broker/files/{tid}/upload/",
            {"filename": "out.txt", "content_b64": base64.b64encode(b"data").decode()},
            format="json",
        )
        files = self.client.get(f"/api/broker/conversations/{self.conv.id}/files/").json()
        self.assertEqual([f["id"] for f in files], [tid])
        self.assertEqual(files[0]["status"], "ready")

    def test_oversize_upload_rejected(self):
        import base64
        with self.settings(FILE_TRANSFER_MAX_BYTES=8):
            created = self.client.post(
                "/api/broker/files/request/",
                {"client_id": str(self.agent.id), "conversation_id": str(self.conv.id),
                 "path": "/big.bin"}, format="json",
            )
            tid = created.json()["id"]
            up = self.client.post(
                f"/api/broker/files/{tid}/upload/",
                {"filename": "big.bin", "content_b64": base64.b64encode(b"0123456789").decode()},
                format="json",
            )
            self.assertEqual(up.status_code, 413)

    def test_foreign_client_rejected(self):
        other = User.objects.create_user(username="o-ft@example.com", email="o-ft@example.com", password="x12345678")
        foreign = AgentClient.objects.create(owner=other, name="Theirs", hostname="t.local")
        r = self.client.post(
            "/api/broker/files/request/",
            {"client_id": str(foreign.id), "path": "/x"}, format="json",
        )
        self.assertEqual(r.status_code, 404)

    def test_download_requires_ready_and_owner(self):
        # 未 ready 不可下载
        created = self.client.post(
            "/api/broker/files/request/",
            {"client_id": str(self.agent.id), "conversation_id": str(self.conv.id),
             "path": "/x.txt"}, format="json",
        )
        tid = created.json()["id"]
        self.assertEqual(self.client.get(f"/api/broker/files/{tid}/download/").status_code, 404)
