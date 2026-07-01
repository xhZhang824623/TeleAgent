import secrets
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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Broker 客户端凭证"
        verbose_name_plural = "Broker 客户端凭证"

    def __str__(self):
        return f"{self.name} ({self.id})"


class BrokerClientToken(models.Model):
    """
    每条 BrokerClientCredential 专属的认证 Token（与 DRF authtoken 同形）。

    LocalBroker 用 client_id + secret 换取此 Token，而非用户的「主」Web Token。
    这样：broker 凭证只代表该凭证本身（最小授权），删除凭证即级联吊销其 Token，
    不会牵连用户的 Web 会话/主 Token。
    """
    key = models.CharField(max_length=40, unique=True, db_index=True, primary_key=True)
    credential = models.OneToOneField(
        BrokerClientCredential,
        on_delete=models.CASCADE,
        related_name="auth_token",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Broker 客户端 Token"
        verbose_name_plural = "Broker 客户端 Token"

    @staticmethod
    def generate_key():
        return secrets.token_hex(20)

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self.generate_key()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.key


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
    # 交互式审批「一直允许」记忆：用户对某工具勾选「本会话总是允许」后追加于此，
    # 后续该工具的审批请求直接判为允许，不再打扰。
    always_allow_tools = models.JSONField(default=list, blank=True, help_text="本会话内总是允许的工具名列表")
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
        indexes = [
            # 轮询拉取队列：按 status 过滤 + created_at 排序。
            models.Index(fields=["status", "created_at"], name="task_status_created_idx"),
            # 回收扫描：按 status=running 过滤 + heartbeat_at 判超时。
            models.Index(fields=["status", "heartbeat_at"], name="task_status_heartbeat_idx"),
        ]
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


class FsBrowseRequest(models.Model):
    """
    远程目录浏览的请求/响应（经轮询中转）。

    新建会话时要选「Agent 那台 PC 上的工作目录」，但那台 PC（LocalBroker）在 NAT 后只能
    出站轮询。于是复用与 ConversationControl 相同的模式：
      Web 建请求（client + path）→ LocalBroker 轮询 /fs/pending/ 拉取 → 本机 os.scandir
      列目录 → PATCH 回传 entries → Web 轮询 /fs/browse/<id>/ 取结果并渲染子节点。
    只读、只列目录（不含文件内容），按 client.owner 归属当前用户校验。
    """
    class Status(models.TextChoices):
        PENDING = "pending", "待处理"
        DONE = "done", "已完成"
        FAILED = "failed", "失败"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(
        AgentClient, on_delete=models.CASCADE, related_name="fs_requests",
        help_text="要浏览哪台 PC 的目录",
    )
    path = models.CharField(max_length=4096, blank=True, help_text="请求列出的目录；空表示起点（root 或主目录）")
    # 选工作目录时只列目录；下载文件浏览器置 True，连文件一起列（带大小）。
    include_files = models.BooleanField(default=False)
    # 约束根：非空时 LocalBroker 只允许在此目录子树内浏览（越界拒绝，根处不返回父级）。
    # 由服务端从会话 cwd 派生（不信任前端），用于下载浏览器的安全合规约束。
    root_path = models.CharField(max_length=4096, blank=True)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="fs_requests", null=True, blank=True,
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    # LocalBroker 实际列出的绝对目录与其父目录（用于树的「向上」与定位）。
    listed_path = models.CharField(max_length=4096, blank=True)
    parent_path = models.CharField(max_length=4096, blank=True, null=True)
    entries = models.JSONField(default=list, blank=True, help_text="子项列表：[{name, path, is_dir}]")
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["client", "status"])]
        verbose_name = "目录浏览请求"
        verbose_name_plural = "目录浏览请求"

    def __str__(self):
        return f"{self.client_id} {self.path or '~'} ({self.status})"


class PermissionRequest(models.Model):
    """
    交互式工具审批的请求/响应（人在环路）。

    Claude Code 常驻进程在 stream-json 模式下会就某个工具调用发来 control_request(can_use_tool)。
    LocalBroker 拦截后建一条本记录（pending）并把它作为事件推进任务流，Web 渲染「允许/拒绝」卡片，
    用户应答 → LocalBroker 轮询拿到 decision → 回写 control_response 给常驻进程，Agent 继续或跳过。
    与 ConversationControl 对称，只是方向相反：broker 建请求、web 应答、broker 取结果。
    """
    class Status(models.TextChoices):
        PENDING = "pending", "待应答"
        ALLOWED = "allowed", "已允许"
        DENIED = "denied", "已拒绝"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="permission_requests"
    )
    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name="permission_requests", null=True, blank=True
    )
    # 常驻进程 control_request 的 request_id，用于 LocalBroker 回写时对齐。
    request_id = models.CharField(max_length=128, blank=True)
    tool_name = models.CharField(max_length=128)
    tool_input = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    # 用户是否勾选「本会话内总是允许该工具」（由 LocalBroker 用于后续自动放行）。
    remember = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["conversation", "status"])]
        verbose_name = "工具审批请求"
        verbose_name_plural = "工具审批请求"

    def __str__(self):
        return f"{self.conversation_id} {self.tool_name} ({self.status})"


def _file_transfer_upload_to(instance, filename):
    return f"transfers/{instance.id}/{filename}"


class FileTransfer(models.Model):
    """
    远程文件下载：把 Agent 那台 PC 上的文件经 Django 中转给 Web 下载。

    PC（LocalBroker）在 NAT 后只能出站，无法被直连读文件。所以：
      - Web 发起（文件浏览器点下载）：建一条 pending 记录 → LocalBroker 轮询拉取 →
        读本机文件 base64 上传 → 状态 ready → Web 凭鉴权下载。
      - AI 发起（teleagent-send 命令）：PC 上的命令直接建记录并上传（一步到位，ready）。
    按 client.owner 归属当前用户校验；有大小上限与过期清理。
    """
    class Status(models.TextChoices):
        PENDING = "pending", "待上传"
        READY = "ready", "可下载"
        FAILED = "failed", "失败"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(
        AgentClient, on_delete=models.CASCADE, related_name="file_transfers"
    )
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="file_transfers", null=True, blank=True
    )
    source_path = models.CharField(max_length=4096, help_text="PC 上的源文件绝对路径")
    # 约束根：非空时只允许读取该目录子树内的文件（由会话 cwd 派生，安全合规）。
    root_path = models.CharField(max_length=4096, blank=True)
    filename = models.CharField(max_length=512, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    content_type = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    # AI 发起的为 True（已直接上传）；Web 发起的为 False（需 LocalBroker 轮询上传）。
    agent_initiated = models.BooleanField(default=False)
    blob = models.FileField(upload_to=_file_transfer_upload_to, null=True, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["client", "status"])]
        verbose_name = "文件传输"
        verbose_name_plural = "文件传输"

    def __str__(self):
        return f"{self.filename or self.source_path} ({self.status})"


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
