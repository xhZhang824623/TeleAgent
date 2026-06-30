#!/usr/bin/env python3
"""
teleagent-send – 让 Agent 把本机文件「发回」给 Web 用户下载。

在 TeleAgent 的 claude_code 常驻会话里，LocalBroker 会给 Agent 子进程注入：
  TELEAGENT_API_BASE / TELEAGENT_TOKEN / TELEAGENT_CLIENT_ID / TELEAGENT_CONVERSATION_ID
Agent（被用户要求「把 X 文件发给我」时）执行：
  teleagent-send /path/to/file
即把文件经 Django 中转，随后用户在 Web 会话里看到一张下载卡片。

也可手动用环境变量驱动：
  TELEAGENT_API_BASE=... TELEAGENT_TOKEN=... TELEAGENT_CLIENT_ID=... \
  TELEAGENT_CONVERSATION_ID=... teleagent-send ./report.pdf

安装：把本文件软链/拷成 PATH 上的 `teleagent-send`，例如
  ln -s "$PWD/LocalBroker/teleagent_send.py" ~/.local/bin/teleagent-send
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from LocalBroker.broker_api import create_file_transfer, upload_file_transfer
    from LocalBroker.broker_worker import read_file_b64, FILE_TRANSFER_MAX_BYTES
except ModuleNotFoundError:
    from broker_api import create_file_transfer, upload_file_transfer
    from broker_worker import read_file_b64, FILE_TRANSFER_MAX_BYTES


def send_file(
    path,
    *,
    base,
    token,
    client_id,
    conversation_id=None,
    root=None,
    max_bytes=FILE_TRANSFER_MAX_BYTES,
    read_fn=read_file_b64,
    create_fn=create_file_transfer,
    upload_fn=upload_file_transfer,
):
    """读文件并经 Django 中转上传。返回服务端的 transfer dict。抛 ValueError/RuntimeError。

    安全：读取受 root 受限根约束（默认取 broker 注入的 TELEAGENT_SESSION_ROOT = 会话工作目录），
    禁止外发受限根之外的任意文件（如 ~/.aws/credentials）。无受限根时拒绝。"""
    if not client_id or not base:
        raise RuntimeError("缺少 broker 上下文（TELEAGENT_API_BASE / TELEAGENT_CLIENT_ID）")
    if root is None:
        root = os.environ.get("TELEAGENT_SESSION_ROOT") or ""
    if not root or not str(root).strip():
        raise RuntimeError(
            "缺少受限根目录（TELEAGENT_SESSION_ROOT）：出于安全，禁止发送任意文件，"
            "仅允许发送会话工作目录内的文件。"
        )
    filename, content_type, content_b64, size = read_fn(path, max_bytes, root=root)
    created = create_fn(
        client_id, os.path.abspath(os.path.expanduser(path)),
        conversation_id=conversation_id, agent_initiated=True, base=base, token=token,
    )
    tid = created.get("id")
    if not tid:
        raise RuntimeError(f"创建传输失败：{created}")
    result = upload_fn(
        tid, filename=filename, content_b64=content_b64, content_type=content_type,
        base=base, token=token,
    )
    return {**result, "filename": filename, "size": size}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="teleagent-send", description="把本机文件发给 Web 用户下载")
    parser.add_argument("path", help="要发送的文件路径")
    args = parser.parse_args(argv)

    base = os.environ.get("TELEAGENT_API_BASE", "")
    token = os.environ.get("TELEAGENT_TOKEN") or None
    client_id = os.environ.get("TELEAGENT_CLIENT_ID", "")
    conv_id = os.environ.get("TELEAGENT_CONVERSATION_ID") or None

    try:
        res = send_file(args.path, base=base, token=token, client_id=client_id, conversation_id=conv_id)
    except Exception as exc:
        print(f"❌ 发送失败：{exc}", file=sys.stderr)
        return 1
    print(f"✅ 已发送 {res.get('filename')}（{res.get('size')} 字节），用户可在 Web 会话里下载。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
