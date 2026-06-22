from rest_framework import serializers
from .models import (
    AGENT_TYPE_CHOICES, AgentClient, BrokerClientCredential, Conversation, Task, Message,
    ConversationControl,
)


class ConversationControlSerializer(serializers.ModelSerializer):
    conversation_id = serializers.SerializerMethodField()

    class Meta:
        model = ConversationControl
        fields = ["id", "conversation_id", "action", "value", "status", "result", "created_at"]
        read_only_fields = fields

    def get_conversation_id(self, obj):
        return str(obj.conversation_id)


class AgentClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentClient
        fields = ["id", "name", "hostname", "supported_agents", "last_seen", "created_at"]
        read_only_fields = ["id", "created_at"]


class BrokerClientCredentialSerializer(serializers.ModelSerializer):
    # 不再暴露 secret：明文仅在创建接口的响应里一次性返回（见 views.post）。
    class Meta:
        model = BrokerClientCredential
        fields = ["id", "name", "created_at"]
        read_only_fields = fields


class BrokerClientCredentialCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128)


class AgentClientRegisterSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128)
    hostname = serializers.CharField(max_length=256, required=False, allow_blank=True, default="")
    supported_agents = serializers.ListField(
        child=serializers.ChoiceField(choices=[choice[0] for choice in AGENT_TYPE_CHOICES]),
        required=False,
        default=list,
    )


class TaskListSerializer(serializers.ModelSerializer):
    assigned_client_id = serializers.SerializerMethodField()
    conversation_id = serializers.SerializerMethodField()
    heartbeat_at = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id", "conversation_id", "prompt", "status", "cwd", "result_text",
            "started_at", "heartbeat_at", "finished_at", "created_at", "exit_code", "assigned_client_id", "agent_type",
        ]
        read_only_fields = fields

    def get_assigned_client_id(self, obj):
        return str(obj.assigned_client_id) if obj.assigned_client_id else None

    def get_conversation_id(self, obj):
        return str(obj.conversation_id) if obj.conversation_id else None

    def get_heartbeat_at(self, obj):
        v = getattr(obj, "heartbeat_at", None)
        return v.isoformat() if hasattr(v, "isoformat") else v


class TaskDetailSerializer(serializers.ModelSerializer):
    conversation_id = serializers.SerializerMethodField()
    assigned_client_id = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id", "conversation_id", "assigned_client_id", "prompt", "status", "cwd",
            "agent_type",
            "force", "options", "stream_partial", "output_format", "timeout_sec",
            "resume_session_id",
            "result_text", "exit_code", "started_at", "heartbeat_at", "finished_at",
            "events", "created_at",
        ]
        read_only_fields = fields

    # events 从 TaskEvent 行读取，仅返回最近若干条（调试用；前端实时事件走 SSE）。
    events = serializers.SerializerMethodField()

    def get_events(self, obj):
        rows = list(
            obj.event_rows.order_by("-seq").values_list("payload", flat=True)[:200]
        )
        return rows[::-1]

    def get_assigned_client_id(self, obj):
        return str(obj.assigned_client_id) if obj.assigned_client_id else None

    # 显式序列化，避免 UUID/datetime 在 JSON 时出错
    started_at = serializers.SerializerMethodField()
    heartbeat_at = serializers.SerializerMethodField()
    finished_at = serializers.SerializerMethodField()
    created_at = serializers.SerializerMethodField()

    def get_conversation_id(self, obj):
        v = getattr(obj, "conversation_id", None)
        return str(v) if v is not None else None

    def _dt(self, obj, attr):
        v = getattr(obj, attr, None)
        if v is None:
            return None
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return v

    def get_started_at(self, obj):
        return self._dt(obj, "started_at")

    def get_finished_at(self, obj):
        return self._dt(obj, "finished_at")

    def get_created_at(self, obj):
        return self._dt(obj, "created_at")

    def get_heartbeat_at(self, obj):
        return self._dt(obj, "heartbeat_at")


class MessageSerializer(serializers.ModelSerializer):
    task = TaskListSerializer(read_only=True)

    class Meta:
        model = Message
        fields = ["id", "prompt", "task", "created_at"]
        read_only_fields = fields


class ConversationListSerializer(serializers.ModelSerializer):
    message_count = serializers.SerializerMethodField()
    assigned_client_id = serializers.SerializerMethodField()
    last_result = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ["id", "cwd", "title", "title_custom", "session_id", "assigned_client_id", "agent_type", "force", "options", "created_at", "updated_at", "message_count", "last_result"]
        read_only_fields = fields

    def get_message_count(self, obj):
        # 优先用视图注解（msg_count）避免 N+1；未注解时回退到 count()。
        v = getattr(obj, "msg_count", None)
        return v if v is not None else obj.messages.count()

    def get_assigned_client_id(self, obj):
        return str(obj.assigned_client_id) if obj.assigned_client_id else None

    def get_last_result(self, obj):
        """最近一个有结果的任务的结果文本预览（供列表「一眼看出处理了什么」）。"""
        if hasattr(obj, "last_result_anno"):
            text = obj.last_result_anno or ""
        else:
            text = (
                obj.tasks.exclude(result_text__isnull=True)
                .exclude(result_text="")
                .order_by("-created_at")
                .values_list("result_text", flat=True)
                .first()
            ) or ""
        return " ".join(text.split())[:140]


class ConversationDetailSerializer(serializers.ModelSerializer):
    messages = MessageSerializer(many=True, read_only=True)
    assigned_client_id = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ["id", "cwd", "title", "title_custom", "session_id", "assigned_client_id", "agent_type", "force", "options", "messages", "created_at", "updated_at"]
        read_only_fields = fields

    def get_assigned_client_id(self, obj):
        return str(obj.assigned_client_id) if obj.assigned_client_id else None


class ConversationActiveSerializer(serializers.ModelSerializer):
    """供 LocalBroker 拉取「正被 Web 打开」的会话，用于预热常驻 Agent 进程。"""
    assigned_client_id = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id", "cwd", "agent_type", "session_id", "assigned_client_id",
            "viewer_heartbeat_at", "updated_at", "force", "options",
        ]
        read_only_fields = fields

    def get_assigned_client_id(self, obj):
        return str(obj.assigned_client_id) if obj.assigned_client_id else None


class ConversationCreateSerializer(serializers.ModelSerializer):
    client_id = serializers.UUIDField(required=False, allow_null=True)
    agent_type = serializers.ChoiceField(choices=[choice[0] for choice in AGENT_TYPE_CHOICES])
    force = serializers.BooleanField(required=False, default=False)
    options = serializers.JSONField(required=False, default=dict)

    class Meta:
        model = Conversation
        fields = ["cwd", "title", "client_id", "agent_type", "force", "options"]

    def create(self, validated_data):
        client_id = validated_data.pop("client_id", None)
        owner = validated_data.pop("owner", None)
        agent_type = validated_data.get("agent_type")
        assigned_client = None
        if client_id:
            try:
                assigned_client = AgentClient.objects.get(pk=client_id, owner=owner)
            except AgentClient.DoesNotExist:
                pass
        if assigned_client and agent_type not in (assigned_client.supported_agents or []):
            raise serializers.ValidationError(
                {"agent_type": "Selected client does not support this agent."}
            )
        # 用户在新建时填了标题 → 视为自定义，自动摘要不得覆盖。
        if (validated_data.get("title") or "").strip():
            validated_data["title_custom"] = True
        return Conversation.objects.create(
            owner=owner, assigned_client=assigned_client, **validated_data
        )


class SendMessageSerializer(serializers.Serializer):
    prompt = serializers.CharField()
    force = serializers.BooleanField(default=False)
    output_format = serializers.ChoiceField(
        choices=["stream-json", "json", "text"], default="stream-json"
    )
    stream_partial = serializers.BooleanField(default=True)
    timeout_sec = serializers.IntegerField(default=1800, min_value=5, max_value=86400)


class TaskUpdateSerializer(serializers.Serializer):
    """LocalBroker 上报任务状态/结果（PATCH tasks/<id>/）"""
    status = serializers.ChoiceField(
        choices=[c[0] for c in Task.Status.choices], required=False
    )
    started_at = serializers.DateTimeField(required=False, allow_null=True)
    heartbeat_at = serializers.DateTimeField(required=False, allow_null=True)
    finished_at = serializers.DateTimeField(required=False, allow_null=True)
    result_text = serializers.CharField(required=False, allow_null=True)
    exit_code = serializers.IntegerField(required=False, allow_null=True)
    # events 仅作旧版/兜底用途（首选走 POST /tasks/<id>/events/ 增量上报）。raw_lines 已弃用。
    events = serializers.ListField(child=serializers.DictField(), required=False)


class TaskEventsAppendSerializer(serializers.Serializer):
    """LocalBroker 追加流式事件（POST tasks/<id>/events/）"""
    events = serializers.ListField(child=serializers.DictField())
