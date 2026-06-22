"""
autotitle.py – 为会话自动生成「一眼看懂」的简短主题标题。

策略
────
首轮任务成功后，用一次性 ``claude -p`` 把首条 prompt 概括成 ≤几个词的主题（复用现有
CLI、无需 API key、独立进程不污染会话上下文）。claude 不可用或失败时退化为启发式标题
（取首行、去 markdown 装饰、限长）。后端保证不会覆盖用户手动设定的标题。
"""

import os
import re
import subprocess
from typing import Callable, Optional

try:
    from LocalBroker.agent_runtime import _resolve_agent_command
except ModuleNotFoundError:
    from agent_runtime import _resolve_agent_command


_TITLE_PROMPT = (
    "用不超过6个词、不要加引号和标点，概括下面这个任务的主题，"
    "只输出标题本身，不要解释：\n\n{prompt}"
)


def heuristic_title(text: str, limit: int = 48) -> str:
    """从文本取一个干净的单行标题：首个非空行、去掉 markdown 装饰、压缩空白、限长。"""
    if not text:
        return ""
    line = ""
    for raw in text.splitlines():
        if raw.strip():
            line = raw
            break
    line = re.sub(r"^[\s#>*\-+`~.]+", "", line)   # 去 markdown 项目符号/标题井号
    line = line.strip().strip("\"'“”‘’")
    line = " ".join(line.split())
    return line[:limit]


def generate_title(
    prompt: str,
    cwd: str,
    *,
    timeout_sec: int = 30,
    which: Optional[Callable[[str], Optional[str]]] = None,
    run: Callable[..., "subprocess.CompletedProcess"] = subprocess.run,
) -> str:
    """
    生成会话标题。优先用一次性 claude -p；不可用/失败/超时则退化为 heuristic_title(prompt)。
    始终返回一个非空（除非 prompt 也为空）的标题字符串。
    """
    fallback = heuristic_title(prompt)
    try:
        kwargs = {"which": which} if which is not None else {}
        command = _resolve_agent_command("claude_code", **kwargs)
    except Exception:
        return fallback
    ask = _TITLE_PROMPT.format(prompt=(prompt or "")[:1000])
    try:
        proc = run(
            [command, "-p", "--output-format", "text", ask],
            cwd=cwd if cwd and os.path.isdir(cwd) else None,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except Exception:
        return fallback
    if getattr(proc, "returncode", 1) != 0:
        return fallback
    title = heuristic_title(getattr(proc, "stdout", "") or "")
    return title or fallback
