"""test_autotitle.py – 标题生成（启发式 + 一次性 CLI，含各类降级）单测。"""

import unittest

try:
    from LocalBroker import autotitle
    from LocalBroker.autotitle import heuristic_title, generate_title
except ModuleNotFoundError:
    import autotitle
    from autotitle import heuristic_title, generate_title


class _FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


class HeuristicTitleTest(unittest.TestCase):
    def test_first_nonempty_line(self):
        self.assertEqual(heuristic_title("\n\n  Fix the login bug \nmore"), "Fix the login bug")

    def test_strips_markdown_and_quotes(self):
        self.assertEqual(heuristic_title('## "Refactor parser"'), "Refactor parser")
        self.assertEqual(heuristic_title("- 处理目录并发锁"), "处理目录并发锁")

    def test_limit(self):
        self.assertEqual(len(heuristic_title("x" * 100, limit=20)), 20)

    def test_empty(self):
        self.assertEqual(heuristic_title(""), "")
        self.assertEqual(heuristic_title("   \n  "), "")


class GenerateTitleTest(unittest.TestCase):
    def test_uses_cli_output_when_available(self):
        calls = {}

        def fake_run(args, **kwargs):
            calls["args"] = args
            return _FakeProc(stdout="给登录加双因子认证\n", returncode=0)

        title = generate_title("帮我给登录流程加上双因子认证，并补测试",
                               "/tmp", which=lambda c: "/usr/bin/claude", run=fake_run)
        self.assertEqual(title, "给登录加双因子认证")
        self.assertIn("-p", calls["args"])

    def test_falls_back_when_cli_nonzero(self):
        title = generate_title("Fix the flaky test in CI", "/tmp",
                               which=lambda c: "/usr/bin/claude",
                               run=lambda *a, **k: _FakeProc(stdout="garbage", returncode=1))
        self.assertEqual(title, "Fix the flaky test in CI")

    def test_falls_back_when_run_raises(self):
        def boom(*a, **k):
            raise RuntimeError("timeout")
        title = generate_title("Add caching layer", "/tmp",
                               which=lambda c: "/usr/bin/claude", run=boom)
        self.assertEqual(title, "Add caching layer")

    def test_falls_back_when_cli_unresolvable(self):
        orig = autotitle._resolve_agent_command
        autotitle._resolve_agent_command = lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError())
        try:
            title = generate_title("Write the deploy script", "/tmp")
            self.assertEqual(title, "Write the deploy script")
        finally:
            autotitle._resolve_agent_command = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
