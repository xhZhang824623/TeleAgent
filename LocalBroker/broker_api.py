"""
broker_api.py – Django Broker API 的同步 HTTP 客户端。

用于 LocalBroker（Qt）与 Django 对接。
环境变量 BROKER_API_BASE：例如 https://localhost:9443 或 http://localhost:9020
"""

import json
import os
import urllib.request
import urllib.error
import ssl
from typing import Any, Dict, List, Optional

DEFAULT_BASE = os.environ.get("BROKER_API_BASE", "http://localhost:9020")
API_PATH = "/api/broker"


class AuthError(RuntimeError):
    """Raised when the Broker API rejects the current token/credentials."""


def _url(path: str, base: str = DEFAULT_BASE) -> str:
    path = path if path.startswith("/") else "/" + path
    base = base.rstrip("/")
    return base + API_PATH + path


def _req(
    method: str,
    path: str,
    body: Optional[Dict] = None,
    base: str = DEFAULT_BASE,
    timeout: int = 30,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    url = _url(path, base)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    headers = {}
    if data:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Token {token}"
    req = urllib.request.Request(
        url, data=data, method=method, headers=headers if headers else {}
    )
    ctx = ssl.create_default_context()
    if base.startswith("https://") and "localhost" in base:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            raw = r.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = ""
        if e.fp:
            try:
                raw = e.fp.read(2000).decode("utf-8", errors="replace")
            except Exception:
                pass
        if e.code == 401:
            raise AuthError(f"API {method} {path}: HTTP 401 {raw[:500]}") from e
        if "<!DOCTYPE" in raw or "<html" in raw.lower():
            raise RuntimeError(
                f"API {method} {path}: HTTP {e.code} – server returned HTML (Django error page?). "
                f"Check BROKER_API_BASE and that the backend is up (e.g. https://localhost:9443/api/broker/)."
            ) from e
        raise RuntimeError(f"API {method} {path}: HTTP {e.code} {raw[:500]}") from e


def _auth_req(method: str, path: str, body: Optional[Dict], base: str) -> Dict:
    """Auth 接口使用 /api/auth/，不用 /api/broker/。"""
    base = base.rstrip("/")
    path = path if path.startswith("/") else "/" + path
    url = base + path
    data = json.dumps(body).encode("utf-8") if body else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    ctx = ssl.create_default_context()
    if base.startswith("https://") and "localhost" in base:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = ""
        if e.fp:
            try:
                raw = e.fp.read(2000).decode("utf-8", errors="replace")
            except Exception:
                pass
        if e.code == 401:
            raise AuthError(f"Auth {method} {path}: HTTP 401 {raw[:500]}") from e
        raise RuntimeError(f"Auth {method} {path}: HTTP {e.code} {raw[:500]}") from e


def login(email: str, password: str, base: str = DEFAULT_BASE) -> Dict:
    """登录，返回 {token, user_id, email}。"""
    return _auth_req(
        "POST",
        "/api/auth/login/",
        {"email": email.strip().lower(), "password": password},
        base,
    )


def register(email: str, password: str, base: str = DEFAULT_BASE) -> Dict:
    """注册，返回 {token, user_id, email}。"""
    return _auth_req(
        "POST",
        "/api/auth/register/",
        {"email": email.strip().lower(), "password": password},
        base,
    )


def client_login(client_id: str, secret_key: str, base: str = DEFAULT_BASE) -> Dict:
    """
    LocalBroker 使用管理平台签发的 客户端 ID + Secret Key 登录。
    返回 {token, user_id, email}，与 login 一致。
    """
    return _auth_req(
        "POST",
        "/api/auth/client-login/",
        {"client_id": client_id.strip(), "secret_key": secret_key},
        base,
    )


def get_conversations(base: str = DEFAULT_BASE, token: Optional[str] = None) -> List[Dict]:
    return _req("GET", "/conversations/", base=base, token=token)


def list_clients(base: str = DEFAULT_BASE, token: Optional[str] = None) -> List[Dict]:
    return _req("GET", "/clients/", base=base, token=token)


def register_client(
    name: str,
    hostname: str = "",
    supported_agents: Optional[List[str]] = None,
    base: str = DEFAULT_BASE,
    token: Optional[str] = None,
) -> Dict:
    return _req(
        "POST",
        "/clients/",
        body={
            "name": name,
            "hostname": hostname or "",
            "supported_agents": supported_agents or [],
        },
        base=base,
        token=token,
    )


def heartbeat_client(
    client_id: str,
    supported_agents: Optional[List[str]] = None,
    base: str = DEFAULT_BASE,
    token: Optional[str] = None,
) -> Dict:
    return _req(
        "PATCH",
        f"/clients/{client_id}/",
        body={"supported_agents": supported_agents or []},
        base=base,
        token=token,
    )


def get_queued_tasks(
    client_id: Optional[str] = None,
    base: str = DEFAULT_BASE,
    token: Optional[str] = None,
) -> List[Dict]:
    path = "/tasks/queued/"
    if client_id:
        path += f"?client_id={client_id}"
    return _req("GET", path, base=base, token=token)


def get_active_conversations(
    client_id: str,
    base: str = DEFAULT_BASE,
    token: Optional[str] = None,
) -> List[Dict]:
    """拉取分配给该 client 且正被 Web 打开的会话（用于预热常驻 Agent 进程）。"""
    return _req(
        "GET",
        f"/conversations/active/?client_id={client_id}",
        base=base,
        token=token,
    )


def open_conversation(
    conv_id: str, base: str = DEFAULT_BASE, token: Optional[str] = None
) -> Dict:
    return _req("POST", f"/conversations/{conv_id}/open/", body={}, base=base, token=token)


def get_pending_controls(
    client_id: str, base: str = DEFAULT_BASE, token: Optional[str] = None
) -> List[Dict]:
    """拉取分配给该 client 的会话上待应用的动态控制指令。"""
    return _req("GET", f"/controls/pending/?client_id={client_id}", base=base, token=token)


def ack_control(
    control_id: str,
    *,
    status: str,
    result: str = "",
    base: str = DEFAULT_BASE,
    token: Optional[str] = None,
) -> Dict:
    """回报某条控制指令的应用结果（applied / failed）。"""
    return _req(
        "PATCH",
        f"/controls/{control_id}/",
        body={"status": status, "result": result},
        base=base,
        token=token,
    )


def close_conversation(
    conv_id: str, base: str = DEFAULT_BASE, token: Optional[str] = None
) -> Dict:
    return _req("POST", f"/conversations/{conv_id}/close/", body={}, base=base, token=token)


def create_conversation(
    cwd: str,
    agent_type: str,
    title: str = "",
    client_id: Optional[str] = None,
    base: str = DEFAULT_BASE,
    token: Optional[str] = None,
) -> Dict:
    body = {"cwd": cwd, "title": title, "agent_type": agent_type}
    if client_id:
        body["client_id"] = client_id
    return _req("POST", "/conversations/", body=body, base=base, token=token)


def get_conversation(
    conv_id: str, base: str = DEFAULT_BASE, token: Optional[str] = None
) -> Dict:
    return _req("GET", f"/conversations/{conv_id}/", base=base, token=token)


def set_conversation_title(
    conv_id: str,
    title: str,
    auto: bool = False,
    base: str = DEFAULT_BASE,
    token: Optional[str] = None,
) -> Dict:
    """更新会话标题。auto=True 为自动摘要（后端在用户已自定义时会拒绝覆盖）。"""
    return _req(
        "PATCH",
        f"/conversations/{conv_id}/",
        body={"title": title, "auto": bool(auto)},
        base=base,
        token=token,
    )


def send_message(
    conv_id: str,
    prompt: str,
    force: bool = False,
    base: str = DEFAULT_BASE,
    token: Optional[str] = None,
) -> Dict:
    return _req(
        "POST",
        f"/conversations/{conv_id}/messages/",
        body={
            "prompt": prompt,
            "force": force,
            "output_format": "stream-json",
            "stream_partial": True,
            "timeout_sec": 1800,
        },
        base=base,
        timeout=60,
        token=token,
    )


def get_conversation_tasks(
    conv_id: str, base: str = DEFAULT_BASE, token: Optional[str] = None
) -> List[Dict]:
    return _req("GET", f"/conversations/{conv_id}/tasks/", base=base, token=token)


def get_task(
    task_id: str, base: str = DEFAULT_BASE, token: Optional[str] = None
) -> Dict:
    return _req("GET", f"/tasks/{task_id}/", base=base, token=token)


def patch_task(
    task_id: str,
    *,
    status: Optional[str] = None,
    started_at: Optional[str] = None,
    heartbeat_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    result_text: Optional[str] = None,
    exit_code: Optional[int] = None,
    events: Optional[List[Dict]] = None,
    raw_lines: Optional[List[str]] = None,
    base: str = DEFAULT_BASE,
    token: Optional[str] = None,
) -> Dict:
    body = {}
    if status is not None:
        body["status"] = status
    if started_at is not None:
        body["started_at"] = started_at
    if heartbeat_at is not None:
        body["heartbeat_at"] = heartbeat_at
    if finished_at is not None:
        body["finished_at"] = finished_at
    if result_text is not None:
        body["result_text"] = result_text
    if exit_code is not None:
        body["exit_code"] = exit_code
    if events is not None:
        body["events"] = events
    if raw_lines is not None:
        body["raw_lines"] = raw_lines
    return _req("PATCH", f"/tasks/{task_id}/", body=body, base=base, token=token)


def post_task_events(
    task_id: str,
    events: List[Dict],
    base: str = DEFAULT_BASE,
    token: Optional[str] = None,
) -> Dict:
    return _req(
        "POST",
        f"/tasks/{task_id}/events/",
        body={"events": events},
        base=base,
        token=token,
    )
