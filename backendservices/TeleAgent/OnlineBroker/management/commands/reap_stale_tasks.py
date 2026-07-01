"""
回收租约心跳超时的 RUNNING 任务：标记为 FAILED（终态），保留其已产出事件，不重排队重跑。

权威回收路径：不依赖某台 client 是否仍在轮询 /queued-tasks/。
  - 一次性：  python manage.py reap_stale_tasks
  - 常驻轮询：python manage.py reap_stale_tasks --loop --interval 30
建议在生产以 --loop 方式常驻，或用 cron 周期性调用一次性模式。
"""
import logging
import time

from django.core.management.base import BaseCommand

from OnlineBroker.reaping import requeue_stale_tasks

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Mark RUNNING tasks whose lease heartbeat has expired as FAILED (events preserved)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--loop", action="store_true",
            help="常驻循环执行（默认只扫一次后退出）。",
        )
        parser.add_argument(
            "--interval", type=float, default=30.0,
            help="--loop 模式下每轮扫描间隔秒数（默认 30）。",
        )

    def handle(self, *args, **options):
        if not options["loop"]:
            count = requeue_stale_tasks()
            self.stdout.write(self.style.SUCCESS(f"failed {count} stale task(s)"))
            return
        interval = max(1.0, float(options["interval"]))
        self.stdout.write(f"reap_stale_tasks loop started (interval={interval}s)")
        while True:
            try:
                count = requeue_stale_tasks()
                if count:
                    logger.info("reap_stale_tasks failed %d stale task(s)", count)
            except Exception:
                logger.exception("reap_stale_tasks scan failed")
            time.sleep(interval)
