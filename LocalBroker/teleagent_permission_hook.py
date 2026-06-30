#!/usr/bin/env python3
"""
teleagent-permission-hook – Claude Code 的 PreToolUse hook：把工具审批转给 Web 用户。

LocalBroker 开启交互式审批时，会给常驻 claude 注入一份 settings，把本脚本配为
敏感工具的 PreToolUse hook，并通过环境变量注入会话上下文：
  TELEAGENT_API_BASE / TELEAGENT_TOKEN / TELEAGENT_CONVERSATION_ID

每次匹配的工具调用前，claude 用 stdin 传入 {tool_name, tool_input, ...}，本脚本：
  建一条 PermissionRequest → 轮询 Web 用户应答（允许/拒绝/一直允许）→ 输出
  {"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"|"deny",...}}
超时/出错一律安全拒绝；无 TeleAgent 上下文（非托管运行）则放行不打扰。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from LocalBroker.broker_api import create_permission_request, get_permission_request
except ModuleNotFoundError:
    from broker_api import create_permission_request, get_permission_request


def _emit(decision, reason=""):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,           # "allow" | "deny"
            "permissionDecisionReason": reason,
        }
    }))


def decide(
    event,
    *,
    base,
    token,
    conversation_id,
    timeout_sec=120.0,
    poll_interval=1.0,
    create_fn=create_permission_request,
    get_fn=get_permission_request,
    sleep_fn=time.sleep,
):
    """返回 (decision, reason)。decision ∈ {"allow","deny"}。"""
    tool_name = event.get("tool_name") or ""
    tool_input = event.get("tool_input") or {}
    try:
        created = create_fn(
            conversation_id, tool_name, tool_input=tool_input, base=base, token=token,
        )
    except Exception as exc:
        return "deny", f"TeleAgent 审批建请求失败：{exc}"
    pid = created.get("id")
    status = created.get("status")
    if not pid:
        return "deny", "TeleAgent 审批未创建"
    attempts = max(1, int(timeout_sec / max(0.1, poll_interval)))
    for _ in range(attempts):
        if status == "allowed":
            return "allow", "用户已允许"
        if status == "denied":
            return "deny", "用户已拒绝"
        sleep_fn(poll_interval)
        try:
            status = get_fn(pid, base=base, token=token).get("status", "pending")
        except Exception:
            status = "pending"
    return "deny", "等待用户审批超时"


def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        _emit("allow", "无法解析 hook 输入，放行")
        return 0

    base = os.environ.get("TELEAGENT_API_BASE", "")
    token = os.environ.get("TELEAGENT_TOKEN") or None
    conv_id = os.environ.get("TELEAGENT_CONVERSATION_ID") or None
    # 非 TeleAgent 托管运行（缺上下文）：放行，不干扰用户本地使用。
    if not base or not conv_id:
        _emit("allow", "非 TeleAgent 托管会话，放行")
        return 0

    # 默认等 15 分钟人来应答（人在环路，宁可久等也别太早放弃）；可用 env 覆盖。
    timeout = float(os.environ.get("BROKER_PERMISSION_TIMEOUT", "900") or 900)
    poll = float(os.environ.get("BROKER_PERMISSION_POLL", "2.0") or 2.0)
    decision, reason = decide(
        event, base=base, token=token, conversation_id=conv_id,
        timeout_sec=timeout, poll_interval=poll,
    )
    _emit(decision, reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
