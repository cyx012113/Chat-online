"""Reusable PyQt6 widgets shared by the client and server windows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import datetime
import html
import re
import shlex
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from markdown_it import MarkdownIt
from mdit_py_plugins.container import container_plugin
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

from PyQt6.QtCore import QEvent, QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QResizeEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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


_SYSTEM_PACKET_TYPES = {
    "error",
    "kicked",
    "moderation",
    "server_shutdown",
    "system",
}
_FILE_PACKET_TYPES = {"file_shared", "encrypted_file_shared"}


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


def _theme_is_dark() -> bool:
    app = QApplication.instance()
    return bool(app is not None and app.property("theme") == "dark")


def _safe_web_url(value: object, *, https_only: bool = False) -> str | None:
    candidate = html.unescape(str(value or "")).strip()
    if not candidate or any(ord(character) < 32 for character in candidate):
        return None
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return None
    allowed = {"https"} if https_only else {"http", "https"}
    if parsed.scheme.lower() not in allowed or not parsed.hostname or port is None and ":" in parsed.netloc.rsplit("@", 1)[-1] and parsed.netloc.endswith(":"):
        return None
    return candidate


def _bilibili_url(value: str) -> str | None:
    if not value.lower().startswith("bilibili:"):
        return None
    spec = value.split(":", 1)[1].strip()
    video_id, separator, query = spec.partition("?")
    if video_id.isdigit():
        video_id = f"av{video_id}"
    elif re.fullmatch(r"(?i)av\d+", video_id):
        video_id = f"av{video_id[2:]}"
    elif not re.fullmatch(r"(?i)BV[0-9A-Za-z]+", video_id):
        return None

    safe_query: list[tuple[str, str]] = []
    if separator:
        for key, item in parse_qsl(query, keep_blank_values=False):
            normalized = "p" if key == "page" else key
            if normalized not in {"p", "t"} or not item.isdigit():
                continue
            safe_query.append((normalized, item))
    suffix = f"?{urlencode(safe_query)}" if safe_query else ""
    return f"https://www.bilibili.com/video/{video_id}{suffix}"


def _markdown_colors() -> dict[str, str]:
    if _theme_is_dark():
        return {
            "muted": "#AAB2BD",
            "border": "#4A515C",
            "surface": "#292D33",
            "surface_alt": "#202328",
            "highlight": "#514819",
            "code": "#F3F4F6",
            "info": "#7DB4FF",
            "success": "#4ADE80",
            "warning": "#FBBF24",
            "error": "#F87171",
        }
    return {
        "muted": "#667085",
        "border": "#D8DEE6",
        "surface": "#F0F2F5",
        "surface_alt": "#FFFFFF",
        "highlight": "#FFF2A8",
        "code": "#1B1F24",
        "info": "#2563EB",
        "success": "#16803C",
        "warning": "#B7791F",
        "error": "#C73535",
    }


def _parse_fence_info(value: str) -> tuple[str, bool, tuple[int, int] | None]:
    try:
        parts = shlex.split(value, posix=True)
    except ValueError:
        parts = value.split()
    language = ""
    numbered = False
    highlighted: tuple[int, int] | None = None
    for part in parts:
        if part == "line-numbers":
            numbered = True
            continue
        match = re.fullmatch(r"lines=(\d+)-(\d+)", part)
        if match:
            start, end = (int(match.group(1)), int(match.group(2)))
            if start > 0 and end > 0:
                highlighted = (min(start, end), max(start, end))
            continue
        if not language and re.fullmatch(r"[A-Za-z0-9_+.#-]{1,40}", part):
            language = part
    return language, numbered, highlighted


def _render_fence(code: str, info: str) -> str:
    language, numbered, highlighted_range = _parse_fence_info(info)
    display_language = language or "cpp"
    try:
        lexer = get_lexer_by_name(display_language)
    except ClassNotFound:
        lexer = TextLexer()
    style = "monokai" if _theme_is_dark() else "friendly"
    formatter = HtmlFormatter(nowrap=True, noclasses=True, style=style)
    rendered = highlight(code, lexer, formatter)
    lines = rendered.split("\n")
    if code.endswith("\n") and lines and lines[-1] == "":
        lines.pop()
    if not lines:
        lines = [""]

    colors = _markdown_colors()
    number_width = len(str(max(1, len(lines))))
    body: list[str] = []
    for number, line in enumerate(lines, start=1):
        prefix = ""
        if numbered:
            label = str(number).rjust(number_width).replace(" ", "&#160;")
            prefix = (
                f'<span style="color:{colors["muted"]};">{label} &#160;</span>'
            )
        is_highlighted = bool(
            highlighted_range
            and highlighted_range[0] <= number <= highlighted_range[1]
        )
        background = colors["highlight"] if is_highlighted else "transparent"
        body.append(
            f'<span data-line="{number}" style="background-color:{background};">'
            f"{prefix}{line or '&#8203;'}</span>"
        )
    language_label = html.escape(display_language, quote=False)
    code_body = "\n".join(body)
    return (
        f'<div class="md-code-block" data-language="{html.escape(display_language, quote=True)}" '
        f'style="margin:5px 0;border:1px solid {colors["border"]};">'
        f'<div style="padding:3px 7px;color:{colors["muted"]};'
        f'background-color:{colors["surface"]};font-size:8pt;">{language_label}</div>'
        f'<pre style="margin:0;padding:7px;color:{colors["code"]};'
        f'background-color:{colors["surface_alt"]};white-space:pre-wrap;'
        f'word-wrap:break-word;overflow-wrap:anywhere;">'
        f"{code_body}</pre></div>\n"
    )


def _container_title(info: str, name: str) -> tuple[str, bool]:
    match = re.fullmatch(
        rf"\s*{re.escape(name)}(?:\[(.*?)\])?\s*(\{{open\}})?\s*",
        info,
    )
    if not match:
        return "", False
    return (match.group(1) or "").strip(), bool(match.group(2))


def _build_markdown_renderer() -> MarkdownIt:
    markdown = MarkdownIt(
        "commonmark",
        {"html": False, "linkify": True, "typographer": False, "breaks": False},
    ).enable(["table", "strikethrough", "linkify"])
    # Tokenize every explicit destination so our renderer can replace unsafe
    # links and images with inert text instead of leaking raw Markdown syntax.
    markdown.validateLink = lambda _url: True
    markdown.use(tasklists_plugin, enabled=False, label=False)
    markdown.use(
        dollarmath_plugin,
        allow_labels=False,
        allow_space=True,
        allow_digits=True,
        allow_blank_lines=True,
    )

    def validate_align(params: str, _markup: str) -> bool:
        return re.fullmatch(r"\s*align\{(?:center|right)\}\s*", params) is not None

    def render_align(self, tokens, index, _options, _env) -> str:
        token = tokens[index]
        if token.nesting < 0:
            return "</div>\n"
        match = re.fullmatch(r"\s*align\{(center|right)\}\s*", token.info)
        alignment = match.group(1) if match else "center"
        return f'<div class="md-align-{alignment}" align="{alignment}">\n'

    markdown.use(
        container_plugin,
        "align",
        validate=validate_align,
        render=render_align,
    )

    def validate_epigraph(params: str, _markup: str) -> bool:
        return re.fullmatch(r"\s*epigraph(?:\[.*?\])?\s*", params) is not None

    def render_epigraph(self, tokens, index, _options, env) -> str:
        token = tokens[index]
        authors = env.setdefault("_md_epigraph_authors", [])
        colors = _markdown_colors()
        if token.nesting > 0:
            author, _open = _container_title(token.info, "epigraph")
            authors.append(author)
            return (
                f'<blockquote class="md-epigraph" style="margin:5px 0;padding:4px 9px;'
                f'border-left:3px solid {colors["border"]};">\n'
            )
        author = authors.pop() if authors else ""
        footer = (
            f'<p align="right" style="color:{colors["muted"]};">'
            f'-- {html.escape(author, quote=False)}</p>\n'
            if author
            else ""
        )
        return f"{footer}</blockquote>\n"

    markdown.use(
        container_plugin,
        "epigraph",
        validate=validate_epigraph,
        render=render_epigraph,
    )

    tone_labels = {
        "info": "提示",
        "success": "成功",
        "warning": "警告",
        "error": "错误",
    }
    for tone, default_title in tone_labels.items():
        def validate_admonition(
            params: str,
            _markup: str,
            *,
            expected: str = tone,
        ) -> bool:
            return re.fullmatch(
                rf"\s*{re.escape(expected)}(?:\[.*?\])?\s*(?:\{{open\}})?\s*",
                params,
            ) is not None

        def render_admonition(
            self,
            tokens,
            index,
            _options,
            _env,
            *,
            expected: str = tone,
            fallback_title: str = default_title,
        ) -> str:
            token = tokens[index]
            if token.nesting < 0:
                return "</td></tr></table>\n"
            title, opened = _container_title(token.info, expected)
            colors = _markdown_colors()
            accent = colors[expected]
            heading = html.escape(title or fallback_title, quote=False)
            return (
                f'<table class="md-admonition md-{expected}" data-open="{str(opened).lower()}" '
                f'cellspacing="0" cellpadding="6" width="100%" '
                f'style="margin:5px 0;border:1px solid {accent};">'
                f'<tr><td><b style="color:{accent};">{heading}</b><br>'
            )

        markdown.use(
            container_plugin,
            tone,
            validate=validate_admonition,
            render=render_admonition,
        )

    def render_fence(self, tokens, index, _options, _env) -> str:
        token = tokens[index]
        return _render_fence(token.content, token.info)

    def render_image(self, tokens, index, _options, _env) -> str:
        token = tokens[index]
        source = str(token.attrGet("src") or "")
        alt = token.content.strip() or "图片"
        bilibili = _bilibili_url(source)
        destination = bilibili or _safe_web_url(source, https_only=True)
        visible = f"[{'Bilibili 视频' if bilibili else '图片'}：{alt}]"
        escaped_visible = html.escape(visible, quote=False)
        if not destination:
            return f'<span class="md-blocked-image">{escaped_visible}（链接已拦截）</span>'
        title = str(token.attrGet("title") or alt)
        return (
            f'<a class="md-image-link" href="{html.escape(destination, quote=True)}" '
            f'title="{html.escape(title, quote=True)}">{escaped_visible}</a>'
        )

    def render_link_open(self, tokens, index, _options, env) -> str:
        token = tokens[index]
        target = _safe_web_url(token.attrGet("href"))
        unsafe_links = env.setdefault("_md_unsafe_links", [])
        unsafe_links.append(target is None)
        if target is None:
            return '<span class="md-blocked-link">'
        title = token.attrGet("title")
        title_attr = (
            f' title="{html.escape(str(title), quote=True)}"' if title else ""
        )
        return (
            f'<a href="{html.escape(target, quote=True)}"{title_attr} '
            'style="text-decoration:underline;">'
        )

    def render_link_close(self, _tokens, _index, _options, env) -> str:
        unsafe_links = env.setdefault("_md_unsafe_links", [])
        unsafe = unsafe_links.pop() if unsafe_links else False
        return "</span>" if unsafe else "</a>"

    def render_html_inline(self, tokens, index, _options, _env) -> str:
        content = tokens[index].content
        if "task-list-item-checkbox" not in content:
            return html.escape(content, quote=False)
        checked = 'checked="checked"' in content
        marker = "&#x2611;" if checked else "&#x2610;"
        return f'<span class="md-task-marker">{marker}</span>'

    def render_math_inline(self, tokens, index, _options, _env) -> str:
        content = html.escape(tokens[index].content, quote=False)
        return f'<span class="md-math">&#36;{content}&#36;</span>'

    def render_math_block(self, tokens, index, _options, _env) -> str:
        content = html.escape(tokens[index].content, quote=False).replace("\n", "<br>")
        return f'<div class="md-math-block">&#36;&#36;<br>{content}<br>&#36;&#36;</div>\n'

    markdown.add_render_rule("fence", render_fence)
    markdown.add_render_rule("image", render_image)
    markdown.add_render_rule("link_open", render_link_open)
    markdown.add_render_rule("link_close", render_link_close)
    markdown.add_render_rule("html_inline", render_html_inline)
    markdown.add_render_rule("math_inline", render_math_inline)
    markdown.add_render_rule("math_block", render_math_block)
    return markdown


_MARKDOWN = _build_markdown_renderer()


def _render_markdown(value: object) -> str:
    source = str(value or "")
    source = re.sub(
        r"(?m)^[ \t]*::cute-table\{tuack\}[ \t]*(?:\r?\n|$)",
        "",
        source,
    )
    colors = _markdown_colors()
    rendered = _MARKDOWN.render(source, {})
    style = f"""
<style>
.md-root {{ margin: 0; padding: 0; }}
.md-root p {{ margin: 3px 0; }}
.md-root h1 {{ font-size: 15pt; margin: 5px 0 3px 0; }}
.md-root h2 {{ font-size: 14pt; margin: 5px 0 3px 0; }}
.md-root h3 {{ font-size: 13pt; margin: 4px 0 2px 0; }}
.md-root h4 {{ font-size: 12pt; margin: 4px 0 2px 0; }}
.md-root h5 {{ font-size: 11pt; margin: 3px 0 2px 0; }}
.md-root h6 {{ font-size: 10pt; margin: 3px 0 2px 0; color: {colors['muted']}; }}
.md-root blockquote {{ margin: 4px 0 4px 6px; padding-left: 8px; border-left: 3px solid {colors['border']}; }}
.md-root pre, .md-root code {{ font-family: Consolas, "Cascadia Mono", monospace; }}
.md-root code {{ background-color: {colors['surface']}; white-space: pre-wrap; }}
.md-root ul, .md-root ol {{ margin: 3px 0 3px 18px; padding: 0; }}
.md-root li {{ margin: 1px 0; }}
.md-root table {{ border-collapse: collapse; margin: 5px 0; }}
.md-root th {{ background-color: {colors['surface']}; font-weight: bold; }}
.md-root th, .md-root td {{ border: 1px solid {colors['border']}; padding: 4px 6px; }}
.md-root hr {{ color: {colors['border']}; }}
.md-math, .md-math-block {{ color: {colors['code']}; background-color: {colors['surface']}; font-family: serif; }}
.md-blocked-link, .md-blocked-image {{ color: {colors['muted']}; }}
</style>
"""
    return f'{style}<div class="md-root">{rendered}</div>'


def _render_plain_message(value: object) -> str:
    escaped = html.escape(str(value or ""), quote=False).replace("\n", "<br>")
    return f'<div class="md-root"><p>{escaped}</p></div>'


def _is_system_packet(packet: Mapping[str, Any]) -> bool:
    return (
        str(packet.get("kind", "")).lower() == "system"
        or str(packet.get("sender_id", "")).lower() == "system"
        or str(packet.get("type", "")).lower() in _SYSTEM_PACKET_TYPES
    )


def _message_text(packet: Mapping[str, Any]) -> str:
    if str(packet.get("type", "")).lower() in _FILE_PACKET_TYPES:
        metadata = packet.get("file")
        file_info = metadata if isinstance(metadata, Mapping) else {}
        name = (
            file_info.get("original_name")
            or file_info.get("name")
            or file_info.get("filename")
            or "Unnamed file"
        )
        return f"共享文件：{name}\n{_format_size(file_info.get('size'))}"
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
        self._body.setMinimumWidth(0)
        self._body.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Preferred,
        )
        self._body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self._body.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._body.setOpenExternalLinks(False)
        self._body.linkActivated.connect(self._activate_url)
        self._bubble_layout.addWidget(self._body)

        self._file_button = QPushButton("下载", self._bubble)
        self._file_button.setProperty("ghost", True)
        self._file_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton)
        )
        self._file_button.setToolTip("下载共享文件")
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
            or ("服务器" if role == "system" else "未知用户")
        )
        self._sender_text = sender
        self._update_sender_label()
        self._time.setText(_timestamp_text(self._packet.get("timestamp")))

        is_file = str(self._packet.get("type", "")).lower() in _FILE_PACKET_TYPES
        message_text = _message_text(self._packet)
        if is_file:
            self._body.setProperty("sourceMarkdown", None)
            self._body.setText(_render_plain_message(message_text))
        else:
            self._body.setProperty("sourceMarkdown", message_text)
            self._body.setText(_render_markdown(message_text))
        metadata = self._packet.get("file")
        has_file_id = isinstance(metadata, Mapping) and bool(
            metadata.get("id") or metadata.get("file_id")
        )
        self._file_button.setVisible(is_file and has_file_id)
        self._bubble.setProperty("messageRole", role)
        self._bubble.setProperty("isFile", is_file)
        self.setProperty("messageRole", role)
        self._rebuild_alignment(role)
        self._repolish(self._bubble)
        self.updateGeometry()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt API name
        super().changeEvent(event)
        if event.type() not in {
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        }:
            return
        if not hasattr(self, "_body"):
            return
        source = self._body.property("sourceMarkdown")
        if isinstance(source, str):
            self._body.setText(_render_markdown(source))

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

    def upsert_message(self, packet: Mapping[str, Any]) -> None:
        """Replace a pending message with its server echo, or append it."""

        copied = _copy_packet(packet)
        message_id = str(copied.get("id", ""))
        if message_id:
            for index, existing in enumerate(self._packets):
                if str(existing.get("id", "")) != message_id:
                    continue
                self._packets[index] = copied
                item = self.item(index)
                row = self.itemWidget(item)
                if isinstance(row, BubbleRow):
                    row.refresh(copied, self._identity)
                self._apply_item_filter(index)
                self._refresh_widths()
                return
        self.append_message(copied)

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
