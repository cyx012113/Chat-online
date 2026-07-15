"""Render deterministic offscreen UI previews for visual regression checks."""

from __future__ import annotations

import argparse
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from chat_online.client_window import ClientWindow
from chat_online.server_window import ServerWindow
from chat_online.storage import AppStorage
from chat_online.theme import apply_theme


def render(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("ChatOnlinePreview")
    app.setApplicationName("ChatOnlinePreview")
    rendered: list[Path] = []

    with tempfile.TemporaryDirectory() as temporary:
        storage = AppStorage(Path(temporary))
        storage.save_file(
            "preview-file",
            "project-notes.txt",
            b"preview",
            {"room_id": "main", "sender": "Alice", "uploaded_at": time.time()},
        )

        apply_theme(app, "light")
        server = ServerWindow(storage=storage, auto_start=False)
        server._on_snapshot(
            {
                "running": False,
                "host": "0.0.0.0",
                "port": 8888,
                "started_at": None,
                "file_count": 1,
                "banned": ["192.168.20.0/24"],
                "users": [
                    {
                        "id": "alice-session",
                        "username": "Alice",
                        "ip": "192.168.1.24",
                        "port": 52314,
                        "current_room": "main",
                        "rooms": ["main"],
                    },
                    {
                        "id": "bob-session",
                        "username": "Bob",
                        "ip": "192.168.1.31",
                        "port": 52315,
                        "current_room": "room-design",
                        "rooms": ["main", "room-design"],
                    },
                ],
                "rooms": [
                    {"id": "main", "name": "Main Lounge", "member_count": 2, "invite_code": None},
                    {"id": "room-design", "name": "Design Team", "member_count": 1, "invite_code": "N7D4K2P9"},
                ],
            }
        )
        server._append_log("info", "Server configuration is ready")
        server._append_log("message", "[Main Lounge] Alice: Morning update")
        server.resize(1180, 760)
        server.show()
        app.processEvents()
        server_path = output_dir / "server-light.png"
        server.grab().save(str(server_path))
        rendered.append(server_path)
        server.close()

        client = ClientWindow("192.168.1.10", 8888, "Alice", auto_connect=False)
        client.session_id = "alice-session"
        client.message_list.set_identity("alice-session")
        client._apply_snapshot(
            {
                "type": "snapshot",
                "current_room": "main",
                "rooms": [
                    {"id": "main", "name": "Main Lounge", "member_count": 3, "role": "member", "muted": False},
                    {"id": "room-design", "name": "Design Team", "member_count": 2, "role": "owner", "muted": False, "invite_code": "N7D4K2P9"},
                ],
                "users": [
                    {"id": "alice-session", "username": "Alice", "role": "member", "muted": False},
                    {"id": "bob-session", "username": "Bob", "role": "admin", "muted": False},
                    {"id": "carol-session", "username": "Carol", "role": "member", "muted": False},
                ],
                "files": [
                    {"id": "preview-file", "name": "project-notes.txt", "size": 2048, "sender": "Bob", "uploaded_at": time.time()},
                ],
            }
        )
        now = time.time()
        client.message_list.set_messages(
            [
                {"type": "message", "sender": "System", "sender_id": "system", "text": "Welcome to Main Lounge", "kind": "system", "timestamp": now - 180},
                {"type": "message", "sender": "Bob", "sender_id": "bob-session", "text": "The latest build is ready for review: https://example.com/review", "kind": "chat", "timestamp": now - 90},
                {"type": "message", "sender": "Alice", "sender_id": "alice-session", "text": "Thanks, I will check it now.", "kind": "chat", "timestamp": now - 35},
                {"type": "file_shared", "sender": "Bob", "file": {"id": "preview-file", "name": "project-notes.txt", "size": 2048}, "timestamp": now - 10},
            ]
        )
        client.resize(1240, 760)
        client.show()
        app.processEvents()
        for theme in ("light", "dark"):
            apply_theme(app, theme)
            app.processEvents()
            path = output_dir / f"client-{theme}.png"
            client.grab().save(str(path))
            rendered.append(path)
        client.close()

    return rendered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    for path in render(args.output_dir):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

