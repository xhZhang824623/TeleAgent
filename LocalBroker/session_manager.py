"""
session_manager.py – 常驻（warm）Agent 会话管理。

动机
────
原模型下每条消息都新起一个一次性子进程，靠 `--resume <session_id>` 冷启动重放上下文。
本模块为「正被 Web 打开」的会话维护一个**常驻子进程**：进程内存里一直保有上下文，
后续每一轮消息直接喂入 stdin、从 stdout 读流式事件，无需每轮 resume。

当前支持
────────
- Claude Code: 原生支持 `--input-format stream-json --output-format stream-json` 持续输入模式，
  完整实现为 ``ClaudeWarmSession``。
- Codex: 暂不支持常驻，``is_warm_capable`` 返回 False，调用方回退到一次性
  ``broker_worker.run_agent_and_report``（每轮独立 exec，无跨轮上下文）。

  为什么 Codex 没做常驻 / 没做 resume（基于 codex-cli 0.139.0 实测，见 P2.5 调研）：
    * `codex app-server`：常驻 JSON-RPC 协议，但是**实验性 + 双向**——服务端会反向发起
      审批请求（ApplyPatch/ExecCommand/FileChange/Permissions 等 5 类），客户端必须应答，
      否则整轮卡死；ClientRequest/ServerNotification 各上百 KB、且分 v1/v2。离线无法可靠
      实现与验证，不宜盲写。
    * `codex exec resume <thread_id>`：能加载到同一个 thread_id，但**不会把上一轮的对话内容
      恢复进上下文**（实测：第一轮"记住 teal"，resume 后追问颜色答"你没在本对话里告诉过我"）。
      即 resume 在该版本下不提供真正的跨轮记忆，接了等于给用户假的连续性，故不接。
  若未来要做 Codex 真常驻：需对接 app-server 协议（含审批自动应答 + TurnCompleted 判定 +
  通知流到事件格式的映射），并对照实际可用的 codex 版本充分联调验证。

线程模型
────────
- 每个 WarmSession 有一个常驻 stdout 读取线程，把解析后的事件投递到内部队列。
- ``run_turn`` 持有 per-session 锁：同一会话同一时刻只跑一轮；不同会话之间并行。
- 事件通过 ``on_event(task_id, raw_obj)`` 回调实时上抛（由调用方做归一化/上报）。

无 Qt / 无第三方依赖。
"""

import json
import subprocess
import threading
import queue
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    from LocalBroker.agent_runtime import _resolve_agent_command, claude_option_args
except ModuleNotFoundError:
    from agent_runtime import _resolve_agent_command, claude_option_args


# 真正支持常驻进程的 agent 类型。其余类型走一次性回退路径。
WARM_CAPABLE_AGENTS = {"claude_code"}

DEFAULT_IDLE_TTL_SEC = 600.0       # 会话关闭后空闲多久回收常驻进程
DEFAULT_MAX_SESSIONS = 8           # 同时保活的常驻进程上限（控内存），超出按 LRU 回收

# on_event(task_id, raw_event_dict) —— 实时上抛一条原始（未归一化）事件
EventSink = Callable[[str, dict], None]


def is_warm_capable(agent_type: str) -> bool:
    return agent_type in WARM_CAPABLE_AGENTS


@dataclass
class TurnResult:
    """一轮对话的执行结果。"""
    status: str                          # "success" | "failed" | "timeout"
    result_text: Optional[str] = None
    session_id: Optional[str] = None
    events: List[dict] = field(default_factory=list)   # 本轮全部原始事件（用于最终 PATCH 兜底）
    error: Optional[str] = None


class WarmSession:
    """常驻 Agent 会话基类。子类实现具体 CLI 的启动与单轮驱动。"""

    def __init__(
        self,
        conv_id: str,
        cwd: str,
        agent_type: str,
        *,
        force: bool = False,
        resume_session_id: Optional[str] = None,
        options: Optional[dict] = None,
    ):
        self.conv_id = conv_id
        self.cwd = cwd
        self.agent_type = agent_type
        self.force = force
        self.options = options or {}
        self.session_id: Optional[str] = resume_session_id
        self.last_activity: float = time.time()
        self._turn_lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None

    # ── 生命周期 ─────────────────────────────────────────────
    def start(self) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                except Exception:
                    proc.kill()
        except Exception:
            pass

    # ── 单轮驱动 ─────────────────────────────────────────────
    def run_turn(self, task_id: str, prompt: str, on_event: EventSink,
                 timeout_sec: float = 1800.0) -> TurnResult:  # pragma: no cover - 抽象
        raise NotImplementedError


class ClaudeWarmSession(WarmSession):
    """
    Claude Code 常驻会话。

    启动: ``claude -p --input-format stream-json --output-format stream-json
            --include-partial-messages [--dangerously-skip-permissions] [--resume <id>]``
    每轮: 向 stdin 写一行 ``{"type":"user","message":{"role":"user","content":[{"type":"text","text":...}]}}``，
          从 stdout 读流式事件，直到出现 ``{"type":"result", ...}`` 表示本轮结束（进程保持存活）。
    """

    def __init__(self, conv_id, cwd, agent_type, *, force=False, resume_session_id=None,
                 options: Optional[dict] = None,
                 which: Optional[Callable[[str], Optional[str]]] = None,
                 home_dir: Optional[Path] = None):
        super().__init__(conv_id, cwd, agent_type, force=force,
                         resume_session_id=resume_session_id, options=options)
        self._which = which
        self._home_dir = home_dir
        self._events: "queue.Queue" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        # 动态控制（control_request/response）通道：与每轮事件分开。
        self._stdin_lock = threading.Lock()
        self._control_cv = threading.Condition()
        self._control_responses: Dict[str, dict] = {}

    def _build_launch_args(self) -> List[str]:
        kwargs = {}
        if self._which is not None:
            kwargs["which"] = self._which
        if self._home_dir is not None:
            kwargs["home_dir"] = self._home_dir
        command = _resolve_agent_command("claude_code", **kwargs)
        args = [
            command, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",  # --print 下 stream-json 输出要求 --verbose
            "--include-partial-messages",
        ]
        args.extend(claude_option_args(self.options, force=self.force))
        if self.session_id:
            args.extend(["--resume", self.session_id])
        return args

    def start(self) -> None:
        args = self._build_launch_args()
        self._proc = subprocess.Popen(
            args,
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        proc = self._proc
        try:
            for raw_line in proc.stdout:
                line = raw_line.rstrip("\r\n")
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    self._events.put(("raw", line))
                    continue
                if obj.get("type") == "control_response":
                    rid = (obj.get("response") or {}).get("request_id")
                    if rid:
                        with self._control_cv:
                            self._control_responses[rid] = obj
                            self._control_cv.notify_all()
                    continue
                self._events.put(("event", obj))
        except Exception:
            pass
        finally:
            self._events.put(("eof", None))
            with self._control_cv:  # 唤醒等待中的 send_control，避免进程退出后卡死
                self._control_cv.notify_all()

    def send_control(self, subtype: str, fields: Optional[dict] = None,
                     timeout: float = 10.0) -> Optional[dict]:
        """
        向运行中的常驻会话发一个 control_request（如 set_permission_mode / set_model / interrupt），
        等待匹配 request_id 的 control_response。成功返回 response 对象，失败/超时返回 None。
        可在某轮执行**进行中**调用（如 interrupt）；stdin 写入用独立锁，不抢 turn 锁。
        """
        if not self.is_alive():
            return None
        rid = f"ctl_{uuid.uuid4().hex[:8]}"
        req = {"type": "control_request", "request_id": rid,
               "request": {"subtype": subtype, **(fields or {})}}
        try:
            with self._stdin_lock:
                self._proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
        except Exception:
            return None
        self.last_activity = time.time()
        deadline = time.time() + timeout
        with self._control_cv:
            while rid not in self._control_responses:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._control_cv.wait(timeout=remaining)
            return self._control_responses.pop(rid)

    def run_turn(self, task_id, prompt, on_event, timeout_sec=1800.0) -> TurnResult:
        with self._turn_lock:
            self.last_activity = time.time()
            if not self.is_alive():
                return TurnResult(status="failed", error="warm session process is not alive")

            envelope = {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "text", "text": prompt}]},
            }
            try:
                with self._stdin_lock:
                    self._proc.stdin.write(json.dumps(envelope, ensure_ascii=False) + "\n")
                    self._proc.stdin.flush()
            except Exception as exc:
                return TurnResult(status="failed", error=f"failed to write prompt: {exc}")

            events: List[dict] = []
            result_text: Optional[str] = None
            deadline = time.time() + timeout_sec
            status = "failed"

            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    status = "timeout"
                    break
                try:
                    kind, payload = self._events.get(timeout=min(remaining, 1.0))
                except queue.Empty:
                    if not self.is_alive():
                        status = "failed"
                        break
                    continue

                if kind == "eof":
                    status = "failed"
                    break
                if kind == "raw":
                    continue  # 非 JSON 行，忽略（与一次性路径一致）

                obj = payload
                events.append(obj)
                etype = obj.get("type")
                if etype == "system" and obj.get("subtype") == "init":
                    sid = obj.get("session_id")
                    if sid:
                        self.session_id = sid
                try:
                    on_event(task_id, obj)
                except Exception:
                    pass

                if etype == "result":
                    result_text = obj.get("result", "")
                    status = "failed" if obj.get("is_error") else "success"
                    break

            self.last_activity = time.time()
            return TurnResult(
                status=status,
                result_text=result_text,
                session_id=self.session_id,
                events=events,
            )


def create_warm_session(
    conv_id: str,
    cwd: str,
    agent_type: str,
    *,
    force: bool = False,
    resume_session_id: Optional[str] = None,
    options: Optional[dict] = None,
    which: Optional[Callable[[str], Optional[str]]] = None,
    home_dir: Optional[Path] = None,
) -> Optional[WarmSession]:
    """按 agent_type 构造对应的常驻会话；不支持常驻的类型返回 None。"""
    if agent_type == "claude_code":
        return ClaudeWarmSession(
            conv_id, cwd, agent_type,
            force=force, resume_session_id=resume_session_id, options=options,
            which=which, home_dir=home_dir,
        )
    # codex / cursor_agent: 暂不支持常驻，调用方回退到一次性路径。
    return None


class SessionManager:
    """
    常驻会话注册表：conv_id -> WarmSession。

    用法（由 broker_worker 的轮询循环驱动）：
      - ``ensure(conv)`` 为「正被打开」的会话预热常驻进程（已知 session_id 则带 resume 冷启动）。
      - ``get(conv_id)`` 取已有会话；``run_turn`` 直接在返回的会话上调用。
      - ``reconcile(active_conv_ids)`` 回收「不再打开且空闲超 TTL」的会话，并按 max_sessions LRU 回收。
    """

    def __init__(self, *, idle_ttl_sec: float = DEFAULT_IDLE_TTL_SEC,
                 max_sessions: int = DEFAULT_MAX_SESSIONS,
                 factory: Callable[..., Optional[WarmSession]] = create_warm_session):
        self._idle_ttl = idle_ttl_sec
        self._max_sessions = max_sessions
        self._factory = factory
        self._sessions: Dict[str, WarmSession] = {}
        self._lock = threading.Lock()

    def get(self, conv_id: str) -> Optional[WarmSession]:
        with self._lock:
            return self._sessions.get(conv_id)

    def ensure(
        self,
        conv_id: str,
        cwd: str,
        agent_type: str,
        *,
        force: bool = False,
        resume_session_id: Optional[str] = None,
        options: Optional[dict] = None,
    ) -> Optional[WarmSession]:
        """
        返回该会话的常驻进程（必要时启动）。不支持常驻或启动失败时返回 None，
        调用方应回退到一次性执行路径。
        """
        if not is_warm_capable(agent_type):
            return None
        with self._lock:
            existing = self._sessions.get(conv_id)
            if existing is not None and existing.is_alive():
                return existing
            # 进程已死/不存在：清理后重建（带 resume 兜底）
            if existing is not None:
                existing.close()
                self._sessions.pop(conv_id, None)
            session = self._factory(
                conv_id, cwd, agent_type,
                force=force,
                resume_session_id=resume_session_id or (existing.session_id if existing else None),
                options=options,
            )
            if session is None:
                return None
            try:
                session.start()
            except Exception:
                try:
                    session.close()
                except Exception:
                    pass
                return None
            self._sessions[conv_id] = session
            self._evict_over_capacity_locked(keep=conv_id)
            return session

    def reconcile(self, active_conv_ids) -> List[str]:
        """
        回收应当关闭的常驻进程：
          - 会话已不在 active 列表（Web 已关闭）且空闲超过 idle_ttl；
          - 进程已死亡。
        返回被回收的 conv_id 列表。
        """
        active = set(active_conv_ids or [])
        now = time.time()
        closed: List[str] = []
        with self._lock:
            for conv_id, session in list(self._sessions.items()):
                dead = not session.is_alive()
                idle_closed = (
                    conv_id not in active
                    and (now - session.last_activity) >= self._idle_ttl
                )
                if dead or idle_closed:
                    session.close()
                    self._sessions.pop(conv_id, None)
                    closed.append(conv_id)
            self._evict_over_capacity_locked()
        return closed

    def _evict_over_capacity_locked(self, keep: Optional[str] = None) -> None:
        """超出 max_sessions 时按 last_activity LRU 回收（保留 keep）。调用方须已持锁。"""
        if len(self._sessions) <= self._max_sessions:
            return
        victims = sorted(self._sessions.items(), key=lambda kv: kv[1].last_activity)
        for conv_id, session in victims:
            if len(self._sessions) <= self._max_sessions:
                break
            if conv_id == keep:
                continue
            session.close()
            self._sessions.pop(conv_id, None)

    def active_conv_ids(self) -> List[str]:
        with self._lock:
            return list(self._sessions.keys())

    def shutdown_all(self) -> None:
        with self._lock:
            for session in self._sessions.values():
                session.close()
            self._sessions.clear()
