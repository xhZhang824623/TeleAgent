"""
Run the Online Broker worker: process QUEUED tasks by invoking the agent CLI (when available)
and update Task status/events in DB. Run in a separate container/process alongside Django.
"""
import json
import logging
import os
import subprocess
import threading
import time
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from OnlineBroker.models import Task, Conversation

logger = logging.getLogger(__name__)

RUN_INTERVAL_SEC = int(os.environ.get("BROKER_POLL_INTERVAL", "2"))
AGENT_TYPES = {
    "codex": {
        "command": "codex",
    },
    "claude_code": {
        "command": "claude",
    },
    "cursor_agent": {
        "command": "agent",
    },
}


def build_agent_command(task: Task, agent_type: str):
    if agent_type not in AGENT_TYPES:
        raise ValueError(f"Unsupported agent_type: {agent_type}")
    args = [AGENT_TYPES[agent_type]["command"], "-p", "--trust"]
    if task.force:
        args.append("--force")
    if task.resume_session_id:
        args.extend(["--resume", task.resume_session_id])
    args.extend(["--output-format", task.output_format])
    if task.output_format == "stream-json" and task.stream_partial:
        args.append("--stream-partial-output")
    args.append(task.prompt)
    return args


class Command(BaseCommand):
    help = "Process OnlineBroker task queue: run agent for QUEUED tasks and update DB."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process one task and exit (for testing).",
        )

    def handle(self, *args, **options):
        once = options.get("once", False)
        self.stdout.write("Broker worker started. Poll every %ss." % RUN_INTERVAL_SEC)
        while True:
            try:
                self._process_one_queued()
            except Exception as e:
                logger.exception("Broker worker iteration error: %s", e)
            if once:
                break
            time.sleep(RUN_INTERVAL_SEC)

    def _process_one_queued(self):
        with transaction.atomic():
            task = Task.objects.select_for_update(skip_locked=True).filter(
                status=Task.Status.QUEUED
            ).order_by("created_at").first()
            if not task:
                return

            task.status = Task.Status.RUNNING
            task.started_at = timezone.now()
            task.save(update_fields=["status", "started_at"])

        # Run agent outside the lock
        self._run_agent_for_task(task)

    def _run_agent_for_task(self, task: Task):
        agent_type = task.agent_type or "cursor_agent"
        try:
            args = build_agent_command(task, agent_type)
        except ValueError as e:
            task.status = Task.Status.FAILED
            task.finished_at = timezone.now()
            task.result_text = str(e)
            task.save(update_fields=["status", "finished_at", "result_text"])
            return

        try:
            proc = subprocess.Popen(
                args,
                cwd=task.cwd or "/",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError:
            task.status = Task.Status.FAILED
            task.finished_at = timezone.now()
            task.result_text = "Agent CLI not found: %s" % AGENT_TYPES[agent_type]["command"]
            task.save(update_fields=["status", "finished_at", "result_text"])
            logger.warning("Agent CLI not found: %s. Install the selected CLI.", AGENT_TYPES[agent_type]["command"])
            return
        except Exception as e:
            task.status = Task.Status.FAILED
            task.finished_at = timezone.now()
            task.result_text = str(e)
            task.save(update_fields=["status", "finished_at", "result_text"])
            return

        events = []
        raw_lines = []
        result_text = None
        session_id = None

        try:
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                raw_lines.append(line)
                try:
                    obj = json.loads(line)
                    events.append(obj)
                    if obj.get("type") == "system" and obj.get("subtype") == "init":
                        session_id = obj.get("session_id")
                        if session_id and task.conversation_id:
                            Conversation.objects.filter(pk=task.conversation_id).update(
                                session_id=session_id
                            )
                    if obj.get("type") == "result":
                        result_text = obj.get("result", "")
                except json.JSONDecodeError:
                    pass
            proc.wait()
        except Exception as e:
            logger.exception("Error reading agent output: %s", e)
            proc.terminate()
            proc.wait()

        task.finished_at = timezone.now()
        task.exit_code = proc.returncode
        task.events = events
        task.raw_lines = raw_lines
        if result_text is not None:
            task.result_text = result_text
        if proc.returncode == 0:
            task.status = Task.Status.SUCCESS
        else:
            task.status = Task.Status.FAILED
        task.save(update_fields=["status", "finished_at", "exit_code", "events", "raw_lines", "result_text"])
        logger.info("Task %s finished with status %s", task.id, task.status)
