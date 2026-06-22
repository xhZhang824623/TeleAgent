"""Qt UI for Local Broker: 本地对话（BrokerCore）或接入云端（CloudBrokerWindow + broker_worker）。"""

import sys
import os
import time
import socket
from typing import Optional, List, Dict

from PyQt5.QtCore import Qt, QObject, pyqtSignal, QTimer, QSize, QUrl, QThread, QSettings
from PyQt5.QtGui import QFont, QColor, QTextCursor, QTextCharFormat
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton, QScrollArea,
    QListWidget, QListWidgetItem, QDialog, QFileDialog, QComboBox,
    QSizePolicy, QFrame, QSplitter, QMessageBox, QDialogButtonBox,
    QGroupBox,
)
from PyQt5.QtGui import QDesktopServices

from broker_core import (
    BrokerCore, BrokerEvent,
    ConversationRecord, TaskRecord, TaskStatus,
)
try:
    from LocalBroker.agent_runtime import discover_supported_agents
except ModuleNotFoundError:
    from agent_runtime import discover_supported_agents
try:
    from LocalBroker.cloud_session import CloudSessionManager
except ModuleNotFoundError:
    from cloud_session import CloudSessionManager

MAX_RECENT_CWDS = 10


# ──────────────────────────────────────────────────────────── helpers

def shorten(s: str, n: int) -> str:
    s = s.replace("\r", "")
    return s if len(s) <= n else s[:n] + "…"


def safe_get(d: dict, path: list, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def first_lines(s: str, n: int = 6) -> str:
    lines = s.splitlines()
    out = "\n".join(lines[:n])
    if len(lines) > n:
        out += f"\n  …({len(lines) - n} more lines)"
    return out


def time_ago(ts: float) -> str:
    d = time.time() - ts
    if d < 60:    return "just now"
    if d < 3600:  return f"{int(d/60)}m ago"
    if d < 86400: return f"{int(d/3600)}h ago"
    return f"{int(d/86400)}d ago"


def format_tool_line(tc: dict, sub: str) -> str:
    """Return a compact one-line description of a tool_call event."""
    if "shellToolCall" in tc:
        if sub == "started":
            cmd = safe_get(tc, ["shellToolCall", "args", "command"], "")
            return f"▶ shell  {shorten(cmd, 80)}"
        if sub == "completed":
            res = safe_get(tc, ["shellToolCall", "result"], {})
            if "success" in res:
                code = res["success"].get("exitCode", "?")
                out = res["success"].get("stdout", "") or res["success"].get("interleavedOutput", "")
                preview = shorten(first_lines(out, 3).replace("\n", " ↵ "), 80)
                return f"✅ exit={code}  {preview}"
            if "failure" in res:
                code = res["failure"].get("exitCode", "?")
                return f"❌ exit={code}"
    if "readToolCall" in tc:
        if sub == "started":
            path = safe_get(tc, ["readToolCall", "args", "path"], "")
            return f"▶ read   {path}"
        if sub == "completed":
            s = safe_get(tc, ["readToolCall", "result", "success"], {})
            return f"✅ read   {s.get('path','')}  ({s.get('totalLines','?')} lines)"
    if "writeToolCall" in tc:
        if sub == "started":
            path = safe_get(tc, ["writeToolCall", "args", "path"], "")
            return f"▶ write  {path}"
        if sub == "completed":
            return "✅ write  done"
    return ""


# ──────────────────────────────────────────────────────────── thread bridge

class BrokerBridge(QObject):
    """Marshals BrokerCore callbacks (background thread) into Qt main thread."""
    event_sig = pyqtSignal(object)

    def on_event(self, event: BrokerEvent):
        self.event_sig.emit(event)


# ──────────────────────────────────────────────────────────── auto-resize text

class StreamTextEdit(QTextEdit):
    """Read-only QTextEdit that auto-resizes to its document height (no scrollbar)."""

    MAX_H = 600

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.setMinimumHeight(24)
        self.document().contentsChanged.connect(self._fit)

    def _fit(self):
        h = int(self.document().size().height()) + 6
        self.setFixedHeight(min(max(24, h), self.MAX_H))
        # tell ancestors to relayout
        p = self.parent()
        while p:
            p.updateGeometry()
            p = p.parent()

    def sizeHint(self) -> QSize:
        h = int(self.document().size().height()) + 6
        return QSize(super().sizeHint().width(), min(max(24, h), self.MAX_H))


# ──────────────────────────────────────────────────────────── message bubbles

class UserBubble(QFrame):
    def __init__(self, prompt: str, parent=None):
        super().__init__(parent)
        self.setObjectName("UserBubble")
        self.setStyleSheet("""
            QFrame#UserBubble {
                background: #E3F2FD;
                border-radius: 8px;
                margin: 4px 60px 4px 4px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        hdr = QLabel("👤  You")
        hdr.setStyleSheet("color:#1565C0; font-weight:bold; font-size:12px;")
        layout.addWidget(hdr)

        lbl = QLabel(prompt)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lbl.setStyleSheet("color:#111; font-size:13px;")
        layout.addWidget(lbl)


_STATUS_LABEL = {
    TaskStatus.QUEUED:    "⏳  Queued…",
    TaskStatus.RUNNING:   "⏳  Thinking…",
    TaskStatus.SUCCESS:   "✅  Done",
    TaskStatus.FAILED:    "❌  Failed",
    TaskStatus.CANCELLED: "⛔  Stopped",
    TaskStatus.TIMEOUT:   "⏰  Timed out",
}


class AgentBubble(QFrame):
    """
    Displays one agent turn.  Supports incremental streaming:
      • append_text(str)      – stream assistant text deltas
      • append_tool(tc, sub)  – add a compact tool summary line
      • set_result(str)       – show final result (if differs from streamed text)
      • set_status(status)    – update header icon
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AgentBubble")
        self.setStyleSheet("""
            QFrame#AgentBubble {
                background: #FAFAFA;
                border: 1px solid #E0E0E0;
                border-radius: 8px;
                margin: 4px 4px 4px 60px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        self._header = QLabel("🤖  ⏳  Thinking…")
        self._header.setStyleSheet("color:#555; font-weight:bold; font-size:12px;")
        layout.addWidget(self._header)

        self._view = StreamTextEdit()
        layout.addWidget(self._view)

        self._accum = ""          # accumulated assistant text
        self._has_tools = False   # whether any tool separator was inserted

    # ── streaming API ────────────────────────────────────────────────

    def append_text(self, text: str):
        if not text:
            return
        self._accum += text
        cur = self._view.textCursor()
        cur.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#111"))
        cur.insertText(text, fmt)
        self._view.setTextCursor(cur)

    def append_tool(self, tc: dict, sub: str):
        line = format_tool_line(tc, sub)
        if not line:
            return
        cur = self._view.textCursor()
        cur.movePosition(QTextCursor.End)
        if not self._has_tools:
            self._has_tools = True
            sep_fmt = QTextCharFormat()
            sep_fmt.setForeground(QColor("#bbb"))
            cur.insertBlock()
            cur.insertText("─" * 40, sep_fmt)
        tool_fmt = QTextCharFormat()
        tool_fmt.setForeground(QColor("#666"))
        f = QFont("Monospace")
        f.setPointSize(9)
        tool_fmt.setFont(f)
        cur.insertBlock()
        cur.insertText(line, tool_fmt)
        self._view.setTextCursor(cur)

    def set_result(self, text: str):
        """Show result block if it differs meaningfully from streamed text."""
        if not text or text.strip() == self._accum.strip():
            return
        cur = self._view.textCursor()
        cur.movePosition(QTextCursor.End)
        sep_fmt = QTextCharFormat()
        sep_fmt.setForeground(QColor("#bbb"))
        cur.insertBlock()
        cur.insertText("─── result ───", sep_fmt)
        res_fmt = QTextCharFormat()
        res_fmt.setForeground(QColor("#2E7D32"))
        cur.insertBlock()
        cur.insertText(text, res_fmt)
        self._view.setTextCursor(cur)

    def append_raw_line(self, line: str):
        """Show a non-JSON stdout line (e.g. error messages) in gray monospace."""
        cur = self._view.textCursor()
        cur.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#c62828"))
        f = QFont("Monospace")
        f.setPointSize(9)
        fmt.setFont(f)
        cur.insertBlock()
        cur.insertText(line, fmt)
        self._view.setTextCursor(cur)

    def set_status(self, status: TaskStatus, exit_code: Optional[int] = None):
        label = _STATUS_LABEL.get(status, str(status))
        if exit_code is not None and status not in (TaskStatus.SUCCESS, TaskStatus.RUNNING,
                                                     TaskStatus.QUEUED):
            label += f"  (exit {exit_code})"
        self._header.setText(f"🤖  {label}")

    def replay(self, task: TaskRecord):
        """Replay all historical events from a completed/running task."""
        for e in task.events:
            t = e.get("type", "")
            if t == "assistant":
                content = safe_get(e, ["message", "content"], [])
                txt = ""
                if isinstance(content, list) and content and isinstance(content[0], dict):
                    txt = content[0].get("text", "") or ""
                self.append_text(txt)
            elif t == "tool_call":
                self.append_tool(e.get("tool_call", {}), e.get("subtype", ""))
        # Show raw lines for failed tasks (contains the actual error message)
        if task.status not in (TaskStatus.SUCCESS, TaskStatus.RUNNING, TaskStatus.QUEUED):
            for line in task.raw_lines:
                self.append_raw_line(line)
        if task.result_text:
            self.set_result(task.result_text)
        self.set_status(task.status, task.exit_code)


# ──────────────────────────────────────────────────────────── chat scroll area

class ChatArea(QScrollArea):
    """
    Scrollable container for message bubbles.
    A top-stretch pushes short conversations to the bottom (Telegram-style).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { border: none; background: #fff; }")

        self._container = QWidget()
        self._container.setStyleSheet("background: #fff;")
        self._layout = QVBoxLayout(self._container)
        self._layout.setSpacing(6)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.addStretch(1)   # top-stretch → messages float to bottom
        self.setWidget(self._container)

    def add_widget(self, w: QWidget):
        self._layout.addWidget(w)
        QTimer.singleShot(60, self.scroll_to_bottom)

    def clear_messages(self):
        # Remove everything except the top stretch (index 0)
        while self._layout.count() > 1:
            item = self._layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

    def scroll_to_bottom(self):
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


# ──────────────────────────────────────────────────────────── new-chat dialog

class NewChatDialog(QDialog):
    def __init__(self, recent_cwds: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Conversation")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select working directory for this conversation:"))

        row = QHBoxLayout()
        self._combo = QComboBox()
        self._combo.setEditable(True)
        for c in recent_cwds:
            self._combo.addItem(c)
        self._combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self._combo, 1)
        row.addWidget(browse)
        layout.addLayout(row)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(
            self, "Select Directory", self._combo.currentText())
        if d:
            self._combo.setCurrentText(d)

    def selected_cwd(self) -> str:
        return self._combo.currentText().strip()


# ──────────────────────────────────────────────────────────── conversation panel

class ConversationPanel(QWidget):
    """
    Right panel: header + chat bubble stream + input area.
    Emits signals instead of calling MainWindow directly.
    """
    message_sent   = pyqtSignal(str, str)   # conv_id, prompt
    stop_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── header ──────────────────────────────────────────────────
        self._header = QLabel("Select or create a conversation")
        self._header.setStyleSheet(
            "padding:8px 14px; background:#fff;"
            "border-bottom:1px solid #e0e0e0; font-size:12px; color:#333;")
        layout.addWidget(self._header)

        # ── chat area ────────────────────────────────────────────────
        self._chat = ChatArea()
        layout.addWidget(self._chat, 1)

        # ── input area ───────────────────────────────────────────────
        input_frame = QFrame()
        input_frame.setStyleSheet(
            "background:#fff; border-top:1px solid #e0e0e0;")
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(10, 8, 10, 8)
        input_layout.setSpacing(8)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a message…  (Enter to send)")
        self._input.setStyleSheet(
            "padding:8px 10px; border:1px solid #ddd; border-radius:6px; font-size:13px;")
        input_layout.addWidget(self._input, 1)

        self._send_btn = QPushButton("▶  Send")
        self._send_btn.setFixedWidth(90)
        self._send_btn.setEnabled(False)
        input_layout.addWidget(self._send_btn)

        self._stop_btn = QPushButton("⛔  Stop")
        self._stop_btn.setFixedWidth(90)
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet("color:#c62828;")
        input_layout.addWidget(self._stop_btn)

        layout.addWidget(input_frame)

        # ── state ────────────────────────────────────────────────────
        self._conv_id: Optional[str] = None
        self._current_bubble: Optional[AgentBubble] = None
        self._running = False

        self._send_btn.clicked.connect(self._do_send)
        self._stop_btn.clicked.connect(self.stop_requested)
        self._input.returnPressed.connect(self._do_send)

    # ── public interface ─────────────────────────────────────────────

    def load_conversation(self, conv: ConversationRecord,
                          get_task_fn) -> Optional[AgentBubble]:
        """
        Load (or reload) a conversation into the chat view.
        Returns the last AgentBubble (may be for a still-running task).
        """
        self._conv_id = conv.conv_id
        self._update_header(conv)
        self._chat.clear_messages()
        self._current_bubble = None

        for msg in conv.messages:
            task = get_task_fn(msg.task_id)
            self._chat.add_widget(UserBubble(msg.prompt))
            bubble = AgentBubble()
            if task:
                bubble.replay(task)
            self._chat.add_widget(bubble)
            self._current_bubble = bubble

        # Running state based on last message
        is_running = False
        if conv.messages:
            last = get_task_fn(conv.messages[-1].task_id)
            is_running = bool(last and last.status == TaskStatus.RUNNING)

        self._set_running(is_running)
        QTimer.singleShot(120, self._chat.scroll_to_bottom)
        return self._current_bubble

    def set_no_conversation(self):
        self._conv_id = None
        self._chat.clear_messages()
        self._current_bubble = None
        self._header.setText("Select or create a conversation")
        self._set_running(False)
        self._send_btn.setEnabled(False)
        self._input.setEnabled(False)

    def add_user_bubble(self, prompt: str):
        self._chat.add_widget(UserBubble(prompt))

    def add_agent_bubble(self) -> AgentBubble:
        bubble = AgentBubble()
        self._chat.add_widget(bubble)
        self._current_bubble = bubble
        return bubble

    def update_header(self, conv: ConversationRecord):
        self._update_header(conv)

    def set_running(self, running: bool):
        self._set_running(running)

    @property
    def conv_id(self) -> Optional[str]:
        return self._conv_id

    @property
    def current_bubble(self) -> Optional[AgentBubble]:
        return self._current_bubble

    # ── internals ────────────────────────────────────────────────────

    def _do_send(self):
        prompt = self._input.text().strip()
        if not prompt or not self._conv_id:
            return
        self._input.clear()
        self.message_sent.emit(self._conv_id, prompt)

    def _set_running(self, running: bool):
        self._running = running
        has_conv = self._conv_id is not None
        self._send_btn.setEnabled(has_conv and not running)
        self._input.setEnabled(has_conv and not running)
        self._stop_btn.setEnabled(running)

    def _update_header(self, conv: ConversationRecord):
        sid = (conv.session_id[:14] + "…") if conv.session_id else "no session yet"
        cwd = conv.cwd
        msgs = len(conv.messages)
        self._header.setText(f"📁  {cwd}    🔑  {sid}    💬  {msgs} message(s)")


# ──────────────────────────────────────────────────────────── conversation list

class ConversationListPanel(QWidget):
    new_chat_requested     = pyqtSignal()
    conversation_selected  = pyqtSignal(str)   # conv_id

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self._new_btn = QPushButton("＋  New Chat")
        self._new_btn.setStyleSheet(
            "font-size:13px; padding:8px; background:#1976D2; color:white; border-radius:4px;")
        self._new_btn.clicked.connect(self.new_chat_requested)
        layout.addWidget(self._new_btn)

        layout.addWidget(QLabel("Conversations:"))

        self._list = QListWidget()
        self._list.setStyleSheet("font-size:12px;")
        self._list.currentRowChanged.connect(self._on_row)
        layout.addWidget(self._list, 1)

    def refresh(self, conversations: List[ConversationRecord],
                selected_id: Optional[str] = None,
                running_ids: Optional[set] = None):
        running_ids = running_ids or set()
        self._list.blockSignals(True)
        self._list.clear()
        for conv in reversed(conversations):
            status = "▶" if conv.conv_id in running_ids else " "
            label = shorten(conv.title or "(untitled)", 32)
            cwd_name = os.path.basename(conv.cwd.rstrip("/\\")) or conv.cwd
            line2 = f"  📁 {cwd_name}  💬 {len(conv.messages)}  {time_ago(conv.created_at)}"
            item = QListWidgetItem(f"{status} {label}\n{line2}")
            item.setData(Qt.UserRole, conv.conv_id)
            if conv.conv_id in running_ids:
                item.setForeground(QColor("#1565C0"))
            self._list.addItem(item)
        self._list.blockSignals(False)

        if selected_id:
            for i in range(self._list.count()):
                itm = self._list.item(i)
                if itm and itm.data(Qt.UserRole) == selected_id:
                    self._list.blockSignals(True)
                    self._list.setCurrentRow(i)
                    self._list.blockSignals(False)
                    break

    def _on_row(self, row: int):
        if row < 0:
            return
        itm = self._list.item(row)
        if itm:
            self.conversation_selected.emit(itm.data(Qt.UserRole))


# ──────────────────────────────────────────────────────────── mode choice & cloud connect

DEFAULT_API_BASE = os.environ.get("BROKER_API_BASE", "http://localhost:9020")


class ModeChoiceDialog(QDialog):
    """启动时选择：纯本地 Qt 对话 或 接入云端（对接 Django，可多设备 Web 交互）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TeleAgent Broker – 选择模式")
        self.setMinimumWidth(420)
        self._choice = None  # "local" | "cloud"
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("请选择与 Agent 的交互方式："))
        grp = QGroupBox()
        grp_layout = QVBoxLayout(grp)
        self._btn_local = QPushButton("💻  本地 (Qt)")
        self._btn_local.setToolTip("纯本地对话，数据仅保存在本机内存")
        self._btn_local.setMinimumHeight(48)
        self._btn_local.clicked.connect(self._on_local)
        grp_layout.addWidget(self._btn_local)
        self._btn_cloud = QPushButton("☁️  接入云端")
        self._btn_cloud.setToolTip("本机仅作 Broker：一头接 Agent、一头接 Django。请在 Web 端选择本机并对话，数据不存本地")
        self._btn_cloud.setMinimumHeight(48)
        self._btn_cloud.clicked.connect(self._on_cloud)
        grp_layout.addWidget(self._btn_cloud)
        layout.addWidget(grp)
        layout.addStretch()

    def _on_local(self):
        self._choice = "local"
        self.accept()

    def _on_cloud(self):
        self._choice = "cloud"
        self.accept()

    def choice(self):
        return self._choice 


# 云端连接配置持久化 key（QSettings）
CLOUD_SETTINGS_ORG = "TeleAgent"
CLOUD_SETTINGS_APP = "Broker"
CLOUD_KEY_API_BASE = "cloud/api_base"
CLOUD_KEY_CLIENT_NAME = "cloud/client_name"
CLOUD_KEY_TOKEN = "cloud/token"
CLOUD_KEY_EMAIL = "cloud/email"
CLOUD_KEY_CLIENT_ID = "cloud/client_id"  # 客户端凭证 ID
CLOUD_KEY_CLIENT_SECRET = "cloud/client_secret"


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
        layout.addWidget(QLabel("请在管理平台（Admin）创建「Broker 客户端凭证」，将下方的 ID 与 Secret Key 填入："))
        grp = QGroupBox("凭证")
        grp_layout = QVBoxLayout(grp)
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
        self._error.setStyleSheet("color: #c62828; font-size: 12px;")
        layout.addWidget(self._error)
        btn_layout = QHBoxLayout()
        login_btn = QPushButton("登录")
        login_btn.clicked.connect(self._do_login)
        btn_layout.addWidget(login_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
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
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
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
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
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

    def stop(self):
        self._stop = True

    def run(self):
        from broker_api import (
            heartbeat_client, patch_task, get_task, post_task_events, get_queued_tasks,
            get_active_conversations, get_pending_controls, ack_control,
        )
        from broker_worker import (
            flush_pending_final_reports, run_agent_and_report,
            sync_warm_sessions, run_warm_turn_and_report, apply_pending_controls,
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

        while not self._stop:
            try:
                now = time.time()
                try:
                    flushed = flush_pending_final_reports(
                        base=self._base,
                        token=self._token,
                        patch_task_fn=lambda *args, **kwargs: _call(patch_task, *args, **kwargs),
                    )
                    if flushed:
                        self.status_update.emit("idle", f"已补写 {flushed} 条离线结果")
                except Exception:
                    pass
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
                # 拉取全部 queued 任务并并发派发（按会话/目录串行约束 + 并发上限）。
                tasks = _call(
                    get_queued_tasks,
                    client_id=self._client_id,
                    base=self._base,
                    token=self._token,
                )
                dispatcher.dispatch(tasks or [])
                active = dispatcher.active_count()
                if active:
                    self.status_update.emit("running", f"并发执行 {active} 个任务…")
                elif not tasks:
                    self.status_update.emit("idle", "等待任务…")
            except Exception as e:
                self.status_update.emit("error", str(e))
            for _ in range(20):  # 2s, 可中断
                if self._stop:
                    warm_manager.shutdown_all()
                    return
                time.sleep(0.1)
        warm_manager.shutdown_all()


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
        self.setMinimumSize(420, 260)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("本机已作为 Broker 接入云端，数据不存本地。"))
        layout.addWidget(QLabel("请在 Web 端选择本机 Agent 进行对话："))
        self._status = QLabel("正在连接…")
        self._status.setStyleSheet("padding:8px; background:#f0f0f0; border-radius:4px;")
        layout.addWidget(self._status)
        open_btn = QPushButton("打开 Broker 网页")
        open_btn.clicked.connect(self._open_web)
        layout.addWidget(open_btn)
        url = f"{self._api_base}/broker"
        layout.addWidget(QLabel(f"地址：{url}"))
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
        if kind == "idle":
            self._status.setText("🟢 " + message)
        elif kind == "running":
            self._status.setText("🟡 " + message)
        elif kind == "success":
            self._status.setText("🟢 " + message)
        elif kind == "failed" or kind == "error":
            self._status.setText("🔴 " + message)
        else:
            self._status.setText(message)

    def _open_web(self):
        QDesktopServices.openUrl(QUrl(f"{self._api_base}/broker"))

    def closeEvent(self, event):
        self._worker.stop()
        self._worker.wait(3000)
        event.accept()


# ──────────────────────────────────────────────────────────── main window

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Local Cursor Broker")
        self.resize(1250, 820)

        self._recent_cwds: List[str] = [os.getcwd()]
        self._active_conv_id: Optional[str] = None

        # task_id → AgentBubble  (for live streaming updates)
        self._task_bubbles: Dict[str, AgentBubble] = {}
        # conv_id → task_id  (which task is currently running for a conversation)
        self._conv_running: Dict[str, str] = {}

        self._bridge = BrokerBridge(self)
        self._bridge.event_sig.connect(self._on_broker_event)
        self._core = BrokerCore(on_event=self._bridge.on_event)

        self._build_ui()
        self._wire()

    # ── UI build ─────────────────────────────────────────────────────

    def _build_ui(self):
        splitter = QSplitter(Qt.Horizontal)

        self._list_panel = ConversationListPanel()
        self._list_panel.setMinimumWidth(200)
        self._list_panel.setMaximumWidth(340)
        splitter.addWidget(self._list_panel)

        self._chat_panel = ConversationPanel()
        splitter.addWidget(self._chat_panel)

        splitter.setStretchFactor(0, 22)
        splitter.setStretchFactor(1, 78)
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("Ready")

    def _wire(self):
        self._list_panel.new_chat_requested.connect(self._on_new_chat)
        self._list_panel.conversation_selected.connect(self._on_conv_selected)
        self._chat_panel.message_sent.connect(self._on_message_sent)
        self._chat_panel.stop_requested.connect(self._core.cancel_current)

    # ── conversation actions ──────────────────────────────────────────

    def _on_new_chat(self):
        dlg = NewChatDialog(self._recent_cwds, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        cwd = dlg.selected_cwd()
        if not cwd or not os.path.isdir(cwd):
            QMessageBox.warning(self, "Invalid directory",
                                "Please choose an existing directory.")
            return
        self._push_recent_cwd(cwd)
        conv_id = self._core.create_conversation(cwd)
        self._active_conv_id = conv_id
        self._refresh_list()
        self._load_conv(conv_id)

    def _on_conv_selected(self, conv_id: str):
        if conv_id == self._active_conv_id:
            return
        self._active_conv_id = conv_id
        self._load_conv(conv_id)

    def _load_conv(self, conv_id: str):
        conv = self._core.get_conversation(conv_id)
        if not conv:
            return
        # Load history; get the last bubble (may be for a running task)
        last_bubble = self._chat_panel.load_conversation(conv, self._core.get_task)

        # If a task is running for this conversation, re-register the new bubble
        running_task_id = self._conv_running.get(conv_id)
        if running_task_id and last_bubble:
            self._task_bubbles[running_task_id] = last_bubble
            self._chat_panel.set_running(True)
        else:
            self._chat_panel.set_running(False)

        self._refresh_list()

    # ── send message ─────────────────────────────────────────────────

    def _on_message_sent(self, conv_id: str, prompt: str):
        # 1. Submit to core (enqueues the task)
        task_id = self._core.send_message(conv_id, prompt)

        # 2. Add bubbles to UI
        self._chat_panel.add_user_bubble(prompt)
        bubble = self._chat_panel.add_agent_bubble()

        # 3. Register bubble for streaming updates
        self._task_bubbles[task_id] = bubble
        self._conv_running[conv_id] = task_id

        # 4. Update UI state
        self._chat_panel.set_running(True)
        self._refresh_list()

    # ── broker events ─────────────────────────────────────────────────

    def _on_broker_event(self, event: BrokerEvent):
        kind    = event.kind
        tid     = event.task_id
        conv_id = event.conv_id
        bubble  = self._task_bubbles.get(tid)

        if kind == "started":
            if bubble:
                bubble.set_status(TaskStatus.RUNNING)

        elif kind == "stream_event":
            if bubble:
                obj = event.data
                t = obj.get("type", "")
                if t == "assistant":
                    content = safe_get(obj, ["message", "content"], [])
                    txt = ""
                    if isinstance(content, list) and content and isinstance(content[0], dict):
                        txt = content[0].get("text", "") or ""
                    bubble.append_text(txt)
                elif t == "tool_call":
                    bubble.append_tool(obj.get("tool_call", {}), obj.get("subtype", ""))
                elif t == "system" and obj.get("subtype") == "init":
                    model = obj.get("model", "?")
                    sid   = str(obj.get("session_id", ""))[:10]
                    self.statusBar().showMessage(f"model={model}   sid={sid}…")
                    # Refresh header once session_id is known
                    if conv_id == self._active_conv_id:
                        conv = self._core.get_conversation(conv_id)
                        if conv:
                            self._chat_panel.update_header(conv)

        elif kind == "raw_line":
            if bubble:
                bubble.append_raw_line(str(event.data))

        elif kind == "finished":
            record: TaskRecord = event.data
            if bubble:
                if record.result_text:
                    bubble.set_result(record.result_text)
                bubble.set_status(record.status, record.exit_code)

            # Clear running state
            if conv_id and self._conv_running.get(conv_id) == tid:
                del self._conv_running[conv_id]
            self._task_bubbles.pop(tid, None)

            # Re-enable input if this is the active conversation
            if conv_id == self._active_conv_id:
                self._chat_panel.set_running(False)
                conv = self._core.get_conversation(conv_id)
                if conv:
                    self._chat_panel.update_header(conv)

            dur = f"{record.duration_sec:.1f}s" if record.duration_sec else ""
            self.statusBar().showMessage(f"{record.status.value}  {dur}")
            self._refresh_list()

        elif kind == "proc_error":
            QMessageBox.critical(self, "Agent Error",
                                 f"Failed to start agent:\n{event.data}\n\n"
                                 "Is 'agent' in PATH and authenticated?")
            self._task_bubbles.pop(tid, None)
            if conv_id and self._conv_running.get(conv_id) == tid:
                del self._conv_running[conv_id]
            if conv_id == self._active_conv_id:
                self._chat_panel.set_running(False)
            self._refresh_list()

        elif kind == "queue_changed":
            q = int(event.data)
            if q:
                self.statusBar().showMessage(f"Queue: {q} pending…")

    # ── helpers ───────────────────────────────────────────────────────

    def _refresh_list(self):
        convs = self._core.get_conversations()
        running = set(self._conv_running.keys())
        self._list_panel.refresh(convs, self._active_conv_id, running)

    def _push_recent_cwd(self, cwd: str):
        if cwd in self._recent_cwds:
            self._recent_cwds.remove(cwd)
        self._recent_cwds.insert(0, cwd)
        self._recent_cwds = self._recent_cwds[:MAX_RECENT_CWDS]

    def closeEvent(self, event):
        self._core.shutdown()
        event.accept()


# ──────────────────────────────────────────────────────────── entry point

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("TeleAgent Broker")

    dlg = ModeChoiceDialog()
    if dlg.exec_() != QDialog.Accepted:
        sys.exit(0)
    choice = dlg.choice()
    if not choice:
        sys.exit(0)

    if choice == "cloud":
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
    else:
        w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
