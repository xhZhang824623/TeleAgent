import shutil
from pathlib import Path
from typing import Callable, List, Optional


AGENT_TYPES = {
    "codex": {
        "label": "Codex",
        "commands": ["codex"],
    },
    "claude_code": {
        "label": "Claude Code",
        "commands": ["claude", "claude-code", "claude_code"],
    },
    "cursor_agent": {
        "label": "Cursor Agent",
        "commands": ["agent", "cursor-agent", "cursor_agent"],
    },
}


def supported_agent_types() -> List[str]:
    return list(AGENT_TYPES.keys())


def discover_supported_agents(
    which: Callable[[str], Optional[str]] = shutil.which,
    home_dir: Optional[Path] = None,
) -> List[str]:
    supported = []
    for agent_type, meta in AGENT_TYPES.items():
        try:
            _resolve_agent_command(agent_type, which=which, home_dir=home_dir)
            supported.append(agent_type)
        except FileNotFoundError:
            continue
    return supported


def _resolve_agent_command(
    agent_type: str,
    *,
    which: Callable[[str], Optional[str]] = shutil.which,
    home_dir: Optional[Path] = None,
) -> str:
    if agent_type not in AGENT_TYPES:
        raise ValueError(f"Unsupported agent_type: {agent_type}")
    for command in AGENT_TYPES[agent_type]["commands"]:
        resolved = which(command)
        if resolved:
            return resolved
    for path in _fallback_command_paths(AGENT_TYPES[agent_type]["commands"], home_dir=home_dir):
        if path.exists():
            return str(path)
    raise FileNotFoundError(f"{agent_type} CLI not found in PATH")


def _fallback_command_paths(commands: List[str], home_dir: Optional[Path] = None) -> List[Path]:
    home = Path.home() if home_dir is None else Path(home_dir)
    candidates: List[Path] = []
    for command in commands:
        candidates.append(home / ".local/bin" / command)
        candidates.extend((home / ".nvm/versions/node").glob(f"*/bin/{command}"))
        candidates.append(home / ".pyenv/shims" / command)
    return candidates


# ── 声明式 Agent 参数 → CLI flag 映射（命令构建侧）──────────────────────────
# 前端按 schema 渲染控件、存进 Conversation.options(JSON)；这里把 options 翻成 flag。
# 加新参数 = 在这里和前端 schema 各加一条，无需迁移。

CLAUDE_PERMISSION_MODES = {"default", "plan", "acceptEdits", "bypassPermissions", "dontAsk", "auto"}
CLAUDE_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def claude_option_args(options: Optional[dict] = None, force: bool = False) -> List[str]:
    """把会话/任务的 options 映射成 claude 的 CLI 参数。未知/非法值忽略，确保不注入任意串。"""
    opts = options or {}
    args: List[str] = []
    pm = opts.get("permission_mode")
    if pm in CLAUDE_PERMISSION_MODES and pm != "default":
        args += ["--permission-mode", pm]
    elif force:
        # 未显式指定 permission_mode 时，沿用旧的 force 语义（= bypassPermissions）。
        args.append("--dangerously-skip-permissions")
    model = opts.get("model")
    if model:
        args += ["--model", str(model)]
    effort = opts.get("effort")
    if effort in CLAUDE_EFFORTS:
        args += ["--effort", effort]
    return args


def build_agent_command(
    agent_type: str,
    *,
    prompt: str,
    force: bool = False,
    resume_session_id: Optional[str] = None,
    output_format: str = "stream-json",
    stream_partial: bool = True,
    options: Optional[dict] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
    home_dir: Optional[Path] = None,
) -> List[str]:
    command = _resolve_agent_command(agent_type, which=which, home_dir=home_dir)
    if agent_type == "codex":
        args = [command, "exec", "--json", "--skip-git-repo-check"]
        if force:
            args.append("--dangerously-bypass-approvals-and-sandbox")
        args.append(prompt)
        return args

    if agent_type == "claude_code":
        args = [command, "-p"]
        args.extend(claude_option_args(options, force=force))
        if resume_session_id:
            args.extend(["--resume", resume_session_id])
        args.extend(["--output-format", output_format])
        if output_format == "stream-json":
            # --print 下 stream-json 输出要求 --verbose
            args.append("--verbose")
            if stream_partial:
                args.append("--include-partial-messages")
        args.append(prompt)
        return args

    args = [command, "-p", "--trust"]
    if force:
        args.append("--force")
    if resume_session_id:
        args.extend(["--resume", resume_session_id])
    args.extend(["--output-format", output_format])
    if output_format == "stream-json" and stream_partial:
        args.append("--stream-partial-output")
    args.append(prompt)
    return args
