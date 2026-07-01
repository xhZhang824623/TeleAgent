"""Qt UI for Local Broker: 接入云端（CloudBrokerWindow + broker_worker）。"""

import sys
import os
import time
import socket
import logging
import threading
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal, QUrl, QThread, QSettings
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QDialog,
    QMessageBox, QDialogButtonBox, QGroupBox, QCheckBox,
)
from PyQt5.QtGui import QDesktopServices

try:
    from LocalBroker.agent_runtime import discover_supported_agents
except ModuleNotFoundError:
    from agent_runtime import discover_supported_agents
try:
    from LocalBroker.cloud_session import CloudSessionManager
except ModuleNotFoundError:
    from cloud_session import CloudSessionManager

MAX_RECENT_CWDS = 10

# 轮询节流：空闲时指数退避，拉到任务/有常驻会话时复位为最小间隔，降低空转开销。
POLL_MIN_INTERVAL = float(os.environ.get("BROKER_POLL_INTERVAL", "2.0") or 2.0)
POLL_MAX_INTERVAL = float(os.environ.get("BROKER_POLL_MAX_INTERVAL", "15.0") or 15.0)
POLL_ERROR_MAX_INTERVAL = float(os.environ.get("BROKER_POLL_ERROR_MAX", "30.0") or 30.0)
POLL_BACKOFF_FACTOR = 1.5


# ──────────────────────────────────────────────────────────── logging

log = logging.getLogger("teleagent.broker")


def _setup_logging() -> None:
    """配置 Broker 的结构化日志（幂等）。级别由 BROKER_LOG_LEVEL 控制，默认 INFO。

    同时输出到：
      1) stderr —— 前台可见，作为服务时由 systemd 收进 journald；
      2) 滚动日志文件 —— 默认 ~/.local/state/teleagent/broker.log。
         可用 BROKER_LOG_FILE 覆盖路径（设为空或 "-" 关闭落盘）；
         BROKER_LOG_MAX_BYTES（默认 10MB）与 BROKER_LOG_BACKUP_COUNT（默认 5）控制轮转。
    落盘失败（无权限/磁盘满等）不阻止 Broker 运行，仅降级为 stderr。
    """
    if log.handlers:
        return
    level = getattr(logging, os.environ.get("BROKER_LOG_LEVEL", "INFO").upper(), logging.INFO)
    log.setLevel(level)
    log.propagate = False

    # 1) 控制台 / journald：短时间戳即可。
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(stream)

    # 2) 落盘：滚动日志文件（含完整日期，便于跨天回溯）。
    default_log = os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
        "teleagent", "broker.log",
    )
    log_path = os.environ.get("BROKER_LOG_FILE", default_log)
    if log_path and log_path != "-":
        try:
            from logging.handlers import RotatingFileHandler
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            max_bytes = int(os.environ.get("BROKER_LOG_MAX_BYTES", str(10 * 1024 * 1024)) or 0)
            backups = int(os.environ.get("BROKER_LOG_BACKUP_COUNT", "5") or 0)
            file_handler = RotatingFileHandler(
                log_path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] %(message)s"))
            log.addHandler(file_handler)
            log.info("日志落盘：%s（maxBytes=%d, backups=%d）", log_path, max_bytes, backups)
        except Exception as exc:  # noqa: BLE001 — 落盘失败不应阻止 Broker 启动
            log.warning("无法写日志文件 %s（%s），仅输出到 stderr。", log_path, exc)


# ──────────────────────────────────────────────────────────── single-instance lock

# 单实例锁：GUI 版与服务版共用同一把锁文件，保证两者不会同时运行（避免重复拉取/执行任务）。
_INSTANCE_LOCK_PATH = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or os.path.expanduser("~/.cache"),
    "teleagent-broker.lock",
)
_instance_lock_handle = None  # 保持引用，进程存活期间不释放锁（关闭/退出时由 OS 自动释放）


def acquire_single_instance_lock():
    """获取单实例文件锁（fcntl.flock，非阻塞）。成功返回锁文件对象并保存引用；已被占用返回 None。"""
    global _instance_lock_handle
    import fcntl
    os.makedirs(os.path.dirname(_INSTANCE_LOCK_PATH), exist_ok=True)
    fh = open(_INSTANCE_LOCK_PATH, "w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.write(str(os.getpid()))
    fh.flush()
    _instance_lock_handle = fh
    return fh


# ──────────────────────────────────────────────────────────── theme

# 与 Web 端 (frontend-next) 设计 token 对齐的品牌配色：浅色 + 靛蓝强调色。
INK         = "#111827"   # 主文字
INK_SOFT    = "#374151"   # 正文
MUTED       = "#6b7280"   # 次要文字
FAINT       = "#9ca3af"   # 占位/提示
LINE        = "#e5e7eb"   # 边框
PAGE        = "#f7f8fa"   # 页面底色
PANEL       = "#ffffff"   # 卡片/面板
ACCENT      = "#4f46e5"   # 强调（靛蓝）
ACCENT_HOV  = "#4338ca"
ACCENT_PRESS = "#3730a3"
SUCCESS_FG, SUCCESS_BG = "#047857", "#d1fae5"
RUNNING_FG, RUNNING_BG = "#075985", "#e0f2fe"
FAILED_FG,  FAILED_BG  = "#b91c1c", "#fef2f2"

APP_QSS = f"""
* {{
    font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
    font-size: 14px;
}}
QWidget {{ background: {PAGE}; color: {INK_SOFT}; }}
QDialog {{ background: {PAGE}; }}

QLabel {{ background: transparent; color: {INK_SOFT}; }}
QLabel#Title    {{ color: {INK}; font-size: 19px; font-weight: 700; }}
QLabel#Subtitle {{ color: {MUTED}; font-size: 13px; }}
QLabel#Hint     {{ color: {FAINT}; font-size: 12px; }}
QLabel#Error    {{ color: {FAILED_FG}; font-size: 12px; }}

QGroupBox {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 12px;
    margin-top: 16px;
    padding: 14px;
    color: {MUTED};
    font-size: 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px; top: 1px;
    padding: 0 4px;
}}

QLineEdit {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 9px;
    padding: 9px 12px;
    color: {INK};
    selection-background-color: #c7d2fe;
    selection-color: {INK};
}}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit:disabled {{ background: #f3f4f6; color: {FAINT}; }}

QPushButton {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 9px;
    padding: 9px 18px;
    color: {INK_SOFT};
    font-weight: 500;
}}
QPushButton:hover  {{ background: #f3f4f6; }}
QPushButton:pressed {{ background: {LINE}; }}
QPushButton#Primary {{
    background: {ACCENT}; border: 1px solid {ACCENT};
    color: #ffffff; font-weight: 600;
}}
QPushButton#Primary:hover   {{ background: {ACCENT_HOV}; border-color: {ACCENT_HOV}; }}
QPushButton#Primary:pressed {{ background: {ACCENT_PRESS}; border-color: {ACCENT_PRESS}; }}

QCheckBox {{ background: transparent; color: {INK_SOFT}; spacing: 9px; }}
QCheckBox::indicator {{
    width: 18px; height: 18px;
    border: 1px solid #d1d5db; border-radius: 5px; background: {PANEL};
}}
QCheckBox::indicator:hover    {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked  {{ background: {ACCENT}; border-color: {ACCENT}; }}

QLabel#StatusPill {{
    border-radius: 9px; padding: 11px 14px; font-weight: 600;
    background: #f3f4f6; color: {MUTED};
}}
QLabel#StatusPill[status="idle"]    {{ background: {SUCCESS_BG}; color: {SUCCESS_FG}; }}
QLabel#StatusPill[status="success"] {{ background: {SUCCESS_BG}; color: {SUCCESS_FG}; }}
QLabel#StatusPill[status="running"] {{ background: {RUNNING_BG}; color: {RUNNING_FG}; }}
QLabel#StatusPill[status="error"]   {{ background: {FAILED_BG};  color: {FAILED_FG};  }}
QLabel#StatusPill[status="failed"]  {{ background: {FAILED_BG};  color: {FAILED_FG};  }}
"""


# ──────────────────────────────────────────────────────────── cloud connect

DEFAULT_API_BASE = os.environ.get("BROKER_API_BASE", "http://localhost:9020")


# 云端连接配置持久化 key（QSettings）
CLOUD_SETTINGS_ORG = "TeleAgent"
CLOUD_SETTINGS_APP = "Broker"
CLOUD_KEY_API_BASE = "cloud/api_base"
CLOUD_KEY_CLIENT_NAME = "cloud/client_name"
CLOUD_KEY_TOKEN = "cloud/token"
CLOUD_KEY_EMAIL = "cloud/email"
CLOUD_KEY_CLIENT_ID = "cloud/client_id"  # 客户端凭证 ID
CLOUD_KEY_CLIENT_SECRET = "cloud/client_secret"
CLOUD_KEY_INTERACTIVE_PERMS = "cloud/interactive_permissions"  # 交互式工具审批开关


def _load_interactive_permissions(default: bool) -> bool:
    s = QSettings(CLOUD_SETTINGS_ORG, CLOUD_SETTINGS_APP)
    return s.value(CLOUD_KEY_INTERACTIVE_PERMS, default, type=bool)


def _save_interactive_permissions(enabled: bool) -> None:
    s = QSettings(CLOUD_SETTINGS_ORG, CLOUD_SETTINGS_APP)
    s.setValue(CLOUD_KEY_INTERACTIVE_PERMS, bool(enabled))


def _load_cloud_settings():
    s = QSettings(CLOUD_SETTINGS_ORG, CLOUD_SETTINGS_APP)
    return {
        "api_base": s.value(CLOUD_KEY_API_BASE, DEFAULT_API_BASE, type=str),
        "client_name": s.value(CLOUD_KEY_CLIENT_NAME, socket.gethostname() or "本机", type=str),
    }


def _save_cloud_settings(api_base: str, client_name: str):
    s = QSettings(CLOUD_SETTINGS_ORG, CLOUD_SETTINGS_APP)
    s.setValue(CLOUD_KEY_API_BASE, api_base)
    s.setValue(CLOUD_KEY_CLIENT_NAME, client_name)


def _save_cloud_token(token: str, email: str):
    s = QSettings(CLOUD_SETTINGS_ORG, CLOUD_SETTINGS_APP)
    s.setValue(CLOUD_KEY_TOKEN, token)
    s.setValue(CLOUD_KEY_EMAIL, email)


def _load_cloud_token():
    s = QSettings(CLOUD_SETTINGS_ORG, CLOUD_SETTINGS_APP)
    return s.value(CLOUD_KEY_TOKEN, "", type=str), s.value(CLOUD_KEY_EMAIL, "", type=str)


def _save_cloud_client_id(client_id: str):
    s = QSettings(CLOUD_SETTINGS_ORG, CLOUD_SETTINGS_APP)
    s.setValue(CLOUD_KEY_CLIENT_ID, client_id.strip())


def _load_cloud_client_id():
    s = QSettings(CLOUD_SETTINGS_ORG, CLOUD_SETTINGS_APP)
    return s.value(CLOUD_KEY_CLIENT_ID, "", type=str)


def _save_cloud_client_credentials(client_id: str, secret_key: str):
    s = QSettings(CLOUD_SETTINGS_ORG, CLOUD_SETTINGS_APP)
    s.setValue(CLOUD_KEY_CLIENT_ID, client_id.strip())
    s.setValue(CLOUD_KEY_CLIENT_SECRET, secret_key)


def _load_cloud_client_credentials():
    s = QSettings(CLOUD_SETTINGS_ORG, CLOUD_SETTINGS_APP)
    return (
        s.value(CLOUD_KEY_CLIENT_ID, "", type=str),
        s.value(CLOUD_KEY_CLIENT_SECRET, "", type=str),
    )


class ClientCredentialDialog(QDialog):
    """
    LocalBroker 使用管理平台签发的 客户端 ID + Secret Key 登录。
    在 Django Admin「Broker 客户端凭证」中创建凭证，将 ID 与 Secret 告知 PC 使用者并在此填写。
    """
    def __init__(self, api_base: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("客户端凭证登录")
        self.setMinimumWidth(400)
        self._api_base = api_base.rstrip("/")
        self._token: Optional[str] = None
        self._email: Optional[str] = None
        self._client_id_value: str = ""
        self._secret_key_value: str = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)

        title = QLabel("客户端凭证登录")
        title.setObjectName("Title")
        layout.addWidget(title)
        subtitle = QLabel("在管理平台（Admin）创建「Broker 客户端凭证」，将 ID 与 Secret Key 填入下方。")
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        grp = QGroupBox("凭证")
        grp_layout = QVBoxLayout(grp)
        grp_layout.setSpacing(10)
        self._edit_client_id = QLineEdit()
        self._edit_client_id.setPlaceholderText("客户端 ID（UUID，从管理平台复制）")
        self._edit_client_id.setClearButtonEnabled(True)
        saved_id, saved_secret = _load_cloud_client_credentials()
        if saved_id:
            self._edit_client_id.setText(saved_id)
        grp_layout.addWidget(self._edit_client_id)
        self._edit_secret = QLineEdit()
        self._edit_secret.setPlaceholderText("Secret Key（已保存在本机时会自动回填）")
        self._edit_secret.setEchoMode(QLineEdit.Password)
        self._edit_secret.setClearButtonEnabled(True)
        if saved_secret:
            self._edit_secret.setText(saved_secret)
        grp_layout.addWidget(self._edit_secret)
        layout.addWidget(grp)

        self._error = QLabel("")
        self._error.setObjectName("Error")
        self._error.setWordWrap(True)
        layout.addWidget(self._error)

        layout.addSpacing(4)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        login_btn = QPushButton("登录")
        login_btn.setObjectName("Primary")
        login_btn.setDefault(True)
        login_btn.clicked.connect(self._do_login)
        btn_layout.addWidget(login_btn)
        layout.addLayout(btn_layout)

    def _do_login(self):
        from broker_api import client_login
        client_id = (self._edit_client_id.text() or "").strip()
        secret_key = self._edit_secret.text() or ""
        self._error.setText("")
        if not client_id or not secret_key:
            self._error.setText("请填写客户端 ID 与 Secret Key")
            return
        try:
            out = client_login(client_id, secret_key, base=self._api_base)
            self._token = out.get("token")
            self._email = out.get("email", "")
            if self._token:
                _save_cloud_token(self._token, self._email)
                _save_cloud_client_credentials(client_id, secret_key)
                self._client_id_value = client_id
                self._secret_key_value = secret_key
                self.accept()
            else:
                self._error.setText("未返回 token")
        except Exception as e:
            self._error.setText(str(e)[:200])

    def token(self) -> Optional[str]:
        return self._token

    def email(self) -> Optional[str]:
        return self._email

    def client_id(self) -> str:
        return self._client_id_value

    def secret_key(self) -> str:
        return self._secret_key_value


class CloudConnectDialog(QDialog):
    """接入云端时填写：API 地址（开发/生产）与本机客户端名称，会记住上次填写"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("云端连接配置")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)

        title = QLabel("接入云端")
        title.setObjectName("Title")
        layout.addWidget(title)
        subtitle = QLabel("本机将作为 Broker 接入云端，请确认服务地址与本机名称。")
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        grp = QGroupBox("云端 API 地址")
        url_layout = QVBoxLayout(grp)
        self._edit_base = QLineEdit()
        self._edit_base.setPlaceholderText("http://localhost:9020 或 https://your-server.com")
        saved = _load_cloud_settings()
        self._edit_base.setText(saved["api_base"] or DEFAULT_API_BASE)
        url_layout.addWidget(self._edit_base)
        layout.addWidget(grp)
        grp2 = QGroupBox("本机客户端名称")
        url2 = QVBoxLayout(grp2)
        self._edit_name = QLineEdit()
        self._edit_name.setPlaceholderText("例如：Neal的笔记本、办公室PC")
        self._edit_name.setText(saved["client_name"] or (socket.gethostname() or "本机"))
        url2.addWidget(self._edit_name)
        layout.addWidget(grp2)

        layout.addSpacing(4)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = btns.button(QDialogButtonBox.Ok)
        ok_btn.setObjectName("Primary")
        ok_btn.setText("连接")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_accept(self):
        api_base = (self._edit_base.text() or "").strip() or DEFAULT_API_BASE
        client_name = (self._edit_name.text() or "").strip() or "本机"
        _save_cloud_settings(api_base, client_name)
        self.accept()

    def api_base(self) -> str:
        return (self._edit_base.text() or "").strip() or DEFAULT_API_BASE

    def client_name(self) -> str:
        return (self._edit_name.text() or "").strip() or "本机"


class BrokerWorkerThread(QThread):
    """后台轮询 queued 任务并执行，通过信号更新 UI。"""
    status_update = pyqtSignal(str, str)  # status_kind, message

    def __init__(
        self,
        api_base: str,
        client_id: str,
        token: Optional[str] = None,
        session_manager: Optional[CloudSessionManager] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._base = api_base
        self._client_id = client_id
        self._token = token
        self._session = session_manager
        self._stop = False
        # 置位后，轮询线程会回收所有常驻进程，使其按新设置（如交互式审批）重建。
        self._warm_restart = threading.Event()

    def stop(self):
        self._stop = True

    def restart_warm_sessions(self):
        """请求回收常驻会话（线程安全）：下一轮轮询里由本线程执行，重建时套用最新设置。"""
        self._warm_restart.set()

    def _sleep_interruptible(self, seconds: float) -> bool:
        """睡 seconds 秒，每 0.1s 检查一次停止标志。返回 True 表示已请求停止。"""
        ticks = max(1, int(seconds / 0.1))
        for _ in range(ticks):
            if self._stop:
                return True
            time.sleep(0.1)
        return False

    def run(self):
        _setup_logging()
        from broker_api import (
            heartbeat_client, patch_task, get_task, post_task_events, get_queued_tasks,
            get_active_conversations, get_pending_controls, ack_control,
            get_pending_fs_requests, ack_fs_request,
            get_pending_file_transfers, upload_file_transfer, fail_file_transfer,
            AuthError,
        )
        from broker_worker import (
            flush_pending_final_reports, run_agent_and_report,
            sync_warm_sessions, run_warm_turn_and_report, apply_pending_controls,
            apply_pending_fs_requests, apply_pending_file_transfers,
            terminate_all_oneshot_procs,
        )
        from session_manager import SessionManager
        from dispatcher import TaskDispatcher
        import time
        last_heartbeat = 0.0
        warm_manager = SessionManager()

        def _call(fn, *args, **kwargs):
            if self._session:
                return self._session.call(fn, *args, **kwargs)
            return fn(*args, **kwargs)

        # 执行单个任务（常驻 or 一次性）并回写。由派发器在独立线程中调用，故须线程安全：
        # _call 已线程安全（CloudSessionManager 加锁）；同会话串行由派发器保证；
        # 同目录串行由派发器内的 cwd 锁保证。
        def _run_task(task):
            task_id = str(task.get("id"))
            conv_id = str(task.get("conversation_id") or "")
            warm = warm_manager.get(conv_id) if conv_id else None
            common = dict(
                token=self._token,
                on_status=lambda k, msg: self.status_update.emit(k, msg),
                get_task_fn=lambda *a, **k: _call(get_task, *a, **k),
                patch_task_fn=lambda *a, **k: _call(patch_task, *a, **k),
                post_task_events_fn=lambda *a, **k: _call(post_task_events, *a, **k),
            )
            if warm is not None and warm.is_alive():
                run_warm_turn_and_report(warm, task_id, task, self._base, **common)
            else:
                run_agent_and_report(task_id, task, self._base, **common)

        dispatcher = TaskDispatcher(
            _run_task,
            max_concurrency=4,
            on_skip=lambda t: self.status_update.emit("idle", f"目录被占用，排队中：{t.get('cwd')}"),
        )

        interval = POLL_MIN_INTERVAL
        log.info("broker worker 启动（client=%s, base=%s, poll=%.1f-%.1fs）",
                 self._client_id, self._base, POLL_MIN_INTERVAL, POLL_MAX_INTERVAL)
        while not self._stop:
            try:
                # 设置变更（如切换交互式审批）→ 回收常驻进程，下次 ensure 按新参数重建。
                if self._warm_restart.is_set():
                    self._warm_restart.clear()
                    # 只回收空闲会话；在途轮次的会话标记为待回收，结束后由 reconcile 回收，
                    # 避免切换交互式审批时打断正在执行的任务。
                    warm_manager.restart_idle_sessions()
                    log.info("已回收空闲常驻会话以套用新设置（繁忙会话延后回收）")
                now = time.time()
                try:
                    flushed = flush_pending_final_reports(
                        base=self._base,
                        token=self._token,
                        patch_task_fn=lambda *args, **kwargs: _call(patch_task, *args, **kwargs),
                    )
                    if flushed:
                        self.status_update.emit("idle", f"已补写 {flushed} 条离线结果")
                        log.info("已补写 %d 条离线结果", flushed)
                except Exception as exc:
                    log.debug("flush 离线结果失败：%s", exc, exc_info=True)
                if now - last_heartbeat >= 30:
                    _call(
                        heartbeat_client,
                        self._client_id,
                        supported_agents=discover_supported_agents(),
                        base=self._base,
                        token=self._token,
                    )
                    last_heartbeat = now
                # 为「正被 Web 打开」的会话预热常驻 Agent 进程，并回收空闲进程。
                sync_warm_sessions(
                    warm_manager,
                    self._base,
                    self._client_id,
                    token=self._token,
                    get_active_conversations_fn=lambda *args, **kwargs: _call(get_active_conversations, *args, **kwargs),
                )
                # 应用 Web 端发来的动态控制（切权限模式/模型/中断）到常驻进程。
                apply_pending_controls(
                    warm_manager,
                    self._base,
                    self._client_id,
                    token=self._token,
                    get_pending_controls_fn=lambda *args, **kwargs: _call(get_pending_controls, *args, **kwargs),
                    ack_control_fn=lambda *args, **kwargs: _call(ack_control, *args, **kwargs),
                )
                # 处理 Web 端发来的目录浏览请求（新建会话时选工作目录的文件夹树）。
                apply_pending_fs_requests(
                    self._base,
                    self._client_id,
                    token=self._token,
                    get_pending_fs_requests_fn=lambda *args, **kwargs: _call(get_pending_fs_requests, *args, **kwargs),
                    ack_fs_request_fn=lambda *args, **kwargs: _call(ack_fs_request, *args, **kwargs),
                )
                # 处理 Web 端发起的文件下载请求：读本机文件并上传中转。
                apply_pending_file_transfers(
                    self._base,
                    self._client_id,
                    token=self._token,
                    get_pending_file_transfers_fn=lambda *args, **kwargs: _call(get_pending_file_transfers, *args, **kwargs),
                    upload_file_transfer_fn=lambda *args, **kwargs: _call(upload_file_transfer, *args, **kwargs),
                    fail_file_transfer_fn=lambda *args, **kwargs: _call(fail_file_transfer, *args, **kwargs),
                )
                # 拉取全部 queued 任务并并发派发（按会话/目录串行约束 + 并发上限）。
                tasks = _call(
                    get_queued_tasks,
                    client_id=self._client_id,
                    base=self._base,
                    token=self._token,
                )
                dispatcher.dispatch(tasks or [])
                active = dispatcher.active_count()
                warm_count = len(warm_manager.active_conv_ids())
                if active:
                    self.status_update.emit("running", f"并发执行 {active} 个任务…")
                elif not tasks:
                    self.status_update.emit("idle", "等待任务…")
                if tasks:
                    log.info("派发 %d 个任务（并发 %d，常驻会话 %d）", len(tasks), active, warm_count)
                # 自适应轮询：有任务/在执行/有常驻会话 → 复位为最小间隔保证低延迟；
                # 完全空闲 → 指数退避到上限，减少对后端的空转轮询。
                if tasks or active > 0 or warm_count > 0:
                    if interval != POLL_MIN_INTERVAL:
                        log.debug("复位轮询间隔 %.1fs→%.1fs", interval, POLL_MIN_INTERVAL)
                    interval = POLL_MIN_INTERVAL
                else:
                    prev = interval
                    interval = min(interval * POLL_BACKOFF_FACTOR, POLL_MAX_INTERVAL)
                    if interval != prev:
                        log.debug("空闲退避 %.1fs→%.1fs", prev, interval)
            except AuthError as e:
                # 认证失效（token 过期/被吊销）：再怎么退避重试后端也不会接受，停止空转。
                # 明确告知 UI 让用户重新登录，并结束轮询线程（不再 hammer 后端）。
                self.status_update.emit("error", "认证失效，请重新登录")
                log.error("认证失效，停止轮询：%s", e)
                break
            except Exception as e:
                self.status_update.emit("error", str(e))
                interval = min(max(interval, POLL_MIN_INTERVAL) * POLL_BACKOFF_FACTOR, POLL_ERROR_MAX_INTERVAL)
                log.warning("轮询循环异常，%.1fs 后重试：%s", interval, e, exc_info=True)
            if self._sleep_interruptible(interval):
                warm_manager.shutdown_all()
                terminate_all_oneshot_procs()
                return
        warm_manager.shutdown_all()
        terminate_all_oneshot_procs()


class CloudBrokerWindow(QWidget):
    """云端模式：本机仅作 Broker（数据中转），显示状态与「打开网页」入口。"""
    def __init__(
        self,
        api_base: str,
        client_id: str,
        client_name: str,
        token: Optional[str] = None,
        session_manager: Optional[CloudSessionManager] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("TeleAgent Broker – 云端（数据中转）")
        self._api_base = api_base.rstrip("/")
        self._client_id = client_id
        self._client_name = client_name
        self._token = token
        self._session = session_manager
        self.setMinimumSize(480, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)

        title = QLabel("TeleAgent Broker")
        title.setObjectName("Title")
        layout.addWidget(title)
        subtitle = QLabel(f"本机「{self._client_name}」已接入云端，作为数据中转，数据不存本地。")
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._status = QLabel("正在连接…")
        self._status.setObjectName("StatusPill")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        grp = QGroupBox("在 Web 端选择本机 Agent 进行对话")
        grp_layout = QVBoxLayout(grp)
        grp_layout.setSpacing(10)
        url = f"{self._api_base}/broker"
        url_label = QLabel(url)
        url_label.setObjectName("Hint")
        url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        grp_layout.addWidget(url_label)
        open_btn = QPushButton("打开 Broker 网页")
        open_btn.setObjectName("Primary")
        open_btn.clicked.connect(self._open_web)
        grp_layout.addWidget(open_btn)
        layout.addWidget(grp)

        # 交互式工具审批开关：勾选后，Agent 的危险操作（运行命令/改文件等）会在网页弹出
        # 审批卡片让你允许/拒绝。状态持久化，并实时作用于之后预热的会话。
        try:
            from session_manager import set_interactive_permissions, interactive_permissions_enabled
        except ModuleNotFoundError:
            from LocalBroker.session_manager import set_interactive_permissions, interactive_permissions_enabled
        self._set_interactive_permissions = set_interactive_permissions
        saved_perms = _load_interactive_permissions(interactive_permissions_enabled())
        set_interactive_permissions(saved_perms)  # 启动即应用，首批常驻会话即生效
        perm_grp = QGroupBox("安全")
        perm_layout = QVBoxLayout(perm_grp)
        perm_layout.setSpacing(8)
        self._perms_cb = QCheckBox("交互式工具审批（Agent 危险操作需在网页确认）")
        self._perms_cb.setChecked(saved_perms)
        self._perms_cb.toggled.connect(self._on_toggle_perms)
        perm_layout.addWidget(self._perms_cb)
        self._perms_hint = QLabel("")
        self._perms_hint.setObjectName("Hint")
        self._perms_hint.setWordWrap(True)
        perm_layout.addWidget(self._perms_hint)
        layout.addWidget(perm_grp)

        layout.addStretch()
        self._worker = BrokerWorkerThread(
            self._api_base,
            self._client_id,
            token=self._token,
            session_manager=self._session,
            parent=self,
        )
        self._worker.status_update.connect(self._on_status)
        self._worker.start()
        self._on_status("idle", "等待任务…")

    def _on_status(self, kind: str, message: str):
        dot = {"idle": "●", "running": "●", "success": "●",
               "failed": "●", "error": "●"}.get(kind, "○")
        self._status.setText(f"{dot}  {message}")
        # 用动态属性驱动 QSS 配色（StatusPill[status="..."]），切换后需重新 polish 才生效。
        self._status.setProperty("status", kind)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _on_toggle_perms(self, checked: bool):
        self._set_interactive_permissions(checked)
        _save_interactive_permissions(checked)
        # 回收常驻会话，使变更对正在打开的会话也尽快生效（下次预热按新设置重建）。
        worker = getattr(self, "_worker", None)
        if worker is not None:
            worker.restart_warm_sessions()
        self._perms_hint.setText(
            "已开启：Agent 的命令/改文件等操作会在网页弹出审批卡片。" if checked
            else "已关闭：Agent 操作不再弹审批（按各自权限模式执行）。"
        )

    def _open_web(self):
        QDesktopServices.openUrl(QUrl(f"{self._api_base}/broker"))

    def closeEvent(self, event):
        self._worker.stop()
        self._worker.wait(3000)
        event.accept()


# ──────────────────────────────────────────────────────────── entry point

def main():
    _setup_logging()
    log.info("启动模式：桌面 GUI（broker_qt）")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("TeleAgent Broker")
    app.setStyleSheet(APP_QSS)

    # 与后台服务互斥：若服务（或另一个 GUI）已在运行，提示并退出，避免重复执行任务。
    if acquire_single_instance_lock() is None:
        QMessageBox.critical(
            None, "已在运行",
            "已有另一个 TeleAgent Broker 实例（桌面版或后台服务）正在运行。\n\n"
            "同一时间只能运行一个，以免重复拉取/执行任务。\n"
            "若是后台服务占用，可先停止它：\n    systemctl --user stop teleagent-broker",
        )
        log.warning("检测到已有实例在运行，桌面版退出。")
        sys.exit(1)

    conn = CloudConnectDialog()
    if conn.exec_() != QDialog.Accepted:
        sys.exit(0)
    api_base = conn.api_base()
    client_name = conn.client_name()
    login_dlg = ClientCredentialDialog(api_base)
    if login_dlg.exec_() != QDialog.Accepted or not login_dlg.token():
        sys.exit(0)
    token = login_dlg.token()
    session = CloudSessionManager(
        api_base=api_base,
        credential_id=login_dlg.client_id(),
        secret_key=login_dlg.secret_key(),
        token=token,
        save_token_fn=_save_cloud_token,
    )
    try:
        from broker_api import register_client
        supported_agents = discover_supported_agents()
        client = session.call(
            register_client,
            name=client_name,
            hostname=socket.gethostname() or "",
            supported_agents=supported_agents,
            base=api_base,
        )
        client_id = str(client["id"])
    except Exception as e:
        QMessageBox.critical(None, "连接失败", "注册云端客户端失败：\n" + str(e))
        sys.exit(1)
    w = CloudBrokerWindow(
        api_base=api_base,
        client_id=client_id,
        client_name=client_name,
        token=token,
        session_manager=session,
    )
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
