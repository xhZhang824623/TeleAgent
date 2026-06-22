"""
cwd_lock.py – 按工作目录（cwd）加的跨进程互斥锁。

为什么需要
──────────
单个 LocalBroker 的执行循环是串行的，同一进程内不会并发。但同一台 PC 上可能跑着
多个 LocalBroker 进程（多个同事的账号 / 多个客户端），若它们的会话指向**同一个项目
目录**，并发执行就会让两个 agent（claude / codex 等）同时改同一份工作树 —— 互相覆盖、
git 打架。上下文是隔离的（见 session_manager / 各 CLI 独立 session 存储），但磁盘工作树
是共享的，需要一把锁来保证「同机同目录，同一时刻只有一个 agent 在执行」。

实现
────
- 基于 POSIX ``fcntl.flock``（按 open file description 加锁，跨进程生效），**非阻塞**：
  拿不到就立刻返回 None，调用方应跳过该任务、保持 queued、下轮重试（不冻结轮询循环）。
- 锁文件放在 ``~/.teleagent_cwd_locks/<sha1(realpath(cwd))>.lock``，不污染项目目录、不进 git。
- 平台/环境不支持 flock 时**优雅降级**：返回一个 noop 句柄允许执行（宁可不加锁也不卡死），
  这与「别人持锁」的 None 语义区分开。
"""

import hashlib
import os
from typing import Optional

try:
    import fcntl  # POSIX only
    _HAVE_FCNTL = True
except Exception:  # pragma: no cover - 非 POSIX
    _HAVE_FCNTL = False


_LOCK_DIR = os.path.join(os.path.expanduser("~"), ".teleagent_cwd_locks")


class CwdLock:
    """持有一个 cwd 互斥锁；用完务必 release()。"""

    def __init__(self, fd: Optional[int], path: Optional[str], noop: bool = False):
        self._fd = fd
        self._path = path
        self._noop = noop
        self._released = False

    @property
    def is_noop(self) -> bool:
        return self._noop

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        if self._noop or self._fd is None:
            return
        try:
            if _HAVE_FCNTL:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            os.close(self._fd)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def _lock_path_for(cwd: str) -> str:
    key = hashlib.sha1(os.path.realpath(cwd).encode("utf-8")).hexdigest()
    return os.path.join(_LOCK_DIR, key + ".lock")


def acquire_cwd_lock(cwd: str) -> Optional[CwdLock]:
    """
    非阻塞地获取 cwd 锁。
      - 成功      → 返回 CwdLock（真锁）
      - 已被他人持有 → 返回 None（调用方应跳过任务、保持 queued、下轮重试）
      - 无法加锁（非 POSIX / 异常）→ 返回 noop CwdLock（允许执行，不提供跨进程保护）
    """
    if not _HAVE_FCNTL:
        return CwdLock(None, None, noop=True)
    try:
        os.makedirs(_LOCK_DIR, exist_ok=True)
        path = _lock_path_for(cwd)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    except Exception:
        return CwdLock(None, None, noop=True)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        try:
            os.close(fd)
        except Exception:
            pass
        return None
    return CwdLock(fd, path)
