"""test_cwd_lock.py – 同机同目录跨进程锁的语义单测（flock 在不同 fd 间即使同进程也互斥）。"""

import os
import tempfile
import unittest

try:
    from LocalBroker.cwd_lock import acquire_cwd_lock, CwdLock, _HAVE_FCNTL
except ModuleNotFoundError:
    from cwd_lock import acquire_cwd_lock, CwdLock, _HAVE_FCNTL


class CwdLockTest(unittest.TestCase):
    def setUp(self):
        self._a = tempfile.mkdtemp()
        self._b = tempfile.mkdtemp()

    def test_second_acquire_same_dir_blocked(self):
        if not _HAVE_FCNTL:
            self.skipTest("fcntl unavailable; lock degrades to noop")
        h1 = acquire_cwd_lock(self._a)
        self.assertIsInstance(h1, CwdLock)
        self.assertFalse(h1.is_noop)
        # 同一目录第二次获取应失败（被占用）
        h2 = acquire_cwd_lock(self._a)
        self.assertIsNone(h2)
        # 释放后可再次获取
        h1.release()
        h3 = acquire_cwd_lock(self._a)
        self.assertIsInstance(h3, CwdLock)
        h3.release()

    def test_different_dirs_independent(self):
        h1 = acquire_cwd_lock(self._a)
        h2 = acquire_cwd_lock(self._b)
        self.assertIsNotNone(h1)
        self.assertIsNotNone(h2)  # 不同目录互不影响
        h1.release()
        h2.release()

    def test_realpath_normalised(self):
        if not _HAVE_FCNTL:
            self.skipTest("fcntl unavailable")
        # 带冗余分隔符/相对片段的同一目录应命中同一把锁
        h1 = acquire_cwd_lock(self._a)
        h2 = acquire_cwd_lock(self._a + "/./")
        self.assertIsNotNone(h1)
        self.assertIsNone(h2)
        h1.release()

    def test_context_manager_releases(self):
        with acquire_cwd_lock(self._a) as lk:
            self.assertIsNotNone(lk)
        # 退出 with 后应已释放，可再次获取
        h = acquire_cwd_lock(self._a)
        self.assertIsNotNone(h)
        h.release()


if __name__ == "__main__":
    unittest.main(verbosity=2)
