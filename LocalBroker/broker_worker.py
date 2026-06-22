"""
broker_worker.py – 与 Django 对接的 Broker 执行逻辑：拉取 queued 任务、本机跑 Agent、回写。

供 Qt 云端模式（CloudBrokerWindow）使用。
"""

import json
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
    )
except ModuleNotFoundError:
    from broker_api import (
        get_queued_tasks, get_task, patch_task, post_task_events, get_active_conversations,
        get_conversation, set_conversation_title, get_pending_controls, ack_control,
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
    from LocalBroker.session_manager import SessionManager, is_warm_capable
except ModuleNotFoundError:
    from session_manager import SessionManager, is_warm_capable


PENDING_REPORTS_DIR = Path(__file__).resolve().parent / ".broker_pending"
TASK_HEARTBEAT_INTERVAL_SEC = 15.0


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

    result_text: str = ""

    try:
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if not line:
                continue
            try:
                obj = json.loads(line)
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
        manager.ensure(
            conv_id,
            cwd=conv.get("cwd") or "/",
            agent_type=agent_type,
            # 权限以会话为粒度（常驻进程启动时一次性决定）。默认 False（更安全），
            # 由用户在新建会话时显式开启「自动执行」。
            force=bool(conv.get("force", False)),
            resume_session_id=conv.get("session_id") or None,
            options=conv.get("options") or {},
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

    try:
        turn = session.run_turn(task_id, prompt, _on_event, timeout_sec=timeout_sec)
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
