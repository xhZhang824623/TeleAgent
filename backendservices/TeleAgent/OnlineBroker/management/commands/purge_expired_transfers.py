"""
删除过期的文件传输（连同落盘的 blob），避免中转文件无限堆积占满磁盘。

  - 一次性：  python manage.py purge_expired_transfers
  - 常驻轮询：python manage.py purge_expired_transfers --loop --interval 3600
"""
import logging
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from OnlineBroker.models import FileTransfer

logger = logging.getLogger(__name__)


def purge_expired():
    """删除 expires_at 已过的传输；返回删除数量。"""
    expired = FileTransfer.objects.filter(
        expires_at__isnull=False, expires_at__lt=timezone.now()
    )
    count = 0
    for transfer in expired.iterator():
        try:
            if transfer.blob:
                transfer.blob.delete(save=False)  # 删磁盘文件
        except Exception:
            logger.exception("删除 blob 失败 transfer=%s", transfer.id)
        transfer.delete()
        count += 1
    return count


class Command(BaseCommand):
    help = "Delete expired file transfers and their stored blobs."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="常驻循环执行。")
        parser.add_argument("--interval", type=float, default=3600.0, help="--loop 间隔秒（默认 3600）。")

    def handle(self, *args, **options):
        if not options["loop"]:
            self.stdout.write(self.style.SUCCESS(f"purged {purge_expired()} expired transfer(s)"))
            return
        interval = max(60.0, float(options["interval"]))
        self.stdout.write(f"purge_expired_transfers loop started (interval={interval}s)")
        while True:
            try:
                n = purge_expired()
                if n:
                    logger.info("purged %d expired transfer(s)", n)
            except Exception:
                logger.exception("purge_expired_transfers scan failed")
            time.sleep(interval)
