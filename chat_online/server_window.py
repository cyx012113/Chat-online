"""PyQt6 server administration window."""

from __future__ import annotations

import html
import time
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QSettings, Qt, QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyle,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .server import MAIN_ROOM_ID, ChatServerEngine
from .storage import AppStorage
from .theme import apply_theme, opposite_theme


class MetricWidget(QWidget):
    def __init__(self, label: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(0)
        self.value_label = QLabel("0")
        self.value_label.setObjectName("metricValue")
        caption = QLabel(label)
        caption.setProperty("muted", True)
        layout.addWidget(self.value_label)
        layout.addWidget(caption)

    def set_value(self, value: str | int) -> None:
        self.value_label.setText(str(value))


class ServerWindow(QMainWindow):
    """Desktop console for the embedded chat server."""

    def __init__(
        self,
        port: int = 8888,
        host: str = "0.0.0.0",
        theme: str = "light",
        storage: AppStorage | None = None,
        auto_start: bool = True,
    ) -> None:
        super().__init__()
        self.setObjectName("serverWindow")
        self.setWindowTitle("Chat Online - 服务器控制台")
        self.setMinimumSize(900, 600)
        self.resize(1180, 760)
        self.settings = QSettings()
        self.theme = theme
        self.bind_host = host
        self.engine = ChatServerEngine(storage)
        self.auto_start = auto_start
        self._started_once = False
        self._last_snapshot: dict[str, Any] = {}

        self._build_ui(port)
        self._connect_signals()
        self._restore_window_state()
        self.uptime_timer = QTimer(self)
        self.uptime_timer.setInterval(1000)
        self.uptime_timer.timeout.connect(self._update_uptime)
        self.uptime_timer.start()

    def _build_ui(self, port: int) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        brand = QLabel("Chat Online")
        brand.setObjectName("brandTitle")
        caption = QLabel("服务器控制台")
        caption.setProperty("muted", True)
        title_box.addWidget(brand)
        title_box.addWidget(caption)
        header.addLayout(title_box)
        header.addSpacing(20)
        self.status_label = QLabel("已停止")
        self.status_label.setObjectName("statusPill")
        self.status_label.setProperty("status", "offline")
        header.addWidget(self.status_label)
        self.endpoint_label = QLabel("0.0.0.0")
        self.endpoint_label.setProperty("muted", True)
        header.addWidget(self.endpoint_label)
        header.addStretch(1)

        port_label = QLabel("端口")
        port_label.setProperty("muted", True)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(0, 65535)
        self.port_spin.setSpecialValueText("自动")
        self.port_spin.setValue(port)
        self.port_spin.setFixedWidth(100)
        header.addWidget(port_label)
        header.addWidget(self.port_spin)
        self.theme_button = QToolButton()
        self.theme_button.setToolTip("切换亮色 / 暗色主题")
        self.theme_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DesktopIcon))
        self.theme_button.clicked.connect(self._toggle_theme)
        header.addWidget(self.theme_button)
        self.start_button = QPushButton("启动")
        self.start_button.setProperty("primary", True)
        self.start_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.start_button.clicked.connect(self.start_server)
        self.stop_button = QPushButton("停止")
        self.stop_button.setProperty("danger", True)
        self.stop_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_server)
        header.addWidget(self.start_button)
        header.addWidget(self.stop_button)
        root.addLayout(header)

        metrics = QFrame()
        metrics.setObjectName("metricsBand")
        metrics_layout = QHBoxLayout(metrics)
        metrics_layout.setContentsMargins(4, 2, 4, 2)
        metrics_layout.setSpacing(0)
        self.user_metric = MetricWidget("在线用户")
        self.room_metric = MetricWidget("聊天室")
        self.file_metric = MetricWidget("共享文件")
        self.uptime_metric = MetricWidget("运行时长")
        for index, widget in enumerate(
            (self.user_metric, self.room_metric, self.file_metric, self.uptime_metric)
        ):
            if index:
                divider = QFrame()
                divider.setFrameShape(QFrame.Shape.VLine)
                divider.setProperty("divider", True)
                metrics_layout.addWidget(divider)
            metrics_layout.addWidget(widget, 1)
        root.addWidget(metrics)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_users_tab(), "用户")
        self.tabs.addTab(self._build_rooms_tab(), "群组")
        self.tabs.addTab(self._build_files_tab(), "文件")
        self.tabs.addTab(self._build_bans_tab(), "封禁")
        splitter.addWidget(self.tabs)
        splitter.addWidget(self._build_log_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([690, 430])
        root.addWidget(splitter, 1)

    def _build_users_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        actions = QHBoxLayout()
        self.kick_button = QPushButton("踢出")
        self.kick_button.setProperty("danger", True)
        self.kick_button.clicked.connect(self._kick_selected)
        self.mute_button = QPushButton("禁言")
        self.mute_button.clicked.connect(lambda: self._mute_selected(True))
        self.unmute_button = QPushButton("解除禁言")
        self.unmute_button.clicked.connect(lambda: self._mute_selected(False))
        self.ban_user_button = QPushButton("封禁 IP")
        self.ban_user_button.setProperty("danger", True)
        self.ban_user_button.clicked.connect(self._ban_selected_user)
        for button in (self.kick_button, self.mute_button, self.unmute_button, self.ban_user_button):
            button.setEnabled(False)
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.users_table = self._new_table(["用户名", "IP 地址", "当前群组", "会话"])
        self.users_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.users_table.customContextMenuRequested.connect(self._show_user_menu)
        self.users_table.itemSelectionChanged.connect(self._update_user_actions)
        layout.addWidget(self.users_table)
        return page

    def _build_rooms_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        actions = QHBoxLayout()
        self.rename_room_button = QPushButton("重命名")
        self.rename_room_button.clicked.connect(self._rename_selected_room)
        self.delete_room_button = QPushButton("删除群组")
        self.delete_room_button.setProperty("danger", True)
        self.delete_room_button.clicked.connect(self._delete_selected_room)
        self.copy_invite_button = QPushButton("复制邀请码")
        self.copy_invite_button.clicked.connect(self._copy_selected_invite)
        for button in (self.rename_room_button, self.delete_room_button, self.copy_invite_button):
            button.setEnabled(False)
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.rooms_table = self._new_table(["群组", "成员", "邀请码", "ID"])
        self.rooms_table.itemSelectionChanged.connect(self._update_room_actions)
        layout.addWidget(self.rooms_table)
        return page

    def _build_files_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        actions = QHBoxLayout()
        self.save_file_button = QPushButton("导出密文")
        self.save_file_button.setToolTip("端到端加密文件只能由收件人客户端解密")
        self.save_file_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.save_file_button.clicked.connect(self._save_selected_file)
        self.delete_file_button = QPushButton("删除")
        self.delete_file_button.setProperty("danger", True)
        self.delete_file_button.clicked.connect(self._delete_selected_file)
        self.save_file_button.setEnabled(False)
        self.delete_file_button.setEnabled(False)
        actions.addWidget(self.save_file_button)
        actions.addWidget(self.delete_file_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.files_table = self._new_table(["文件名", "大小", "发送者", "群组", "时间"])
        self.files_table.itemSelectionChanged.connect(self._update_file_actions)
        self.files_table.doubleClicked.connect(self._save_selected_file)
        layout.addWidget(self.files_table)
        return page

    def _build_bans_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        actions = QHBoxLayout()
        self.ban_edit = QLineEdit()
        self.ban_edit.setPlaceholderText("IP 或 CIDR，例如 192.168.1.0/24")
        self.ban_edit.returnPressed.connect(self._ban_address)
        ban_button = QPushButton("封禁")
        ban_button.setProperty("danger", True)
        ban_button.clicked.connect(self._ban_address)
        self.unban_button = QPushButton("解除封禁")
        self.unban_button.setEnabled(False)
        self.unban_button.clicked.connect(self._unban_selected)
        actions.addWidget(self.ban_edit, 1)
        actions.addWidget(ban_button)
        actions.addWidget(self.unban_button)
        layout.addLayout(actions)
        self.ban_list = QListWidget()
        self.ban_list.itemSelectionChanged.connect(
            lambda: self.unban_button.setEnabled(bool(self.ban_list.selectedItems()))
        )
        layout.addWidget(self.ban_list)
        return page

    def _build_log_panel(self) -> QWidget:
        panel = QFrame()
        panel.setProperty("panel", True)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        title = QHBoxLayout()
        label = QLabel("活动日志")
        label.setObjectName("sectionTitle")
        title.addWidget(label)
        title.addStretch(1)
        self.log_view = QTextBrowser()
        self.log_view.setReadOnly(True)
        self.log_view.setOpenExternalLinks(True)
        clear_button = QToolButton()
        clear_button.setToolTip("清空日志视图")
        clear_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton))
        clear_button.clicked.connect(self.log_view.clear)
        title.addWidget(clear_button)
        layout.addLayout(title)
        layout.addWidget(self.log_view, 1)
        broadcast = QHBoxLayout()
        self.broadcast_edit = QLineEdit()
        self.broadcast_edit.setPlaceholderText("向聊天室发送系统消息")
        self.broadcast_edit.returnPressed.connect(self._send_broadcast)
        self.broadcast_room = QComboBox()
        self.broadcast_room.setMinimumWidth(130)
        send = QPushButton("发送")
        send.setProperty("primary", True)
        send.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowForward))
        send.clicked.connect(self._send_broadcast)
        broadcast.addWidget(self.broadcast_room)
        broadcast.addWidget(self.broadcast_edit, 1)
        broadcast.addWidget(send)
        layout.addLayout(broadcast)
        return panel

    @staticmethod
    def _new_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        for index in range(len(headers) - 1):
            table.horizontalHeader().setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        return table

    def _connect_signals(self) -> None:
        self.engine.signals.running_changed.connect(self._on_running_changed)
        self.engine.signals.snapshot_changed.connect(self._on_snapshot)
        self.engine.signals.log.connect(self._append_log)
        self.engine.signals.error.connect(self._show_error)

    def start_server(self) -> None:
        self.status_label.setText("启动中")
        self._set_status("busy")
        if not self.engine.start(host=self.bind_host, port=self.port_spin.value()):
            self.status_label.setText("启动失败")
            self._set_status("error")

    def stop_server(self) -> None:
        self.stop_button.setEnabled(False)
        self.status_label.setText("停止中")
        self._set_status("busy")
        self.engine.stop()

    def _on_running_changed(self, running: bool, host: str, port: int) -> None:
        self.status_label.setText("运行中" if running else "已停止")
        self._set_status("online" if running else "offline")
        self.endpoint_label.setText(f"{host}:{port}" if running else "0.0.0.0")
        if running:
            self.port_spin.setValue(port)
        self.port_spin.setEnabled(not running)
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def _on_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._last_snapshot = snapshot
        users = snapshot.get("users", [])
        rooms = snapshot.get("rooms", [])
        self.user_metric.set_value(len(users))
        self.room_metric.set_value(len(rooms))
        self.file_metric.set_value(snapshot.get("file_count", 0))
        self._populate_users(users)
        self._populate_rooms(rooms)
        self._populate_files(self.engine.storage.list_files())
        self.ban_list.clear()
        self.ban_list.addItems(snapshot.get("banned", []))
        self.broadcast_room.clear()
        for room in rooms:
            self.broadcast_room.addItem(room["name"], room["id"])

    def _populate_users(self, users: list[dict[str, Any]]) -> None:
        room_names = {room["id"]: room["name"] for room in self._last_snapshot.get("rooms", [])}
        self.users_table.setRowCount(len(users))
        for row, user in enumerate(users):
            values = (
                user.get("username", ""),
                user.get("ip", ""),
                room_names.get(user.get("current_room"), user.get("current_room", "")),
                user.get("id", "")[:8],
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, user)
                self.users_table.setItem(row, column, item)
        self._update_user_actions()

    def _populate_rooms(self, rooms: list[dict[str, Any]]) -> None:
        self.rooms_table.setRowCount(len(rooms))
        for row, room in enumerate(rooms):
            values = (room["name"], room["member_count"], room.get("invite_code") or "-", room["id"])
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, room)
                self.rooms_table.setItem(row, column, item)
        self._update_room_actions()

    def _populate_files(self, files: list[dict[str, Any]]) -> None:
        room_names = {room["id"]: room["name"] for room in self._last_snapshot.get("rooms", [])}
        self.files_table.setRowCount(len(files))
        for row, metadata in enumerate(files):
            uploaded = time.strftime("%Y-%m-%d %H:%M", time.localtime(metadata.get("uploaded_at", 0)))
            encrypted = isinstance(metadata.get("envelope"), dict)
            values = (
                "加密文件" if encrypted else "旧版文件（不可中继）",
                self._format_size(
                    int(metadata.get("plaintext_size", metadata.get("size", 0)))
                ),
                metadata.get("sender", ""),
                room_names.get(metadata.get("room_id"), metadata.get("room_id", "")),
                uploaded,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, metadata)
                self.files_table.setItem(row, column, item)
        self._update_file_actions()

    def _selected_data(self, table: QTableWidget) -> dict[str, Any] | None:
        rows = table.selectionModel().selectedRows()
        if not rows:
            return None
        item = table.item(rows[0].row(), 0)
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return value if isinstance(value, dict) else None

    def _update_user_actions(self) -> None:
        enabled = self._selected_data(self.users_table) is not None and self.engine.running
        for button in (self.kick_button, self.mute_button, self.unmute_button, self.ban_user_button):
            button.setEnabled(enabled)

    def _update_room_actions(self) -> None:
        room = self._selected_data(self.rooms_table)
        selected = room is not None
        is_main = bool(room and room.get("id") == MAIN_ROOM_ID)
        self.rename_room_button.setEnabled(selected and not is_main)
        self.delete_room_button.setEnabled(selected and not is_main)
        self.copy_invite_button.setEnabled(selected and bool(room.get("invite_code") if room else False))

    def _update_file_actions(self) -> None:
        enabled = self._selected_data(self.files_table) is not None
        self.save_file_button.setEnabled(enabled)
        self.delete_file_button.setEnabled(enabled)

    def _kick_selected(self) -> None:
        user = self._selected_data(self.users_table)
        if not user:
            return
        answer = QMessageBox.question(self, "确认踢出", f"断开 {user['username']} 的连接？")
        if answer == QMessageBox.StandardButton.Yes:
            self.engine.kick_user(user["id"])

    def _mute_selected(self, muted: bool) -> None:
        user = self._selected_data(self.users_table)
        if not user:
            return
        self.engine.mute_user(user["id"], user.get("current_room", MAIN_ROOM_ID), muted)

    def _ban_selected_user(self) -> None:
        user = self._selected_data(self.users_table)
        if not user:
            return
        ok, message = self.engine.ban_address(user["ip"])
        if not ok:
            self._show_error(message)

    def _show_user_menu(self, position) -> None:
        if not self._selected_data(self.users_table):
            return
        menu = QMenu(self)
        kick = menu.addAction("踢出")
        mute = menu.addAction("禁言当前群组")
        unmute = menu.addAction("解除禁言")
        menu.addSeparator()
        ban = menu.addAction("封禁 IP")
        chosen = menu.exec(self.users_table.viewport().mapToGlobal(position))
        if chosen == kick:
            self._kick_selected()
        elif chosen == mute:
            self._mute_selected(True)
        elif chosen == unmute:
            self._mute_selected(False)
        elif chosen == ban:
            self._ban_selected_user()

    def _rename_selected_room(self) -> None:
        room = self._selected_data(self.rooms_table)
        if not room:
            return
        name, accepted = QInputDialog.getText(self, "重命名群组", "群组名称", text=room["name"])
        if accepted and name.strip():
            ok, message = self.engine.rename_room(room["id"], name.strip())
            if not ok:
                self._show_error(message)

    def _delete_selected_room(self) -> None:
        room = self._selected_data(self.rooms_table)
        if not room:
            return
        answer = QMessageBox.warning(
            self,
            "删除群组",
            f"删除“{room['name']}”？成员将返回主群。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.engine.delete_room(room["id"])

    def _copy_selected_invite(self) -> None:
        room = self._selected_data(self.rooms_table)
        if room and room.get("invite_code"):
            QApplication.clipboard().setText(str(room["invite_code"]))
            self.statusBar().showMessage("邀请码已复制", 2500)

    def _save_selected_file(self, *_args) -> None:
        metadata = self._selected_data(self.files_table)
        if not metadata:
            return
        file_id = str(metadata.get("id", metadata.get("file_id", "")))
        display_name = f"{file_id or 'encrypted-file'}.ciphertext"
        path, _ = QFileDialog.getSaveFileName(self, "导出加密文件", display_name)
        if not path:
            return
        try:
            _info, data = self.engine.storage.read_file(file_id)
            Path(path).write_bytes(data)
        except OSError as exc:
            self._show_error(f"保存失败：{exc}")
            return
        self.statusBar().showMessage(f"密文已导出到 {path}", 4000)

    def _delete_selected_file(self) -> None:
        metadata = self._selected_data(self.files_table)
        if not metadata:
            return
        display_name = metadata.get("name", metadata.get("original_name", "file"))
        answer = QMessageBox.question(self, "删除文件", f"删除“{display_name}”？")
        if answer == QMessageBox.StandardButton.Yes:
            self.engine.delete_shared_file(metadata.get("id", metadata.get("file_id", "")))
            self._on_snapshot(self.engine.snapshot())

    def _ban_address(self) -> None:
        value = self.ban_edit.text().strip()
        if not value:
            return
        ok, result = self.engine.ban_address(value)
        if ok:
            self.ban_edit.clear()
        else:
            self._show_error(f"无效地址：{result}")

    def _unban_selected(self) -> None:
        item = self.ban_list.currentItem()
        if item:
            self.engine.unban_address(item.text())

    def _send_broadcast(self) -> None:
        text = self.broadcast_edit.text().strip()
        room_id = self.broadcast_room.currentData() or MAIN_ROOM_ID
        if text and self.engine.broadcast_system(text, str(room_id)):
            self.broadcast_edit.clear()

    def _append_log(self, level: str, message: str) -> None:
        colors = {"warning": "#b7791f", "error": "#c73535", "message": "#2563eb"}
        color = colors.get(level, "#667085")
        timestamp = time.strftime("%H:%M:%S")
        self.log_view.append(
            f'<span style="color:{color}">{timestamp}</span> '
            f'<span>{html.escape(message)}</span>'
        )

    def _show_error(self, message: str) -> None:
        self._append_log("error", message)
        QMessageBox.critical(self, "服务器错误", message)

    def _toggle_theme(self) -> None:
        self.theme = opposite_theme(self.theme)
        apply_theme(QApplication.instance(), self.theme)
        self.settings.setValue("appearance/theme", self.theme)

    def _set_status(self, status: str) -> None:
        self.status_label.setProperty("status", status)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _update_uptime(self) -> None:
        started_at = self._last_snapshot.get("started_at")
        if not self.engine.running or not started_at:
            self.uptime_metric.set_value("-")
            return
        elapsed = max(0, int(time.time() - float(started_at)))
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.uptime_metric.set_value(f"{hours:02}:{minutes:02}:{seconds:02}")

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"

    def _restore_window_state(self) -> None:
        geometry = self.settings.value("server/geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.auto_start and not self._started_once:
            self._started_once = True
            QTimer.singleShot(0, self.start_server)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.engine.running:
            answer = QMessageBox.question(
                self,
                "关闭服务器",
                "关闭窗口将断开所有客户端，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.settings.setValue("server/geometry", self.saveGeometry())
        self.engine.stop()
        event.accept()
