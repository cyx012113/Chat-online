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
from chat_online.secure_protocol import identity_fields
from chat_online.security import Identity, encrypt_file
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
        alice_identity = Identity.generate()
        bob_identity = Identity.generate()
        carol_identity = Identity.generate()
        storage = AppStorage(Path(temporary))
        encrypted_preview = encrypt_file(
            b"preview",
            "project-notes.txt",
            bob_identity.private_key,
            [
                alice_identity.public_key,
                bob_identity.public_key,
                carol_identity.public_key,
            ],
            authenticated_metadata={"room_id": "main", "timestamp": time.time()},
        )
        stored_preview = storage.save_file(
            encrypted_preview.envelope["file_id"],
            "encrypted-file",
            encrypted_preview.ciphertext,
            {
                "room_id": "main",
                "sender": "Bob",
                "sender_id": "bob-session",
                "uploaded_at": time.time(),
                "plaintext_size": len(b"preview"),
                "envelope": encrypted_preview.envelope,
                **identity_fields(bob_identity, sender=True),
            },
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

        client = ClientWindow(
            "192.168.1.10",
            8888,
            "Alice",
            auto_connect=False,
            security_dir=Path(temporary) / "client-security",
            identity=alice_identity,
        )
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
                    {
                        "id": "alice-session",
                        "username": "Alice",
                        "role": "member",
                        "muted": False,
                        **identity_fields(alice_identity),
                    },
                    {
                        "id": "bob-session",
                        "username": "Bob",
                        "role": "admin",
                        "muted": False,
                        **identity_fields(bob_identity),
                    },
                    {
                        "id": "carol-session",
                        "username": "Carol",
                        "role": "member",
                        "muted": False,
                        **identity_fields(carol_identity),
                    },
                ],
                "files": [
                    {
                        **stored_preview,
                        "id": encrypted_preview.envelope["file_id"],
                        "name": "Encrypted file",
                        "size": len(b"preview"),
                    },
                ],
            }
        )
        now = time.time()
        client.message_list.set_messages(
            [
                {"type": "message", "sender": "System", "sender_id": "system", "text": "Welcome to Main Lounge", "kind": "system", "timestamp": now - 180},
                {
                    "type": "message",
                    "sender": "Bob",
                    "sender_id": "bob-session",
                    "text": (
                        "### Build ready\n\n"
                        "- [x] TLS 1.3\n"
                        "- [ ] UI review\n\n"
                        "```python line-numbers lines=2-2\n"
                        "status = 'secure'\n"
                        "print(status)\n"
                        "```"
                    ),
                    "kind": "chat",
                    "timestamp": now - 90,
                },
                {
                    "type": "message",
                    "sender": "Alice",
                    "sender_id": "alice-session",
                    "text": "**收到**，查看 [测试报告](https://example.com/review)。",
                    "kind": "chat",
                    "timestamp": now - 35,
                },
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
