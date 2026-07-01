import json
import logging
import secrets
import threading
import time
from django.http import StreamingHttpResponse, JsonResponse
from django.contrib.auth.hashers import make_password
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.authtoken.models import Token

from django.utils import timezone
from django.db import transaction, connection
from django.db.models import Q, Count, OuterRef, Subquery, Max
import base64
import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.http import FileResponse

from .models import (
    AgentClient, BrokerClientCredential, Conversation, Task, Message, TaskEvent,
    ConversationControl, FsBrowseRequest, PermissionRequest, FileTransfer,
)
from .reaping import TASK_LEASE_TIMEOUT_SECONDS, requeue_stale_tasks
from .serializers import (
    AgentClientSerializer,
    BrokerClientCredentialCreateSerializer,
    BrokerClientCredentialSerializer,
    AgentClientRegisterSerializer,
    FsBrowseRequestSerializer,
    FsBrowseCreateSerializer,
    FsBrowseAckSerializer,
    PermissionRequestSerializer,
    PermissionRequestCreateSerializer,
    PermissionAnswerSerializer,
    FileTransferSerializer,
    FileTransferRequestSerializer,
    FileTransferUploadSerializer,
    ConversationListSerializer,
    ConversationDetailSerializer,
    ConversationCreateSerializer,
    ConversationActiveSerializer,
    ConversationControlSerializer,
    TaskListSerializer,
    TaskDetailSerializer,
    MessageSerializer,
    SendMessageSerializer,
    TaskUpdateSerializer,
    TaskEventsAppendSerializer,
)

logger = logging.getLogger(__name__)
# TASK_LEASE_TIMEOUT_SECONDS 与回收逻辑统一在 reaping 模块（视图内联与管理命令共用）。
# 过期任务回收扫描的最小间隔：每次 client 轮询都扫一遍太重，按 client 限频。
STALE_SCAN_MIN_INTERVAL_SECONDS = 30.0
_STALE_SCAN_LOCK = threading.Lock()
_LAST_STALE_SCAN = {}  # client_id -> 上次扫描的 monotonic 时间戳
# Web 端"打开会话"心跳新鲜窗口；超过则视为已关闭，LocalBroker 不再为其保活常驻进程。
VIEWER_HEARTBEAT_FRESH_SECONDS = 120


# ---------- REST API (DRF) ----------
# Broker API 供前端 / LocalBroker 调用，豁免 CSRF（无 session 表单场景）

def _clients_for_user(request):
    return AgentClient.objects.filter(owner=request.user)


def _conversations_for_user(request):
    return Conversation.objects.filter(owner=request.user)


@method_decorator(csrf_exempt, name="dispatch")
class BrokerAPIView(APIView):
    permission_classes = [IsAuthenticated]


class BrokerClientCredentialListCreateView(BrokerAPIView):
    def get(self, request):
        qs = BrokerClientCredential.objects.filter(user=request.user).order_by("-created_at")
        return Response(BrokerClientCredentialSerializer(qs, many=True).data)

    def post(self, request):
        serializer = BrokerClientCredentialCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        secret_key = secrets.token_urlsafe(24)
        credential = BrokerClientCredential.objects.create(
            name=serializer.validated_data["name"],
            user=request.user,
            secret_hash=make_password(secret_key),
            # 不再持久化明文 secret（仅存 hash）；明文只在本次创建响应里返回一次。
        )
        data = BrokerClientCredentialSerializer(credential).data
        data["secret_key"] = secret_key  # 一次性返回，之后无法再查看
        return Response(data, status=status.HTTP_201_CREATED)


# ---------- Agent 客户端注册（多台 PC 时 Web 端选择与哪台对话）----------

class AgentClientListView(BrokerAPIView):
    def get(self, request):
        qs = _clients_for_user(request)
        serializer = AgentClientSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        """注册本机为 Agent 客户端，按 hostname 去重；归属当前用户。"""
        serializer = AgentClientRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        supported_agents = data.get("supported_agents", [])
        hostname = (data.get("hostname") or "").strip()
        if hostname:
            client = (
                AgentClient.objects.filter(owner=request.user, hostname=hostname)
                .order_by("-last_seen")
                .first()
            )
            if client:
                client.name = data["name"]
                client.supported_agents = supported_agents
                client.last_seen = timezone.now()
                client.save(update_fields=["name", "supported_agents", "last_seen"])
                return Response(AgentClientSerializer(client).data, status=status.HTTP_200_OK)
            client = AgentClient.objects.create(
                owner=request.user,
                name=data["name"],
                hostname=hostname,
                supported_agents=supported_agents,
            )
            return Response(
                AgentClientSerializer(client).data,
                status=status.HTTP_201_CREATED,
            )
        client = AgentClient.objects.create(
            owner=request.user,
            name=data["name"],
            hostname="",
            supported_agents=supported_agents,
        )
        return Response(
            AgentClientSerializer(client).data,
            status=status.HTTP_201_CREATED,
        )


class AgentClientDetailView(BrokerAPIView):
    def patch(self, request, client_id):
        """心跳：更新 last_seen，并可刷新本机支持的 agent 类型。"""
        try:
            client = AgentClient.objects.get(pk=client_id, owner=request.user)
        except AgentClient.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = AgentClientRegisterSerializer(data=request.data or {}, partial=True)
        serializer.is_valid(raise_exception=True)
        client.last_seen = timezone.now()
        update_fields = ["last_seen"]
        if "supported_agents" in serializer.validated_data:
            client.supported_agents = serializer.validated_data["supported_agents"]
            update_fields.append("supported_agents")
        client.save(update_fields=update_fields)
        return Response(AgentClientSerializer(client).data)


class QueuedTasksView(BrokerAPIView):
    """拉取可执行的任务：client_id 必填，仅返回分配给该客户端或未分配且会话属于当前用户的任务。"""

    def get(self, request):
        client_id = request.query_params.get("client_id")
        if not client_id:
            return Response(
                {"detail": "client_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            AgentClient.objects.get(pk=client_id, owner=request.user)
        except AgentClient.DoesNotExist:
            return Response({"detail": "Client not found."}, status=status.HTTP_404_NOT_FOUND)
        _maybe_requeue_stale_tasks(request.user, client_id)
        # 原子认领：多台 client 同时轮询时，未分配的 QUEUED 任务可能被同一任务发给多端 → 重复执行。
        # 在事务内用 select_for_update(skip_locked) 锁定匹配行，并立刻把未分配的盖上 assigned_client，
        # 这样第二个轮询者不会再拿到它们。状态保持 QUEUED（broker PATCH 时再翻为 running）。
        with transaction.atomic():
            base_qs = (
                Task.objects.filter(status=Task.Status.QUEUED)
                .filter(
                    Q(assigned_client_id__isnull=True) | Q(assigned_client_id=client_id)
                )
                .filter(conversation__owner=request.user)
                .order_by("created_at")
            )
            # SQLite 不支持行锁（select_for_update 会被忽略）；Postgres 下用 skip_locked 跳过被他人锁住的行。
            if connection.features.has_select_for_update:
                base_qs = base_qs.select_for_update(
                    skip_locked=connection.features.has_select_for_update_skip_locked
                )
            tasks = list(base_qs)
            unassigned_ids = [t.id for t in tasks if t.assigned_client_id is None]
            if unassigned_ids:
                Task.objects.filter(id__in=unassigned_ids).update(assigned_client_id=client_id)
                for t in tasks:
                    if t.assigned_client_id is None:
                        t.assigned_client_id = client_id
            serializer = TaskListSerializer(tasks, many=True)
            return Response(serializer.data)


class ConversationListCreateView(BrokerAPIView):
    def get(self, request):
        # 用注解一次性算出 message_count 与 last_result，消除按会话逐行的 N+1 查询。
        last_result_sq = (
            Task.objects.filter(conversation=OuterRef("pk"))
            .exclude(result_text__isnull=True)
            .exclude(result_text="")
            .order_by("-created_at")
            .values("result_text")[:1]
        )
        qs = _conversations_for_user(request).annotate(
            msg_count=Count("messages", distinct=True),
            last_result_anno=Subquery(last_result_sq),
        )
        # 有界返回：默认最多 100 条（按 -updated_at），支持 ?limit/?offset 翻页，保持数组结构。
        try:
            limit = int(request.query_params.get("limit", 100))
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 200))
        try:
            offset = max(0, int(request.query_params.get("offset", 0)))
        except (TypeError, ValueError):
            offset = 0
        qs = qs[offset:offset + limit]
        return Response(ConversationListSerializer(qs, many=True).data)

    def post(self, request):
        serializer = ConversationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conv = serializer.save(owner=request.user)
        return Response(
            ConversationDetailSerializer(conv).data,
            status=status.HTTP_201_CREATED,
        )


class ConversationDetailView(BrokerAPIView):
    def get(self, request, conv_id):
        try:
            # 预取 messages 及其 task，避免序列化 N 条消息时按消息逐条查 task 的 N+1。
            conv = (
                Conversation.objects.prefetch_related("messages__task")
                .get(pk=conv_id, owner=request.user)
            )
        except Conversation.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(ConversationDetailSerializer(conv).data)

    def patch(self, request, conv_id):
        """更新会话标题。auto=true 为自动摘要（不覆盖用户自定义标题）；否则视为用户重命名。"""
        try:
            conv = Conversation.objects.get(pk=conv_id, owner=request.user)
        except Conversation.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        title = request.data.get("title")
        if title is None:
            return Response({"detail": "title is required."}, status=status.HTTP_400_BAD_REQUEST)
        title = str(title).strip()[:256]
        is_auto = str(request.data.get("auto", "")).lower() in ("1", "true", "yes")
        if is_auto:
            # 自动摘要：仅在用户未自定义标题时写入，避免竞态覆盖用户重命名。
            if conv.title_custom:
                return Response(ConversationDetailSerializer(conv).data)
            if not title:
                return Response(ConversationDetailSerializer(conv).data)
            conv.title = title
            conv.save(update_fields=["title"])
        else:
            conv.title = title
            conv.title_custom = True
            conv.save(update_fields=["title", "title_custom"])
        return Response(ConversationDetailSerializer(conv).data)

    def delete(self, request, conv_id):
        try:
            conv = Conversation.objects.get(pk=conv_id, owner=request.user)
        except Conversation.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        conv.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConversationOpenView(BrokerAPIView):
    """Web 端进入会话 / 周期心跳：标记会话为打开，刷新 viewer 心跳。"""

    def post(self, request, conv_id):
        try:
            conv = Conversation.objects.get(pk=conv_id, owner=request.user)
        except Conversation.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        conv.is_open = True
        conv.viewer_heartbeat_at = timezone.now()
        conv.save(update_fields=["is_open", "viewer_heartbeat_at"])
        return Response({"id": str(conv.id), "is_open": True})


class ConversationCloseView(BrokerAPIView):
    """Web 端离开会话：标记会话为关闭（LocalBroker 将在空闲 TTL 后回收常驻进程）。"""

    def post(self, request, conv_id):
        try:
            conv = Conversation.objects.get(pk=conv_id, owner=request.user)
        except Conversation.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        conv.is_open = False
        conv.save(update_fields=["is_open"])
        return Response({"id": str(conv.id), "is_open": False})


class ActiveConversationsView(BrokerAPIView):
    """LocalBroker 拉取「分配给该 client 且正被 Web 打开（心跳新鲜）」的会话，用于预热/回收常驻进程。"""

    def get(self, request):
        client_id = request.query_params.get("client_id")
        if not client_id:
            return Response({"detail": "client_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            AgentClient.objects.get(pk=client_id, owner=request.user)
        except AgentClient.DoesNotExist:
            return Response({"detail": "Client not found."}, status=status.HTTP_404_NOT_FOUND)
        fresh_before = timezone.now() - timezone.timedelta(seconds=VIEWER_HEARTBEAT_FRESH_SECONDS)
        qs = (
            Conversation.objects.filter(
                owner=request.user, is_open=True, assigned_client_id=client_id
            )
            .filter(viewer_heartbeat_at__gte=fresh_before)
            .order_by("-viewer_heartbeat_at")
        )
        return Response(ConversationActiveSerializer(qs, many=True).data)


class ConversationControlView(BrokerAPIView):
    """Web 端对会话发起动态控制（切权限模式/模型/中断）：入队 + 持久化 set_* 到 options。"""

    ALLOWED = {"set_permission_mode", "set_model", "interrupt"}

    def post(self, request, conv_id):
        try:
            conv = Conversation.objects.get(pk=conv_id, owner=request.user)
        except Conversation.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        action = request.data.get("action")
        value = str(request.data.get("value") or "")
        if action not in self.ALLOWED:
            return Response({"detail": "unsupported action."}, status=status.HTTP_400_BAD_REQUEST)
        # set_* 同步持久化到 options，保证对未来重建的常驻进程也生效。
        if action == "set_permission_mode" and value:
            conv.options = {**(conv.options or {}), "permission_mode": value}
            conv.force = value == "bypassPermissions"
            conv.save(update_fields=["options", "force"])
        elif action == "set_model":
            opts = {**(conv.options or {})}
            if value:
                opts["model"] = value
            else:
                opts.pop("model", None)
            conv.options = opts
            conv.save(update_fields=["options"])
        control = ConversationControl.objects.create(conversation=conv, action=action, value=value)
        return Response(ConversationControlSerializer(control).data, status=status.HTTP_201_CREATED)


class PendingControlsView(BrokerAPIView):
    """LocalBroker 拉取待应用的控制指令（分配给该 client 的会话）。"""

    def get(self, request):
        client_id = request.query_params.get("client_id")
        if not client_id:
            return Response({"detail": "client_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            AgentClient.objects.get(pk=client_id, owner=request.user)
        except AgentClient.DoesNotExist:
            return Response({"detail": "Client not found."}, status=status.HTTP_404_NOT_FOUND)
        qs = (
            ConversationControl.objects.filter(
                status=ConversationControl.Status.PENDING,
                conversation__owner=request.user,
                conversation__assigned_client_id=client_id,
            )
            .order_by("created_at")[:50]
        )
        return Response(ConversationControlSerializer(qs, many=True).data)


class ControlDetailView(BrokerAPIView):
    """LocalBroker 回报控制指令的应用结果。"""

    def patch(self, request, control_id):
        try:
            control = ConversationControl.objects.get(
                pk=control_id, conversation__owner=request.user
            )
        except ConversationControl.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        new_status = request.data.get("status")
        if new_status in (ConversationControl.Status.APPLIED, ConversationControl.Status.FAILED):
            control.status = new_status
            control.result = str(request.data.get("result") or "")[:2000]
            control.applied_at = timezone.now()
            control.save(update_fields=["status", "result", "applied_at"])
        return Response(ConversationControlSerializer(control).data)


# ---------- 远程目录浏览（新建会话时选 Agent PC 上的工作目录）----------

class FsBrowseCreateView(BrokerAPIView):
    """Web 发起目录浏览请求：指定 client 与要列出的 path（空=该机用户主目录）。"""

    def post(self, request):
        serializer = FsBrowseCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        client_id = serializer.validated_data["client_id"]
        try:
            client = AgentClient.objects.get(pk=client_id, owner=request.user)
        except AgentClient.DoesNotExist:
            return Response({"detail": "Client not found."}, status=status.HTTP_404_NOT_FOUND)
        # 指定会话时，约束根从会话 cwd 服务端派生（不信任前端），浏览被限制在 cwd 子树内。
        conv = None
        root_path = ""
        conv_id = serializer.validated_data.get("conversation_id")
        if conv_id:
            conv = Conversation.objects.filter(pk=conv_id, owner=request.user).first()
            if conv is None:
                return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)
            root_path = conv.cwd or ""
        # 安全（纵深防御）：列出文件内容的浏览（include_files=True，下载文件浏览器）必须有可解析的
        # 约束根（来自会话 cwd），否则可遍历该机任意文件树。无根则拒绝（broker 侧亦将空根视为拒绝）。
        # 注：选工作目录的目录选择器（include_files=False）按设计跨全机浏览，不在此约束内。
        include_files = bool(serializer.validated_data.get("include_files"))
        if include_files and not root_path:
            return Response(
                {"detail": "conversation_id with a working directory is required for file browsing."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        req = FsBrowseRequest.objects.create(
            client=client,
            conversation=conv,
            path=serializer.validated_data.get("path") or "",
            include_files=include_files,
            root_path=root_path,
        )
        return Response(FsBrowseRequestSerializer(req).data, status=status.HTTP_201_CREATED)


class FsBrowseDetailView(BrokerAPIView):
    """Web 轮询目录浏览结果。"""

    def get(self, request, req_id):
        try:
            req = FsBrowseRequest.objects.get(pk=req_id, client__owner=request.user)
        except FsBrowseRequest.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(FsBrowseRequestSerializer(req).data)


class PendingFsRequestsView(BrokerAPIView):
    """LocalBroker 拉取分配给该 client 的待处理目录浏览请求。"""

    def get(self, request):
        client_id = request.query_params.get("client_id")
        if not client_id:
            return Response({"detail": "client_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            AgentClient.objects.get(pk=client_id, owner=request.user)
        except AgentClient.DoesNotExist:
            return Response({"detail": "Client not found."}, status=status.HTTP_404_NOT_FOUND)
        qs = (
            FsBrowseRequest.objects.filter(
                status=FsBrowseRequest.Status.PENDING,
                client_id=client_id,
                client__owner=request.user,
            )
            .order_by("created_at")[:20]
        )
        return Response(FsBrowseRequestSerializer(qs, many=True).data)


class FsRequestAckView(BrokerAPIView):
    """LocalBroker 回传列目录结果（或失败原因）。"""

    def patch(self, request, req_id):
        try:
            req = FsBrowseRequest.objects.get(pk=req_id, client__owner=request.user)
        except FsBrowseRequest.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = FsBrowseAckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        req.status = data["status"]
        req.listed_path = data.get("listed_path") or ""
        req.parent_path = data.get("parent_path")
        req.entries = data.get("entries") or []
        req.error = data.get("error") or ""
        req.resolved_at = timezone.now()
        req.save(update_fields=["status", "listed_path", "parent_path", "entries", "error", "resolved_at"])
        return Response(FsBrowseRequestSerializer(req).data)


# ---------- 交互式工具审批（人在环路）----------

class PermissionRequestCreateView(BrokerAPIView):
    """LocalBroker 就某个工具调用发起审批请求（pending），等待 Web 用户应答。"""

    def post(self, request):
        serializer = PermissionRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            conv = Conversation.objects.get(pk=data["conversation_id"], owner=request.user)
        except Conversation.DoesNotExist:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)
        task = None
        if data.get("task_id"):
            task = Task.objects.filter(pk=data["task_id"], conversation=conv).first()
        # 「本会话总是允许」的工具：直接判为允许，hook 轮询即得 allowed，不再弹卡片。
        remembered = data["tool_name"] in (conv.always_allow_tools or [])
        req = PermissionRequest.objects.create(
            conversation=conv,
            task=task,
            request_id=data.get("request_id") or "",
            tool_name=data["tool_name"],
            tool_input=data.get("tool_input") or {},
            status=PermissionRequest.Status.ALLOWED if remembered else PermissionRequest.Status.PENDING,
            resolved_at=timezone.now() if remembered else None,
        )
        return Response(PermissionRequestSerializer(req).data, status=status.HTTP_201_CREATED)


class PermissionRequestDetailView(BrokerAPIView):
    """GET：LocalBroker 轮询应答结果；PATCH：Web 用户应答（allow/deny，可勾选记住）。"""

    def get(self, request, perm_id):
        try:
            req = PermissionRequest.objects.get(pk=perm_id, conversation__owner=request.user)
        except PermissionRequest.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(PermissionRequestSerializer(req).data)

    def patch(self, request, perm_id):
        try:
            req = PermissionRequest.objects.get(pk=perm_id, conversation__owner=request.user)
        except PermissionRequest.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = PermissionAnswerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # 仅 pending 可应答；重复应答幂等返回当前状态。
        if req.status == PermissionRequest.Status.PENDING:
            decision = serializer.validated_data["decision"]
            allow = decision == "allow"
            req.status = PermissionRequest.Status.ALLOWED if allow else PermissionRequest.Status.DENIED
            req.remember = bool(serializer.validated_data.get("remember"))
            req.resolved_at = timezone.now()
            req.save(update_fields=["status", "remember", "resolved_at"])
            # 勾选「一直允许」+ 允许 → 持久化到会话，后续同工具自动放行。
            if req.remember and allow:
                conv = req.conversation
                tools = list(conv.always_allow_tools or [])
                if req.tool_name not in tools:
                    tools.append(req.tool_name)
                    conv.always_allow_tools = tools
                    conv.save(update_fields=["always_allow_tools"])
        return Response(PermissionRequestSerializer(req).data)


class PendingPermissionsView(BrokerAPIView):
    """Web 拉取某会话下仍待应答的审批请求（断线重连/刷新后兜底，不丢审批）。"""

    def get(self, request, conv_id):
        try:
            conv = Conversation.objects.get(pk=conv_id, owner=request.user)
        except Conversation.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        qs = (
            PermissionRequest.objects.filter(
                conversation=conv, status=PermissionRequest.Status.PENDING
            )
            .order_by("created_at")[:50]
        )
        return Response(PermissionRequestSerializer(qs, many=True).data)


class ConversationPermissionsView(BrokerAPIView):
    """Web 拉取某会话内近期的全部审批（含已批准/已拒绝），按时间正序渲染进对话流。"""

    def get(self, request, conv_id):
        try:
            conv = Conversation.objects.get(pk=conv_id, owner=request.user)
        except Conversation.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        qs = PermissionRequest.objects.filter(conversation=conv).order_by("created_at")[:100]
        return Response(PermissionRequestSerializer(qs, many=True).data)


# ---------- 远程文件下载（Agent PC → Django 中转 → Web 下载）----------

def _mark_transfer_ready(transfer):
    transfer.status = FileTransfer.Status.READY
    transfer.ready_at = timezone.now()
    transfer.expires_at = transfer.ready_at + timezone.timedelta(
        hours=getattr(settings, "FILE_TRANSFER_TTL_HOURS", 24)
    )


class FileTransferRequestView(BrokerAPIView):
    """发起一次文件传输：Web 文件浏览器点下载（pending，待 broker 上传），或 AI 命令直接建（随后上传）。"""

    def post(self, request):
        serializer = FileTransferRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            client = AgentClient.objects.get(pk=data["client_id"], owner=request.user)
        except AgentClient.DoesNotExist:
            return Response({"detail": "Client not found."}, status=status.HTTP_404_NOT_FOUND)
        conv = None
        if data.get("conversation_id"):
            conv = Conversation.objects.filter(pk=data["conversation_id"], owner=request.user).first()
            if conv is None:
                return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)
        root_path = (conv.cwd or "") if conv else ""
        agent_initiated = bool(data.get("agent_initiated"))
        # 安全（纵深防御）：web 发起的下载由 LocalBroker 读取 PC 上的文件，必须有可解析的约束根
        # （来自会话 cwd），否则可被诱导读取任意路径（如 /etc/passwd）。无根则拒绝。
        # agent 发起（teleagent-send）由 PC 命令自身读文件并直接上传，不经 broker 读盘，故不强制。
        if not agent_initiated and not root_path:
            return Response(
                {"detail": "conversation_id with a working directory is required for file download."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        transfer = FileTransfer.objects.create(
            client=client,
            conversation=conv,
            source_path=data["path"],
            # 指定会话时，下载也约束在会话 cwd 子树内（与浏览一致）。
            root_path=root_path,
            filename=os.path.basename(data["path"].rstrip("/")) or "download",
            agent_initiated=agent_initiated,
        )
        return Response(FileTransferSerializer(transfer).data, status=status.HTTP_201_CREATED)


class PendingFileTransfersView(BrokerAPIView):
    """LocalBroker 拉取该 client 待上传的文件传输（仅 Web 发起的 pending 需要 broker 读盘上传）。"""

    def get(self, request):
        client_id = request.query_params.get("client_id")
        if not client_id:
            return Response({"detail": "client_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            AgentClient.objects.get(pk=client_id, owner=request.user)
        except AgentClient.DoesNotExist:
            return Response({"detail": "Client not found."}, status=status.HTTP_404_NOT_FOUND)
        qs = (
            FileTransfer.objects.filter(
                status=FileTransfer.Status.PENDING, client_id=client_id, client__owner=request.user,
            )
            .order_by("created_at")[:20]
        )
        return Response(FileTransferSerializer(qs, many=True).data)


class FileTransferUploadView(BrokerAPIView):
    """LocalBroker / teleagent-send 上传文件内容（base64）。校验大小上限后落盘并置为 ready。"""

    def post(self, request, transfer_id):
        try:
            transfer = FileTransfer.objects.get(pk=transfer_id, client__owner=request.user)
        except FileTransfer.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = FileTransferUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            raw = base64.b64decode(data["content_b64"], validate=True)
        except Exception:
            transfer.status = FileTransfer.Status.FAILED
            transfer.error = "invalid base64 content"
            transfer.save(update_fields=["status", "error"])
            return Response({"detail": "invalid base64 content."}, status=status.HTTP_400_BAD_REQUEST)
        max_bytes = getattr(settings, "FILE_TRANSFER_MAX_BYTES", 50 * 1024 * 1024)
        if len(raw) > max_bytes:
            transfer.status = FileTransfer.Status.FAILED
            transfer.error = f"file too large ({len(raw)} > {max_bytes} bytes)"
            transfer.save(update_fields=["status", "error"])
            return Response({"detail": transfer.error}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        filename = data["filename"] or transfer.filename or "download"
        transfer.filename = filename
        transfer.content_type = data.get("content_type") or "application/octet-stream"
        transfer.size = len(raw)
        transfer.blob.save(filename, ContentFile(raw), save=False)
        _mark_transfer_ready(transfer)
        transfer.error = ""
        transfer.save(update_fields=["filename", "content_type", "size", "blob", "status", "ready_at", "expires_at", "error"])
        return Response(FileTransferSerializer(transfer).data)


class FileTransferDetailView(BrokerAPIView):
    """GET：Web 轮询状态；PATCH：LocalBroker 回报失败（读不到/过大/无权限）。"""

    def get(self, request, transfer_id):
        try:
            transfer = FileTransfer.objects.get(pk=transfer_id, client__owner=request.user)
        except FileTransfer.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(FileTransferSerializer(transfer).data)

    def patch(self, request, transfer_id):
        try:
            transfer = FileTransfer.objects.get(pk=transfer_id, client__owner=request.user)
        except FileTransfer.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if request.data.get("status") == FileTransfer.Status.FAILED and transfer.status == FileTransfer.Status.PENDING:
            transfer.status = FileTransfer.Status.FAILED
            transfer.error = str(request.data.get("error") or "")[:1000]
            transfer.save(update_fields=["status", "error"])
        return Response(FileTransferSerializer(transfer).data)


class ConversationFilesView(BrokerAPIView):
    """Web 拉取某会话内可下载的文件（AI 主动发送的文件出现在这里，渲染下载卡片）。"""

    def get(self, request, conv_id):
        try:
            conv = Conversation.objects.get(pk=conv_id, owner=request.user)
        except Conversation.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        qs = (
            FileTransfer.objects.filter(conversation=conv, status=FileTransfer.Status.READY)
            .order_by("-created_at")[:100]
        )
        return Response(FileTransferSerializer(qs, many=True).data)


def file_download_view(request, transfer_id):
    """鉴权后流式下载文件内容（手动 token 鉴权，避免 DRF 内容协商干扰二进制响应）。"""
    user = _authenticate_stream_request(request)
    if user is None:
        return JsonResponse({"detail": "Authentication required."}, status=401)
    try:
        transfer = FileTransfer.objects.get(
            pk=transfer_id, client__owner=user, status=FileTransfer.Status.READY,
        )
    except FileTransfer.DoesNotExist:
        return JsonResponse({"detail": "Not found."}, status=404)
    if not transfer.blob:
        return JsonResponse({"detail": "File not available."}, status=404)
    response = FileResponse(
        transfer.blob.open("rb"),
        as_attachment=True,
        filename=transfer.filename or "download",
        content_type=transfer.content_type or "application/octet-stream",
    )
    return response


class SendMessageView(BrokerAPIView):
    """Create a new message and enqueue a task for the conversation."""

    def post(self, request, conv_id):
        try:
            conv = Conversation.objects.get(pk=conv_id, owner=request.user)
        except Conversation.DoesNotExist:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Create task（继承会话绑定的客户端，仅该客户端可拉取执行）
        task = Task.objects.create(
            conversation=conv,
            assigned_client=conv.assigned_client,
            agent_type=conv.agent_type,
            prompt=data["prompt"],
            cwd=conv.cwd,
            force=data["force"] or conv.force,  # 会话级 force 兜底（常驻进程以会话粒度决定权限）
            options=conv.options or {},  # 任务继承会话级 Agent 参数
            stream_partial=data["stream_partial"],
            output_format=data["output_format"],
            timeout_sec=data["timeout_sec"],
            resume_session_id=conv.session_id or None,
        )
        # Create message linked to task
        msg = Message.objects.create(
            conversation=conv,
            prompt=data["prompt"],
            task=task,
        )
        if not conv.title:
            conv.title = (data["prompt"][:60] + "…") if len(data["prompt"]) > 60 else data["prompt"]
            conv.save(update_fields=["title"])

        # In a full implementation, you would enqueue task to Celery/worker here.
        # For now we only persist; optional: run a sync runner or leave status QUEUED
        # and document that a worker must process it.
        return Response(
            {
                "message_id": str(msg.id),
                "task_id": str(task.id),
                "status": task.status,
            },
            status=status.HTTP_201_CREATED,
        )


def _apply_session_id_from_events(task, events):
    """若 events 中有 system:init 且带 session_id，回写会话的 session_id"""
    if not events or not task.conversation_id:
        return
    for ev in events:
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            sid = ev.get("session_id")
            if sid:
                Conversation.objects.filter(pk=task.conversation_id).update(
                    session_id=sid
                )
            break


def _append_task_events(task, events):
    """把一批事件作为 TaskEvent 行追加（O(1)/批，自增 seq）。返回追加数量。"""
    if not events:
        return 0
    with transaction.atomic():
        last = (
            TaskEvent.objects.select_for_update()
            .filter(task=task)
            .aggregate(m=Max("seq"))["m"]
        ) or 0
        rows = [
            TaskEvent(task=task, seq=last + i + 1, payload=ev)
            for i, ev in enumerate(events)
        ]
        TaskEvent.objects.bulk_create(rows)
    return len(rows)


def _task_for_user(request, task_id):
    """Get task if it belongs to current user's conversation."""
    try:
        return Task.objects.get(pk=task_id, conversation__owner=request.user)
    except Task.DoesNotExist:
        return None


def _authenticate_stream_request(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Token "):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            return user
        return None
    token_key = auth.split(" ", 1)[1].strip()
    if token_key:
        try:
            return Token.objects.select_related("user").get(key=token_key).user
        except Token.DoesNotExist:
            return None
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return user
    return None


def _maybe_requeue_stale_tasks(user, client_id):
    """按 client 限频地执行过期回收扫描（首次必扫；之后每 client 最多每 30s 一次）。"""
    now = time.monotonic()
    key = str(client_id)
    with _STALE_SCAN_LOCK:
        last = _LAST_STALE_SCAN.get(key, 0.0)
        if now - last < STALE_SCAN_MIN_INTERVAL_SECONDS:
            return 0
        _LAST_STALE_SCAN[key] = now
    # 内联回收按 client 限频；权威的全量回收由 reap_stale_tasks 管理命令周期性执行。
    return requeue_stale_tasks(user=user, client_id=client_id)


class TaskDetailView(BrokerAPIView):
    def get(self, request, task_id):
        task = _task_for_user(request, task_id)
        if not task:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            return Response(TaskDetailSerializer(task).data)
        except Exception as e:
            logger.exception("TaskDetailSerializer failed for task %s", task_id)
            return Response(
                {"detail": "Serialization error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def patch(self, request, task_id):
        """LocalBroker 上报任务状态/结果"""
        task = _task_for_user(request, task_id)
        if not task:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = TaskUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        update_fields = []
        if "status" in data:
            task.status = data["status"]
            update_fields.append("status")
        if "started_at" in data:
            task.started_at = data["started_at"]
            update_fields.append("started_at")
        if "heartbeat_at" in data:
            task.heartbeat_at = data["heartbeat_at"]
            update_fields.append("heartbeat_at")
        if "finished_at" in data:
            task.finished_at = data["finished_at"]
            update_fields.append("finished_at")
        if "result_text" in data:
            task.result_text = data["result_text"]
            update_fields.append("result_text")
        if "exit_code" in data:
            task.exit_code = data["exit_code"]
            update_fields.append("exit_code")
        if "events" in data and data["events"]:
            # 兜底：仅当该任务还没有任何事件行时，把 PATCH 里带的 events 落为事件行
            # （正常路径走 POST /tasks/<id>/events/ 增量上报；新版 broker 不在 PATCH 里带 events）。
            if not task.event_rows.exists():
                _append_task_events(task, data["events"])
            _apply_session_id_from_events(task, data["events"])
        if "status" in data and data["status"] in (
            Task.Status.QUEUED,
            Task.Status.SUCCESS,
            Task.Status.FAILED,
            Task.Status.CANCELLED,
            Task.Status.TIMEOUT,
        ):
            task.heartbeat_at = None
            if "heartbeat_at" not in update_fields:
                update_fields.append("heartbeat_at")
        elif "status" in data and data["status"] == Task.Status.RUNNING and "heartbeat_at" not in data:
            task.heartbeat_at = timezone.now()
            if "heartbeat_at" not in update_fields:
                update_fields.append("heartbeat_at")
        if update_fields:
            task.save(update_fields=update_fields)
        return Response(TaskDetailSerializer(task).data)


class TaskEventsAppendView(BrokerAPIView):
    """LocalBroker 追加流式事件"""

    def post(self, request, task_id):
        task = _task_for_user(request, task_id)
        if not task:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = TaskEventsAppendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        events = serializer.validated_data["events"]
        if events:
            _append_task_events(task, events)  # INSERT 行，不再读改写整块 JSON
            task.heartbeat_at = timezone.now()
            task.save(update_fields=["heartbeat_at"])
            _apply_session_id_from_events(task, events)
        return Response({"appended": len(events)})


class TaskListByConversationView(BrokerAPIView):
    def get(self, request, conv_id):
        try:
            Conversation.objects.get(pk=conv_id, owner=request.user)
        except Conversation.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        tasks = Task.objects.filter(conversation_id=conv_id).order_by("-created_at")
        return Response(TaskListSerializer(tasks, many=True).data)


# ---------- Health (for Nginx / Docker) ----------

@require_http_methods(["GET"])
def health(request):
    return JsonResponse({"status": "ok"})


# ---------- SSE stream ----------
# 事件存在 TaskEvent 行里；这里按 seq 只取增量行（索引命中），不再每次重读整列。


def _iter_task_events(task_loader, events_fetcher, sleep_fn):
    """
    task_loader() -> 任务对象（用于读 status）。
    events_fetcher(after_seq) -> 有序的 (seq, payload) 列表（seq > after_seq 的增量事件）。
    """
    sent_seq = 0
    while True:
        for seq, payload in events_fetcher(sent_seq):
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            sent_seq = seq
        task = task_loader()
        if task.status not in (Task.Status.QUEUED, Task.Status.RUNNING):
            # 结束前再排空一次，避免错过紧跟状态翻转写入的尾部事件。
            for seq, payload in events_fetcher(sent_seq):
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                sent_seq = seq
            yield f"data: {json.dumps({'type': 'system', 'subtype': 'end', 'status': task.status})}\n\n"
            break
        sleep_fn(0.5)


def task_stream_view(request, task_id):
    """SSE stream endpoint with token auth handled manually to avoid DRF 406 negotiation."""
    user = _authenticate_stream_request(request)
    if user is None:
        return JsonResponse({"detail": "Authentication required."}, status=401)
    try:
        Task.objects.get(pk=task_id, conversation__owner=user)
    except Task.DoesNotExist:
        return JsonResponse({"detail": "Not found."}, status=404)

    def _fetch_events(after_seq):
        return list(
            TaskEvent.objects.filter(task_id=task_id, seq__gt=after_seq)
            .order_by("seq")
            .values_list("seq", "payload")
        )

    def event_stream():
        import time
        yield from _iter_task_events(
            lambda: Task.objects.get(pk=task_id, conversation__owner=user),
            _fetch_events,
            sleep_fn=time.sleep,
        )

    response = StreamingHttpResponse(
        event_stream(),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
