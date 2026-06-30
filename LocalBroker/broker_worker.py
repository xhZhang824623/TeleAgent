"""
broker_worker.py – 与 Django 对接的 Broker 执行逻辑：拉取 queued 任务、本机跑 Agent、回写。

供 Qt 云端模式（CloudBrokerWindow）使用。
"""

import base64
import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Tuple

try:
    from LocalBroker.broker_api import (
        get_queued_tasks, get_task, patch_task, post_task_events, get_active_conversations,
        get_conversation, set_conversation_title, get_pending_controls, ack_control,
        get_pending_fs_requests, ack_fs_request,
        create_permission_request, get_permission_request,
        get_pending_file_transfers, upload_file_transfer, fail_file_transfer,
    )
except ModuleNotFoundError:
    from broker_api import (
        get_queued_tasks, get_task, patch_task, post_task_events, get_active_conversations,
        get_conversation, set_conversation_title, get_pending_controls, ack_control,
        get_pending_fs_requests, ack_fs_request,
        create_permission_request, get_permission_request,
        get_pending_file_transfers, upload_file_transfer, fail_file_transfer,
    )
try:
    from LocalBroker.autotitle import generate_title
except ModuleNotFoundError:
    from autotitle import generate_title
try:
    from LocalBroker.agent_runtime import build_agent_command
except ModuleNotFoundError:
    from agent_runtime import build_agent_command
try:
    from LocalBroker.session_manager import (
        SessionManager, is_warm_capable, interactive_permissions_enabled,
        _ensure_hook_settings, _strip_permission_overrides,
    )
except ModuleNotFoundError:
    from session_manager import (
        SessionManager, is_warm_capable, interactive_permissions_enabled,
        _ensure_hook_settings, _strip_permission_overrides,
    )


PENDING_REPORTS_DIR = Path(__file__).resolve().parent / ".broker_pending"
TASK_HEARTBEAT_INTERVAL_SEC = 15.0

# 一次性（one-shot）Agent 子进程登记表：spawn 时登记、退出时注销，关停时统一回收，
# 避免应用/轮询线程退出后留下孤儿进程继续改文件。线程安全（_oneshot_lock 保护）。
_oneshot_procs: set = set()
_oneshot_lock = threading.Lock()


def _register_oneshot(proc) -> None:
    with _oneshot_lock:
        _oneshot_procs.add(proc)


def _deregister_oneshot(proc) -> None:
    with _oneshot_lock:
        _oneshot_procs.discard(proc)


def terminate_all_oneshot_procs() -> None:
    """关停所有在跑的一次性子进程：先 terminate，短等后仍存活则 kill。供 worker 关停路径调用。"""
    with _oneshot_lock:
        procs = list(_oneshot_procs)
        _oneshot_procs.clear()
    for p in procs:
        try:
            if p.poll() is None:
                p.terminate()
        except Exception:
            pass
    for p in procs:
        try:
            p.wait(timeout=2.0)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

# 交互式工具审批（人在环路）的等待参数。开关本身由 session_manager 统一持有
# （interactive_permissions_enabled），可由 Qt 界面运行时切换。注意：真正生效的审批走
# PreToolUse hook（见 session_manager）；下面的 gateway 是早期 control-协议路径，现为惰性兜底。
PERMISSION_WAIT_TIMEOUT_SEC = float(os.environ.get("BROKER_PERMISSION_TIMEOUT", "900") or 900)
PERMISSION_POLL_INTERVAL_SEC = float(os.environ.get("BROKER_PERMISSION_POLL", "2.0") or 2.0)

# 文件传输单文件大小上限（字节），默认 50MB；须与 Django 的 FILE_TRANSFER_MAX_BYTES 对齐。
FILE_TRANSFER_MAX_BYTES = int(os.environ.get("BROKER_FILE_MAX_BYTES", str(50 * 1024 * 1024)))


def _call_with_retry(
    fn,
    *args,
    attempts: int = 3,
    sleep_fn: Callable[[float], None] = time.sleep,
    **kwargs,
):
    last_error = None
    for attempt in range(attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            if attempt == attempts - 1:
                raise
            sleep_fn(min(5.0, 0.5 * (attempt + 1)))
    raise last_error


def _store_pending_final_report(task_id: str, payload: dict, dir_path: Path = PENDING_REPORTS_DIR) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    report_path = dir_path / f"{task_id}.json"
    report_path.write_text(
        json.dumps({"task_id": task_id, "payload": payload}, ensure_ascii=False),
        encoding="utf-8",
    )
    return report_path


def flush_pending_final_reports(
    *,
    base: str,
    token: Optional[str] = None,
    dir_path: Path = PENDING_REPORTS_DIR,
    patch_task_fn=patch_task,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    if not dir_path.exists():
        return 0
    flushed = 0
    for report_path in sorted(dir_path.glob("*.json")):
        data = json.loads(report_path.read_text(encoding="utf-8"))
        _call_with_retry(
            patch_task_fn,
            data["task_id"],
            attempts=5,
            sleep_fn=sleep_fn,
            base=base,
            token=token,
            **data["payload"],
        )
        report_path.unlink()
        flushed += 1
    return flushed


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_stream_event(obj: dict) -> Optional[dict]:
    if not isinstance(obj, dict):
        # 合法 JSON 但非对象（如 [1,2] / 42）：跳过，避免 .get() 抛 AttributeError 终止整条任务。
        return None
    event_type = obj.get("type")
    if event_type == "item.completed":
        item = obj.get("item") or {}
        if item.get("type") == "agent_message" and item.get("text"):
            text = item["text"]
            return {
                "type": "assistant",
                "message": {"content": [{"text": text}]},
                "_result_text": text,
            }
        if item.get("type") == "command_execution":
            return {
                "type": "tool_call",
                "subtype": "completed",
                "tool_call": {
                    "shellToolCall": {
                        "args": {"command": item.get("command", "")},
                        "result": {
                            "success": {
                                "exitCode": item.get("exit_code"),
                                "stdout": item.get("aggregated_output", ""),
                            }
                        },
                    }
                },
            }
    if event_type == "item.started":
        item = obj.get("item") or {}
        if item.get("type") == "command_execution":
            return {
                "type": "tool_call",
                "subtype": "started",
                "tool_call": {
                    "shellToolCall": {
                        "args": {"command": item.get("command", "")},
                    }
                },
            }
    return obj


def find_one_queued_task(
    base: str,
    client_id: Optional[str] = None,
    token: Optional[str] = None,
    verbose: bool = False,
    get_queued_tasks_fn=get_queued_tasks,
) -> Optional[Tuple[str, str, dict]]:
    """
    返回 (conv_id, task_id, task_detail) 或 None。
    若传 client_id，仅拉取分配给该客户端或未分配的任务。token 为当前用户认证。
    """
    tasks = _call_with_retry(
        get_queued_tasks_fn,
        client_id=client_id,
        base=base,
        token=token,
        attempts=3,
    )
    if verbose:
        print(f"  [worker] GET queued tasks: {len(tasks)} (client_id={client_id or 'any'})", file=sys.stderr)
    for t in tasks:
        task_id = str(t["id"])
        conv_id = t.get("conversation_id")
        conv_id = str(conv_id) if conv_id is not None else ""
        if verbose:
            print(f"  [worker] found queued task {task_id[:8]}... conv={conv_id[:8] if conv_id else '?'}", file=sys.stderr)
        return conv_id, task_id, t
    return None


def run_agent_and_report(
    task_id: str,
    task_detail: dict,
    base: str,
    token: Optional[str] = None,
    on_status: Optional[Callable[[str, str], None]] = None,
    get_task_fn=get_task,
    patch_task_fn=patch_task,
    post_task_events_fn=post_task_events,
) -> None:
    """
    拉取任务详情、本机执行 agent、PATCH/POST 回写 Django。
    on_status("running"|"success"|"failed", message) 可选，用于 UI 回调。
    """
    full = _call_with_retry(
        get_task_fn,
        task_id,
        base=base,
        token=token,
        attempts=3,
    )
    prompt = full.get("prompt", "") or task_detail.get("prompt", "")
    cwd = full.get("cwd") or task_detail.get("cwd") or "/"
    force = full.get("force", task_detail.get("force", False))
    resume = full.get("resume_session_id") or task_detail.get("resume_session_id")
    output_format = full.get("output_format", "stream-json")
    stream_partial = full.get("stream_partial", True)
    timeout_sec = int(full.get("timeout_sec", 1800) or task_detail.get("timeout_sec", 1800))
    agent_type = full.get("agent_type") or task_detail.get("agent_type") or "cursor_agent"
    options = full.get("options") or task_detail.get("options") or {}

    try:
        args = build_agent_command(
            agent_type,
            prompt=prompt,
            force=force,
            resume_session_id=resume,
            output_format=output_format,
            stream_partial=stream_partial,
            options=options,
        )
    except ValueError as e:
        _call_with_retry(
            patch_task_fn,
            task_id,
            attempts=3,
            status="failed",
            finished_at=iso_now(),
            result_text=str(e),
            exit_code=-1,
            base=base,
            token=token,
        )
        if on_status:
            on_status("failed", str(e))
        return

    # 交互式工具审批（人在环路）在一次性路径下的覆盖：
    #  - claude 兼容：注入与常驻会话相同的 PreToolUse hook（剥离会绕过 hook 的权限覆盖，
    #    并注入 TELEAGENT_* 上下文让 hook 能回调后端建审批），真正拦截危险工具。
    #  - cursor/codex：无法强制审批，绝不静默以 force 跑——明确告警让用户知情，避免误以为有审批。
    extra_env: dict = {}
    conv_id_for_perm = full.get("conversation_id") or task_detail.get("conversation_id")
    if interactive_permissions_enabled():
        if agent_type == "claude_code":
            args = _strip_permission_overrides(args)
            hook_settings = _ensure_hook_settings()
            # 把 --settings 插在 prompt（最后一个位置参数）之前。
            args = args[:-1] + ["--settings", hook_settings] + args[-1:]
            extra_env = {
                "TELEAGENT_API_BASE": base or "",
                "TELEAGENT_CONVERSATION_ID": str(conv_id_for_perm or ""),
                "TELEAGENT_SESSION_ROOT": cwd or "",
            }
            if token:
                extra_env["TELEAGENT_TOKEN"] = token
        else:
            msg = f"⚠️ 交互式审批暂不支持 {agent_type}（仅 claude 可拦截）：本任务将按其权限模式执行，未做审批拦截。"
            print(f"  [worker] {msg}", file=sys.stderr)
            if on_status:
                on_status("running", msg)

    try:
        _call_with_retry(
            patch_task_fn,
            task_id,
            attempts=5,
            status="running",
            started_at=iso_now(),
            heartbeat_at=iso_now(),
            base=base,
            token=token,
        )
    except Exception as e:
        if on_status:
            on_status("failed", str(e))
        raise
    if on_status:
        on_status("running", task_id)

    events_batch: list = []
    BATCH_SIZE = 20
    heartbeat_stop = threading.Event()

    def _heartbeat_loop():
        while not heartbeat_stop.wait(TASK_HEARTBEAT_INTERVAL_SEC):
            try:
                _call_with_retry(
                    patch_task_fn,
                    task_id,
                    attempts=2,
                    heartbeat_at=iso_now(),
                    base=base,
                    token=token,
                )
            except Exception:
                pass

    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    def _stop_heartbeat():
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1.0)

    try:
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env={**os.environ, **extra_env} if extra_env else None,
        )
    except FileNotFoundError:
        if not Path(cwd).exists():
            message = f"Working directory does not exist: {cwd}"
        else:
            message = f"{agent_type} CLI not found: {args[0]}"
        _call_with_retry(
            patch_task_fn,
            task_id,
            attempts=3,
            status="failed",
            finished_at=iso_now(),
            result_text=message,
            exit_code=-1,
            base=base,
            token=token,
        )
        if on_status:
            on_status("failed", message)
        _stop_heartbeat()
        return
    except Exception as e:
        _call_with_retry(
            patch_task_fn,
            task_id,
            attempts=3,
            status="failed",
            finished_at=iso_now(),
            result_text=str(e),
            exit_code=-1,
            base=base,
            token=token,
        )
        if on_status:
            on_status("failed", str(e))
        _stop_heartbeat()
        return

    _register_oneshot(proc)
    result_text: str = ""

    try:
        try:
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    # 合法 JSON 但非对象（如 [1,2]/42）：_normalize 返回 None → 跳过，不崩任务。
                    normalized = _normalize_stream_event(obj)
                    if normalized is None:
                        continue
                    result_candidate = normalized.pop("_result_text", None)
                    # 不再累积完整事件列表（避免长任务内存无界）；事件经 events_batch 增量上报。
                    if normalized.get("type") == "result":
                        result_text = normalized.get("result", "")
                    elif result_candidate:
                        result_text = result_candidate
                    events_batch.append(normalized)
                    if len(events_batch) >= BATCH_SIZE:
                        try:
                            _call_with_retry(
                                post_task_events_fn,
                                task_id,
                                list(events_batch),
                                base=base,
                                token=token,
                                attempts=3,
                            )
                            events_batch = []
                        except Exception:
                            # Keep the batch in memory so the final full PATCH can still reconcile.
                            pass
                except json.JSONDecodeError:
                    pass
            proc.wait()
        except Exception:
            proc.terminate()
            proc.wait()
    finally:
        _deregister_oneshot(proc)

    if events_batch:
        try:
            _call_with_retry(
                post_task_events_fn,
                task_id,
                list(events_batch),
                base=base,
                token=token,
                attempts=5,
            )
        except Exception:
            pass

    _stop_heartbeat()

    status = "success" if proc.returncode == 0 else "failed"
    # 事件已通过 events 端点增量上报；最终 PATCH 只回报状态/结果，不再附带整段事件。
    final_payload = {
        "status": status,
        "finished_at": iso_now(),
        "result_text": result_text or None,
        "exit_code": proc.returncode,
    }
    try:
        _call_with_retry(
            patch_task_fn,
            task_id,
            attempts=8,
            base=base,
            token=token,
            **final_payload,
        )
    except Exception as exc:
        _store_pending_final_report(task_id, final_payload)
        if on_status:
            on_status("failed", f"result queued for sync: {exc}")
        return
    if status == "success" and not resume:
        conv_id = full.get("conversation_id") or task_detail.get("conversation_id")
        if conv_id:
            maybe_autotitle(str(conv_id), prompt, cwd, base, token=token)
    if on_status:
        on_status(status, result_text[:200] if result_text else "")


def maybe_autotitle(
    conv_id: str,
    prompt: str,
    cwd: str,
    base: str,
    token: Optional[str] = None,
    get_conversation_fn=get_conversation,
    set_conversation_title_fn=set_conversation_title,
    generate_title_fn=generate_title,
) -> None:
    """
    首轮成功后，在后台线程为会话生成简短主题标题（不阻塞执行循环）。
    仅当用户未自定义标题时写入；后端对 auto 标题也做了同样的兜底校验。
    """
    def _worker():
        try:
            conv = get_conversation_fn(conv_id, base=base, token=token)
            if conv.get("title_custom"):
                return
            title = generate_title_fn(prompt, cwd)
            if not title:
                return
            set_conversation_title_fn(conv_id, title, auto=True, base=base, token=token)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


_CONTROL_MAP = {
    "set_permission_mode": lambda v: ("set_permission_mode", {"mode": v}),
    "set_model": lambda v: ("set_model", {"model": v}),
    "interrupt": lambda v: ("interrupt", {}),
}


def apply_pending_controls(
    manager: SessionManager,
    base: str,
    client_id: str,
    token: Optional[str] = None,
    get_pending_controls_fn=get_pending_controls,
    ack_control_fn=ack_control,
) -> None:
    """
    拉取待应用的动态控制指令，写入对应会话常驻进程的 stdin（control_request），并回报结果。
    无常驻进程时标记失败（set_* 已在后端持久化到 options，会在下次常驻进程启动时生效）。
    """
    try:
        controls = _call_with_retry(
            get_pending_controls_fn, client_id=client_id, base=base, token=token, attempts=2,
        )
    except Exception:
        return

    def _ack(cid, status, result):
        try:
            _call_with_retry(
                ack_control_fn, cid, status=status, result=result,
                base=base, token=token, attempts=2,
            )
        except Exception:
            pass

    for c in controls or []:
        cid = str(c.get("id"))
        conv_id = str(c.get("conversation_id") or "")
        mapper = _CONTROL_MAP.get(c.get("action"))
        if mapper is None:
            _ack(cid, "failed", f"unknown action {c.get('action')}")
            continue
        warm = manager.get(conv_id) if conv_id else None
        if warm is None or not warm.is_alive():
            _ack(cid, "failed", "会话未就绪（无常驻进程；已持久化到会话参数，下次启动生效）")
            continue
        subtype, fields = mapper(c.get("value") or "")
        resp = warm.send_control(subtype, fields, timeout=10.0)
        ok = bool(resp) and (resp.get("response") or {}).get("subtype") == "success"
        _ack(cid, "applied" if ok else "failed", "ok" if ok else "no/err control_response")


def _within(child: Path, root: Path) -> bool:
    """child 是否在 root 子树内（含相等）。两者均应已 resolve（符号链接解析后做词法判断）。"""
    try:
        child.relative_to(root)
        return True
    except ValueError:
        return False


def list_directory(req_path: str, include_files: bool = False, root: Optional[str] = None) -> Tuple[str, Optional[str], list]:
    """
    列出 req_path 下的直接子项（懒加载树的一层）。返回 (listed_path, parent_path, entries)。
    - 安全策略（分级）：
      * include_files=True（下载浏览器，会暴露文件名/大小）：**必须**提供非空 root，空 root 一律拒绝，
        并强制只在 root 子树内浏览；
      * include_files=False（选工作目录，只列文件夹、不暴露文件内容）：允许无 root 的全盘文件夹导航
        （这是选目录的固有需求）；若提供了 root 则仍约束在其子树内。
    - req_path 为空/~ → 起点为 root（有 root 时）或用户主目录（无 root 时）；
    - 有受限根时越界抛 ValueError，根处不返回父级；
    - 按「目录在前、再按名」排序，跳过隐藏项与无权限项；
    入参非法或不是目录时抛 ValueError。
    """
    raw = (req_path or "").strip()
    has_root = bool(root and str(root).strip())
    # 暴露文件内容的浏览（下载浏览器）默认拒绝无 root；仅列文件夹则允许无 root 全盘导航。
    if include_files and not has_root:
        raise ValueError("拒绝访问：未提供受限根目录（root），禁止浏览任意路径")

    root_resolved = None
    if has_root:
        try:
            root_resolved = Path(os.path.expanduser(root.strip())).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"无法解析约束根：{root}") from exc

    if not raw or raw == "~":
        base_dir = root_resolved if root_resolved is not None else Path(os.path.expanduser("~"))
    else:
        base_dir = Path(os.path.expanduser(raw))
    try:
        listed = base_dir.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"无法解析路径：{raw}") from exc
    if not listed.is_dir():
        raise ValueError(f"不是目录：{listed}")
    # 有受限根时，强制列出的目录在 root 子树内（解析符号链接后判断，防越界/穿越）。
    if root_resolved is not None and not _within(listed, root_resolved):
        raise ValueError("越界访问被拒绝：超出工作目录范围")

    entries = []
    with os.scandir(listed) as it:
        for e in it:
            name = e.name
            if name.startswith("."):  # 跳过隐藏项，避免噪音
                continue
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue  # 无权限/损坏项跳过
            if is_dir:
                entries.append({"name": name, "path": str(listed / name), "is_dir": True})
            elif include_files:
                try:
                    size = e.stat(follow_symlinks=False).st_size
                except OSError:
                    size = 0
                entries.append({"name": name, "path": str(listed / name), "is_dir": False, "size": size})
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    # 到达文件系统根、或（有约束根时）到达约束根时，不返回父级（约束根处禁止向上跳出）。
    at_root = listed.parent == listed or (root_resolved is not None and listed == root_resolved)
    parent = None if at_root else str(listed.parent)
    return str(listed), parent, entries


def apply_pending_fs_requests(
    base: str,
    client_id: str,
    token: Optional[str] = None,
    get_pending_fs_requests_fn=get_pending_fs_requests,
    ack_fs_request_fn=ack_fs_request,
) -> None:
    """拉取待处理的目录浏览请求，本机列目录后回传结果。网络错误不应中断主循环。"""
    try:
        requests = _call_with_retry(
            get_pending_fs_requests_fn, client_id=client_id, base=base, token=token, attempts=2,
        )
    except Exception:
        return

    for r in requests or []:
        rid = str(r.get("id"))
        try:
            listed_path, parent_path, entries = list_directory(
                r.get("path") or "", include_files=bool(r.get("include_files")),
                root=r.get("root_path") or None,
            )
            status, kwargs = "done", {
                "listed_path": listed_path, "parent_path": parent_path,
                "entries": entries, "error": "",
            }
        except Exception as exc:
            status, kwargs = "failed", {"error": str(exc)[:500]}
        try:
            _call_with_retry(
                ack_fs_request_fn, rid, status=status, base=base, token=token, attempts=2, **kwargs,
            )
        except Exception:
            pass


def read_file_b64(path: str, max_bytes: int = FILE_TRANSFER_MAX_BYTES, root: Optional[str] = None):
    """读取本机文件，返回 (filename, content_type, content_b64, size)。
    只读普通文件；不存在/是目录/无权限/超过 max_bytes 时抛 ValueError。
    安全策略：**默认拒绝**。必须提供非空 root 作为受限根，且文件解析后须落在该根子树内；
    root 为空/缺失一律拒绝（否则等于允许读取任意文件，如 ~/.ssh/id_rsa）。
    供 broker 上传与 teleagent-send 命令共用。"""
    # 默认拒绝：未提供受限根时不允许读取任意文件。
    if not root or not str(root).strip():
        raise ValueError("拒绝访问：未提供受限根目录（root），禁止读取任意路径")
    p = Path(os.path.expanduser((path or "").strip()))
    try:
        resolved = p.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"无法解析路径：{path}") from exc
    try:
        root_resolved = Path(os.path.expanduser(root.strip())).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"无法解析约束根：{root}") from exc
    if not _within(resolved, root_resolved):
        raise ValueError("越界访问被拒绝：超出工作目录范围")
    if resolved.is_dir():
        raise ValueError(f"是目录而非文件：{resolved}")
    if not resolved.is_file():
        raise ValueError(f"不是普通文件：{resolved}")
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise ValueError(f"无法读取文件信息：{exc}") from exc
    if size > max_bytes:
        raise ValueError(f"文件过大（{size} > {max_bytes} 字节）")
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise ValueError(f"读取失败（可能无权限）：{exc}") from exc
    content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
    return resolved.name, content_type, base64.b64encode(raw).decode("ascii"), len(raw)


def apply_pending_file_transfers(
    base: str,
    client_id: str,
    token: Optional[str] = None,
    get_pending_file_transfers_fn=get_pending_file_transfers,
    upload_file_transfer_fn=upload_file_transfer,
    fail_file_transfer_fn=fail_file_transfer,
    max_bytes: int = FILE_TRANSFER_MAX_BYTES,
) -> None:
    """拉取 Web 发起的待上传文件传输，读本机文件 base64 上传；读不到/过大则回报失败。"""
    try:
        transfers = _call_with_retry(
            get_pending_file_transfers_fn, client_id=client_id, base=base, token=token, attempts=2,
        )
    except Exception:
        return

    for t in transfers or []:
        tid = str(t.get("id"))
        path = t.get("source_path") or ""
        try:
            filename, content_type, content_b64, _size = read_file_b64(
                path, max_bytes, root=t.get("root_path") or None,
            )
        except Exception as exc:
            try:
                _call_with_retry(fail_file_transfer_fn, tid, error=str(exc)[:500],
                                 base=base, token=token, attempts=2)
            except Exception:
                pass
            continue
        try:
            _call_with_retry(
                upload_file_transfer_fn, tid, filename=filename,
                content_b64=content_b64, content_type=content_type,
                base=base, token=token, attempts=2,
            )
        except Exception:
            pass  # 网络失败：留作 pending，下轮重试


def make_permission_gateway(
    conversation_id: str,
    task_id: str,
    base: str,
    token: Optional[str],
    emit_fn: Callable[[dict], None],
    remembered: set,
    *,
    create_fn=create_permission_request,
    get_fn=get_permission_request,
    poll_interval: float = PERMISSION_POLL_INTERVAL_SEC,
    timeout_sec: float = PERMISSION_WAIT_TIMEOUT_SEC,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Callable[[str, str, dict], bool]:
    """
    构造一个交互式审批回调 gateway(request_id, tool_name, tool_input) -> bool(allow)。
    建审批请求 → 把事件即时推进任务流让 Web 渲染卡片 → 轮询 Web 应答 → 返回允许/拒绝。
    超时/出错/无应答一律安全拒绝；勾选「总是允许」则把该工具加入本会话记忆集，后续直接放行。
    """
    def gateway(request_id: str, tool_name: str, tool_input: dict) -> bool:
        if tool_name in remembered:
            return True
        # 先建审批请求拿到 perm_id，再把事件即时推进任务流（直接 POST、不走批量），
        # 让 Web 在常驻进程阻塞等待期间立刻看到审批卡片，并据 perm_id 回写应答。
        try:
            created = create_fn(
                conversation_id, tool_name, tool_input=tool_input,
                request_id=request_id, task_id=task_id, base=base, token=token,
            )
        except Exception:
            return False
        perm_id = created.get("id")
        if not perm_id:
            return False
        emit_fn({
            "type": "permission_request", "id": perm_id, "request_id": request_id,
            "tool_name": tool_name, "tool_input": tool_input,
        })
        attempts = max(1, int(timeout_sec / max(0.1, poll_interval)))
        status, remember = "pending", False
        for _ in range(attempts):
            try:
                cur = get_fn(perm_id, base=base, token=token)
            except Exception:
                cur = {}
            status = cur.get("status", "pending")
            if status != "pending":
                remember = bool(cur.get("remember"))
                break
            sleep_fn(poll_interval)
        allow = status == "allowed"
        if allow and remember:
            remembered.add(tool_name)
        emit_fn({
            "type": "permission_resolved", "id": perm_id, "request_id": request_id,
            "tool_name": tool_name, "decision": "allow" if allow else "deny",
        })
        return allow

    return gateway


def sync_warm_sessions(
    manager: SessionManager,
    base: str,
    client_id: str,
    token: Optional[str] = None,
    get_active_conversations_fn=get_active_conversations,
) -> None:
    """
    拉取「正被 Web 打开」的会话，为其预热常驻进程；并回收已关闭+空闲超 TTL 的进程。
    任何网络错误都不应中断主轮询循环。
    """
    try:
        active = _call_with_retry(
            get_active_conversations_fn,
            client_id=client_id,
            base=base,
            token=token,
            attempts=2,
        )
    except Exception:
        active = None
    if active is None:
        # 拉取失败：保守起见仅回收已死进程，不改动保活集合。
        manager.reconcile(manager.active_conv_ids())
        return

    active_ids = []
    for conv in active:
        conv_id = str(conv.get("id") or "")
        agent_type = conv.get("agent_type") or ""
        if not conv_id or not is_warm_capable(agent_type):
            continue
        active_ids.append(conv_id)
        # 注入 broker 上下文到 Agent 子进程，供 teleagent-send 命令把文件发回 Web。
        # TELEAGENT_SESSION_ROOT = 会话工作目录，作为 teleagent-send 的受限根（禁止外发任意文件）。
        extra_env = {
            "TELEAGENT_API_BASE": base or "",
            "TELEAGENT_CLIENT_ID": str(client_id or ""),
            "TELEAGENT_CONVERSATION_ID": conv_id,
            "TELEAGENT_SESSION_ROOT": conv.get("cwd") or "",
        }
        if token:
            extra_env["TELEAGENT_TOKEN"] = token
        manager.ensure(
            conv_id,
            cwd=conv.get("cwd") or "/",
            agent_type=agent_type,
            # 权限以会话为粒度（常驻进程启动时一次性决定）。默认 False（更安全），
            # 由用户在新建会话时显式开启「自动执行」。
            force=bool(conv.get("force", False)),
            resume_session_id=conv.get("session_id") or None,
            options=conv.get("options") or {},
            extra_env=extra_env,
        )
    manager.reconcile(active_ids)


def run_warm_turn_and_report(
    session,
    task_id: str,
    task_detail: dict,
    base: str,
    token: Optional[str] = None,
    on_status: Optional[Callable[[str, str], None]] = None,
    get_task_fn=get_task,
    patch_task_fn=patch_task,
    post_task_events_fn=post_task_events,
) -> None:
    """
    将一条任务作为「一轮」喂入已存在的常驻会话，并把流式事件/最终结果回写 Django。
    与 run_agent_and_report 的关键区别：进程在轮次结束后**保持存活**，任务完成由 result 事件判定。
    """
    full = _call_with_retry(get_task_fn, task_id, base=base, token=token, attempts=3)
    prompt = full.get("prompt", "") or task_detail.get("prompt", "")
    timeout_sec = int(full.get("timeout_sec", 1800) or task_detail.get("timeout_sec", 1800))

    try:
        _call_with_retry(
            patch_task_fn, task_id, attempts=5,
            status="running", started_at=iso_now(), heartbeat_at=iso_now(),
            base=base, token=token,
        )
    except Exception as e:
        if on_status:
            on_status("failed", str(e))
        raise
    if on_status:
        on_status("running", task_id)

    events_batch: list = []
    BATCH_SIZE = 20
    heartbeat_stop = threading.Event()

    def _heartbeat_loop():
        while not heartbeat_stop.wait(TASK_HEARTBEAT_INTERVAL_SEC):
            try:
                _call_with_retry(
                    patch_task_fn, task_id, attempts=2,
                    heartbeat_at=iso_now(), base=base, token=token,
                )
            except Exception:
                pass

    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    def _on_event(_tid, raw_obj):
        normalized = _normalize_stream_event(raw_obj)
        if normalized is None:
            return
        normalized.pop("_result_text", None)
        # 不再累积完整事件列表（避免长任务内存无界）；事件经 events_batch 增量上报。
        events_batch.append(normalized)
        if len(events_batch) >= BATCH_SIZE:
            try:
                _call_with_retry(
                    post_task_events_fn, task_id, list(events_batch),
                    base=base, token=token, attempts=3,
                )
                events_batch.clear()
            except Exception:
                pass

    # 交互式审批（默认关）：拦截常驻进程的工具审批请求，转给 Web 用户允许/拒绝。
    on_permission = None
    if interactive_permissions_enabled():
        conv_id = full.get("conversation_id") or task_detail.get("conversation_id")
        if conv_id:
            if not hasattr(session, "remembered_tools"):
                session.remembered_tools = set()  # 「总是允许」记忆集，随常驻会话存活

            def _emit_perm(ev):
                try:
                    _call_with_retry(
                        post_task_events_fn, task_id, [ev], base=base, token=token, attempts=2,
                    )
                except Exception:
                    pass

            on_permission = make_permission_gateway(
                str(conv_id), task_id, base, token, _emit_perm, session.remembered_tools,
            )

    try:
        turn = session.run_turn(
            task_id, prompt, _on_event, timeout_sec=timeout_sec, on_permission=on_permission,
        )
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1.0)

    if events_batch:
        try:
            _call_with_retry(
                post_task_events_fn, task_id, list(events_batch),
                base=base, token=token, attempts=5,
            )
        except Exception:
            pass

    # 超时/进程异常：关闭常驻进程，下一轮 ensure 会带 resume 重建。
    if turn.status in ("timeout", "failed") and not session.is_alive():
        session.close()
    elif turn.status == "timeout":
        session.close()

    status = turn.status if turn.status in ("success", "failed", "timeout") else "failed"
    # 事件已通过 events 端点增量上报；最终 PATCH 只回报状态/结果，不再附带整段事件。
    final_payload = {
        "status": status,
        "finished_at": iso_now(),
        "result_text": turn.result_text or (turn.error or None),
        "exit_code": 0 if status == "success" else 1,
    }
    try:
        _call_with_retry(
            patch_task_fn, task_id, attempts=8, base=base, token=token, **final_payload,
        )
    except Exception as exc:
        _store_pending_final_report(task_id, final_payload)
        if on_status:
            on_status("failed", f"result queued for sync: {exc}")
        return
    if status == "success" and not full.get("resume_session_id"):
        conv_id = full.get("conversation_id") or task_detail.get("conversation_id")
        if conv_id:
            maybe_autotitle(str(conv_id), prompt, full.get("cwd") or session.cwd, base, token=token)
    if on_status:
        on_status(status, (turn.result_text or "")[:200])
