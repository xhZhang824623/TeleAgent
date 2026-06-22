"""
test_session_manager.py – 常驻会话路由与回收单测。

不依赖真实 Agent CLI：用 FakeProc 注入 stdout 事件、用 fake factory 构造可控会话。
运行：python -m pytest LocalBroker/test_session_manager.py   或   python LocalBroker/test_session_manager.py
"""

import io
import json
import threading
import time
import unittest

try:
    from LocalBroker.session_manager import (
        SessionManager, ClaudeWarmSession, WarmSession, is_warm_capable,
    )
except ModuleNotFoundError:
    from session_manager import (
        SessionManager, ClaudeWarmSession, WarmSession, is_warm_capable,
    )


class _FakeStdin:
    def __init__(self):
        self.written = []
        self.closed = False

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakeProc:
    """最小化的 Popen 替身：poll() 返回 None 表示存活。"""
    def __init__(self):
        self.stdin = _FakeStdin()
        self._returncode = None

    def poll(self):
        return self._returncode

    def terminate(self):
        self._returncode = -15

    def kill(self):
        self._returncode = -9

    def wait(self, timeout=None):
        self._returncode = self._returncode or 0
        return self._returncode


class _FakeWarmSession(WarmSession):
    """可控的常驻会话替身，用于 SessionManager 测试。"""
    def __init__(self, conv_id, cwd, agent_type, *, force=False, resume_session_id=None, options=None):
        super().__init__(conv_id, cwd, agent_type, force=force, resume_session_id=resume_session_id, options=options)
        self._alive = True
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self._alive

    def close(self):
        self.closed = True
        self._alive = False


def _fake_factory(conv_id, cwd, agent_type, *, force=False, resume_session_id=None, options=None):
    if agent_type != "claude_code":
        return None
    return _FakeWarmSession(conv_id, cwd, agent_type, force=force, resume_session_id=resume_session_id)


class ClaudeWarmSessionTurnTest(unittest.TestCase):
    def _session_with_events(self, events):
        s = ClaudeWarmSession("c1", "/tmp", "claude_code")
        s._proc = _FakeProc()
        for ev in events:
            s._events.put(("event", ev))
        return s

    def test_run_turn_success_collects_result_and_session_id(self):
        events = [
            {"type": "system", "subtype": "init", "session_id": "sess-123"},
            {"type": "assistant", "message": {"content": [{"text": "hi"}]}},
            {"type": "result", "subtype": "success", "result": "done", "is_error": False},
        ]
        s = self._session_with_events(events)
        seen = []
        turn = s.run_turn("t1", "hello", lambda tid, obj: seen.append(obj), timeout_sec=5)

        self.assertEqual(turn.status, "success")
        self.assertEqual(turn.result_text, "done")
        self.assertEqual(turn.session_id, "sess-123")
        self.assertEqual(s.session_id, "sess-123")
        # prompt 以 stream-json user 信封写入 stdin
        self.assertEqual(len(s._proc.stdin.written), 1)
        envelope = json.loads(s._proc.stdin.written[0])
        self.assertEqual(envelope["type"], "user")
        self.assertEqual(envelope["message"]["content"][0]["text"], "hello")
        # 事件被实时上抛
        self.assertEqual(len(seen), 3)

    def test_run_turn_result_error_marks_failed(self):
        events = [{"type": "result", "subtype": "error", "result": "boom", "is_error": True}]
        s = self._session_with_events(events)
        turn = s.run_turn("t1", "hi", lambda *a: None, timeout_sec=5)
        self.assertEqual(turn.status, "failed")
        self.assertEqual(turn.result_text, "boom")

    def test_run_turn_eof_is_failure(self):
        s = ClaudeWarmSession("c1", "/tmp", "claude_code")
        s._proc = _FakeProc()
        s._events.put(("eof", None))
        turn = s.run_turn("t1", "hi", lambda *a: None, timeout_sec=5)
        self.assertEqual(turn.status, "failed")

    def test_run_turn_dead_process(self):
        s = ClaudeWarmSession("c1", "/tmp", "claude_code")
        proc = _FakeProc()
        proc._returncode = 1  # 已退出
        s._proc = proc
        turn = s.run_turn("t1", "hi", lambda *a: None, timeout_sec=5)
        self.assertEqual(turn.status, "failed")


class SessionManagerTest(unittest.TestCase):
    def setUp(self):
        self.mgr = SessionManager(idle_ttl_sec=0.0, max_sessions=3, factory=_fake_factory)

    def test_non_warm_agent_returns_none(self):
        self.assertFalse(is_warm_capable("codex"))
        self.assertIsNone(self.mgr.ensure("c1", "/tmp", "codex"))
        self.assertIsNone(self.mgr.ensure("c2", "/tmp", "cursor_agent"))

    def test_ensure_starts_and_is_idempotent(self):
        s1 = self.mgr.ensure("c1", "/tmp", "claude_code")
        self.assertIsNotNone(s1)
        self.assertTrue(s1.started)
        s2 = self.mgr.ensure("c1", "/tmp", "claude_code")
        self.assertIs(s1, s2)  # 存活则复用同一进程
        self.assertIs(self.mgr.get("c1"), s1)

    def test_ensure_rebuilds_dead_session_with_resume(self):
        s1 = self.mgr.ensure("c1", "/tmp", "claude_code", resume_session_id=None)
        s1.session_id = "sess-abc"
        s1._alive = False  # 进程死亡
        s2 = self.mgr.ensure("c1", "/tmp", "claude_code")
        self.assertIsNot(s1, s2)
        self.assertTrue(s1.closed)
        # 旧 session_id 透传给新会话用于 resume 冷启动
        self.assertEqual(s2.session_id, "sess-abc")

    def test_reconcile_evicts_closed_idle_session(self):
        s1 = self.mgr.ensure("c1", "/tmp", "claude_code")
        # active 集合为空 + idle_ttl=0 → 应被回收
        closed = self.mgr.reconcile([])
        self.assertIn("c1", closed)
        self.assertTrue(s1.closed)
        self.assertIsNone(self.mgr.get("c1"))

    def test_reconcile_keeps_active_session(self):
        s1 = self.mgr.ensure("c1", "/tmp", "claude_code")
        closed = self.mgr.reconcile(["c1"])
        self.assertEqual(closed, [])
        self.assertIs(self.mgr.get("c1"), s1)

    def test_reconcile_evicts_dead_even_if_active(self):
        s1 = self.mgr.ensure("c1", "/tmp", "claude_code")
        s1._alive = False
        closed = self.mgr.reconcile(["c1"])
        self.assertIn("c1", closed)

    def test_max_sessions_lru_eviction(self):
        mgr = SessionManager(idle_ttl_sec=9999, max_sessions=2, factory=_fake_factory)
        a = mgr.ensure("a", "/tmp", "claude_code")
        time.sleep(0.01)
        a.last_activity = time.time()  # a 最近活跃
        b = mgr.ensure("b", "/tmp", "claude_code")
        time.sleep(0.01)
        b.last_activity = time.time()
        # 加入 c 触发超容量；最久未活跃者（a）被回收
        c = mgr.ensure("c", "/tmp", "claude_code")
        ids = set(mgr.active_conv_ids())
        self.assertIn("c", ids)
        self.assertEqual(len(ids), 2)
        self.assertTrue(a.closed)


class WarmSessionControlTest(unittest.TestCase):
    def test_send_control_roundtrip(self):
        import json
        s = ClaudeWarmSession("c1", "/tmp", "claude_code")
        s._proc = _FakeProc()

        def responder():
            for _ in range(400):
                if s._proc.stdin.written:
                    break
                time.sleep(0.002)
            rid = json.loads(s._proc.stdin.written[-1])["request_id"]
            resp = {"type": "control_response",
                    "response": {"subtype": "success", "request_id": rid, "response": {"mode": "plan"}}}
            with s._control_cv:
                s._control_responses[rid] = resp
                s._control_cv.notify_all()

        threading.Thread(target=responder, daemon=True).start()
        out = s.send_control("set_permission_mode", {"mode": "plan"}, timeout=3)
        self.assertIsNotNone(out)
        self.assertEqual(out["response"]["subtype"], "success")
        req = json.loads(s._proc.stdin.written[-1])
        self.assertEqual(req["type"], "control_request")
        self.assertEqual(req["request"]["subtype"], "set_permission_mode")
        self.assertEqual(req["request"]["mode"], "plan")

    def test_send_control_times_out_without_response(self):
        s = ClaudeWarmSession("c1", "/tmp", "claude_code")
        s._proc = _FakeProc()
        self.assertIsNone(s.send_control("interrupt", timeout=0.2))

    def test_send_control_dead_process_returns_none(self):
        s = ClaudeWarmSession("c1", "/tmp", "claude_code")
        proc = _FakeProc()
        proc._returncode = 1
        s._proc = proc
        self.assertIsNone(s.send_control("interrupt"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
