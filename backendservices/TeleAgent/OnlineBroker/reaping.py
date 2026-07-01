"""
过期（stale）任务回收：把租约心跳超时的 RUNNING 任务标记为 FAILED（终态）。

为什么是 FAILED 而非「重新入队」：把还在跑的任务重置回 QUEUED 会导致
  - 设备只是短暂卡顿/网络抖动时被误判 → 同一任务被第二台/再次拉取重复执行；
  - 旧的 TaskEvent 被删除 → 已产出的输出丢失。
因此这里采取最安全的行为：超过（更长的）租约仍无心跳即判定设备失联，标记任务 FAILED 并
保留已有事件行，绝不删除事件、绝不重排队重跑。

两条调用路径共用同一套逻辑：
  - 视图内联：client 轮询 /queued-tasks/ 时按 client 限频扫一遍（best-effort，低延迟）；
  - 管理命令 reap_stale_tasks：后台周期性全量扫描（authoritative，不依赖 client 是否在轮询）。
"""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Task

# RUNNING 任务超过该时长没有心跳即视为设备失联（标记 FAILED）。任务 PATCH 与事件上报都会刷新
# 心跳，所以只有真正掉线的任务才会被回收。取较安全的 10 分钟，避免长耗时步骤被误杀。
TASK_LEASE_TIMEOUT_SECONDS = 600

# 失联回收时写入的错误说明。
STALE_TASK_ERROR_NOTE = "设备无响应/租约超时"


def stale_running_tasks_qs(user=None, client_id=None):
    """构造「心跳超时的 RUNNING 任务」查询集；user/client_id 为 None 时不按其过滤（全量）。"""
    stale_before = timezone.now() - timezone.timedelta(seconds=TASK_LEASE_TIMEOUT_SECONDS)
    qs = Task.objects.filter(status=Task.Status.RUNNING)
    if user is not None:
        qs = qs.filter(conversation__owner=user)
    if client_id is not None:
        qs = qs.filter(Q(assigned_client_id__isnull=True) | Q(assigned_client_id=client_id))
    return qs.filter(
        Q(heartbeat_at__lt=stale_before)
        | Q(heartbeat_at__isnull=True, started_at__lt=stale_before)
    )


def requeue_stale_tasks(user=None, client_id=None):
    """
    把租约心跳超时的 RUNNING 任务标记为 FAILED（终态），保留其事件行，返回处理数量。

    （历史函数名保留以减少改动面；行为已从「重排队重跑」改为「失败终态」，详见模块顶部说明。）
    """
    with transaction.atomic():
        stale = list(stale_running_tasks_qs(user, client_id).select_for_update())
        if not stale:
            return 0
        now = timezone.now()
        for task in stale:
            task.status = Task.Status.FAILED
            task.heartbeat_at = None
            task.finished_at = now
            # 保留已产出的 result_text 与事件行；仅在末尾追加失联说明，便于排障。
            note = f"[{STALE_TASK_ERROR_NOTE}]"
            task.result_text = (
                f"{task.result_text}\n{note}" if task.result_text else note
            )
        Task.objects.bulk_update(
            stale,
            ["status", "heartbeat_at", "finished_at", "result_text"],
        )
        # 不删除 TaskEvent：避免已产出的输出丢失（与重排队重跑的旧行为相反）。
    return len(stale)
