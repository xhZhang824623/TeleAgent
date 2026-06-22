from django.contrib.auth.models import User
from django.contrib.auth.hashers import check_password
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from types import SimpleNamespace

from .models import AgentClient, BrokerClientCredential, Conversation, Task, TaskEvent
from .views import _iter_task_events


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

    def test_stale_running_task_is_requeued_when_client_polls(self):
        task = Task.objects.create(
            conversation=self.conversation,
            assigned_client=self.agent_client,
            agent_type="codex",
            prompt="Recover me",
            status=Task.Status.RUNNING,
            cwd="/tmp/project",
            started_at=timezone.now() - timedelta(minutes=5),
            heartbeat_at=timezone.now() - timedelta(minutes=5),
            result_text="partial output",
            exit_code=123,
        )
        TaskEvent.objects.create(
            task=task, seq=1,
            payload={"type": "assistant", "message": {"content": [{"text": "partial"}]}},
        )

        response = self.client.get(f"/api/broker/tasks/queued/?client_id={self.agent_client.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()], [str(task.id)])

        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.QUEUED)
        self.assertIsNone(task.started_at)
        self.assertIsNone(task.finished_at)
        self.assertIsNone(task.heartbeat_at)
        self.assertIsNone(task.result_text)
        self.assertIsNone(task.exit_code)
        self.assertEqual(task.event_rows.count(), 0)  # 重新入队会清掉上一轮事件行

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
