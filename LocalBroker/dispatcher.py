"""
dispatcher.py – 并发任务派发器。

把原来「一次只跑一个任务」的串行循环，换成按会话并发执行：
  - 不同会话 / 不同目录的任务**并行**跑（各自一个线程）；
  - 同一会话的任务**串行**（per-conv 互斥，避免一个会话内两轮同时跑、串了上下文）；
  - 同一工作目录的任务由 cwd 锁串行（跨线程/跨进程，见 cwd_lock）；
  - 总并发有上限（信号量），超出则该轮不派发、留 queued 下轮重试。

派发器只管「认领 / 并发 / 串行约束 / cwd 锁」；真正的执行+回写由注入的 run_fn 负责，
便于单测。run_fn(task_dict) 抛异常不会影响其它任务（每个 worker 独立 try/finally 清理）。
"""

import threading
from typing import Callable, Optional

try:
    from LocalBroker.cwd_lock import acquire_cwd_lock as _default_acquire_cwd_lock
except ModuleNotFoundError:
    from cwd_lock import acquire_cwd_lock as _default_acquire_cwd_lock


class TaskDispatcher:
    def __init__(
        self,
        run_fn: Callable[[dict], None],
        *,
        max_concurrency: int = 4,
        acquire_cwd_lock: Callable = _default_acquire_cwd_lock,
        on_skip: Optional[Callable[[dict], None]] = None,
        spawn: Optional[Callable] = None,
    ):
        self._run_fn = run_fn
        self._acquire_cwd_lock = acquire_cwd_lock
        self._on_skip = on_skip
        self._sem = threading.BoundedSemaphore(max(1, max_concurrency))
        self._lock = threading.Lock()
        self._inflight_tasks = set()   # 已认领的 task_id（防止轮询窗口内重复派发）
        self._inflight_convs = set()   # 有在跑任务的 conv_id（per-conv 串行）
        # spawn 注入便于测试（默认起 daemon 线程）
        self._spawn = spawn or (lambda target, args: threading.Thread(
            target=target, args=args, daemon=True).start())

    def dispatch(self, tasks) -> int:
        """
        遍历 queued 任务，派发符合约束的任务到独立线程。返回本轮派发数量。
        tasks: 每项含 'id'、'conversation_id'、'cwd'。
        """
        dispatched = 0
        for t in tasks or []:
            task_id = str(t.get("id"))
            conv_id = str(t.get("conversation_id") or "")
            with self._lock:
                if task_id in self._inflight_tasks:
                    continue
                if conv_id and conv_id in self._inflight_convs:
                    continue  # 同会话已有任务在跑 → 串行
                if not self._sem.acquire(blocking=False):
                    break     # 到达并发上限 → 本轮不再派发
                self._inflight_tasks.add(task_id)
                if conv_id:
                    self._inflight_convs.add(conv_id)
            try:
                self._spawn(self._worker, (t, task_id, conv_id))
                dispatched += 1
            except Exception:
                # 起线程失败：回滚认领与信号量
                self._release(task_id, conv_id)
        return dispatched

    def _worker(self, t: dict, task_id: str, conv_id: str) -> None:
        try:
            cwd = t.get("cwd") or "/"
            lock = self._acquire_cwd_lock(cwd)
            if lock is None:
                # 该目录被（本机其它任务/进程）占用：留 queued，下轮重试。
                if self._on_skip:
                    try:
                        self._on_skip(t)
                    except Exception:
                        pass
                return
            try:
                self._run_fn(t)
            finally:
                lock.release()
        except Exception:
            pass
        finally:
            self._release(task_id, conv_id)

    def _release(self, task_id: str, conv_id: str) -> None:
        with self._lock:
            self._inflight_tasks.discard(task_id)
            if conv_id:
                self._inflight_convs.discard(conv_id)
        try:
            self._sem.release()
        except ValueError:
            pass

    def active_count(self) -> int:
        with self._lock:
            return len(self._inflight_tasks)

    def active_conv_count(self) -> int:
        with self._lock:
            return len(self._inflight_convs)
