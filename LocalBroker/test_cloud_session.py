"""test_cloud_session.py – CloudSessionManager 并发线程安全：初始登录与 401 重登都不形成惊群。"""

import threading
import time
import unittest

try:
    from LocalBroker.cloud_session import CloudSessionManager
    from LocalBroker.broker_api import AuthError
except ModuleNotFoundError:
    from cloud_session import CloudSessionManager
    from broker_api import AuthError


class CloudSessionConcurrencyTest(unittest.TestCase):
    def _make(self):
        self.login_count = 0
        self._count_lock = threading.Lock()

        def login_fn(cred, secret, base=None):
            time.sleep(0.01)  # 放大窗口，逼出惊群
            with self._count_lock:
                self.login_count += 1
                n = self.login_count
            return {"token": f"tok-{n}", "email": "e"}

        return CloudSessionManager(
            api_base="http://x", credential_id="c", secret_key="s", login_fn=login_fn,
        )

    def test_initial_login_happens_once_under_concurrency(self):
        mgr = self._make()
        ok = []

        def worker():
            res = mgr.call(lambda base=None, token=None: ("ok", token))
            ok.append(res)

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(self.login_count, 1)        # 仅一次初始登录
        self.assertEqual(len(ok), 12)
        self.assertTrue(all(tok == "tok-1" for _, tok in ok))

    def test_401_refresh_happens_once(self):
        mgr = self._make()
        results = []

        def flaky(base=None, token=None):
            # 用初始 token 调用一律 401；刷新后的 token 才成功
            if token == "tok-1":
                raise AuthError("expired")
            return token

        def worker():
            results.append(mgr.call(flaky))

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 1 次初始登录 + 1 次刷新 = 2，无论多少并发
        self.assertEqual(self.login_count, 2)
        self.assertTrue(all(r == "tok-2" for r in results))


if __name__ == "__main__":
    unittest.main(verbosity=2)
