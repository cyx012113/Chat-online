"""Startup dialog for choosing server or client mode."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QSettings, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .theme import apply_theme, opposite_theme


@dataclass(frozen=True)
class LaunchConfig:
    mode: str
    host: str
    port: int
    username: str
    theme: str


class LauncherDialog(QDialog):
    """Compact, keyboard-friendly startup configuration dialog."""

    theme_changed = pyqtSignal(str)

    def __init__(self, initial_mode: str = "client", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("launcher")
        self.setWindowTitle("Chat Online")
        self.setModal(True)
        self.setMinimumSize(500, 430)
        self.resize(520, 460)

        self.settings = QSettings()
        self.theme = str(self.settings.value("appearance/theme", "light"))
        self._build_ui()
        self.set_mode(initial_mode if initial_mode in {"server", "client"} else "client")

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(18)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        brand = QLabel("Chat Online")
        brand.setObjectName("brandTitle")
        subtitle = QLabel("局域网即时通信")
        subtitle.setProperty("muted", True)
        title_box.addWidget(brand)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)

        self.theme_button = QToolButton()
        self.theme_button.setObjectName("themeButton")
        self.theme_button.setToolTip("切换亮色 / 暗色主题")
        self.theme_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DesktopIcon))
        self.theme_button.clicked.connect(self._toggle_theme)
        header.addWidget(self.theme_button)
        root.addLayout(header)

        mode_frame = QFrame()
        mode_frame.setObjectName("segmentedControl")
        mode_layout = QHBoxLayout(mode_frame)
        mode_layout.setContentsMargins(3, 3, 3, 3)
        mode_layout.setSpacing(2)
        self.server_mode = QPushButton("服务器")
        self.client_mode = QPushButton("客户端")
        for button in (self.server_mode, self.client_mode):
            button.setCheckable(True)
            button.setProperty("segment", True)
            mode_layout.addWidget(button)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.server_mode)
        self.mode_group.addButton(self.client_mode)
        self.server_mode.clicked.connect(lambda: self.set_mode("server"))
        self.client_mode.clicked.connect(lambda: self.set_mode("client"))
        root.addWidget(mode_frame)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_server_page())
        self.pages.addWidget(self._build_client_page())
        root.addWidget(self.pages, 1)

        self.error_label = QLabel()
        self.error_label.setObjectName("formError")
        self.error_label.setWordWrap(True)
        self.error_label.setMinimumHeight(20)
        root.addWidget(self.error_label)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setProperty("ghost", True)
        cancel.clicked.connect(self.reject)
        self.start_button = QPushButton("连接")
        self.start_button.setProperty("primary", True)
        self.start_button.setDefault(True)
        self.start_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowForward))
        self.start_button.clicked.connect(self._validate_and_accept)
        actions.addWidget(cancel)
        actions.addWidget(self.start_button)
        root.addLayout(actions)

    def _build_server_page(self) -> QWidget:
        page = QWidget()
        layout = QFormLayout(page)
        layout.setContentsMargins(2, 12, 2, 8)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(16)
        self.server_port = QSpinBox()
        self.server_port.setRange(1, 65535)
        self.server_port.setValue(int(self.settings.value("server/port", 8888)))
        self.server_port.setAccelerated(True)
        layout.addRow("监听端口", self.server_port)
        address = QLineEdit("0.0.0.0")
        address.setReadOnly(True)
        address.setToolTip("监听所有本机网络接口")
        layout.addRow("监听地址", address)
        return page

    def _build_client_page(self) -> QWidget:
        page = QWidget()
        layout = QFormLayout(page)
        layout.setContentsMargins(2, 12, 2, 8)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(16)
        self.host_edit = QLineEdit(str(self.settings.value("client/host", "127.0.0.1")))
        self.host_edit.setPlaceholderText("127.0.0.1")
        self.host_edit.setClearButtonEnabled(True)
        self.client_port = QSpinBox()
        self.client_port.setRange(1, 65535)
        self.client_port.setValue(int(self.settings.value("client/port", 8888)))
        self.client_port.setAccelerated(True)
        self.username_edit = QLineEdit(str(self.settings.value("client/username", "")))
        self.username_edit.setPlaceholderText("用户名")
        self.username_edit.setMaxLength(24)
        self.username_edit.setClearButtonEnabled(True)
        layout.addRow("服务器", self.host_edit)
        layout.addRow("端口", self.client_port)
        layout.addRow("用户名", self.username_edit)
        return page

    def set_mode(self, mode: str) -> None:
        is_server = mode == "server"
        self.server_mode.setChecked(is_server)
        self.client_mode.setChecked(not is_server)
        self.pages.setCurrentIndex(0 if is_server else 1)
        self.start_button.setText("打开控制台" if is_server else "连接")
        icon = QStyle.StandardPixmap.SP_ComputerIcon if is_server else QStyle.StandardPixmap.SP_ArrowForward
        self.start_button.setIcon(self.style().standardIcon(icon))
        self.error_label.clear()

    def selected_mode(self) -> str:
        return "server" if self.server_mode.isChecked() else "client"

    def config(self) -> LaunchConfig:
        if self.selected_mode() == "server":
            return LaunchConfig("server", "0.0.0.0", self.server_port.value(), "", self.theme)
        return LaunchConfig(
            "client",
            self.host_edit.text().strip(),
            self.client_port.value(),
            " ".join(self.username_edit.text().split()),
            self.theme,
        )

    def _validate_and_accept(self) -> None:
        config = self.config()
        if config.mode == "client":
            if not config.host:
                self._show_error("请输入服务器地址。", self.host_edit)
                return
            if not config.username:
                self._show_error("请输入用户名。", self.username_edit)
                return
            if any(ord(char) < 32 for char in config.username):
                self._show_error("用户名包含无效字符。", self.username_edit)
                return
        self.settings.setValue("appearance/theme", self.theme)
        self.settings.setValue("server/port", self.server_port.value())
        self.settings.setValue("client/host", self.host_edit.text().strip())
        self.settings.setValue("client/port", self.client_port.value())
        self.settings.setValue("client/username", self.username_edit.text().strip())
        self.accept()

    def _show_error(self, message: str, widget: QWidget) -> None:
        self.error_label.setText(message)
        widget.setFocus(Qt.FocusReason.OtherFocusReason)

    def _toggle_theme(self) -> None:
        self.theme = opposite_theme(self.theme)
        apply_theme(QApplication.instance(), self.theme)
        self.theme_changed.emit(self.theme)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.reject()
        event.accept()

