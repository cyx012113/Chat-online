"""Application and command-line entry points."""

from __future__ import annotations

import argparse
import getpass
import sys
import traceback
from pathlib import Path
from typing import Sequence

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from .assets import app_icon
from .cli import run_headless_server
from .client_window import ClientWindow
from .launcher import LauncherDialog
from .server_window import ServerWindow
from .storage import AppStorage, StorageError
from .theme import THEME_NAMES, apply_theme, normalize_theme
from .version import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chat-online",
        description="PyQt6 LAN chat client and server",
    )
    parser.add_argument("--mode", choices=("server", "client"), help="start directly in server or client mode")
    parser.add_argument("--host", help="server address (client) or bind address (server)")
    parser.add_argument("--port", type=int, help="TCP port, default 8888")
    parser.add_argument("--username", help="client username; defaults to the operating-system account")
    parser.add_argument("--theme", choices=THEME_NAMES, help="light or dark interface theme")
    parser.add_argument("--data-dir", type=Path, help="server history and attachment directory")
    parser.add_argument("--headless", action="store_true", help="run the server without a graphical window")
    parser.add_argument("--no-auto-start", action="store_true", help="open the selected window without connecting or listening")
    parser.add_argument("--reset-settings", action="store_true", help="clear saved window and connection settings")
    parser.add_argument("--version", action="version", version=f"Chat Online {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.port is not None and not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.mode == "client" and args.port == 0:
        parser.error("--port 0 is only valid for server mode")
    if args.headless and args.mode != "server":
        parser.error("--headless requires --mode server")
    if args.headless and args.no_auto_start:
        parser.error("--headless cannot be combined with --no-auto-start")

    if args.headless:
        try:
            storage = AppStorage(args.data_dir)
        except (OSError, StorageError) as exc:
            print(f"chat-online: cannot initialize data directory: {exc}", file=sys.stderr)
            return 2
        headless_port = args.port if args.port is not None else 8888
        return run_headless_server(args.host or "0.0.0.0", headless_port, storage)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setOrganizationName("ChatOnline")
    app.setOrganizationDomain("chat-online.local")
    app.setApplicationName("Chat Online")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(True)

    settings = QSettings()
    if args.reset_settings:
        settings.clear()
    theme = normalize_theme(args.theme or str(settings.value("appearance/theme", "light")))
    apply_theme(app, theme)
    settings.setValue("appearance/theme", theme)

    try:
        storage = AppStorage(args.data_dir)
    except (OSError, StorageError) as exc:
        QMessageBox.critical(None, "无法初始化数据目录", str(exc))
        return 2
    _install_exception_hook(storage)

    mode = args.mode
    host = args.host
    port = args.port
    username = args.username
    if mode is None:
        launcher = LauncherDialog("client")
        if launcher.exec() != QDialog.DialogCode.Accepted:
            return 0
        config = launcher.config()
        mode = config.mode
        host = config.host
        port = config.port
        username = config.username
        theme = config.theme

    if mode == "server":
        window = ServerWindow(
            host=host or "0.0.0.0",
            port=port if port is not None else int(settings.value("server/port", 8888)),
            theme=theme,
            storage=storage,
            auto_start=not args.no_auto_start,
        )
    else:
        default_username = str(settings.value("client/username", "")).strip() or getpass.getuser()
        clean_username = " ".join((username or default_username or "User").split())[:24]
        window = ClientWindow(
            host=host or str(settings.value("client/host", "127.0.0.1")),
            port=port if port is not None else int(settings.value("client/port", 8888)),
            username=clean_username,
            theme=theme,
            auto_connect=not args.no_auto_start,
        )
    window.setWindowIcon(app_icon())
    window.show()
    return app.exec()


def _install_exception_hook(storage: AppStorage) -> None:
    log_path = storage.root / "crash.log"

    def report(exc_type, exc_value, exc_traceback) -> None:
        rendered = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        try:
            log_path.write_text(rendered, encoding="utf-8")
        except OSError:
            pass
        if sys.stderr is not None:
            print(rendered, file=sys.stderr)
        app = QApplication.instance()
        if app is not None:
            QMessageBox.critical(
                None,
                "Chat Online 遇到错误",
                f"{exc_value}\n\n诊断信息已写入：{log_path}",
            )

    sys.excepthook = report
