"""test_dispatcher.py – 并发派发约束单测（确定性：用手动 spawner 控制 worker 执行时机）。"""

import threading
import time
import unittest

try:
    from LocalBroker.dispatcher import TaskDispatcher
except ModuleNotFoundError:
    from dispatcher import TaskDispatcher


class _FakeLock:
    def release(self):
        pass


def _ok_lock(cwd):
    return _FakeLock()


def _busy_lock(cwd):
    return None


class _ManualSpawner:
    """把 worker 攒起来不执行，便于在『在飞行中』状态做断言；run_all 再统一执行。"""
    def __init__(self):
        self.queued = []

    def __call__(self, target, args):
        self.queued.append((target, args))

    def run_all(self):
        while self.queued:
            target, args = self.queued.pop(0)
            target(*args)


def _task(i, conv, cwd):
    return {"id": str(i), "conversation_id": conv, "cwd": cwd}


class DispatcherTest(unittest.TestCase):
    def test_parallel_across_convs(self):
        ran = []
        sp = _ManualSpawner()
        d = TaskDispatcher(lambda t: ran.append(t["id"]), max_concurrency=4,
                           acquire_cwd_lock=_ok_lock, spawn=sp)
        n = d.dispatch([_task(1, "A", "/a"), _task(2, "B", "/b"), _task(3, "C", "/c")])
        self.assertEqual(n, 3)
        self.assertEqual(d.active_count(), 3)  # 三个都在飞行中
        sp.run_all()
        self.assertEqual(sorted(ran), ["1", "2", "3"])
        self.assertEqual(d.active_count(), 0)  # 释放干净

    def test_same_conv_serialized(self):
        ran = []
        sp = _ManualSpawner()
        d = TaskDispatcher(lambda t: ran.append(t["id"]), acquire_cwd_lock=_ok_lock, spawn=sp)
        # 同一会话 A 的两条任务（不同 cwd，隔离出 conv 约束）
        n = d.dispatch([_task(1, "A", "/a1"), _task(2, "A", "/a2")])
        self.assertEqual(n, 1)               # 只派发一条
        self.assertEqual(d.active_conv_count(), 1)
        sp.run_all()                          # 第一条跑完释放
        self.assertEqual(ran, ["1"])
        n2 = d.dispatch([_task(2, "A", "/a2")])  # 现在第二条可派发
        self.assertEqual(n2, 1)
        sp.run_all()
        self.assertEqual(ran, ["1", "2"])

    def test_max_concurrency_cap(self):
        sp = _ManualSpawner()
        d = TaskDispatcher(lambda t: None, max_concurrency=2, acquire_cwd_lock=_ok_lock, spawn=sp)
        n = d.dispatch([_task(1, "A", "/a"), _task(2, "B", "/b"), _task(3, "C", "/c")])
        self.assertEqual(n, 2)               # 到达并发上限即停
        self.assertEqual(d.active_count(), 2)
        sp.run_all()
        self.assertEqual(d.active_count(), 0)
        self.assertEqual(d.dispatch([_task(3, "C", "/c")]), 1)  # 释放后可继续

    def test_no_double_dispatch_same_task(self):
        ran = []
        sp = _ManualSpawner()
        d = TaskDispatcher(lambda t: ran.append(t["id"]), acquire_cwd_lock=_ok_lock, spawn=sp)
        d.dispatch([_task(1, "A", "/a")])
        d.dispatch([_task(1, "A", "/a")])    # 仍在飞行 → 不重复派发
        self.assertEqual(len(sp.queued), 1)
        sp.run_all()
        self.assertEqual(ran, ["1"])

    def test_cwd_busy_skips_without_running(self):
        ran, skipped = [], []
        sp = _ManualSpawner()
        d = TaskDispatcher(lambda t: ran.append(t["id"]), acquire_cwd_lock=_busy_lock,
                           on_skip=lambda t: skipped.append(t["id"]), spawn=sp)
        d.dispatch([_task(1, "A", "/a")])
        sp.run_all()
        self.assertEqual(ran, [])            # 目录被占 → 没执行
        self.assertEqual(skipped, ["1"])     # 触发了 on_skip
        self.assertEqual(d.active_count(), 0)  # 认领已释放（下轮可重试）

    def test_run_fn_exception_isolated_and_released(self):
        sp = _ManualSpawner()

        def boom(t):
            raise RuntimeError("task blew up")

        d = TaskDispatcher(boom, acquire_cwd_lock=_ok_lock, spawn=sp)
        d.dispatch([_task(1, "A", "/a")])
        sp.run_all()                          # 不应抛出
        self.assertEqual(d.active_count(), 0)  # 异常也释放认领

    def test_real_threads_smoke(self):
        ran, lock = [], threading.Lock()

        def rec(t):
            time.sleep(0.01)
            with lock:
                ran.append(t["id"])

        d = TaskDispatcher(rec, max_concurrency=4, acquire_cwd_lock=_ok_lock)  # 默认真线程
        d.dispatch([_task(i, f"C{i}", f"/c{i}") for i in range(4)])
        for _ in range(200):
            if d.active_count() == 0:
                break
            time.sleep(0.01)
        self.assertEqual(sorted(ran), ["0", "1", "2", "3"])
        self.assertEqual(d.active_count(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
