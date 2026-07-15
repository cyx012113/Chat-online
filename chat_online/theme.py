"""Application-wide light and dark themes for the PyQt6 interface."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PyQt6.QtWidgets import QApplication


THEME_NAMES: Final[tuple[str, str]] = ("light", "dark")

_FONT_CANDIDATES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("Microsoft YaHei UI", ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc")),
    ("Segoe UI", ("C:/Windows/Fonts/segoeui.ttf",)),
    ("PingFang SC", ("/System/Library/Fonts/PingFang.ttc",)),
    ("Noto Sans CJK SC", ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",)),
    ("DejaVu Sans", ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",)),
)


_PALETTES: Final[dict[str, dict[str, str]]] = {
    "light": {
        "bg": "#F6F7F9",
        "surface": "#FFFFFF",
        "surface_alt": "#F0F2F5",
        "surface_hover": "#E8ECF1",
        "text": "#1B1F24",
        "muted": "#667085",
        "disabled": "#98A2B3",
        "border": "#D8DEE6",
        "border_strong": "#B8C1CC",
        "accent": "#2563EB",
        "accent_hover": "#1D4ED8",
        "accent_pressed": "#1E40AF",
        "accent_soft": "#DBEAFE",
        "accent_soft_hover": "#BFDBFE",
        "success": "#16803C",
        "success_soft": "#DCFCE7",
        "warning": "#B7791F",
        "warning_soft": "#FEF3C7",
        "danger": "#C73535",
        "danger_hover": "#A92525",
        "danger_soft": "#FEE2E2",
        "danger_text": "#FFFFFF",
        "link": "#1D4ED8",
        "selection_text": "#FFFFFF",
        "tooltip_bg": "#202328",
        "tooltip_text": "#F8FAFC",
    },
    "dark": {
        "bg": "#17191D",
        "surface": "#202328",
        "surface_alt": "#292D33",
        "surface_hover": "#30353D",
        "text": "#F3F4F6",
        "muted": "#AAB2BD",
        "disabled": "#737B87",
        "border": "#343A43",
        "border_strong": "#4A515C",
        "accent": "#60A5FA",
        "accent_hover": "#7DB4FF",
        "accent_pressed": "#3B82F6",
        "accent_soft": "#1E3A5F",
        "accent_soft_hover": "#234A76",
        "success": "#4ADE80",
        "success_soft": "#173B28",
        "warning": "#FBBF24",
        "warning_soft": "#493817",
        "danger": "#F87171",
        "danger_hover": "#FCA5A5",
        "danger_soft": "#4A2024",
        "danger_text": "#1B1F24",
        "link": "#7DB4FF",
        "selection_text": "#111827",
        "tooltip_bg": "#F3F4F6",
        "tooltip_text": "#202328",
    },
}


def normalize_theme(name: object) -> str:
    """Return a supported theme name, defaulting invalid values to light."""

    candidate = str(name).strip().lower() if name is not None else ""
    return candidate if candidate in THEME_NAMES else "light"


def opposite_theme(name: object) -> str:
    """Return the theme on the opposite side of the light/dark pair."""

    return "dark" if normalize_theme(name) == "light" else "light"


def configure_application_font(app: QApplication) -> str:
    """Select and, when needed, register a readable cross-platform UI font."""
    available = set(QFontDatabase.families())
    selected = next((family for family, _paths in _FONT_CANDIDATES if family in available), "")
    if not selected:
        for preferred_family, paths in _FONT_CANDIDATES:
            for path in paths:
                if not Path(path).is_file():
                    continue
                font_id = QFontDatabase.addApplicationFont(path)
                if font_id < 0:
                    continue
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    selected = families[0]
                    break
            if selected:
                break
    if not selected:
        selected = "sans-serif"
    font = QFont(selected)
    font.setPointSize(10)
    app.setFont(font)
    return selected


def _build_palette(colors: dict[str, str]) -> QPalette:
    palette = QPalette()
    role = QPalette.ColorRole
    group = QPalette.ColorGroup

    palette.setColor(role.Window, QColor(colors["bg"]))
    palette.setColor(role.WindowText, QColor(colors["text"]))
    palette.setColor(role.Base, QColor(colors["surface"]))
    palette.setColor(role.AlternateBase, QColor(colors["surface_alt"]))
    palette.setColor(role.ToolTipBase, QColor(colors["tooltip_bg"]))
    palette.setColor(role.ToolTipText, QColor(colors["tooltip_text"]))
    palette.setColor(role.Text, QColor(colors["text"]))
    palette.setColor(role.Button, QColor(colors["surface_alt"]))
    palette.setColor(role.ButtonText, QColor(colors["text"]))
    palette.setColor(role.BrightText, QColor(colors["danger"]))
    palette.setColor(role.Link, QColor(colors["link"]))
    palette.setColor(role.LinkVisited, QColor(colors["link"]))
    palette.setColor(role.Highlight, QColor(colors["accent"]))
    palette.setColor(role.HighlightedText, QColor(colors["selection_text"]))
    palette.setColor(role.PlaceholderText, QColor(colors["muted"]))

    palette.setColor(group.Disabled, role.WindowText, QColor(colors["disabled"]))
    palette.setColor(group.Disabled, role.Text, QColor(colors["disabled"]))
    palette.setColor(group.Disabled, role.ButtonText, QColor(colors["disabled"]))
    palette.setColor(group.Disabled, role.Highlight, QColor(colors["surface_hover"]))
    palette.setColor(group.Disabled, role.HighlightedText, QColor(colors["disabled"]))
    return palette


def _build_stylesheet(colors: dict[str, str]) -> str:
    return f"""
QWidget {{
    color: {colors['text']};
    background-color: transparent;
    selection-background-color: {colors['accent']};
    selection-color: {colors['selection_text']};
}}

QMainWindow,
QDialog {{
    background-color: {colors['bg']};
}}

QWidget#sidebar,
QFrame#sidebar,
QWidget[role="sidebar"],
QFrame[role="sidebar"] {{
    background-color: {colors['surface']};
    border-right: 1px solid {colors['border']};
}}

QWidget#panel,
QFrame#panel,
QWidget[role="panel"],
QFrame[role="panel"] {{
    background-color: {colors['surface']};
    border: 1px solid {colors['border']};
    border-radius: 8px;
}}

QLabel[muted="true"] {{
    color: {colors['muted']};
}}

QPushButton,
QToolButton {{
    min-height: 22px;
    padding: 6px 12px;
    color: {colors['text']};
    background-color: {colors['surface_alt']};
    border: 1px solid {colors['border']};
    border-radius: 6px;
}}

QToolButton {{
    padding: 5px;
}}

QPushButton:hover,
QToolButton:hover {{
    background-color: {colors['surface_hover']};
    border-color: {colors['border_strong']};
}}

QPushButton:pressed,
QToolButton:pressed {{
    background-color: {colors['border']};
}}

QPushButton:focus,
QToolButton:focus {{
    border: 1px solid {colors['accent']};
}}

QPushButton:disabled,
QToolButton:disabled {{
    color: {colors['disabled']};
    background-color: {colors['surface_alt']};
    border-color: {colors['border']};
}}

QPushButton[primary="true"],
QToolButton[primary="true"] {{
    color: {colors['selection_text']};
    background-color: {colors['accent']};
    border-color: {colors['accent']};
    font-weight: 600;
}}

QPushButton[primary="true"]:hover,
QToolButton[primary="true"]:hover {{
    background-color: {colors['accent_hover']};
    border-color: {colors['accent_hover']};
}}

QPushButton[primary="true"]:pressed,
QToolButton[primary="true"]:pressed {{
    background-color: {colors['accent_pressed']};
    border-color: {colors['accent_pressed']};
}}

QPushButton[danger="true"],
QToolButton[danger="true"] {{
    color: {colors['danger_text']};
    background-color: {colors['danger']};
    border-color: {colors['danger']};
}}

QPushButton[danger="true"]:hover,
QToolButton[danger="true"]:hover {{
    background-color: {colors['danger_hover']};
    border-color: {colors['danger_hover']};
}}

QPushButton[ghost="true"],
QToolButton[ghost="true"] {{
    color: {colors['text']};
    background-color: transparent;
    border-color: transparent;
}}

QPushButton[ghost="true"]:hover,
QToolButton[ghost="true"]:hover {{
    background-color: {colors['surface_hover']};
    border-color: transparent;
}}

QLineEdit,
QPlainTextEdit,
QTextEdit,
QTextBrowser,
QSpinBox,
QDoubleSpinBox,
QComboBox {{
    color: {colors['text']};
    background-color: {colors['surface']};
    border: 1px solid {colors['border']};
    border-radius: 6px;
    padding: 7px 9px;
}}

QLineEdit:focus,
QPlainTextEdit:focus,
QTextEdit:focus,
QTextBrowser:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QComboBox:focus {{
    border-color: {colors['accent']};
}}

QLineEdit:disabled,
QPlainTextEdit:disabled,
QTextEdit:disabled,
QComboBox:disabled {{
    color: {colors['disabled']};
    background-color: {colors['surface_alt']};
}}

QComboBox::drop-down {{
    width: 26px;
    border: 0;
}}

QComboBox QAbstractItemView {{
    color: {colors['text']};
    background-color: {colors['surface']};
    border: 1px solid {colors['border']};
    selection-background-color: {colors['accent_soft']};
    selection-color: {colors['text']};
    outline: 0;
}}

QListWidget,
QTreeWidget,
QTableWidget,
QListView,
QTreeView,
QTableView {{
    color: {colors['text']};
    background-color: {colors['surface']};
    alternate-background-color: {colors['surface_alt']};
    border: 1px solid {colors['border']};
    border-radius: 6px;
    outline: 0;
}}

QListWidget::item,
QListView::item,
QTreeView::item {{
    padding: 6px;
    border-radius: 4px;
}}

QListWidget::item:hover,
QListView::item:hover,
QTreeView::item:hover {{
    background-color: {colors['surface_hover']};
}}

QListWidget::item:selected,
QListView::item:selected,
QTreeView::item:selected,
QTableView::item:selected {{
    color: {colors['text']};
    background-color: {colors['accent_soft']};
}}

QListWidget#messageList {{
    background-color: transparent;
    border: 0;
}}

QListWidget#messageList::item,
QListWidget#messageList::item:hover,
QListWidget#messageList::item:selected {{
    background-color: transparent;
    border: 0;
    padding: 0;
}}

QHeaderView::section {{
    color: {colors['muted']};
    background-color: {colors['surface_alt']};
    border: 0;
    border-right: 1px solid {colors['border']};
    border-bottom: 1px solid {colors['border']};
    padding: 7px 9px;
    font-weight: 600;
}}

QTabWidget::pane {{
    background-color: {colors['surface']};
    border: 1px solid {colors['border']};
    border-radius: 6px;
    top: -1px;
}}

QTabBar::tab {{
    color: {colors['muted']};
    background-color: transparent;
    border: 0;
    border-bottom: 2px solid transparent;
    padding: 8px 12px;
}}

QTabBar::tab:hover {{
    color: {colors['text']};
    background-color: {colors['surface_hover']};
}}

QTabBar::tab:selected {{
    color: {colors['text']};
    border-bottom-color: {colors['accent']};
    font-weight: 600;
}}

QSplitter::handle {{
    background-color: {colors['border']};
}}

QSplitter::handle:horizontal {{
    width: 1px;
    margin: 0 3px;
}}

QSplitter::handle:vertical {{
    height: 1px;
    margin: 3px 0;
}}

QSplitter::handle:hover {{
    background-color: {colors['accent']};
}}

QMenuBar {{
    color: {colors['text']};
    background-color: {colors['surface']};
    border-bottom: 1px solid {colors['border']};
}}

QMenuBar::item {{
    padding: 6px 9px;
    background-color: transparent;
}}

QMenuBar::item:selected {{
    background-color: {colors['surface_hover']};
    border-radius: 4px;
}}

QMenu {{
    color: {colors['text']};
    background-color: {colors['surface']};
    border: 1px solid {colors['border']};
    padding: 5px;
}}

QMenu::item {{
    padding: 7px 28px 7px 10px;
    border-radius: 4px;
}}

QMenu::item:selected {{
    background-color: {colors['accent_soft']};
}}

QMenu::item:disabled {{
    color: {colors['disabled']};
}}

QMenu::separator {{
    height: 1px;
    background-color: {colors['border']};
    margin: 5px 8px;
}}

QToolTip {{
    color: {colors['tooltip_text']};
    background-color: {colors['tooltip_bg']};
    border: 1px solid {colors['border_strong']};
    border-radius: 4px;
    padding: 5px 7px;
}}

QScrollBar:vertical {{
    width: 11px;
    margin: 2px;
    background: transparent;
}}

QScrollBar:horizontal {{
    height: 11px;
    margin: 2px;
    background: transparent;
}}

QScrollBar::handle:vertical,
QScrollBar::handle:horizontal {{
    min-height: 28px;
    min-width: 28px;
    background-color: {colors['border_strong']};
    border-radius: 4px;
}}

QScrollBar::handle:vertical:hover,
QScrollBar::handle:horizontal:hover {{
    background-color: {colors['muted']};
}}

QScrollBar::add-line,
QScrollBar::sub-line,
QScrollBar::add-page,
QScrollBar::sub-page {{
    width: 0;
    height: 0;
    background: transparent;
}}

QFrame#messageBubble {{
    background-color: {colors['surface_alt']};
    border: 1px solid {colors['border']};
    border-radius: 8px;
}}

QFrame#messageBubble[messageRole="self"] {{
    background-color: {colors['accent_soft']};
    border-color: {colors['accent_soft_hover']};
}}

QFrame#messageBubble[messageRole="system"] {{
    background-color: transparent;
    border-color: transparent;
}}

QFrame#messageBubble[isFile="true"] {{
    border-left: 3px solid {colors['accent']};
}}

QLabel#messageMeta {{
    color: {colors['muted']};
    font-size: 9pt;
}}

QLabel#messageBody {{
    color: {colors['text']};
    background-color: transparent;
}}

QLabel#statusPill {{
    color: {colors['muted']};
    background-color: {colors['surface_alt']};
    border: 1px solid {colors['border']};
    border-radius: 8px;
    padding: 2px 8px;
    font-size: 9pt;
    font-weight: 600;
}}

QLabel#statusPill[tone="info"] {{
    color: {colors['accent']};
    background-color: {colors['accent_soft']};
    border-color: {colors['accent_soft_hover']};
}}

QLabel#statusPill[tone="success"] {{
    color: {colors['success']};
    background-color: {colors['success_soft']};
    border-color: {colors['success']};
}}

QLabel#statusPill[tone="warning"] {{
    color: {colors['warning']};
    background-color: {colors['warning_soft']};
    border-color: {colors['warning']};
}}

QLabel#statusPill[tone="danger"] {{
    color: {colors['danger']};
    background-color: {colors['danger_soft']};
    border-color: {colors['danger']};
}}
"""


def apply_theme(app: QApplication, name: object) -> str:
    """Apply a normalized theme to *app* and return its canonical name."""

    normalized = normalize_theme(name)
    colors = _PALETTES[normalized]
    app.setStyle("Fusion")
    configure_application_font(app)
    app.setProperty("theme", normalized)
    app.setPalette(_build_palette(colors))
    app.setStyleSheet(_build_stylesheet(colors))
    return normalized


__all__ = [
    "THEME_NAMES",
    "apply_theme",
    "normalize_theme",
    "opposite_theme",
]
