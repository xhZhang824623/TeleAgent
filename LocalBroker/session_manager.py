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
import os
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

# 交互式审批：开启后给常驻 claude 注入 PreToolUse hook（把审批转给 Web 用户）。
# 运行时可由 Qt 界面的开关切换；默认值来自环境变量 BROKER_INTERACTIVE_PERMISSIONS。
_INTERACTIVE_PERMISSIONS = os.environ.get("BROKER_INTERACTIVE_PERMISSIONS", "0").lower() in ("1", "true", "yes")


def interactive_permissions_enabled() -> bool:
    return _INTERACTIVE_PERMISSIONS


def set_interactive_permissions(enabled: bool) -> None:
    """运行时切换交互式审批（影响之后新预热的常驻会话的启动参数）。"""
    global _INTERACTIVE_PERMISSIONS
    _INTERACTIVE_PERMISSIONS = bool(enabled)
# 触发审批 hook 的敏感工具（只读工具不打扰）。matcher 为按工具名匹配的正则。
# 覆盖会改文件/跑命令/联网的工具；只读工具（Read/Grep/Glob/LS 等）不拦，避免打扰。
PERMISSION_HOOK_MATCHER = os.environ.get(
    "BROKER_PERMISSION_HOOK_MATCHER",
    "Bash|Edit|Write|MultiEdit|NotebookEdit|WebFetch|WebSearch|Task|KillShell",
)
_HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
_HOOK_SCRIPT = os.path.join(_HOOK_DIR, "teleagent_permission_hook.py")
_HOOK_SETTINGS_PATH = os.path.join(_HOOK_DIR, ".teleagent_hook_settings.json")


def _strip_permission_overrides(args: List[str]) -> List[str]:
    """移除会绕过 hook 的权限参数（skip / 各种非 default 的 --permission-mode），强制走默认模式让 hook 生效。"""
    out: List[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--dangerously-skip-permissions":
            i += 1
            continue
        if a == "--permission-mode" and i + 1 < len(args):
            i += 2  # 丢弃 mode 及其取值
            continue
        out.append(a)
        i += 1
    return out


def _ensure_hook_settings() -> str:
    """写出（一次）含 PreToolUse 审批 hook 的 settings 文件并返回其路径。"""
    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": PERMISSION_HOOK_MATCHER,
                    "hooks": [{"type": "command", "command": f'python3 "{_HOOK_SCRIPT}"'}],
                }
            ]
        }
    }
    with open(_HOOK_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False)
    return _HOOK_SETTINGS_PATH

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
        extra_env: Optional[dict] = None,
    ):
        self.conv_id = conv_id
        self.cwd = cwd
        self.agent_type = agent_type
        self.force = force
        self.options = options or {}
        # 注入到 Agent 子进程的额外环境变量（如 TELEAGENT_* 上下文，供 teleagent-send 使用）。
        self.extra_env = extra_env or {}
        self.session_id: Optional[str] = resume_session_id
        self.last_activity: float = time.time()
        self._turn_lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        # 在跑一轮（run_turn 进行中）的繁忙标志：用于避免在轮次进行中拆除会话（杀掉在途任务）。
        self._busy = False
        self._busy_lock = threading.Lock()
        # 「待回收」标记：交互式审批等设置变更时，繁忙会话延后到空闲再回收，避免打断在途任务。
        self.recycle_requested = False

    # ── 生命周期 ─────────────────────────────────────────────
    def start(self) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ── 繁忙/回收标记（并发安全拆除）────────────────────────────
    def is_busy(self) -> bool:
        with self._busy_lock:
            return self._busy

    def _set_busy(self, value: bool) -> None:
        with self._busy_lock:
            self._busy = bool(value)

    def mark_for_recycle(self) -> None:
        """标记本会话待回收：繁忙时由 reconcile 在其空闲后回收（套用新设置）。"""
        self.recycle_requested = True

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
                 extra_env: Optional[dict] = None,
                 which: Optional[Callable[[str], Optional[str]]] = None,
                 home_dir: Optional[Path] = None):
        super().__init__(conv_id, cwd, agent_type, force=force,
                         resume_session_id=resume_session_id, options=options,
                         extra_env=extra_env)
        self._which = which
        self._home_dir = home_dir
        self._events: "queue.Queue" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        # 动态控制（control_request/response）通道：与每轮事件分开。
        self._stdin_lock = threading.Lock()
        self._control_cv = threading.Condition()
        self._control_responses: Dict[str, dict] = {}
        # 仍在等待应答的 control_request id 集合：读循环只收录这些 id 的响应，
        # 超时/已放弃的 id 的迟到响应一律丢弃，避免 _control_responses 无界增长（内存泄漏）。
        self._pending_control_ids: set = set()
        # 交互式工具审批回调：on_permission(request_id, tool_name, tool_input) -> bool(allow)。
        # 按 turn 设置（run_turn 期间生效），由读循环拦截 CLI 发来的 can_use_tool 请求时调用。
        self._on_permission: Optional[Callable[[str, str, dict], bool]] = None

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
        opt_args = claude_option_args(self.options, force=self.force)
        if interactive_permissions_enabled():
            # 交互式审批靠 PreToolUse hook：注入 settings，并去掉会绕过 hook 的权限覆盖（走默认模式）。
            opt_args = _strip_permission_overrides(opt_args)
            args.extend(["--settings", _ensure_hook_settings()])
        args.extend(opt_args)
        if self.session_id:
            args.extend(["--resume", self.session_id])
        return args

    def start(self) -> None:
        args = self._build_launch_args()
        env = {**os.environ, **self.extra_env} if self.extra_env else None
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
            env=env,
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
                # 单行处理隔离：任何一行解析/分发异常都不得杀死读线程（否则会话被「卡死」）。
                try:
                    self._handle_read_line(line)
                except Exception:
                    self._events.put(("raw", line))
        except Exception:
            pass
        finally:
            self._events.put(("eof", None))
            with self._control_cv:  # 唤醒等待中的 send_control，避免进程退出后卡死
                self._control_cv.notify_all()

    def _handle_read_line(self, line: str) -> None:
        """解析并分发一行 stdout。解析失败/非对象 JSON 当作原始行处理，绝不抛到读循环外。"""
        try:
            obj = json.loads(line)
        except Exception:
            self._events.put(("raw", line))
            return
        # 合法 JSON 但非对象（如 [1,2]/42）：不能 .get()，当作原始行跳过，避免 AttributeError 杀死读线程。
        if not isinstance(obj, dict):
            self._events.put(("raw", line))
            return
        if obj.get("type") == "control_response":
            rid = (obj.get("response") or {}).get("request_id")
            if rid:
                with self._control_cv:
                    # 只收录仍在等待的 id；超时/未知 id 的迟到响应丢弃（避免无界增长）。
                    if rid in self._pending_control_ids:
                        self._control_responses[rid] = obj
                        self._control_cv.notify_all()
            return
        # CLI 反向发来的 control_request（目前只处理工具审批 can_use_tool）。
        if obj.get("type") == "control_request":
            self._maybe_handle_permission(obj)
            return
        self._events.put(("event", obj))

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
        # 登记为「等待中」，读循环据此只收录本 id 的响应。
        with self._control_cv:
            self._pending_control_ids.add(rid)
        try:
            with self._stdin_lock:
                self._proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
        except Exception:
            with self._control_cv:
                self._pending_control_ids.discard(rid)
                self._control_responses.pop(rid, None)
            return None
        self.last_activity = time.time()
        deadline = time.time() + timeout
        with self._control_cv:
            while rid not in self._control_responses:
                remaining = deadline - time.time()
                if remaining <= 0:
                    # 超时放弃：清掉等待登记，迟到的响应将被读循环丢弃，不再驻留。
                    self._pending_control_ids.discard(rid)
                    self._control_responses.pop(rid, None)
                    return None
                self._control_cv.wait(timeout=remaining)
            self._pending_control_ids.discard(rid)
            return self._control_responses.pop(rid)

    # ── 交互式工具审批（人在环路）──────────────────────────────────
    # CLI 在 stream-json 模式下就某个工具调用反向发来 control_request(can_use_tool)。
    # 这里拦截 → 在独立线程里问回调（避免阻塞读循环）→ 回写 control_response。
    #
    # ⚠️ 注意：can_use_tool 的请求/响应确切字段名属于 Claude Code 未公开文档的协议，
    # 下面的解析与回写按 SDK 行为做了最可能的推断，**需在装有 claude 的真机上实测对齐**。
    # 解析/回写各自隔离在一个方法里，便于按实测结果调整。
    @staticmethod
    def _parse_permission_request(obj: dict) -> Optional[tuple]:
        req = obj.get("request") or {}
        if req.get("subtype") != "can_use_tool":
            return None  # 其它 control_request 暂不处理
        rid = obj.get("request_id") or req.get("request_id") or ""
        tool_name = req.get("tool_name") or req.get("name") or ""
        tool_input = req.get("input") or req.get("tool_input") or {}
        return rid, tool_name, tool_input

    def _maybe_handle_permission(self, obj: dict) -> None:
        parsed = self._parse_permission_request(obj)
        if parsed is None:
            return
        rid, tool_name, tool_input = parsed
        cb = self._on_permission

        def _resolve():
            allow = False
            try:
                if cb is not None:
                    allow = bool(cb(rid, tool_name, tool_input))
            except Exception:
                allow = False  # 网关异常/无回调 → 安全默认拒绝
            self._respond_permission(rid, allow, tool_input)

        threading.Thread(target=_resolve, daemon=True).start()

    def _respond_permission(self, request_id: str, allow: bool, tool_input: dict) -> None:
        inner = (
            {"behavior": "allow", "updatedInput": tool_input}
            if allow else
            {"behavior": "deny", "message": "用户拒绝了该工具调用"}
        )
        envelope = {
            "type": "control_response",
            "response": {"subtype": "success", "request_id": request_id, "response": inner},
        }
        try:
            with self._stdin_lock:
                self._proc.stdin.write(json.dumps(envelope, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
        except Exception:
            pass

    def run_turn(self, task_id, prompt, on_event, timeout_sec=1800.0,
                 on_permission=None) -> TurnResult:
        with self._turn_lock:
          self._on_permission = on_permission
          self._set_busy(True)
          try:
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
          finally:
            self._on_permission = None
            self._set_busy(False)


def create_warm_session(
    conv_id: str,
    cwd: str,
    agent_type: str,
    *,
    force: bool = False,
    resume_session_id: Optional[str] = None,
    options: Optional[dict] = None,
    extra_env: Optional[dict] = None,
    which: Optional[Callable[[str], Optional[str]]] = None,
    home_dir: Optional[Path] = None,
) -> Optional[WarmSession]:
    """按 agent_type 构造对应的常驻会话；不支持常驻的类型返回 None。"""
    if agent_type == "claude_code":
        return ClaudeWarmSession(
            conv_id, cwd, agent_type,
            force=force, resume_session_id=resume_session_id, options=options,
            extra_env=extra_env, which=which, home_dir=home_dir,
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
        extra_env: Optional[dict] = None,
    ) -> Optional[WarmSession]:
        """
        返回该会话的常驻进程（必要时启动）。不支持常驻或启动失败时返回 None，
        调用方应回退到一次性执行路径。
        """
        if not is_warm_capable(agent_type):
            return None
        evicted: List[WarmSession] = []
        with self._lock:
            existing = self._sessions.get(conv_id)
            if existing is not None and existing.is_alive():
                return existing
            # 进程已死/不存在：清理后重建（带 resume 兜底）。死进程 close 很快，留在锁内可接受。
            if existing is not None:
                existing.close()
                self._sessions.pop(conv_id, None)
            session = self._factory(
                conv_id, cwd, agent_type,
                force=force,
                resume_session_id=resume_session_id or (existing.session_id if existing else None),
                options=options,
                extra_env=extra_env,
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
            evicted = self._evict_over_capacity_locked(keep=conv_id)
        # 超容量回收的会话在锁外 close（terminate + 最长 3s wait），避免拖住派发/轮询。
        for s in evicted:
            try:
                s.close()
            except Exception:
                pass
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
        to_close: List[WarmSession] = []
        with self._lock:
            for conv_id, session in list(self._sessions.items()):
                dead = not session.is_alive()
                busy = session.is_busy()
                # 繁忙（在途轮次）会话不因空闲/待回收而被拆除，避免打断在途任务。
                idle_closed = (
                    not busy
                    and conv_id not in active
                    and (now - session.last_activity) >= self._idle_ttl
                )
                recycle = session.recycle_requested and not busy
                if dead or idle_closed or recycle:
                    self._sessions.pop(conv_id, None)
                    to_close.append(session)
                    closed.append(conv_id)
            to_close.extend(self._evict_over_capacity_locked())
        # close（含 terminate + 最长 3s wait）放到锁外，避免拖住派发/轮询。
        for session in to_close:
            try:
                session.close()
            except Exception:
                pass
        return closed

    def _evict_over_capacity_locked(self, keep: Optional[str] = None) -> List[WarmSession]:
        """超出 max_sessions 时按 last_activity LRU 选出待回收会话，从注册表移除并返回。
        调用方须已持锁，并在**锁外** close 返回的会话。跳过繁忙会话（容量可短暂超限）。"""
        if len(self._sessions) <= self._max_sessions:
            return []
        victims = sorted(self._sessions.items(), key=lambda kv: kv[1].last_activity)
        evicted: List[WarmSession] = []
        for conv_id, session in victims:
            if len(self._sessions) <= self._max_sessions:
                break
            if conv_id == keep:
                continue
            if session.is_busy():
                continue
            self._sessions.pop(conv_id, None)
            evicted.append(session)
        return evicted

    def active_conv_ids(self) -> List[str]:
        with self._lock:
            return list(self._sessions.keys())

    def restart_idle_sessions(self) -> List[str]:
        """回收所有空闲常驻会话以套用新设置；繁忙会话标记为待回收，待其空闲后由 reconcile 回收。
        不打断在途轮次。返回被立即回收的 conv_id 列表。"""
        closed: List[str] = []
        to_close: List[WarmSession] = []
        with self._lock:
            for conv_id, session in list(self._sessions.items()):
                if session.is_busy():
                    session.mark_for_recycle()
                    continue
                self._sessions.pop(conv_id, None)
                to_close.append(session)
                closed.append(conv_id)
        for session in to_close:
            try:
                session.close()
            except Exception:
                pass
        return closed

    def shutdown_all(self) -> None:
        """关停所有常驻会话（应用退出路径）：强制关闭，包括繁忙会话。"""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass
