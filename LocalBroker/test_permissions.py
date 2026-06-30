"""test_permissions.py – 交互式工具审批：网关轮询 + 常驻会话 control_request 拦截。"""

import json
import time
import unittest

try:
    from LocalBroker.broker_worker import make_permission_gateway
    from LocalBroker.session_manager import ClaudeWarmSession
except ModuleNotFoundError:
    from broker_worker import make_permission_gateway
    from session_manager import ClaudeWarmSession


class _FakeStdin:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass


class _FakeProc:
    def __init__(self, lines):
        self.stdin = _FakeStdin()
        self.stdout = iter(lines)

    def poll(self):
        return None


class GatewayTests(unittest.TestCase):
    def _gw(self, polls, remembered=None, **kw):
        it = iter(polls)
        self.emitted = []
        return make_permission_gateway(
            "conv", "task", "http://b", "tok",
            lambda ev: self.emitted.append(ev),
            remembered if remembered is not None else set(),
            create_fn=lambda *a, **k: {"id": "p1"},
            get_fn=lambda *a, **k: next(it),
            poll_interval=0.01, timeout_sec=10, sleep_fn=lambda s: None,
            **kw,
        )

    def test_allow_emits_request_and_resolved(self):
        gw = self._gw([{"status": "pending"}, {"status": "allowed", "remember": False}])
        self.assertTrue(gw("r1", "Bash", {"command": "ls"}))
        self.assertEqual([e["type"] for e in self.emitted], ["permission_request", "permission_resolved"])
        self.assertEqual(self.emitted[-1]["decision"], "allow")

    def test_deny(self):
        gw = self._gw([{"status": "denied"}])
        self.assertFalse(gw("r1", "Write", {"path": "/etc/x"}))
        self.assertEqual(self.emitted[-1]["decision"], "deny")

    def test_timeout_defaults_to_deny(self):
        # 全程 pending：很短的超时窗口下应安全拒绝。
        it = iter(lambda: {"status": "pending"}, None)
        emitted = []
        gw = make_permission_gateway(
            "conv", "task", "http://b", "tok", lambda ev: emitted.append(ev), set(),
            create_fn=lambda *a, **k: {"id": "p1"},
            get_fn=lambda *a, **k: {"status": "pending"},
            poll_interval=0.01, timeout_sec=0.03, sleep_fn=lambda s: None,
        )
        self.assertFalse(gw("r1", "Bash", {}))

    def test_remember_short_circuits_next_call(self):
        remembered = set()
        gw = self._gw([{"status": "allowed", "remember": True}], remembered=remembered)
        self.assertTrue(gw("r1", "Bash", {}))
        self.assertIn("Bash", remembered)
        # 第二次同名工具：直接放行，不再建请求/发事件
        before = len(self.emitted)
        self.assertTrue(gw("r2", "Bash", {}))
        self.assertEqual(len(self.emitted), before)

    def test_create_failure_denies(self):
        emitted = []
        gw = make_permission_gateway(
            "conv", "task", "http://b", "tok", lambda ev: emitted.append(ev), set(),
            create_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
            get_fn=lambda *a, **k: {"status": "allowed"},
            poll_interval=0.01, timeout_sec=1, sleep_fn=lambda s: None,
        )
        self.assertFalse(gw("r1", "Bash", {}))


class ReadLoopInterceptTests(unittest.TestCase):
    def _run_loop_with(self, decision_cb):
        sess = ClaudeWarmSession("c1", "/tmp", "claude_code")
        line = json.dumps({
            "type": "control_request", "request_id": "req1",
            "request": {"subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "ls"}},
        })
        sess._proc = _FakeProc([line + "\n"])
        sess._on_permission = decision_cb
        sess._read_loop()  # stdout 迭代结束后返回
        # 等待拦截线程把 control_response 写回 stdin
        for _ in range(100):
            if sess._proc.stdin.written:
                break
            time.sleep(0.01)
        return sess

    def test_allow_writes_control_response(self):
        seen = {}

        def cb(rid, tool, inp):
            seen.update(rid=rid, tool=tool, inp=inp)
            return True

        sess = self._run_loop_with(cb)
        self.assertEqual(seen["tool"], "Bash")
        self.assertEqual(seen["inp"], {"command": "ls"})
        resp = json.loads(sess._proc.stdin.written[-1])
        self.assertEqual(resp["type"], "control_response")
        self.assertEqual(resp["response"]["request_id"], "req1")
        self.assertEqual(resp["response"]["response"]["behavior"], "allow")

    def test_deny_writes_deny_behavior(self):
        sess = self._run_loop_with(lambda rid, tool, inp: False)
        resp = json.loads(sess._proc.stdin.written[-1])
        self.assertEqual(resp["response"]["response"]["behavior"], "deny")

    def test_non_permission_control_request_ignored(self):
        sess = ClaudeWarmSession("c1", "/tmp", "claude_code")
        line = json.dumps({
            "type": "control_request", "request_id": "x",
            "request": {"subtype": "something_else"},
        })
        sess._proc = _FakeProc([line + "\n"])
        sess._on_permission = lambda *a: True
        sess._read_loop()
        time.sleep(0.05)
        self.assertEqual(sess._proc.stdin.written, [])  # 未回写


if __name__ == "__main__":
    unittest.main()


class HookDecideTests(unittest.TestCase):
    def _decide(self, polls, created_status="pending"):
        try:
            from LocalBroker.teleagent_permission_hook import decide
        except ModuleNotFoundError:
            from teleagent_permission_hook import decide
        it = iter(polls)
        return decide(
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            base="http://x", token="t", conversation_id="conv1",
            timeout_sec=5, poll_interval=0.01, sleep_fn=lambda s: None,
            create_fn=lambda cid, tool, **kw: {"id": "p1", "status": created_status},
            get_fn=lambda pid, **kw: {"status": next(it)},
        )

    def test_allow_after_poll(self):
        d, _ = self._decide(["pending", "allowed"])
        self.assertEqual(d, "allow")

    def test_deny_after_poll(self):
        d, _ = self._decide(["denied"])
        self.assertEqual(d, "deny")

    def test_immediate_allowed_when_remembered(self):
        # create 直接回 allowed（会话已「一直允许」）→ 不轮询即放行
        d, _ = self._decide([], created_status="allowed")
        self.assertEqual(d, "allow")

    def test_timeout_denies(self):
        d, reason = self._decide(["pending"] * 50)
        self.assertEqual(d, "deny")
        self.assertIn("超时", reason)


class InteractivePermissionsToggleTests(unittest.TestCase):
    def setUp(self):
        try:
            from LocalBroker.session_manager import (
                ClaudeWarmSession, set_interactive_permissions, interactive_permissions_enabled,
            )
        except ModuleNotFoundError:
            from session_manager import (
                ClaudeWarmSession, set_interactive_permissions, interactive_permissions_enabled,
            )
        self.CWS = ClaudeWarmSession
        self.set_flag = set_interactive_permissions
        self.get_flag = interactive_permissions_enabled
        self._orig = interactive_permissions_enabled()

    def tearDown(self):
        self.set_flag(self._orig)

    def _args(self):
        s = self.CWS("c", "/tmp", "claude_code", which=lambda c: "/usr/bin/claude")
        return s._build_launch_args()

    def test_toggle_controls_hook_settings_injection(self):
        self.set_flag(False)
        self.assertFalse(self.get_flag())
        self.assertNotIn("--settings", self._args())
        self.set_flag(True)
        self.assertTrue(self.get_flag())
        self.assertIn("--settings", self._args())

    def test_interactive_strips_skip_permissions(self):
        # bypassPermissions 会绕过 hook：交互式开启时应被剥离，回到默认模式让 hook 生效。
        self.set_flag(True)
        s = self.CWS("c", "/tmp", "claude_code", force=True,
                     options={"permission_mode": "bypassPermissions"}, which=lambda c: "/usr/bin/claude")
        args = s._build_launch_args()
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertNotIn("bypassPermissions", args)
        self.assertIn("--settings", args)
