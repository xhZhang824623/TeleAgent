"""
broker_core.py – Pure-Python agent subprocess manager.

Two-layer design
────────────────
Task layer      : TaskSpec / TaskRecord – one agent subprocess invocation.
Conversation    : ConversationRecord / MessageRecord – groups messages into a
                  multi-turn chat session tied to a working directory.
                  Successive messages automatically use --resume <session_id>.

No Qt dependency.  Event callback may be called from a background thread.
"""

import subprocess
import threading
import queue
import time
import json
import uuid
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Callable, Tuple
from enum import Enum


# ─────────────────────────────────────────────────────────── enums / structs

class TaskStatus(str, Enum):
    QUEUED    = "queued"
    RUNNING   = "running"
    SUCCESS   = "success"
    FAILED    = "failed"
    CANCELLED = "cancelled"
    TIMEOUT   = "timeout"


@dataclass
class TaskSpec:
    """Everything needed to launch one agent subprocess invocation."""
    prompt: str
    cwd: str
    force: bool = False
    stream_partial: bool = True
    output_format: str = "stream-json"
    timeout_sec: int = 1800
    resume_session_id: Optional[str] = None
    task_id: str = field(default_factory=lambda: f"t_{uuid.uuid4().hex[:8]}")


@dataclass
class TaskRecord:
    """Live + historic state of a single agent invocation."""
    spec: TaskSpec
    status: TaskStatus = TaskStatus.QUEUED
    events: List[Dict[str, Any]] = field(default_factory=list)
    raw_lines: List[str] = field(default_factory=list)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result_text: Optional[str] = None
    session_id: Optional[str] = None   # from agent system:init event
    exit_code: Optional[int] = None

    @property
    def duration_sec(self) -> Optional[float]:
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        return None


@dataclass
class MessageRecord:
    """One user turn within a Conversation (maps 1-to-1 to a TaskRecord)."""
    msg_id: str
    conv_id: str
    prompt: str
    task_id: str           # look up full data via BrokerCore.get_task(task_id)


@dataclass
class ConversationRecord:
    """
    A multi-turn chat session tied to one working directory.

    session_id is None until the first agent response comes back with a
    system:init event.  All subsequent messages use --resume session_id.
    """
    conv_id: str
    cwd: str
    title: str = ""
    session_id: Optional[str] = None
    messages: List[MessageRecord] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


@dataclass
class BrokerEvent:
    """
    Emitted for every notable occurrence.  Callback may be on any thread.

    kind values
    ───────────
    queued        – task enqueued               data = TaskRecord
    started       – subprocess launched         data = TaskRecord
    stream_event  – one parsed JSON object      data = dict
    raw_line      – non-JSON stdout line        data = str
    finished      – subprocess ended            data = TaskRecord
    proc_error    – failed to start process     data = str
    queue_changed – pending queue depth changed data = int
    """
    task_id: str
    kind: str
    data: Any = None
    conv_id: Optional[str] = None   # set automatically when task belongs to a conv
    msg_id: Optional[str] = None


EventCallback = Callable[[BrokerEvent], None]


# ─────────────────────────────────────────────────────────── BrokerCore

class BrokerCore:
    """
    Manages a FIFO task queue and runs agent subprocesses one at a time.
    Thread-safe.  The event callback may be called from a background thread.
    """

    def __init__(self, on_event: EventCallback):
        self._on_event = on_event

        # ── task layer ───────────────────────────────────────────────
        self._history: Dict[str, TaskRecord] = {}
        self._history_order: List[str] = []
        self._task_queue: queue.Queue[Optional[TaskSpec]] = queue.Queue()

        self._lock = threading.Lock()
        self._current_proc: Optional[subprocess.Popen] = None
        self._current_record: Optional[TaskRecord] = None
        self._cancel_requested = False
        self._timeout_timer: Optional[threading.Timer] = None

        # ── conversation layer ───────────────────────────────────────
        self._conversations: Dict[str, ConversationRecord] = {}
        self._conv_order: List[str] = []
        # task_id → (conv_id, msg_id)
        self._task_to_msg: Dict[str, Tuple[str, str]] = {}

        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    # ═══════════════════════════════════════ conversation public API

    def create_conversation(self, cwd: str) -> str:
        """Create a new conversation for the given directory.  Returns conv_id."""
        conv_id = f"c_{uuid.uuid4().hex[:8]}"
        record = ConversationRecord(conv_id=conv_id, cwd=cwd)
        with self._lock:
            self._conversations[conv_id] = record
            self._conv_order.append(conv_id)
        return conv_id

    def send_message(self, conv_id: str, prompt: str,
                     force: bool = False,
                     output_format: str = "stream-json",
                     stream_partial: bool = True,
                     timeout_sec: int = 1800) -> str:
        """
        Enqueue a new user message in the conversation.
        Automatically adds --resume <session_id> if the conversation already has one.
        Returns the underlying task_id.
        """
        with self._lock:
            conv = self._conversations.get(conv_id)
            if not conv:
                raise ValueError(f"Unknown conversation: {conv_id}")
            session_id = conv.session_id   # None for the very first message

        msg_id = f"m_{uuid.uuid4().hex[:8]}"
        spec = TaskSpec(
            prompt=prompt,
            cwd=conv.cwd,
            force=force,
            stream_partial=stream_partial,
            output_format=output_format,
            timeout_sec=timeout_sec,
            resume_session_id=session_id,
        )
        msg = MessageRecord(
            msg_id=msg_id,
            conv_id=conv_id,
            prompt=prompt,
            task_id=spec.task_id,
        )
        with self._lock:
            conv.messages.append(msg)
            if not conv.title:
                conv.title = prompt[:60]
            self._task_to_msg[spec.task_id] = (conv_id, msg_id)

        self.submit_task(spec)
        return spec.task_id

    def get_conversations(self) -> List[ConversationRecord]:
        with self._lock:
            return [self._conversations[cid]
                    for cid in self._conv_order
                    if cid in self._conversations]

    def get_conversation(self, conv_id: str) -> Optional[ConversationRecord]:
        with self._lock:
            return self._conversations.get(conv_id)

    # ═══════════════════════════════════════ task public API

    def submit_task(self, spec: TaskSpec) -> str:
        record = TaskRecord(spec=spec)
        with self._lock:
            self._history[spec.task_id] = record
            self._history_order.append(spec.task_id)
        self._task_queue.put(spec)
        self._emit(BrokerEvent(task_id=spec.task_id, kind="queued", data=record))
        self._emit(BrokerEvent(task_id=spec.task_id, kind="queue_changed",
                               data=self._task_queue.qsize()))
        return spec.task_id

    def cancel_current(self):
        with self._lock:
            self._cancel_requested = True
            proc = self._current_proc
            timer = self._timeout_timer
        if timer:
            timer.cancel()
        if proc and proc.poll() is None:
            proc.terminate()
            threading.Timer(3.0, self._force_kill, args=[proc]).start()

    def queue_size(self) -> int:
        return self._task_queue.qsize()

    def is_busy(self) -> bool:
        with self._lock:
            return self._current_proc is not None

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        with self._lock:
            return self._history.get(task_id)

    def shutdown(self):
        self.cancel_current()
        self._task_queue.put(None)   # poison pill

    # ═══════════════════════════════════════ internals

    def _emit(self, event: BrokerEvent):
        with self._lock:
            pair = self._task_to_msg.get(event.task_id)
        if pair:
            event.conv_id, event.msg_id = pair
        try:
            self._on_event(event)
        except Exception:
            pass

    def _force_kill(self, proc: subprocess.Popen):
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass

    def _worker_loop(self):
        while True:
            spec = self._task_queue.get()
            if spec is None:
                break
            self._run_task(spec)

    def _run_task(self, spec: TaskSpec):
        with self._lock:
            record = self._history.get(spec.task_id)
            if not record:
                return
            self._cancel_requested = False

        record.status = TaskStatus.RUNNING
        record.started_at = time.time()
        self._emit(BrokerEvent(task_id=spec.task_id, kind="started", data=record))

        args = ["agent", "-p", "--trust"]   # broker always trusts the user-selected dir
        if spec.force:
            args.append("--force")
        if spec.resume_session_id:
            args += ["--resume", spec.resume_session_id]
        args += ["--output-format", spec.output_format]
        if spec.output_format == "stream-json" and spec.stream_partial:
            args.append("--stream-partial-output")
        args.append(spec.prompt)

        try:
            proc = subprocess.Popen(
                args,
                cwd=spec.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception as exc:
            record.status = TaskStatus.FAILED
            record.finished_at = time.time()
            self._emit(BrokerEvent(task_id=spec.task_id, kind="proc_error", data=str(exc)))
            self._emit(BrokerEvent(task_id=spec.task_id, kind="queue_changed",
                                   data=self._task_queue.qsize()))
            return

        with self._lock:
            self._current_proc = proc
            self._current_record = record

        timed_out = threading.Event()

        def _on_timeout():
            timed_out.set()
            record.status = TaskStatus.TIMEOUT
            proc.terminate()
            threading.Timer(3.0, self._force_kill, args=[proc]).start()

        timer = threading.Timer(spec.timeout_sec, _on_timeout)
        with self._lock:
            self._timeout_timer = timer
        timer.start()

        try:
            for raw_line in proc.stdout:
                line = raw_line.rstrip("\r\n")
                if not line:
                    continue
                record.raw_lines.append(line)
                try:
                    obj = json.loads(line)
                except Exception:
                    self._emit(BrokerEvent(task_id=spec.task_id, kind="raw_line", data=line))
                    continue

                record.events.append(obj)
                evt_type = obj.get("type", "")

                if evt_type == "system" and obj.get("subtype") == "init":
                    sid = obj.get("session_id")
                    record.session_id = sid
                    # Propagate session_id to conversation (first message wins)
                    with self._lock:
                        pair = self._task_to_msg.get(spec.task_id)
                        if pair and sid:
                            conv = self._conversations.get(pair[0])
                            if conv and not conv.session_id:
                                conv.session_id = sid

                if evt_type == "result":
                    record.result_text = obj.get("result", "")

                self._emit(BrokerEvent(task_id=spec.task_id, kind="stream_event", data=obj))
        except Exception:
            pass

        proc.wait()
        timer.cancel()

        with self._lock:
            cancelled = self._cancel_requested
            self._current_proc = None
            self._current_record = None
            self._timeout_timer = None

        record.exit_code = proc.returncode
        record.finished_at = time.time()

        if not timed_out.is_set():
            if cancelled:
                record.status = TaskStatus.CANCELLED
            elif proc.returncode == 0:
                record.status = TaskStatus.SUCCESS
            else:
                record.status = TaskStatus.FAILED

        self._emit(BrokerEvent(task_id=spec.task_id, kind="finished", data=record))
        self._emit(BrokerEvent(task_id=spec.task_id, kind="queue_changed",
                               data=self._task_queue.qsize()))
