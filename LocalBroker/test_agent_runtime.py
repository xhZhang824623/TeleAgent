import unittest
import importlib.util
import json
import pathlib
import sys
import tempfile
from pathlib import Path
from unittest import mock


class AgentRuntimeTests(unittest.TestCase):
    def _direct_script_sys_path(self, localbroker_dir: pathlib.Path):
        root_dir = str(localbroker_dir.parent)
        return [
            str(localbroker_dir),
            *[
                entry
                for entry in sys.path
                if entry and entry != root_dir
            ],
        ]

    def test_discover_supported_agents_reports_available_clis(self):
        from LocalBroker.agent_runtime import discover_supported_agents
        with tempfile.TemporaryDirectory() as tempdir:
            home = Path(tempdir)

            def fake_which(name):
                return {
                    "codex": "/usr/bin/codex",
                    "agent": "/usr/bin/agent",
                }.get(name)

            supported = discover_supported_agents(which=fake_which, home_dir=home)

            self.assertEqual(supported, ["codex", "cursor_agent"])

    def test_discover_supported_agents_accepts_alias_commands(self):
        from LocalBroker.agent_runtime import discover_supported_agents
        with tempfile.TemporaryDirectory() as tempdir:
            home = Path(tempdir)

            def fake_which(name):
                return {
                    "claude-code": "/usr/bin/claude-code",
                    "cursor-agent": "/usr/bin/cursor-agent",
                }.get(name)

            supported = discover_supported_agents(which=fake_which, home_dir=home)

            self.assertEqual(supported, ["claude_code", "cursor_agent"])

    def test_build_agent_command_uses_selected_agent_type(self):
        from LocalBroker.agent_runtime import build_agent_command

        args = build_agent_command(
            "codex",
            prompt="Explain failure",
            force=True,
            resume_session_id="sess-123",
            output_format="stream-json",
            stream_partial=True,
            which=lambda name: {"codex": "/usr/bin/codex"}.get(name),
            home_dir=Path("/tmp/nonexistent-home"),
        )

        self.assertEqual(
            args,
            [
                "/usr/bin/codex",
                "exec",
                "--json",
                "--skip-git-repo-check",
                "--dangerously-bypass-approvals-and-sandbox",
                "Explain failure",
            ],
        )

    def test_build_agent_command_uses_detected_alias_command(self):
        from LocalBroker.agent_runtime import build_agent_command

        def fake_which(name):
            return {
                "claude-code": "/usr/bin/claude-code",
            }.get(name)

        args = build_agent_command(
            "claude_code",
            prompt="Explain failure",
            which=fake_which,
            home_dir=Path("/tmp/nonexistent-home"),
        )

        self.assertEqual(
            args,
            [
                "/usr/bin/claude-code",
                "-p",
                "--output-format",
                "stream-json",
                "--verbose",
                "--include-partial-messages",
                "Explain failure",
            ],
        )

    def test_build_agent_command_falls_back_to_common_install_dirs(self):
        from LocalBroker.agent_runtime import build_agent_command

        with tempfile.TemporaryDirectory() as tempdir:
            home = Path(tempdir)
            codex_path = home / ".nvm/versions/node/v20.20.1/bin/codex"
            codex_path.parent.mkdir(parents=True, exist_ok=True)
            codex_path.write_text("#!/bin/sh\n")

            args = build_agent_command(
                "codex",
                prompt="Explain failure",
                which=lambda _name: None,
                home_dir=home,
            )

            self.assertEqual(args[0], str(codex_path))

    def test_run_agent_and_report_marks_missing_cwd_clearly(self):
        from LocalBroker import broker_worker

        patched = []

        broker_worker.run_agent_and_report(
            "task-1",
            {
                "prompt": "?",
                "cwd": "/definitely/missing/path",
                "agent_type": "codex",
            },
            base="http://localhost:9020",
            token="token",
            get_task_fn=lambda *_args, **_kwargs: {
                "prompt": "?",
                "cwd": "/definitely/missing/path",
                "agent_type": "codex",
                "force": False,
                "resume_session_id": None,
                "output_format": "stream-json",
                "stream_partial": True,
                "timeout_sec": 1800,
            },
            patch_task_fn=lambda task_id, **kwargs: patched.append((task_id, kwargs)) or {},
            post_task_events_fn=lambda *_args, **_kwargs: {},
        )

        self.assertEqual(patched[-1][1]["status"], "failed")
        self.assertIn("Working directory does not exist", patched[-1][1]["result_text"])

    def test_codex_agent_message_is_normalized_to_assistant_and_result(self):
        from LocalBroker import broker_worker

        event = {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "您好，我在这里。",
            },
        }

        normalized = broker_worker._normalize_stream_event(event)

        self.assertEqual(
            normalized,
            {
                "type": "assistant",
                "message": {"content": [{"text": "您好，我在这里。"}]},
                "_result_text": "您好，我在这里。",
            },
        )

    def test_runtime_module_imports_when_executed_from_localbroker_directory(self):
        localbroker_dir = pathlib.Path(__file__).resolve().parent
        runtime_path = localbroker_dir / "agent_runtime.py"
        old_path = sys.path[:]
        try:
            sys.path = self._direct_script_sys_path(localbroker_dir)
            spec = importlib.util.spec_from_file_location("agent_runtime_local", runtime_path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            self.assertEqual(
                module.build_agent_command(
                    "cursor_agent",
                    prompt="hi",
                    which=lambda name: {"agent": "/usr/bin/agent"}.get(name),
                    home_dir=Path("/tmp/nonexistent-home"),
                ),
                ["/usr/bin/agent", "-p", "--trust", "--output-format", "stream-json", "--stream-partial-output", "hi"],
            )
        finally:
            sys.path = old_path

    def test_broker_worker_imports_when_executed_from_localbroker_directory(self):
        localbroker_dir = pathlib.Path(__file__).resolve().parent
        worker_path = localbroker_dir / "broker_worker.py"
        old_path = sys.path[:]
        try:
            sys.path = self._direct_script_sys_path(localbroker_dir)
            with mock.patch.dict(sys.modules, {"LocalBroker": None, "LocalBroker.agent_runtime": None}):
                spec = importlib.util.spec_from_file_location("broker_worker_local", worker_path)
                module = importlib.util.module_from_spec(spec)
                assert spec.loader is not None
                spec.loader.exec_module(module)
                self.assertTrue(callable(module.run_agent_and_report))
        finally:
            sys.path = old_path

    def test_call_with_retry_retries_transient_errors(self):
        from LocalBroker import broker_worker

        calls = {"count": 0}

        def flaky():
            calls["count"] += 1
            if calls["count"] < 3:
                raise RuntimeError("temporary network error")
            return "ok"

        result = broker_worker._call_with_retry(flaky, attempts=3, sleep_fn=lambda _seconds: None)

        self.assertEqual(result, "ok")
        self.assertEqual(calls["count"], 3)

    def test_flush_pending_final_reports_replays_and_deletes_local_spool(self):
        from LocalBroker import broker_worker

        with tempfile.TemporaryDirectory() as tempdir:
            pending_dir = pathlib.Path(tempdir)
            report_path = pending_dir / "task-1.json"
            report_path.write_text(json.dumps({
                "task_id": "task-1",
                "payload": {
                    "status": "success",
                    "finished_at": "2026-03-06T00:00:00Z",
                    "result_text": "done",
                    "exit_code": 0,
                    "events": [{"type": "result", "result": "done"}],
                    "raw_lines": ['{"type":"result","result":"done"}'],
                },
            }))

            patched = []

            def fake_patch_task(task_id, **kwargs):
                patched.append((task_id, kwargs))
                return {}

            flushed = broker_worker.flush_pending_final_reports(
                base="http://localhost:9020",
                token="token",
                dir_path=pending_dir,
                patch_task_fn=fake_patch_task,
                sleep_fn=lambda _seconds: None,
            )

            self.assertEqual(flushed, 1)
            self.assertEqual(len(patched), 1)
            self.assertEqual(patched[0][0], "task-1")
            self.assertFalse(report_path.exists())

    def test_cloud_session_manager_reauths_and_retries_after_auth_error(self):
        from LocalBroker.broker_api import AuthError
        from LocalBroker.cloud_session import CloudSessionManager

        login_calls = []
        api_tokens = []

        def fake_login(client_id, secret_key, base):
            login_calls.append((client_id, secret_key, base))
            return {"token": "fresh-token", "email": "pc@example.com"}

        def fake_api(*, token=None, base=None):
            api_tokens.append(token)
            if token == "stale-token":
                raise AuthError("expired")
            return {"ok": True, "token": token, "base": base}

        saved = []
        manager = CloudSessionManager(
            api_base="http://localhost:9020",
            credential_id="cred-1",
            secret_key="secret-1",
            token="stale-token",
            login_fn=fake_login,
            save_token_fn=lambda token, email: saved.append((token, email)),
        )

        result = manager.call(fake_api)

        self.assertEqual(result["token"], "fresh-token")
        self.assertEqual(api_tokens, ["stale-token", "fresh-token"])
        self.assertEqual(login_calls, [("cred-1", "secret-1", "http://localhost:9020")])
        self.assertEqual(saved, [("fresh-token", "pc@example.com")])

    def test_cloud_session_manager_logs_in_when_no_token(self):
        from LocalBroker.cloud_session import CloudSessionManager

        login_calls = []

        def fake_login(client_id, secret_key, base):
            login_calls.append((client_id, secret_key, base))
            return {"token": "token-1", "email": ""}

        manager = CloudSessionManager(
            api_base="http://localhost:9020",
            credential_id="cred-2",
            secret_key="secret-2",
            login_fn=fake_login,
        )

        result = manager.call(lambda *, token=None, base=None: {"token": token, "base": base})

        self.assertEqual(result, {"token": "token-1", "base": "http://localhost:9020"})
        self.assertEqual(login_calls, [("cred-2", "secret-2", "http://localhost:9020")])


class ClaudeOptionArgsTest(unittest.TestCase):
    def test_permission_mode_plan(self):
        from LocalBroker.agent_runtime import claude_option_args
        self.assertEqual(claude_option_args({"permission_mode": "plan"}), ["--permission-mode", "plan"])

    def test_default_permission_mode_emits_nothing(self):
        from LocalBroker.agent_runtime import claude_option_args
        self.assertEqual(claude_option_args({"permission_mode": "default"}), [])

    def test_model_and_effort(self):
        from LocalBroker.agent_runtime import claude_option_args
        self.assertEqual(
            claude_option_args({"model": "sonnet", "effort": "high"}),
            ["--model", "sonnet", "--effort", "high"],
        )

    def test_invalid_effort_ignored(self):
        from LocalBroker.agent_runtime import claude_option_args
        self.assertEqual(claude_option_args({"effort": "turbo"}), [])

    def test_force_fallback_when_no_permission_mode(self):
        from LocalBroker.agent_runtime import claude_option_args
        self.assertEqual(claude_option_args({}, force=True), ["--dangerously-skip-permissions"])

    def test_permission_mode_wins_over_force(self):
        from LocalBroker.agent_runtime import claude_option_args
        self.assertEqual(
            claude_option_args({"permission_mode": "acceptEdits"}, force=True),
            ["--permission-mode", "acceptEdits"],
        )

    def test_build_agent_command_includes_options(self):
        from pathlib import Path
        from LocalBroker.agent_runtime import build_agent_command
        args = build_agent_command(
            "claude_code", prompt="hi",
            options={"permission_mode": "plan", "model": "opus"},
            which=lambda c: "/usr/bin/claude" if c == "claude" else None,
            home_dir=Path("/tmp/nonexistent"),
        )
        self.assertIn("--permission-mode", args)
        self.assertEqual(args[args.index("--permission-mode") + 1], "plan")
        self.assertIn("--model", args)
        self.assertEqual(args[args.index("--model") + 1], "opus")


if __name__ == "__main__":
    unittest.main()
