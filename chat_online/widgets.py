"""Reusable PyQt6 widgets shared by the client and server windows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import datetime
import html
import re
from typing import Any

from PyQt6.QtCore import QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QResizeEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)


_URL_RE = re.compile(r"(?i)(?:https?://|www\.)[^\s<>\"']+")
_SYSTEM_PACKET_TYPES = {
    "error",
    "kicked",
    "moderation",
    "server_shutdown",
    "system",
}


def _copy_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(packet, Mapping):
        raise TypeError("packet must be a mapping")
    return deepcopy(dict(packet))


def _timestamp_text(value: Any) -> str:
    if isinstance(value, bool):
        return "--:--"
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp).strftime("%H:%M")
        except (OSError, OverflowError, ValueError):
            return "--:--"
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone()
            return parsed.strftime("%H:%M")
        except ValueError:
            match = re.search(r"\b\d{1,2}:\d{2}\b", raw)
            return match.group(0) if match else "--:--"
    return "--:--"


def _format_size(value: Any) -> str:
    try:
        size = max(0, int(value))
    except (TypeError, ValueError):
        return "Unknown size"
    units = ("B", "KB", "MB", "GB")
    amount = float(size)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{size} B"


def _trim_url_suffix(value: str) -> tuple[str, str]:
    trailing = ""
    while value and value[-1] in ".,!?;:":
        trailing = value[-1] + trailing
        value = value[:-1]
    pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    for opening, closing in pairs:
        while value.endswith(closing) and value.count(opening) < value.count(closing):
            trailing = closing + trailing
            value = value[:-1]
    return value, trailing


def _escape_text(value: str) -> str:
    return html.escape(value, quote=False).replace("\n", "<br>")


def _linkify(value: str) -> str:
    chunks: list[str] = []
    position = 0
    for match in _URL_RE.finditer(value):
        chunks.append(_escape_text(value[position : match.start()]))
        visible, trailing = _trim_url_suffix(match.group(0))
        target = visible if visible.lower().startswith(("http://", "https://")) else f"https://{visible}"
        chunks.append(
            f'<a href="{html.escape(target, quote=True)}">'
            f"{html.escape(visible, quote=False)}</a>"
        )
        chunks.append(_escape_text(trailing))
        position = match.end()
    chunks.append(_escape_text(value[position:]))
    return "".join(chunks)


def _is_system_packet(packet: Mapping[str, Any]) -> bool:
    return (
        str(packet.get("kind", "")).lower() == "system"
        or str(packet.get("sender_id", "")).lower() == "system"
        or str(packet.get("type", "")).lower() in _SYSTEM_PACKET_TYPES
    )


def _message_text(packet: Mapping[str, Any]) -> str:
    if str(packet.get("type", "")).lower() == "file_shared":
        metadata = packet.get("file")
        file_info = metadata if isinstance(metadata, Mapping) else {}
        name = (
            file_info.get("original_name")
            or file_info.get("name")
            or file_info.get("filename")
            or "Unnamed file"
        )
        return f"Shared file: {name}\n{_format_size(file_info.get('size'))}"
    return str(packet.get("text") or packet.get("message") or "")


def _searchable_packet_text(packet: Mapping[str, Any]) -> str:
    values: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, Mapping):
            for nested in value.values():
                collect(nested)
        elif isinstance(value, (list, tuple, set)):
            for nested in value:
                collect(nested)
        elif value is not None:
            values.append(str(value))

    collect(packet)
    return "\n".join(values).casefold()


class StatusPill(QLabel):
    """Compact status label with neutral, info, success, warning and danger tones."""

    TONES = ("neutral", "info", "success", "warning", "danger")

    def __init__(
        self,
        text: str = "",
        tone: str = "neutral",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("statusPill")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        normalized = str(tone).strip().lower()
        if normalized not in self.TONES:
            normalized = "neutral"
        self.setProperty("tone", normalized)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_status(self, text: str, tone: str | None = None) -> None:
        self.setText(str(text))
        if tone is not None:
            self.set_tone(tone)


class BubbleRow(QWidget):
    """One adaptive message row with left, right or centered alignment."""

    urlActivated = pyqtSignal(QUrl)
    fileActivated = pyqtSignal(dict)

    def __init__(
        self,
        packet: Mapping[str, Any],
        identity: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._packet = _copy_packet(packet)
        self._identity = str(identity or "")
        self._max_bubble_width = 0
        self._sender_text = ""

        self._row_layout = QHBoxLayout(self)
        self._row_layout.setContentsMargins(4, 2, 4, 2)
        self._row_layout.setSpacing(0)

        self._bubble = QFrame(self)
        self._bubble.setObjectName("messageBubble")
        self._bubble.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Preferred,
        )
        self._bubble_layout = QVBoxLayout(self._bubble)
        self._bubble_layout.setContentsMargins(12, 8, 12, 8)
        self._bubble_layout.setSpacing(4)

        meta_layout = QHBoxLayout()
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(10)
        self._sender = QLabel(self._bubble)
        self._sender.setObjectName("messageMeta")
        self._sender.setTextFormat(Qt.TextFormat.PlainText)
        self._sender.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._time = QLabel(self._bubble)
        self._time.setObjectName("messageMeta")
        self._time.setTextFormat(Qt.TextFormat.PlainText)
        self._time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        meta_layout.addWidget(self._sender)
        meta_layout.addStretch(1)
        meta_layout.addWidget(self._time)
        self._bubble_layout.addLayout(meta_layout)

        self._body = QLabel(self._bubble)
        self._body.setObjectName("messageBody")
        self._body.setTextFormat(Qt.TextFormat.RichText)
        self._body.setWordWrap(True)
        self._body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self._body.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._body.setOpenExternalLinks(False)
        self._body.linkActivated.connect(self._activate_url)
        self._bubble_layout.addWidget(self._body)

        self._file_button = QPushButton("Download", self._bubble)
        self._file_button.setProperty("ghost", True)
        self._file_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton)
        )
        self._file_button.setToolTip("Download shared file")
        self._file_button.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )
        self._file_button.clicked.connect(self._activate_file)
        self._bubble_layout.addWidget(
            self._file_button,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )

        self.set_maximum_bubble_width(520)
        self.refresh(self._packet, self._identity)

    def packet(self) -> dict[str, Any]:
        return deepcopy(self._packet)

    def refresh(self, packet: Mapping[str, Any], identity: str) -> None:
        self._packet = _copy_packet(packet)
        self._identity = str(identity or "")
        sender_id = str(
            self._packet.get("sender_id")
            or self._packet.get("session_id")
            or ""
        )
        if _is_system_packet(self._packet):
            role = "system"
        elif self._identity and sender_id == self._identity:
            role = "self"
        else:
            role = "other"

        sender = str(
            self._packet.get("sender")
            or ("System" if role == "system" else "Unknown")
        )
        self._sender_text = sender
        self._update_sender_label()
        self._time.setText(_timestamp_text(self._packet.get("timestamp")))
        self._body.setText(_linkify(_message_text(self._packet)))

        is_file = str(self._packet.get("type", "")).lower() == "file_shared"
        metadata = self._packet.get("file")
        has_file_id = isinstance(metadata, Mapping) and bool(metadata.get("file_id"))
        self._file_button.setVisible(is_file and has_file_id)
        self._bubble.setProperty("messageRole", role)
        self._bubble.setProperty("isFile", is_file)
        self.setProperty("messageRole", role)
        self._rebuild_alignment(role)
        self._repolish(self._bubble)
        self.updateGeometry()

    def set_maximum_bubble_width(self, width: int) -> None:
        normalized = max(140, int(width))
        if normalized == self._max_bubble_width:
            return
        self._max_bubble_width = normalized
        self._bubble.setMaximumWidth(normalized)
        self._body.setMaximumWidth(max(100, normalized - 26))
        self._update_sender_label()
        self._bubble_layout.invalidate()
        self._row_layout.invalidate()
        self.updateGeometry()

    def _update_sender_label(self) -> None:
        if not hasattr(self, "_sender"):
            return
        available = max(70, int(self._max_bubble_width * 0.62))
        elided = self._sender.fontMetrics().elidedText(
            self._sender_text,
            Qt.TextElideMode.ElideRight,
            available,
        )
        self._sender.setMaximumWidth(available)
        self._sender.setText(elided)
        self._sender.setToolTip(self._sender_text if elided != self._sender_text else "")

    def _rebuild_alignment(self, role: str) -> None:
        while self._row_layout.count():
            self._row_layout.takeAt(0)
        if role == "self":
            self._row_layout.addStretch(1)
            self._row_layout.addWidget(
                self._bubble,
                0,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
            )
        elif role == "system":
            self._row_layout.addStretch(1)
            self._row_layout.addWidget(
                self._bubble,
                0,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            )
            self._row_layout.addStretch(1)
        else:
            self._row_layout.addWidget(
                self._bubble,
                0,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            )
            self._row_layout.addStretch(1)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _activate_url(self, value: str) -> None:
        url = QUrl(value)
        if url.scheme().lower() not in {"http", "https"}:
            return
        self.urlActivated.emit(url)
        QDesktopServices.openUrl(url)

    def _activate_file(self) -> None:
        metadata = self._packet.get("file")
        if isinstance(metadata, Mapping):
            self.fileActivated.emit(deepcopy(dict(metadata)))


class MessageList(QListWidget):
    """Message view that preserves packet order while filtering presentation."""

    urlActivated = pyqtSignal(QUrl)
    fileActivated = pyqtSignal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._identity = ""
        self._packets: list[dict[str, Any]] = []
        self._filter_query = ""
        self.setObjectName("messageList")
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSpacing(4)

    def set_identity(self, session_id: str | None) -> None:
        normalized = str(session_id or "")
        if normalized == self._identity:
            return
        self._identity = normalized
        for index, packet in enumerate(self._packets):
            item = self.item(index)
            row = self.itemWidget(item)
            if isinstance(row, BubbleRow):
                row.refresh(packet, self._identity)
        self._refresh_widths()

    def append_message(self, packet: Mapping[str, Any]) -> None:
        copied = _copy_packet(packet)
        scroll_bar = self.verticalScrollBar()
        follow_tail = scroll_bar.value() >= scroll_bar.maximum() - 24
        self._packets.append(copied)
        self._append_row(copied)
        self._apply_item_filter(self.count() - 1)
        self._refresh_widths()
        if follow_tail:
            self.scrollToBottom()

    def set_messages(self, packets: Iterable[Mapping[str, Any]]) -> None:
        if isinstance(packets, (str, bytes, bytearray)):
            raise TypeError("packets must be an iterable of mappings")
        copied = [_copy_packet(packet) for packet in packets]
        super().clear()
        self._packets = copied
        for packet in self._packets:
            self._append_row(packet)
        self.filter_messages(self._filter_query)
        self._refresh_widths()
        self.scrollToBottom()

    def filter_messages(self, query: object) -> None:
        self._filter_query = str(query or "").strip().casefold()
        for index in range(self.count()):
            self._apply_item_filter(index)

    def packets(self) -> list[dict[str, Any]]:
        return deepcopy(self._packets)

    def clear(self) -> None:
        self._packets.clear()
        super().clear()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API name
        super().resizeEvent(event)
        self._refresh_widths()

    def _append_row(self, packet: Mapping[str, Any]) -> None:
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        row = BubbleRow(packet, self._identity, self.viewport())
        row.urlActivated.connect(self.urlActivated)
        row.fileActivated.connect(self.fileActivated)
        self.addItem(item)
        self.setItemWidget(item, row)

    def _apply_item_filter(self, index: int) -> None:
        if not (0 <= index < len(self._packets)):
            return
        visible = not self._filter_query or self._filter_query in _searchable_packet_text(
            self._packets[index]
        )
        self.item(index).setHidden(not visible)

    def _refresh_widths(self) -> None:
        viewport_width = max(1, self.viewport().width() - 8)
        bubble_width = max(140, min(680, int(viewport_width * 0.72)))
        for index in range(self.count()):
            item = self.item(index)
            row = self.itemWidget(item)
            if not isinstance(row, BubbleRow):
                continue
            row.setFixedWidth(viewport_width)
            row.set_maximum_bubble_width(bubble_width)
            row.layout().activate()
            height = max(48, row.sizeHint().height())
            item.setSizeHint(QSize(viewport_width, height))


__all__ = ["BubbleRow", "MessageList", "StatusPill"]
