#!/usr/bin/env python3
"""
Headless LocalBroker 服务入口（无界面），适合用 systemd --user 托管，避免前台 GUI 被误关。

复用与 GUI 版完全相同、已测试的轮询/执行循环（broker_qt.BrokerWorkerThread）：
QtCore 的 QThread/QSettings/信号机制不需要图形显示，故用 QCoreApplication 即可 headless 运行。

凭据来源：与 GUI 版共享同一份 QSettings（~/.config/TeleAgent/Broker.conf）。
首次用 `broker_service.py login ...` 写入，或先跑一次 GUI 登录。
token 过期由 CloudSessionManager 凭 client_id+secret 自动重登，无需人工干预。

用法：
  broker_service.py login --api-base http://server:9020 --client-id <UUID> --secret <KEY> [--name 本机名]
  broker_service.py run
  broker_service.py status
"""

import argparse
import logging
import os
import signal
import socket
import sys

# 同时支持「python -m LocalBroker.broker_service」与「python broker_service.py」两种运行方式。
try:
    from LocalBroker import broker_qt as bq
    from LocalBroker.broker_api import register_client, client_login, AuthError
except ModuleNotFoundError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import broker_qt as bq
    from broker_api import register_client, client_login, AuthError

from PyQt5.QtCore import QCoreApplication

log = logging.getLogger("teleagent.broker.service")


def _read_saved_credentials():
    """从 QSettings 读出服务运行所需的凭据（与 GUI 版同一份）。"""
    settings = bq._load_cloud_settings()
    cred_id, secret = bq._load_cloud_client_credentials()
    token, _email = bq._load_cloud_token()
    return {
        "api_base": settings["api_base"],
        "client_name": settings["client_name"],
        "credential_id": cred_id,
        "secret_key": secret,
        "token": token,
    }


def cmd_login(args) -> int:
    """命令行登录：保存配置/凭据，并立即用 client_id+secret 换取 token 验证。"""
    bq._setup_logging()
    api_base = (args.api_base or "").strip() or bq.DEFAULT_API_BASE
    client_name = (args.name or "").strip() or (socket.gethostname() or "本机")
    cred_id = (args.client_id or "").strip()
    secret = args.secret or ""
    if not cred_id or not secret:
        print("错误：必须提供 --client-id 与 --secret", file=sys.stderr)
        return 2
    try:
        out = client_login(cred_id, secret, base=api_base)
    except Exception as exc:  # noqa: BLE001 — 给用户可读的失败原因
        print(f"登录失败：{exc}", file=sys.stderr)
        return 1
    token = out.get("token")
    if not token:
        print("登录失败：服务端未返回 token", file=sys.stderr)
        return 1
    bq._save_cloud_settings(api_base, client_name)
    bq._save_cloud_client_credentials(cred_id, secret)
    bq._save_cloud_token(token, out.get("email", ""))
    print(f"登录成功，凭据已保存（{api_base}，client={client_name}）。现在可启动服务：broker_service.py run")
    return 0


def cmd_status(_args) -> int:
    """打印当前保存的凭据概要（不泄露 secret/token 明文）。"""
    creds = _read_saved_credentials()
    have = bool(creds["api_base"] and creds["credential_id"] and creds["secret_key"])
    print(f"API 地址   : {creds['api_base'] or '(未设置)'}")
    print(f"本机名称   : {creds['client_name'] or '(未设置)'}")
    print(f"客户端凭证 : {'已保存' if creds['credential_id'] else '(未设置)'}")
    print(f"Secret     : {'已保存' if creds['secret_key'] else '(未设置)'}")
    print(f"Token      : {'已保存' if creds['token'] else '(无，将在启动时自动登录)'}")
    print(f"可启动服务 : {'是' if have else '否（请先 login）'}")
    return 0 if have else 1


def cmd_run(_args) -> int:
    """headless 启动：读凭据 → 注册客户端 → 跑与 GUI 版相同的 worker 循环。"""
    bq._setup_logging()
    log.info("启动模式：systemd 服务（headless）")

    # 与 GUI 版共用同一把单实例锁，二者不能同时运行。
    lock_file = bq.acquire_single_instance_lock()
    if lock_file is None:
        log.error("已有另一个 Broker 实例在运行（桌面版或服务）。请先关闭它，避免重复执行任务。"
                  "若是桌面版占用，请关闭其窗口后重试。")
        return 3

    creds = _read_saved_credentials()
    if not (creds["api_base"] and creds["credential_id"] and creds["secret_key"]):
        log.error("未找到已保存的凭据。请先运行：broker_service.py login --api-base ... --client-id ... --secret ...")
        return 2

    app = QCoreApplication(sys.argv)

    session = bq.CloudSessionManager(
        api_base=creds["api_base"],
        credential_id=creds["credential_id"],
        secret_key=creds["secret_key"],
        token=creds["token"],
        save_token_fn=bq._save_cloud_token,
    )

    # 套用已保存的「交互式工具审批」设置（与 GUI 版一致），保证安全策略在 headless 下也生效。
    try:
        from session_manager import set_interactive_permissions, interactive_permissions_enabled
    except ModuleNotFoundError:
        from LocalBroker.session_manager import set_interactive_permissions, interactive_permissions_enabled
    set_interactive_permissions(bq._load_interactive_permissions(interactive_permissions_enabled()))

    # 注册客户端记录（拿 client_id）；token 失效时 session.call 会自动用 secret 重登后重试。
    try:
        client = session.call(
            register_client,
            name=creds["client_name"],
            hostname=socket.gethostname() or "",
            supported_agents=bq.discover_supported_agents(),
            base=creds["api_base"],
        )
        client_id = str(client["id"])
    except AuthError:
        log.error("认证失败：客户端凭证可能已被吊销或 secret 不正确。请重新 login。")
        return 4
    except Exception as exc:  # noqa: BLE001
        log.error("注册云端客户端失败：%s", exc)
        return 1

    worker = bq.BrokerWorkerThread(
        creds["api_base"], client_id, token=session.token, session_manager=session,
    )
    worker.status_update.connect(lambda kind, msg: log.info("[%s] %s", kind, msg))

    def _shutdown(signum, _frame):
        log.info("收到信号 %s，正在优雅停止 worker…", signum)
        worker.stop()
        worker.wait(8000)
        app.quit()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    worker.start()
    log.info("Broker 服务已启动（client=%s，base=%s）。", client_id, creds["api_base"])
    rc = app.exec_()
    log.info("Broker 服务已退出（code=%s）。", rc)
    return rc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="TeleAgent LocalBroker headless 服务")
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="保存凭据并验证登录")
    p_login.add_argument("--api-base", help="云端 API 地址，如 http://server:9020")
    p_login.add_argument("--client-id", help="客户端凭证 ID（UUID）")
    p_login.add_argument("--secret", help="客户端 Secret Key")
    p_login.add_argument("--name", help="本机名称（可选）")
    p_login.set_defaults(func=cmd_login)

    p_run = sub.add_parser("run", help="启动 headless 服务（systemd ExecStart 用）")
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="查看已保存凭据概要")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
