import uuid
from django.conf import settings
from django.db import models


AGENT_TYPE_CHOICES = [
    ("codex", "Codex"),
    ("claude_code", "Claude Code"),
    ("cursor_agent", "Cursor Agent"),
]


class BrokerClientCredential(models.Model):
    """
    管理平台为 PC（LocalBroker）签发的登录凭证：ID + Secret Key。
    在 Admin 中创建后，将 ID 与 Secret 告知 PC 使用者，PC 端用此凭证换取 Token 登录。
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, help_text="备注名，如「Neal 的笔记本」")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="broker_client_credentials",
        help_text="该凭证登录后代表的用户（会话与客户端归属此用户）",
    )
    secret_hash = models.CharField(max_length=128, help_text="Secret Key 的哈希，不存明文")
    secret_value = models.CharField(max_length=255, blank=True, help_text="用于前端二次查看的明文 Secret Key")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Broker 客户端凭证"
        verbose_name_plural = "Broker 客户端凭证"

    def __str__(self):
        return f"{self.name} ({self.id})"


class AgentClient(models.Model):
    """注册的本机 Agent 客户端（按 hostname 去重，同一设备只保留一个）。"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="broker_clients",
        help_text="所属用户，未登录注册的客户端为 null（兼容旧数据）",
    )
    name = models.CharField(max_length=128, help_text="显示名，如「Neal 的笔记本」")
    hostname = models.CharField(max_length=256, blank=True)
    supported_agents = models.JSONField(default=list, blank=True, help_text="本机支持的 Agent CLI 类型列表")
    last_seen = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-last_seen', '-created_at']
        verbose_name = "Agent 客户端"
        verbose_name_plural = "Agent 客户端"

    def __str__(self):
        return f"{self.name} ({self.id})"


class Conversation(models.Model):
    """Multi-turn chat session tied to a working directory (like LocalBroker ConversationRecord)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="broker_conversations",
        help_text="所属用户；未登录创建的为 null（兼容旧数据）",
    )
    cwd = models.CharField(max_length=2048, help_text="Working directory for agent tasks")
    title = models.CharField(max_length=256, blank=True)
    # True 表示用户显式设定/重命名过标题，自动摘要不得覆盖；False 时允许 AI/启发式自动命名。
    title_custom = models.BooleanField(default=False, help_text="标题是否由用户显式设定（自动摘要不覆盖）")
    session_id = models.CharField(max_length=128, blank=True, null=True)  # from agent system:init
    agent_type = models.CharField(max_length=32, choices=AGENT_TYPE_CHOICES, blank=True)
    # Warm-session presence: Web 端打开此会话时置位，LocalBroker 据此预热常驻 Agent 进程。
    is_open = models.BooleanField(default=False, help_text="Web 端是否正打开此会话（用于预热常驻 Agent 进程）")
    viewer_heartbeat_at = models.DateTimeField(null=True, blank=True, help_text="Web 端最近一次打开心跳时间")
    # 会话级权限：开启后 Agent 可自动执行命令/改文件（跳过逐步确认）。默认关闭（更安全）。
    # 常驻进程在启动时一次性决定权限，故 force 以会话为粒度；本会话所有任务继承之。
    force = models.BooleanField(default=False, help_text="是否允许 Agent 自动执行（跳过确认）；常驻进程启动时生效")
    # 声明式 Agent 参数（如 permission_mode/model/effort）。后端只做透传与存储，
    # 具体如何映射成 CLI flag 由 LocalBroker 的 schema 决定；前端按 schema 动态渲染控件。
    options = models.JSONField(default=dict, blank=True, help_text="会话级 Agent 参数（permission_mode/model/effort 等）")
    assigned_client = models.ForeignKey(
        AgentClient, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="conversations", help_text="指定由哪台 PC 的 Agent 执行"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name = "会话"
        verbose_name_plural = "会话"

    def __str__(self):
        return f"{self.title or self.cwd} ({self.id})"


class Task(models.Model):
    """Single agent invocation (like LocalBroker TaskRecord)."""
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        TIMEOUT = "timeout", "Timeout"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="tasks", null=True, blank=True
    )
    assigned_client = models.ForeignKey(
        AgentClient, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tasks", help_text="仅该客户端可拉取并执行此任务"
    )
    agent_type = models.CharField(max_length=32, choices=AGENT_TYPE_CHOICES, blank=True)
    prompt = models.TextField()
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.QUEUED)
    cwd = models.CharField(max_length=2048)
    force = models.BooleanField(default=False)
    stream_partial = models.BooleanField(default=True)
    output_format = models.CharField(max_length=32, default="stream-json")
    timeout_sec = models.PositiveIntegerField(default=1800)
    resume_session_id = models.CharField(max_length=128, blank=True, null=True)
    options = models.JSONField(default=dict, blank=True, help_text="任务级 Agent 参数（建任务时从会话继承）")
    # Result
    result_text = models.TextField(blank=True, null=True)
    exit_code = models.IntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # Stream events stored as JSON list (for polling / replay)
    events = models.JSONField(default=list, blank=True)
    raw_lines = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "任务"
        verbose_name_plural = "任务"

    def __str__(self):
        return f"{self.prompt[:50]}... ({self.status})"


class TaskEvent(models.Model):
    """
    单条流式事件（每个 Agent 输出事件一行）。

    取代原来把所有事件塞进 Task.events 这个会无界增长的 JSON 大字段：
      - 追加 = 一次 INSERT（O(1)），不再读改写整块 JSON；
      - SSE/轮询 = 按 (task, seq) 索引只取增量行，不再每次重读整列；
      - 行级存储，单个任务事件再多也不会让 Task 行膨胀。
    """
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="event_rows")
    seq = models.PositiveIntegerField(help_text="任务内自增序号（从 1 开始），用于增量拉取与去重")
    payload = models.JSONField(help_text="单条归一化事件")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["seq"]
        constraints = [
            models.UniqueConstraint(fields=["task", "seq"], name="uniq_task_event_seq"),
        ]
        indexes = [models.Index(fields=["task", "seq"])]
        verbose_name = "任务事件"
        verbose_name_plural = "任务事件"

    def __str__(self):
        return f"{self.task_id} #{self.seq}"


class ConversationControl(models.Model):
    """
    会话的动态控制指令队列（运行中改 permission_mode / model / interrupt）。

    Web 入队 → LocalBroker 轮询拉取 → 写 control_request 到该会话常驻进程的 stdin →
    回报结果。set_* 类指令同时会持久化进 Conversation.options，确保对未来重建的常驻进程也生效。
    """
    class Action(models.TextChoices):
        SET_PERMISSION_MODE = "set_permission_mode", "切换权限模式"
        SET_MODEL = "set_model", "切换模型"
        INTERRUPT = "interrupt", "中断当前执行"

    class Status(models.TextChoices):
        PENDING = "pending", "待应用"
        APPLIED = "applied", "已应用"
        FAILED = "failed", "失败"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="controls"
    )
    action = models.CharField(max_length=32, choices=Action.choices)
    value = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    result = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["conversation", "status"])]
        verbose_name = "会话控制指令"
        verbose_name_plural = "会话控制指令"

    def __str__(self):
        return f"{self.conversation_id} {self.action}={self.value} ({self.status})"


class Message(models.Model):
    """One user turn in a conversation (maps to one Task)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    prompt = models.TextField()
    task = models.OneToOneField(
        Task, on_delete=models.CASCADE, related_name="message", null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = "消息"
        verbose_name_plural = "消息"

    def __str__(self):
        return f"{self.prompt[:50]}..."
