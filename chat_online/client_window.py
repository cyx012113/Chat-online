"""Modern PyQt6 chat client window."""

from __future__ import annotations

import json
import math
import mimetypes
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric import rsa
from PyQt6.QtCore import QEvent, QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QCloseEvent, QKeyEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .client import ChatClientConnection
from .protocol import MAX_FILE_BYTES
from .secure_protocol import (
    MAX_MESSAGE_CHARS,
    PROTOCOL_VERSION,
    SECURITY_VERSION,
    SecureProtocolError,
    decode_file_ciphertext,
    encode_file_ciphertext,
    load_identity_fields,
    message_envelope,
    validate_file_envelope,
    validate_message_envelope,
)
from .security import (
    Identity,
    PeerPublicKeyPinStore,
    PinMismatchError,
    SecurityError,
    decrypt_file,
    decrypt_file_manifest,
    decrypt_message_text,
    default_client_security_dir,
    encrypt_file,
)
from .server import MAIN_ROOM_ID, MAIN_ROOM_NAME
from .theme import apply_theme, opposite_theme
from .widgets import MessageList


class ClientWindow(QMainWindow):
    """Chat workspace that only communicates with the socket layer via signals."""

    file_upload_finished = pyqtSignal(str, bool, str)

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        theme: str = "light",
        auto_connect: bool = True,
        connection: ChatClientConnection | None = None,
        security_dir: str | Path | None = None,
        identity: Identity | None = None,
        peer_pin_store: PeerPublicKeyPinStore | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("clientWindow")
        self.setWindowTitle("Chat Online")
        self.setMinimumSize(900, 600)
        self.resize(1240, 760)

        self.settings = QSettings()
        self.theme = theme
        self.host = host
        self.port = int(port)
        self.username = username
        self.security_dir = (
            Path(security_dir)
            if security_dir is not None
            else default_client_security_dir()
        )
        self.connection = connection or ChatClientConnection(
            security_dir=self.security_dir,
            identity=identity,
        )
        self.identity = identity or self.connection.ensure_identity(username)
        self.peer_pin_store = peer_pin_store or PeerPublicKeyPinStore(
            self.security_dir / "peer-pins.json"
        )
        self.peer_keys: dict[str, rsa.RSAPublicKey] = {}
        self._peer_key_warnings: set[str] = set()
        self.session_id = ""
        self.current_room_id = MAIN_ROOM_ID
        self.current_room_name = MAIN_ROOM_NAME
        self.private_target: str | None = None
        self.rooms: dict[str, dict[str, Any]] = {}
        self.users: dict[str, dict[str, Any]] = {}
        self.files: dict[str, dict[str, Any]] = {}
        self.private_contacts: set[str] = set()
        self.unread: dict[str, int] = {}
        self.pending_packets: list[dict[str, Any]] = []
        self.pending_downloads: dict[str, Path] = {}
        self.max_file_bytes = MAX_FILE_BYTES
        self.auto_reconnect = self._setting_bool("client/auto_reconnect", True)
        self._manual_disconnect = False
        self._connected = False
        self._ever_connected = False
        self._reconnect_attempt = 0
        self._typing_active = False
        self._last_attention_at = 0.0
        self._attention_interval = 2.0
        self._pending_uploads: set[str] = set()
        self._snapshot_ready = False

        self._build_ui()
        self._connect_signals()
        self._restore_window_state()

        self.reconnect_timer = QTimer(self)
        self.reconnect_timer.setSingleShot(True)
        self.reconnect_timer.timeout.connect(self.connect_to_server)
        self.typing_timer = QTimer(self)
        self.typing_timer.setSingleShot(True)
        self.typing_timer.setInterval(1200)
        self.typing_timer.timeout.connect(self._stop_typing)
        self.heartbeat_timer = QTimer(self)
        self.heartbeat_timer.setInterval(20000)
        self.heartbeat_timer.timeout.connect(
            lambda: self.connection.send({"type": "ping", "timestamp": time.time()})
        )

        if auto_connect:
            QTimer.singleShot(0, self.connect_to_server)

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("clientSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_chat_area())
        splitter.addWidget(self._build_details_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([250, 720, 270])
        self.main_splitter = splitter
        self.setCentralWidget(splitter)

        self.statusBar().setSizeGripEnabled(True)
        self.statusBar().showMessage("尚未连接")
        self.search_shortcut = QShortcut(QKeySequence("Ctrl+L"), self)
        self.search_shortcut.activated.connect(self.message_search.setFocus)
        self.theme_shortcut = QShortcut(QKeySequence("Ctrl+Shift+T"), self)
        self.theme_shortcut.activated.connect(self._toggle_theme)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(220)
        sidebar.setMaximumWidth(310)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(10)

        brand_row = QHBoxLayout()
        brand = QLabel("Chat Online")
        brand.setObjectName("brandTitle")
        brand_row.addWidget(brand)
        brand_row.addStretch(1)
        self.connection_button = QToolButton()
        self.connection_button.setToolTip("重新连接")
        self.connection_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.connection_button.clicked.connect(self._manual_reconnect)
        brand_row.addWidget(self.connection_button)
        layout.addLayout(brand_row)

        self.connection_status = QLabel("离线")
        self.connection_status.setObjectName("statusPill")
        self.connection_status.setProperty("status", "offline")
        layout.addWidget(self.connection_status, 0, Qt.AlignmentFlag.AlignLeft)

        self.conversation_search = QLineEdit()
        self.conversation_search.setPlaceholderText("搜索会话")
        self.conversation_search.setClearButtonEnabled(True)
        self.conversation_search.textChanged.connect(self._filter_conversations)
        layout.addWidget(self.conversation_search)

        rooms_header = QHBoxLayout()
        room_label = QLabel("群聊")
        room_label.setObjectName("sectionTitle")
        rooms_header.addWidget(room_label)
        rooms_header.addStretch(1)
        create_button = QToolButton()
        create_button.setToolTip("创建群组")
        create_button.setText("+")
        create_button.clicked.connect(self._create_room)
        join_button = QToolButton()
        join_button.setToolTip("使用邀请码加入群组")
        join_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        join_button.clicked.connect(self._join_room)
        rooms_header.addWidget(create_button)
        rooms_header.addWidget(join_button)
        layout.addLayout(rooms_header)

        self.room_list = QListWidget()
        self.room_list.setObjectName("conversationList")
        self.room_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.room_list.itemActivated.connect(self._activate_room_item)
        self.room_list.itemClicked.connect(self._activate_room_item)
        self.room_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.room_list.customContextMenuRequested.connect(self._show_room_menu)
        layout.addWidget(self.room_list, 3)

        private_label = QLabel("私聊")
        private_label.setObjectName("sectionTitle")
        layout.addWidget(private_label)
        self.private_list = QListWidget()
        self.private_list.setObjectName("conversationList")
        self.private_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.private_list.itemActivated.connect(self._activate_private_item)
        self.private_list.itemClicked.connect(self._activate_private_item)
        layout.addWidget(self.private_list, 2)

        account = QFrame()
        account.setObjectName("accountBand")
        account_layout = QVBoxLayout(account)
        account_layout.setContentsMargins(10, 8, 10, 8)
        account_layout.setSpacing(0)
        self.account_name = QLabel(self.username)
        self.account_name.setObjectName("accountName")
        self.endpoint = QLabel(f"{self.host}:{self.port}")
        self.endpoint.setProperty("muted", True)
        self.security_status = QLabel("TLS 1.3 · E2EE")
        self.security_status.setProperty("muted", True)
        self.security_status.setToolTip(
            f"本机端到端身份指纹：{self.identity.fingerprint}"
        )
        account_layout.addWidget(self.account_name)
        account_layout.addWidget(self.endpoint)
        account_layout.addWidget(self.security_status)
        layout.addWidget(account)
        return sidebar

    def _build_chat_area(self) -> QWidget:
        area = QFrame()
        area.setObjectName("chatArea")
        layout = QVBoxLayout(area)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        self.chat_title = QLabel(MAIN_ROOM_NAME)
        self.chat_title.setObjectName("chatTitle")
        self.chat_subtitle = QLabel("等待连接")
        self.chat_subtitle.setProperty("muted", True)
        title_box.addWidget(self.chat_title)
        title_box.addWidget(self.chat_subtitle)
        header.addLayout(title_box)
        header.addStretch(1)
        self.message_search = QLineEdit()
        self.message_search.setPlaceholderText("搜索消息")
        self.message_search.setClearButtonEnabled(True)
        self.message_search.setMaximumWidth(220)
        self.message_search.textChanged.connect(self._filter_messages)
        header.addWidget(self.message_search)
        self.room_menu_button = QToolButton()
        self.room_menu_button.setToolTip("会话操作")
        self.room_menu_button.setText("...")
        self.room_menu_button.clicked.connect(self._show_header_menu)
        header.addWidget(self.room_menu_button)
        self.export_button = QToolButton()
        self.export_button.setToolTip("导出当前会话")
        self.export_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.export_button.clicked.connect(self._export_conversation)
        header.addWidget(self.export_button)
        self.theme_button = QToolButton()
        self.theme_button.setToolTip("切换亮色 / 暗色主题")
        self.theme_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DesktopIcon))
        self.theme_button.clicked.connect(self._toggle_theme)
        header.addWidget(self.theme_button)
        layout.addLayout(header)

        self.message_list = MessageList()
        self.message_list.setObjectName("messageList")
        layout.addWidget(self.message_list, 1)

        self.typing_label = QLabel(" ")
        self.typing_label.setObjectName("typingLabel")
        self.typing_label.setFixedHeight(20)
        self.typing_label.setProperty("muted", True)
        layout.addWidget(self.typing_label)

        composer_band = QFrame()
        composer_band.setObjectName("composerBand")
        composer_layout = QVBoxLayout(composer_band)
        composer_layout.setContentsMargins(10, 9, 10, 9)
        composer_layout.setSpacing(7)
        self.composer = QPlainTextEdit()
        self.composer.setObjectName("composer")
        self.composer.setPlaceholderText("输入消息")
        self.composer.setMinimumHeight(62)
        self.composer.setMaximumHeight(112)
        self.composer.installEventFilter(self)
        self.composer.textChanged.connect(self._composer_changed)
        composer_layout.addWidget(self.composer)

        tool_row = QHBoxLayout()
        tool_row.setContentsMargins(0, 0, 0, 0)
        tool_row.setSpacing(7)
        self.emoji_button = QToolButton()
        self.emoji_button.setToolTip("插入表情")
        self.emoji_button.setText("☺")
        self.emoji_button.setFixedSize(36, 34)
        self.emoji_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.emoji_button.setMenu(self._build_emoji_menu())
        tool_row.addWidget(self.emoji_button)
        self.attach_button = QToolButton()
        self.attach_button.setToolTip("发送文件")
        self.attach_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        self.attach_button.setFixedSize(36, 34)
        self.attach_button.clicked.connect(self._upload_file)
        tool_row.addWidget(self.attach_button)
        self.transfer_status = QLabel("文件传输就绪")
        self.transfer_status.setObjectName("transferStatus")
        self.transfer_status.setProperty("muted", True)
        self.transfer_status.setMinimumWidth(0)
        self.transfer_status.setMaximumWidth(260)
        self.transfer_status.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        tool_row.addWidget(self.transfer_status, 1)
        tool_row.addStretch(1)
        self.send_button = QPushButton("发送")
        self.send_button.setProperty("primary", True)
        self.send_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowForward))
        self.send_button.setMinimumSize(88, 34)
        self.send_button.clicked.connect(self.send_message)
        tool_row.addWidget(self.send_button)
        composer_layout.addLayout(tool_row)
        layout.addWidget(composer_band)
        return area

    def _build_details_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("detailsPanel")
        panel.setMinimumWidth(230)
        panel.setMaximumWidth(340)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 14, 10, 12)
        self.details_tabs = QTabWidget()
        self.details_tabs.setDocumentMode(True)
        self.details_tabs.addTab(self._build_users_page(), "成员")
        self.details_tabs.addTab(self._build_files_page(), "文件")
        layout.addWidget(self.details_tabs)
        return panel

    def _build_users_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        self.user_list = QListWidget()
        self.user_list.setObjectName("memberList")
        self.user_list.itemDoubleClicked.connect(self._start_private_from_user)
        self.user_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.user_list.customContextMenuRequested.connect(self._show_user_menu)
        layout.addWidget(self.user_list)
        return page

    def _build_files_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        self.file_list = QListWidget()
        self.file_list.setObjectName("fileList")
        self.file_list.itemDoubleClicked.connect(self._download_file)
        self.file_list.itemSelectionChanged.connect(
            lambda: self.download_button.setEnabled(bool(self.file_list.selectedItems()))
        )
        layout.addWidget(self.file_list, 1)
        self.download_button = QPushButton("下载")
        self.download_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self._download_file)
        layout.addWidget(self.download_button)
        return page

    def _build_emoji_menu(self) -> QMenu:
        menu = QMenu(self)
        emojis = ("😀", "😂", "😊", "😍", "🤔", "👍", "👏", "🎉", "❤️", "🔥", "✅", "👋")
        for emoji in emojis:
            action = menu.addAction(emoji)
            action.triggered.connect(
                lambda _checked=False, value=emoji: self.composer.insertPlainText(value)
            )
        return menu

    def _connect_signals(self) -> None:
        signals = self.connection.signals
        signals.state_changed.connect(self._on_connection_state)
        signals.packet_received.connect(self._handle_packet)
        signals.connected.connect(self._on_connected)
        signals.disconnected.connect(self._on_disconnected)
        signals.error.connect(self._on_connection_error)
        self.message_list.fileActivated.connect(self._download_shared_metadata)
        self.file_upload_finished.connect(self._on_file_upload_finished)

    def connect_to_server(self) -> None:
        if self.connection.is_connected:
            return
        self._manual_disconnect = False
        self.connection.connect_to(self.host, self.port, self.username)

    def _manual_reconnect(self) -> None:
        self._manual_disconnect = False
        self.reconnect_timer.stop()
        self.connection.disconnect()
        QTimer.singleShot(150, self.connect_to_server)

    def _on_connection_state(self, state: str, detail: str) -> None:
        labels = {
            "connecting": "连接中",
            "securing": "TLS 验证中",
            "handshaking": "验证中",
            "connected": "在线",
            "disconnected": "离线",
            "reconnecting": "重连中",
        }
        self.connection_status.setText(labels.get(state, state))
        status = (
            "online"
            if state == "connected"
            else "busy"
            if state in {"connecting", "securing", "handshaking", "reconnecting"}
            else "offline"
        )
        self._set_connection_status(status)
        self.statusBar().showMessage(detail or labels.get(state, state), 5000)

    def _on_connected(self, hello: dict[str, Any]) -> None:
        if (
            hello.get("protocol_version") != PROTOCOL_VERSION
            or hello.get("security_version") != SECURITY_VERSION
            or hello.get("e2ee_required") is not True
        ):
            self._manual_disconnect = True
            self.connection.disconnect()
            QMessageBox.critical(
                self,
                "安全协议不兼容",
                "服务器未启用当前版本要求的端到端加密，连接已终止。",
            )
            return
        self._connected = True
        self._ever_connected = True
        self._reconnect_attempt = 0
        self.session_id = str(hello.get("session_id", self.session_id))
        self.username = str(hello.get("username", self.username))
        self.current_room_id = str(hello.get("current_room", self.current_room_id))
        self.max_file_bytes = int(hello.get("max_file_bytes", MAX_FILE_BYTES))
        self.message_list.set_identity(self.session_id)
        self.account_name.setText(self.username)
        self.connection_status.setText("在线")
        self._set_connection_status("online")
        self.heartbeat_timer.start()
        self._snapshot_ready = False
        tls_fingerprint = str(hello.get("tls_fingerprint", ""))
        self.security_status.setText("TLS 1.3 · E2EE 已启用")
        self.security_status.setToolTip(
            "\n".join(
                (
                    f"服务器 TLS 指纹：{tls_fingerprint}",
                    f"本机端到端身份指纹：{self.identity.fingerprint}",
                )
            )
        )

    def _on_disconnected(self, reason: str, expected: bool) -> None:
        self._connected = False
        self._snapshot_ready = False
        self.heartbeat_timer.stop()
        self.connection_status.setText("离线")
        self._set_connection_status("offline")
        self.chat_subtitle.setText(reason or "连接已断开")
        if self._manual_disconnect or expected or not self.auto_reconnect:
            return
        self._schedule_reconnect()

    def _on_connection_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 6000)
        if not self._ever_connected and self._reconnect_attempt == 0:
            QMessageBox.warning(self, "连接失败", message)

    def _schedule_reconnect(self) -> None:
        self._reconnect_attempt += 1
        delay = min(30, 2 ** min(self._reconnect_attempt, 5))
        self.connection_status.setText(f"{delay} 秒后重连")
        self._set_connection_status("busy")
        self.reconnect_timer.start(delay * 1000)

    def _handle_packet(self, packet: dict[str, Any]) -> None:
        packet_type = packet.get("type")
        if packet_type == "hello_ok":
            if not self._connected:
                self._on_connected(packet)
        elif packet_type == "snapshot":
            self._apply_snapshot(packet)
        elif packet_type == "history":
            self._apply_history(packet)
        elif packet_type == "message":
            self._receive_message(packet)
        elif packet_type == "file_shared":
            self._receive_file_shared(packet)
        elif packet_type == "file_data":
            self._receive_file_data(packet)
        elif packet_type == "file_deleted":
            self.files.pop(str(packet.get("file_id", "")), None)
            self._refresh_files()
        elif packet_type == "room_joined":
            self._room_joined(packet)
        elif packet_type == "room_switched":
            self._room_switched(packet)
        elif packet_type in {"room_left", "room_deleted"}:
            self._room_removed(packet)
        elif packet_type == "room_renamed":
            room_id = str(packet.get("room_id", ""))
            if room_id in self.rooms:
                self.rooms[room_id]["name"] = str(packet.get("name", self.rooms[room_id]["name"]))
                self._refresh_rooms()
        elif packet_type == "typing":
            self._show_typing(packet)
        elif packet_type == "moderation":
            self._handle_moderation(packet)
        elif packet_type == "error":
            self._handle_server_error(packet)
        elif packet_type == "kicked":
            self._manual_disconnect = True
            QMessageBox.warning(self, "连接已终止", str(packet.get("message", "你已被服务器移除。")))
        elif packet_type == "server_shutdown":
            self._manual_disconnect = True
            self.statusBar().showMessage(str(packet.get("message", "服务器已停止")), 5000)

    def _apply_snapshot(self, packet: dict[str, Any]) -> None:
        self.current_room_id = str(packet.get("current_room", self.current_room_id))
        self.rooms = {str(room["id"]): room for room in packet.get("rooms", [])}
        self.users = {}
        for raw_user in packet.get("users", []):
            if not isinstance(raw_user, dict) or not raw_user.get("id"):
                continue
            user = dict(raw_user)
            self._remember_peer_key(user)
            self.users[str(user["id"])] = user
        self.files = {}
        for raw_file in packet.get("files", []):
            if not isinstance(raw_file, dict):
                continue
            metadata = self._decrypt_file_metadata(raw_file)
            if metadata is not None and metadata.get("id"):
                self.files[str(metadata["id"])] = metadata
        room = self.rooms.get(self.current_room_id)
        if room:
            self.current_room_name = str(room.get("name", self.current_room_name))
        self._refresh_rooms()
        self._refresh_users()
        self._refresh_files()
        if not self.private_target:
            self._update_conversation_header()
        self._update_composer_state()
        self._snapshot_ready = True
        self._flush_pending_messages()

    def _apply_history(self, packet: dict[str, Any]) -> None:
        scope = packet.get("scope")
        if scope == "room":
            if self.private_target or str(packet.get("room_id")) != self.current_room_id:
                return
        elif scope == "private":
            target = str(packet.get("with", ""))
            if not self.private_target or target.casefold() != self.private_target.casefold():
                return
        decoded_messages: list[dict[str, Any]] = []
        for raw_message in packet.get("messages", []):
            if not isinstance(raw_message, dict):
                continue
            if raw_message.get("type") == "message":
                decoded_messages.append(self._decrypt_message_packet(raw_message))
            elif raw_message.get("type") == "file_shared":
                decoded_messages.append(self._decrypt_file_event(raw_message))
        self.message_list.set_messages(decoded_messages)
        self._filter_messages(self.message_search.text())

    def _receive_message(self, packet: dict[str, Any]) -> None:
        decoded = self._decrypt_message_packet(packet)
        self._alert_for_packet(decoded)
        if decoded.get("scope") == "private":
            sender = str(decoded.get("sender", ""))
            target = str(decoded.get("to", ""))
            other = target if str(decoded.get("sender_id")) == self.session_id else sender
            if other and other != self.username:
                self.private_contacts.add(other)
            visible = bool(self.private_target and other.casefold() == self.private_target.casefold())
            if visible:
                self.message_list.upsert_message(decoded)
            else:
                key = f"private:{other.casefold()}"
                self.unread[key] = self.unread.get(key, 0) + 1
                self.statusBar().showMessage(f"来自 {other} 的新消息", 4000)
            self._refresh_private_contacts()
            return

        room_id = str(decoded.get("room_id", ""))
        visible = not self.private_target and room_id == self.current_room_id
        if visible:
            self.message_list.upsert_message(decoded)
        else:
            self.unread[room_id] = self.unread.get(room_id, 0) + 1
            self._refresh_rooms()

    def _receive_file_shared(self, packet: dict[str, Any]) -> None:
        decoded = self._decrypt_file_event(packet)
        metadata = decoded.get("file") or {}
        if isinstance(metadata, dict) and metadata.get("id"):
            self.files[str(metadata["id"])] = metadata
            self._refresh_files()
            name = str(metadata.get("name", ""))
            own_upload = str(metadata.get("sender_id", "")) == self.session_id
            if own_upload and name in self._pending_uploads:
                self._pending_uploads.discard(name)
                self._set_transfer_status(f"已发送：{name}", "success")
        room_id = str(decoded.get("room_id", ""))
        if not self.private_target and room_id == self.current_room_id:
            self.message_list.append_message(decoded)
        self._alert_for_packet(decoded)

    def _receive_file_data(self, packet: dict[str, Any]) -> None:
        raw_metadata = packet.get("file") or {}
        file_id = str(raw_metadata.get("id", "")) if isinstance(raw_metadata, dict) else ""
        destination = self.pending_downloads.pop(file_id, None)
        if destination is None:
            return
        try:
            if not isinstance(raw_metadata, dict):
                raise SecureProtocolError("下载响应缺少加密文件元数据")
            metadata = self._decrypt_file_metadata(raw_metadata)
            if metadata is None:
                raise SecureProtocolError("无法验证文件发送者身份")
            envelope = raw_metadata.get("envelope")
            if not isinstance(envelope, dict):
                raise SecureProtocolError("下载响应缺少加密文件清单")
            sender_key = self._trusted_sender_key(raw_metadata)
            ciphertext = decode_file_ciphertext(packet.get("data"))
            validate_file_envelope(
                envelope,
                sender_key,
                room_id=str(raw_metadata.get("room_id", "")),
                ciphertext=ciphertext,
            )
            decrypted = decrypt_file(
                ciphertext,
                envelope,
                self.identity.private_key,
                sender_key,
            )
            destination.write_bytes(decrypted.data)
        except (OSError, SecurityError, SecureProtocolError, TypeError, ValueError) as exc:
            name = (
                str(raw_metadata.get("name", "文件"))
                if isinstance(raw_metadata, dict)
                else "文件"
            )
            self._set_transfer_status(f"下载失败：{name}", "danger")
            QMessageBox.critical(self, "下载失败", str(exc))
            return
        self._set_transfer_status(f"已下载：{metadata.get('name', destination.name)}", "success")
        self.statusBar().showMessage(f"文件已保存到 {destination}", 5000)

    def _room_joined(self, packet: dict[str, Any]) -> None:
        room = packet.get("room") or {}
        if not isinstance(room, dict) or not room.get("id"):
            return
        self.rooms[str(room["id"])] = room
        self.private_target = None
        self.current_room_id = str(room["id"])
        self.current_room_name = str(room.get("name", self.current_room_id))
        self.message_list.clear()
        self._refresh_rooms()
        self._update_conversation_header()
        invite = packet.get("invite_code")
        if invite:
            QApplication.clipboard().setText(str(invite))
            QMessageBox.information(self, "群组已创建", f"邀请码 {invite} 已复制到剪贴板。")

    def _room_switched(self, packet: dict[str, Any]) -> None:
        room = packet.get("room") or {}
        self.private_target = None
        self.current_room_id = str(room.get("id", self.current_room_id))
        self.current_room_name = str(room.get("name", self.current_room_name))
        self.unread.pop(self.current_room_id, None)
        self.message_list.clear()
        self._refresh_rooms()
        self._update_conversation_header()

    def _room_removed(self, packet: dict[str, Any]) -> None:
        removed = str(packet.get("room_id", ""))
        self.rooms.pop(removed, None)
        self.private_target = None
        self.current_room_id = str(packet.get("current_room", MAIN_ROOM_ID))
        self.current_room_name = str(self.rooms.get(self.current_room_id, {}).get("name", MAIN_ROOM_NAME))
        self.message_list.clear()
        self._refresh_rooms()
        self._update_conversation_header()

    def _show_typing(self, packet: dict[str, Any]) -> None:
        if self.private_target or str(packet.get("room_id")) != self.current_room_id:
            return
        self.typing_label.setText(
            f"{packet.get('username')} 正在输入..." if packet.get("active") else " "
        )

    def _handle_moderation(self, packet: dict[str, Any]) -> None:
        action = str(packet.get("action", ""))
        labels = {
            "muted": "你已被禁言。",
            "mute": "你已被禁言。",
            "unmuted": "你的禁言已解除。",
            "unmute": "你的禁言已解除。",
            "kick": "你已被移出群组。",
            "promote": "你已成为群组管理员。",
            "demote": "你的管理员身份已移除。",
        }
        if action in labels:
            self.statusBar().showMessage(labels[action], 5000)

    def _handle_server_error(self, packet: dict[str, Any]) -> None:
        code = str(packet.get("code", "error"))
        message = str(packet.get("message", "Unknown server error"))
        critical = {
            "duplicate_username",
            "banned",
            "invalid_username",
            "hello_timeout",
            "incompatible_protocol",
            "incompatible_security",
            "invalid_identity",
        }
        if code in {
            "invalid_file",
            "file_storage_error",
            "replayed_file",
        } and self._pending_uploads:
            self._pending_uploads.clear()
            self._set_transfer_status("文件发送被服务器拒绝", "danger")
        if code in critical:
            QMessageBox.warning(self, "服务器拒绝连接", message)
        else:
            self.statusBar().showMessage(message, 6000)

    def send_message(self) -> None:
        text = self.composer.toPlainText().strip()
        if not text:
            return
        if len(text) > MAX_MESSAGE_CHARS:
            QMessageBox.warning(
                self,
                "消息过长",
                f"单条消息不能超过 {MAX_MESSAGE_CHARS} 个字符。",
            )
            return
        message_id = uuid.uuid4().hex
        timestamp = time.time()
        if self.private_target:
            packet = {
                "type": "private_message",
                "to": self.private_target,
                "text": text,
                "id": message_id,
                "timestamp": timestamp,
            }
            display = {
                "type": "message",
                "id": message_id,
                "scope": "private",
                "sender": self.username,
                "sender_id": self.session_id or "pending-self",
                "to": self.private_target,
                "text": text,
                "kind": "pending",
                "timestamp": timestamp,
            }
        else:
            packet = {
                "type": "message",
                "room_id": self.current_room_id,
                "text": text,
                "id": message_id,
                "timestamp": timestamp,
            }
            display = {
                "type": "message",
                "id": message_id,
                "scope": "room",
                "room_id": self.current_room_id,
                "sender": self.username,
                "sender_id": self.session_id or "pending-self",
                "text": text,
                "kind": "pending",
                "timestamp": timestamp,
            }
        sent = (
            self.connection.is_connected
            and self._snapshot_ready
            and self._send_encrypted_message(packet)
        )
        if not sent:
            self.pending_packets.append(packet)
            self.message_list.append_message(display)
            message = (
                "等待安全密钥同步后发送"
                if self.connection.is_connected
                else "消息已加入离线队列"
            )
            self.statusBar().showMessage(message, 3000)
        self.composer.clear()
        self._stop_typing()

    def _send_encrypted_message(self, draft: dict[str, Any]) -> bool:
        try:
            if draft.get("type") == "private_message":
                target = str(draft.get("to", ""))
                recipients = self._private_recipient_keys(target)
                envelope = message_envelope(
                    self.identity,
                    recipients,
                    str(draft.get("text", "")),
                    scope="private",
                    target=target,
                    message_id=str(draft.get("id", "")),
                    timestamp=float(draft.get("timestamp", time.time())),
                )
                wire_packet = {
                    "type": "private_message",
                    "to": target,
                    "envelope": envelope,
                }
            else:
                room_id = str(draft.get("room_id", ""))
                recipients = self._room_recipient_keys(room_id)
                envelope = message_envelope(
                    self.identity,
                    recipients,
                    str(draft.get("text", "")),
                    scope="room",
                    room_id=room_id,
                    message_id=str(draft.get("id", "")),
                    timestamp=float(draft.get("timestamp", time.time())),
                )
                wire_packet = {
                    "type": "message",
                    "room_id": room_id,
                    "envelope": envelope,
                }
        except (SecurityError, SecureProtocolError, TypeError, ValueError) as exc:
            self.statusBar().showMessage(f"无法加密消息：{exc}", 6000)
            return False
        return self.connection.send(wire_packet)

    def _flush_pending_messages(self) -> None:
        if not self.connection.is_connected or not self._snapshot_ready:
            return
        queued, remaining = self.pending_packets, []
        sent_count = 0
        for draft in queued:
            if self._send_encrypted_message(draft):
                sent_count += 1
            else:
                remaining.append(draft)
        self.pending_packets = remaining
        if sent_count:
            self.statusBar().showMessage(f"已安全发送 {sent_count} 条离线消息", 4000)

    def _remember_peer_key(self, user: dict[str, Any]) -> None:
        username = str(user.get("username", "")).strip()
        if not username:
            user["key_trusted"] = False
            return
        peer_id = username.casefold()
        try:
            public_key, fingerprint = load_identity_fields(user)
            is_self = (
                str(user.get("id", "")) == self.session_id
                or peer_id == self.username.casefold()
            )
            if is_self:
                if fingerprint != self.identity.fingerprint:
                    raise SecureProtocolError("服务器返回的本机身份密钥不匹配")
            else:
                self.peer_pin_store.verify(peer_id, public_key)
            self.peer_keys[peer_id] = public_key
            user["key_trusted"] = True
        except (SecurityError, SecureProtocolError, TypeError, ValueError) as exc:
            self.peer_keys.pop(peer_id, None)
            user["key_trusted"] = False
            user["key_error"] = str(exc)
            self._warn_security_once(f"peer:{peer_id}", f"{username} 的身份密钥异常：{exc}")

    def _trusted_sender_key(self, packet: dict[str, Any]) -> rsa.RSAPublicKey:
        username = str(packet.get("sender", "")).strip()
        if not username:
            raise SecureProtocolError("加密数据缺少发送者名称")
        public_key, fingerprint = load_identity_fields(packet, sender=True)
        is_self = (
            str(packet.get("sender_id", "")) == self.session_id
            or username.casefold() == self.username.casefold()
        )
        if is_self:
            if fingerprint != self.identity.fingerprint:
                raise SecureProtocolError("服务器返回的本机签名密钥不匹配")
        else:
            try:
                self.peer_pin_store.verify(username.casefold(), public_key)
            except SecurityError:
                self.peer_keys.pop(username.casefold(), None)
                raise
        self.peer_keys[username.casefold()] = public_key
        return public_key

    def _room_recipient_keys(self, room_id: str) -> list[rsa.RSAPublicKey]:
        if room_id != self.current_room_id or self.private_target:
            raise SecureProtocolError("需要先切换到该群组并同步成员密钥")
        recipients: dict[str, rsa.RSAPublicKey] = {
            self.identity.fingerprint: self.identity.public_key
        }
        if not self.users:
            raise SecureProtocolError("群组成员密钥尚未同步")
        for user in self.users.values():
            username = str(user.get("username", "")).strip()
            if not username or not user.get("key_trusted"):
                raise SecureProtocolError(f"{username or '成员'} 的身份密钥不可用")
            key = self.peer_keys.get(username.casefold())
            if key is None:
                raise SecureProtocolError(f"{username} 的身份密钥尚未同步")
            fingerprint = str(user.get("identity_fingerprint", "")).lower()
            recipients[fingerprint] = key
        return list(recipients.values())

    def _private_recipient_keys(self, target: str) -> list[rsa.RSAPublicKey]:
        normalized = str(target).strip().casefold()
        if not normalized or normalized == self.username.casefold():
            raise SecureProtocolError("私聊目标无效")
        target_key = self.peer_keys.get(normalized)
        if target_key is None:
            raise SecureProtocolError("对方身份密钥尚未同步或已被阻止")
        return [self.identity.public_key, target_key]

    def _decrypt_message_packet(self, packet: dict[str, Any]) -> dict[str, Any]:
        if (
            str(packet.get("kind", "")).lower() == "system"
            or str(packet.get("sender_id", "")).lower() in {"system", "server"}
        ):
            return dict(packet)
        try:
            envelope = packet.get("envelope")
            if not isinstance(envelope, dict):
                raise SecureProtocolError("用户消息不是端到端加密格式")
            sender_key = self._trusted_sender_key(packet)
            scope = str(packet.get("scope", ""))
            if scope == "private":
                metadata = validate_message_envelope(
                    envelope,
                    sender_key,
                    scope="private",
                    target=str(packet.get("to", "")),
                )
            else:
                metadata = validate_message_envelope(
                    envelope,
                    sender_key,
                    scope="room",
                    room_id=str(packet.get("room_id", "")),
                )
            if str(packet.get("id", "")) != str(metadata["id"]):
                raise SecureProtocolError("消息 ID 与签名内容不匹配")
            plaintext = decrypt_message_text(
                envelope,
                self.identity.private_key,
                sender_key,
            )
            decoded = dict(packet)
            decoded["text"] = plaintext
            decoded["timestamp"] = float(metadata["timestamp"])
            decoded["encrypted"] = True
            return decoded
        except (SecurityError, SecureProtocolError, TypeError, ValueError) as exc:
            sender = str(packet.get("sender", "未知用户"))
            self._warn_security_once(
                f"message:{packet.get('id', '')}:{sender}",
                f"已阻止一条无法验证的消息：{exc}",
            )
            return self._security_placeholder(packet, "一条消息未通过端到端验证，已阻止显示。")

    def _decrypt_file_metadata(
        self, metadata: dict[str, Any]
    ) -> dict[str, Any] | None:
        try:
            envelope = metadata.get("envelope")
            if not isinstance(envelope, dict):
                raise SecureProtocolError("文件缺少加密清单")
            sender_key = self._trusted_sender_key(metadata)
            room_id = str(metadata.get("room_id", ""))
            unsigned = validate_file_envelope(
                envelope,
                sender_key,
                room_id=room_id,
            )
            file_id = str(metadata.get("id") or metadata.get("file_id") or "")
            if file_id != str(unsigned["file_id"]):
                raise SecureProtocolError("文件 ID 与签名内容不匹配")
            manifest = decrypt_file_manifest(
                envelope,
                self.identity.private_key,
                sender_key,
            )
            decoded = dict(metadata)
            decoded["id"] = file_id
            decoded["name"] = manifest.filename
            decoded["size"] = manifest.size
            decoded["media_type"] = manifest.media_type
            decoded["sha256"] = manifest.sha256
            decoded["encrypted"] = True
            return decoded
        except (PinMismatchError, SecurityError, SecureProtocolError, TypeError, ValueError) as exc:
            file_id = str(metadata.get("id") or metadata.get("file_id") or "")
            self._warn_security_once(
                f"file:{file_id}",
                f"已阻止一个无法验证的文件：{exc}",
            )
            return None

    def _decrypt_file_event(self, packet: dict[str, Any]) -> dict[str, Any]:
        raw_metadata = packet.get("file")
        if not isinstance(raw_metadata, dict):
            return self._security_placeholder(packet, "文件通知缺少加密元数据。")
        metadata = self._decrypt_file_metadata(raw_metadata)
        if metadata is None:
            return self._security_placeholder(
                packet,
                "一个文件未通过端到端验证，已阻止下载。",
            )
        decoded = dict(packet)
        decoded["file"] = metadata
        decoded["sender_id"] = str(
            packet.get("sender_id") or metadata.get("sender_id") or ""
        )
        return decoded

    @staticmethod
    def _security_placeholder(
        packet: dict[str, Any], message: str
    ) -> dict[str, Any]:
        try:
            timestamp = float(packet.get("timestamp", time.time()))
            if not math.isfinite(timestamp):
                raise ValueError("timestamp is not finite")
        except (TypeError, ValueError):
            timestamp = time.time()
        return {
            "type": "message",
            "id": str(packet.get("id", "")),
            "scope": str(packet.get("scope", "room")),
            "room_id": str(packet.get("room_id", "")),
            "to": str(packet.get("to", "")),
            "sender": "安全验证",
            "sender_id": str(packet.get("sender_id", "")),
            "kind": "system",
            "text": message,
            "timestamp": timestamp,
        }

    def _warn_security_once(self, key: str, message: str) -> None:
        if key in self._peer_key_warnings:
            return
        self._peer_key_warnings.add(key)
        self.statusBar().showMessage(message, 8000)

    def _send_or_warn(self, packet: dict[str, Any]) -> bool:
        if not self.connection.is_connected:
            self.statusBar().showMessage("当前未连接服务器", 3500)
            return False
        return self.connection.send(packet)

    def _create_room(self) -> None:
        name, accepted = QInputDialog.getText(self, "创建群组", "群组名称")
        if accepted and name.strip():
            self._send_or_warn({"type": "create_room", "name": name.strip()})

    def _join_room(self) -> None:
        code, accepted = QInputDialog.getText(self, "加入群组", "邀请码")
        if accepted and code.strip():
            self._send_or_warn({"type": "join_room", "invite_code": code.strip().upper()})

    def _leave_current_room(self) -> None:
        if self.private_target or self.current_room_id == MAIN_ROOM_ID:
            return
        answer = QMessageBox.question(self, "退出群组", f"退出“{self.current_room_name}”？")
        if answer == QMessageBox.StandardButton.Yes:
            self._send_or_warn({"type": "leave_room", "room_id": self.current_room_id})

    def _activate_room_item(self, item: QListWidgetItem) -> None:
        room_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not room_id:
            return
        self.private_target = None
        self.current_room_id = room_id
        self.current_room_name = str(self.rooms.get(room_id, {}).get("name", room_id))
        self.unread.pop(room_id, None)
        self.message_list.clear()
        self._update_conversation_header()
        self._refresh_rooms()
        self._send_or_warn({"type": "switch_room", "room_id": room_id})

    def _activate_private_item(self, item: QListWidgetItem) -> None:
        target = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if target:
            self._activate_private(target)

    def _start_private_from_user(self, item: QListWidgetItem) -> None:
        user = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(user, dict) and str(user.get("id")) != self.session_id:
            self._activate_private(str(user.get("username", "")))

    def _activate_private(self, target: str) -> None:
        if not target or target.casefold() == self.username.casefold():
            return
        self.private_contacts.add(target)
        self.private_target = target
        self.unread.pop(f"private:{target.casefold()}", None)
        self.message_list.clear()
        self._refresh_private_contacts()
        self._update_conversation_header()
        self._send_or_warn({"type": "private_history", "with": target})

    def _refresh_rooms(self) -> None:
        selected = self.current_room_id if not self.private_target else None
        query = self.conversation_search.text().strip().casefold()
        self.room_list.blockSignals(True)
        self.room_list.clear()
        rooms = sorted(self.rooms.values(), key=lambda room: (room.get("id") != MAIN_ROOM_ID, str(room.get("name", "")).casefold()))
        for room in rooms:
            name = str(room.get("name", room.get("id", "")))
            if query and query not in name.casefold():
                continue
            unread = self.unread.get(str(room["id"]), 0)
            text = f"{name}   {unread}" if unread else name
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, room["id"])
            item.setToolTip(f"{room.get('member_count', 0)} 位成员")
            self.room_list.addItem(item)
            if selected == room["id"]:
                self.room_list.setCurrentItem(item)
        self.room_list.blockSignals(False)

    def _refresh_private_contacts(self) -> None:
        selected = self.private_target
        query = self.conversation_search.text().strip().casefold()
        self.private_list.blockSignals(True)
        self.private_list.clear()
        for target in sorted(self.private_contacts, key=str.casefold):
            if query and query not in target.casefold():
                continue
            unread = self.unread.get(f"private:{target.casefold()}", 0)
            item = QListWidgetItem(f"{target}   {unread}" if unread else target)
            item.setData(Qt.ItemDataRole.UserRole, target)
            self.private_list.addItem(item)
            if selected and selected.casefold() == target.casefold():
                self.private_list.setCurrentItem(item)
        self.private_list.blockSignals(False)

    def _refresh_users(self) -> None:
        self.user_list.clear()
        for user in sorted(self.users.values(), key=lambda item: str(item.get("username", "")).casefold()):
            role = str(user.get("role", "member"))
            suffix = {"owner": "群主", "admin": "管理员"}.get(role, "")
            muted = " · 已禁言" if user.get("muted") else ""
            key_state = " · 密钥异常" if not user.get("key_trusted", False) else ""
            label = str(user.get("username", ""))
            if suffix or muted or key_state:
                label += f"  {suffix}{muted}{key_state}"
            item = QListWidgetItem(label)
            if user.get("key_error"):
                item.setToolTip(str(user["key_error"]))
            item.setData(Qt.ItemDataRole.UserRole, user)
            self.user_list.addItem(item)

    def _refresh_files(self) -> None:
        self.file_list.clear()
        ordered = sorted(self.files.values(), key=lambda item: float(item.get("uploaded_at", 0)), reverse=True)
        for metadata in ordered:
            item = QListWidgetItem(
                f"{metadata.get('name', 'file')}\n{self._format_size(int(metadata.get('size', 0)))} · {metadata.get('sender', '')}"
            )
            item.setData(Qt.ItemDataRole.UserRole, metadata)
            self.file_list.addItem(item)
        self.download_button.setEnabled(False)

    def _update_conversation_header(self) -> None:
        if self.private_target:
            self.chat_title.setText(self.private_target)
            online = any(
                str(user.get("username", "")).casefold() == self.private_target.casefold()
                for user in self.users.values()
            )
            self.chat_subtitle.setText("在线" if online else "当前不在此群组成员列表中")
        else:
            room = self.rooms.get(self.current_room_id, {})
            self.chat_title.setText(str(room.get("name", self.current_room_name)))
            role = {"owner": "群主", "admin": "管理员", "member": "成员"}.get(str(room.get("role", "member")), "成员")
            self.chat_subtitle.setText(f"{room.get('member_count', len(self.users))} 位成员 · {role}")
        self._update_composer_state()

    def _update_composer_state(self) -> None:
        room = self.rooms.get(self.current_room_id, {})
        muted = bool(room.get("muted")) and not self.private_target
        self.composer.setEnabled(not muted)
        self.send_button.setEnabled(not muted)
        self.composer.setPlaceholderText("你已被禁言" if muted else "输入消息")

    def _filter_conversations(self, _query: str) -> None:
        self._refresh_rooms()
        self._refresh_private_contacts()

    def _filter_messages(self, query: str) -> None:
        self.message_list.filter_messages(query)

    def _show_room_menu(self, position) -> None:
        item = self.room_list.itemAt(position)
        if item:
            self.room_list.setCurrentItem(item)
        room_id = str(item.data(Qt.ItemDataRole.UserRole)) if item else self.current_room_id
        room = self.rooms.get(room_id, {})
        menu = QMenu(self)
        copy_invite = menu.addAction("复制邀请码")
        copy_invite.setEnabled(bool(room.get("invite_code")))
        leave = menu.addAction("退出群组")
        leave.setEnabled(room_id != MAIN_ROOM_ID)
        chosen = menu.exec(self.room_list.viewport().mapToGlobal(position))
        if chosen == copy_invite:
            QApplication.clipboard().setText(str(room.get("invite_code")))
        elif chosen == leave:
            if room_id != self.current_room_id:
                self.current_room_id = room_id
                self.current_room_name = str(room.get("name", room_id))
            self._leave_current_room()

    def _show_header_menu(self) -> None:
        menu = QMenu(self)
        if not self.private_target:
            room = self.rooms.get(self.current_room_id, {})
            copy_invite = menu.addAction("复制邀请码")
            copy_invite.setEnabled(bool(room.get("invite_code")))
            leave = menu.addAction("退出群组")
            leave.setEnabled(self.current_room_id != MAIN_ROOM_ID)
        else:
            copy_invite = None
            leave = None
        auto_reconnect = menu.addAction("自动重连")
        auto_reconnect.setCheckable(True)
        auto_reconnect.setChecked(self.auto_reconnect)
        chosen = menu.exec(self.room_menu_button.mapToGlobal(self.room_menu_button.rect().bottomLeft()))
        if chosen == copy_invite:
            QApplication.clipboard().setText(str(self.rooms[self.current_room_id].get("invite_code")))
        elif chosen == leave:
            self._leave_current_room()
        elif chosen == auto_reconnect:
            self.auto_reconnect = auto_reconnect.isChecked()
            self.settings.setValue("client/auto_reconnect", self.auto_reconnect)

    def _show_user_menu(self, position) -> None:
        item = self.user_list.itemAt(position)
        if not item:
            return
        self.user_list.setCurrentItem(item)
        target = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(target, dict):
            return
        is_self = str(target.get("id")) == self.session_id
        menu = QMenu(self)
        private_action = menu.addAction("发起私聊")
        private_action.setEnabled(not is_self)
        room = self.rooms.get(self.current_room_id, {})
        own_role = str(room.get("role", "member"))
        target_role = str(target.get("role", "member"))
        actions: dict[QAction, str] = {}
        if not self.private_target and self.current_room_id != MAIN_ROOM_ID and own_role in {"owner", "admin"} and not is_self:
            menu.addSeparator()
            if target.get("muted"):
                actions[menu.addAction("解除禁言")] = "unmute"
            else:
                actions[menu.addAction("禁言")] = "mute"
            actions[menu.addAction("移出群组")] = "kick"
            if own_role == "owner" and target_role != "owner":
                actions[menu.addAction("取消管理员" if target_role == "admin" else "设为管理员")] = (
                    "demote" if target_role == "admin" else "promote"
                )
        chosen = menu.exec(self.user_list.viewport().mapToGlobal(position))
        if chosen == private_action:
            self._activate_private(str(target.get("username", "")))
        elif chosen in actions:
            self._send_or_warn(
                {
                    "type": "room_action",
                    "room_id": self.current_room_id,
                    "target_id": target["id"],
                    "action": actions[chosen],
                }
            )

    def _upload_file(self) -> None:
        if self.private_target:
            self._set_transfer_status("私聊暂不支持文件", "warning")
            self.statusBar().showMessage("文件目前只能发送到群组", 3500)
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择文件")
        if not path:
            return
        source = Path(path)
        self._set_transfer_status(f"已选择：{source.name}", "info")
        try:
            size = source.stat().st_size
        except OSError as exc:
            self._set_transfer_status(f"无法读取：{source.name}", "danger")
            QMessageBox.critical(self, "无法读取文件", str(exc))
            return
        if size > self.max_file_bytes:
            self._set_transfer_status(f"文件过大：{source.name}", "danger")
            QMessageBox.warning(self, "文件过大", f"单个文件不能超过 {self._format_size(self.max_file_bytes)}。")
            return
        if not self.connection.is_connected:
            self._set_transfer_status("离线，无法发送文件", "warning")
            self.statusBar().showMessage("离线时无法发送文件", 3500)
            return
        room_id = self.current_room_id
        try:
            recipients = self._room_recipient_keys(room_id)
        except (SecurityError, SecureProtocolError, TypeError, ValueError) as exc:
            self._set_transfer_status("成员密钥尚未就绪", "danger")
            self.statusBar().showMessage(f"无法加密文件：{exc}", 6000)
            return
        uploaded_at = time.time()
        media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        self._pending_uploads.add(source.name)
        self._set_transfer_status(f"发送中：{source.name}", "info")
        self.statusBar().showMessage(f"正在发送 {source.name}")

        def worker() -> None:
            try:
                data = source.read_bytes()
                if len(data) > self.max_file_bytes:
                    raise ValueError(
                        f"文件读取后超过 {self._format_size(self.max_file_bytes)}"
                    )
                encrypted = encrypt_file(
                    data,
                    source.name,
                    self.identity.private_key,
                    recipients,
                    media_type=media_type,
                    authenticated_metadata={
                        "room_id": room_id,
                        "timestamp": uploaded_at,
                    },
                )
                encoded_data = encode_file_ciphertext(encrypted.ciphertext)
            except (OSError, SecurityError, SecureProtocolError, ValueError) as exc:
                self.file_upload_finished.emit(source.name, False, str(exc))
                return
            sent = self.connection.send(
                {
                    "type": "upload_file",
                    "room_id": room_id,
                    "envelope": encrypted.envelope,
                    "data": encoded_data,
                }
            )
            self.file_upload_finished.emit(
                source.name,
                bool(sent),
                "" if sent else "连接已断开",
            )

        threading.Thread(target=worker, name="chat-file-upload", daemon=True).start()

    def _download_file(self, *_args) -> None:
        item = self.file_list.currentItem()
        metadata = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(metadata, dict):
            return
        self._download_shared_metadata(metadata)

    def _download_shared_metadata(self, metadata: dict[str, Any]) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "保存文件", str(metadata.get("name", "file")))
        if not path:
            return
        file_id = str(metadata.get("id") or metadata.get("file_id") or "")
        if not file_id:
            self._set_transfer_status("文件标识无效", "danger")
            self.statusBar().showMessage("无法下载：文件标识无效", 4000)
            return
        self.pending_downloads[file_id] = Path(path)
        self._set_transfer_status(f"下载中：{metadata.get('name', '文件')}", "info")
        if not self._send_or_warn({"type": "download_file", "file_id": file_id}):
            self.pending_downloads.pop(file_id, None)
            self._set_transfer_status(f"下载失败：{metadata.get('name', '文件')}", "danger")

    def _on_file_upload_finished(self, name: str, sent: bool, error: str) -> None:
        if sent:
            self._set_transfer_status(f"等待服务器确认：{name}", "info")
            return
        self._pending_uploads.discard(name)
        self._set_transfer_status(f"发送失败：{name}", "danger")
        self.statusBar().showMessage(error or "文件发送失败", 5000)

    def _set_transfer_status(self, text: str, tone: str = "neutral") -> None:
        self.transfer_status.setText(text)
        self.transfer_status.setToolTip(text)
        self.transfer_status.setProperty("tone", tone)
        self.transfer_status.style().unpolish(self.transfer_status)
        self.transfer_status.style().polish(self.transfer_status)

    def _alert_for_packet(self, packet: dict[str, Any]) -> None:
        metadata = packet.get("file")
        nested_sender_id = metadata.get("sender_id") if isinstance(metadata, dict) else ""
        sender_id = str(packet.get("sender_id") or nested_sender_id or "")
        if self.session_id and sender_id == self.session_id:
            return
        if self.isActiveWindow() and not self.isMinimized():
            return
        now = time.monotonic()
        if now - self._last_attention_at < self._attention_interval:
            return
        self._last_attention_at = now
        self._request_attention()

    def _request_attention(self) -> None:
        QApplication.alert(self, 1500)

    def _export_conversation(self) -> None:
        default_name = f"{self.private_target or self.current_room_name}-{time.strftime('%Y%m%d')}.json"
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出会话",
            default_name,
            "JSON (*.json);;文本 (*.txt)",
        )
        if not path:
            return
        packets = self.message_list.packets()
        try:
            if path.lower().endswith(".txt") or selected_filter.startswith("文本"):
                lines = []
                for packet in packets:
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(packet.get("timestamp", 0))))
                    sender = packet.get("sender", "System")
                    text = packet.get("text") or (packet.get("file") or {}).get("name", "")
                    lines.append(f"[{timestamp}] {sender}: {text}")
                Path(path).write_text("\n".join(lines), encoding="utf-8")
            else:
                Path(path).write_text(json.dumps(packets, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.statusBar().showMessage(f"会话已导出到 {path}", 5000)

    def _composer_changed(self) -> None:
        if not self.connection.is_connected or self.private_target:
            return
        active = bool(self.composer.toPlainText().strip())
        if active and not self._typing_active:
            self._typing_active = True
            self.connection.send({"type": "typing", "room_id": self.current_room_id, "active": True})
        self.typing_timer.start()

    def _stop_typing(self) -> None:
        if self._typing_active:
            self._typing_active = False
            if self.connection.is_connected and not self.private_target:
                self.connection.send({"type": "typing", "room_id": self.current_room_id, "active": False})

    def _toggle_theme(self) -> None:
        self.theme = opposite_theme(self.theme)
        apply_theme(QApplication.instance(), self.theme)
        self.settings.setValue("appearance/theme", self.theme)

    def _set_connection_status(self, status: str) -> None:
        self.connection_status.setProperty("status", status)
        self.connection_status.style().unpolish(self.connection_status)
        self.connection_status.style().polish(self.connection_status)

    def eventFilter(self, watched, event: QEvent) -> bool:
        if watched is self.composer and event.type() == QEvent.Type.KeyPress:
            key_event = event
            if isinstance(key_event, QKeyEvent) and key_event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
                if not key_event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.send_message()
                    return True
        return super().eventFilter(watched, event)

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"

    def _setting_bool(self, key: str, default: bool) -> bool:
        value = self.settings.value(key, default)
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _restore_window_state(self) -> None:
        geometry = self.settings.value("client/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        splitter_state = self.settings.value("client/splitter")
        if splitter_state:
            self.main_splitter.restoreState(splitter_state)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._manual_disconnect = True
        self.reconnect_timer.stop()
        self.heartbeat_timer.stop()
        self.settings.setValue("client/geometry", self.saveGeometry())
        self.settings.setValue("client/splitter", self.main_splitter.saveState())
        self.settings.setValue("client/host", self.host)
        self.settings.setValue("client/port", self.port)
        self.settings.setValue("client/username", self.username)
        self.connection.disconnect()
        event.accept()
