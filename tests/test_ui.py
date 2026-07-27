from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from chat_online.client_window import ClientWindow
from chat_online.launcher import LauncherDialog
from chat_online.secure_protocol import (
    encode_file_ciphertext,
    identity_fields,
    message_envelope,
)
from chat_online.security import Identity, encrypt_file
from chat_online.server import MAIN_ROOM_ID
from chat_online.server_window import ServerWindow
from chat_online.storage import AppStorage
from chat_online.theme import apply_theme, configure_application_font
from chat_online.widgets import BubbleRow


APP = QApplication.instance() or QApplication([])
APP.setOrganizationName("ChatOnlineTests")
APP.setApplicationName("ChatOnlineTests")


class UiSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            self.temporary.name,
        )
        QSettings().clear()
        apply_theme(APP, "light")

    def tearDown(self) -> None:
        APP.closeAllWindows()
        APP.processEvents()
        self.temporary.cleanup()

    def test_launcher_constructs_and_switches_modes(self) -> None:
        self.assertTrue(configure_application_font(APP))
        dialog = LauncherDialog("client")
        dialog.show()
        APP.processEvents()
        self.assertEqual(dialog.selected_mode(), "client")
        dialog.set_mode("server")
        self.assertEqual(dialog.config().mode, "server")
        self.assertGreater(dialog.width(), 0)
        self.assertTrue(self._nonblank(dialog))
        dialog.close()

    def test_server_window_constructs_at_minimum_size_in_both_themes(self) -> None:
        storage = AppStorage(Path(self.temporary.name) / "server-data")
        window = ServerWindow(storage=storage, auto_start=False)
        window.resize(900, 600)
        window.show()
        APP.processEvents()
        self.assertFalse(window.engine.running)
        self.assertTrue(window.start_button.isEnabled())
        self.assertTrue(self._nonblank(window))
        light = APP.palette().color(APP.palette().ColorRole.Window)
        apply_theme(APP, "dark")
        APP.processEvents()
        dark = APP.palette().color(APP.palette().ColorRole.Window)
        self.assertNotEqual(light, dark)
        window.close()

    def test_client_window_constructs_and_queues_offline_message(self) -> None:
        window = ClientWindow(
            "127.0.0.1",
            65535,
            "Alice",
            auto_connect=False,
            security_dir=Path(self.temporary.name) / "client-security",
        )
        window.resize(900, 600)
        window.show()
        APP.processEvents()
        window.composer.setPlainText("offline hello")
        window.send_message()
        self.assertEqual(len(window.pending_packets), 1)
        self.assertEqual(len(window.message_list.packets()), 1)
        self.assertTrue(self._nonblank(window))
        window.close()

    def test_client_composer_layout_is_stable_at_supported_sizes(self) -> None:
        window = ClientWindow(
            "127.0.0.1",
            65535,
            "Alice",
            auto_connect=False,
            security_dir=Path(self.temporary.name) / "client-security",
        )
        window.show()
        for width, height in ((900, 600), (1240, 760)):
            window.resize(width, height)
            APP.processEvents()
            self.assertGreaterEqual(window.composer.height(), 62)
            self.assertGreater(window.composer.width(), 220)
            self.assertLess(window.composer.geometry().bottom(), window.emoji_button.geometry().top())
            self.assertEqual(window.emoji_button.geometry().top(), window.attach_button.geometry().top())
            self.assertLessEqual(
                abs(
                    window.attach_button.geometry().center().y()
                    - window.send_button.geometry().center().y()
                ),
                1,
            )
            self.assertLessEqual(
                window.send_button.geometry().right(),
                window.send_button.parentWidget().contentsRect().right(),
            )
            self.assertTrue(self._nonblank(window))
        window.close()

    def test_system_bubble_and_file_id_are_presented_as_actions(self) -> None:
        system = BubbleRow({"type": "system", "text": "服务器维护通知"})
        self.assertEqual(system._bubble.property("messageRole"), "system")

        received: list[dict] = []
        file_row = BubbleRow(
            {
                "type": "file_shared",
                "file": {"id": "file-1", "name": "report.txt", "size": 12},
            }
        )
        file_row.fileActivated.connect(received.append)
        file_row.show()
        APP.processEvents()
        self.assertFalse(file_row._file_button.isHidden())
        file_row._file_button.click()
        self.assertEqual(received[0]["id"], "file-1")
        system.close()
        file_row.close()

    def test_file_bubble_download_uses_metadata_id(self) -> None:
        window = ClientWindow(
            "127.0.0.1",
            65535,
            "Alice",
            auto_connect=False,
            security_dir=Path(self.temporary.name) / "client-security",
        )
        destination = Path(self.temporary.name) / "download.txt"
        window._send_or_warn = Mock(return_value=True)
        window.message_list.append_message(
            {
                "type": "file_shared",
                "file": {"id": "file-2", "name": "download.txt", "size": 4},
            }
        )
        row = window.message_list.itemWidget(window.message_list.item(0))
        with patch(
            "chat_online.client_window.QFileDialog.getSaveFileName",
            return_value=(str(destination), ""),
        ):
            row._file_button.click()
        window._send_or_warn.assert_called_once_with(
            {"type": "download_file", "file_id": "file-2"}
        )
        self.assertEqual(window.pending_downloads["file-2"], destination)
        self.assertEqual(window.transfer_status.property("tone"), "info")
        window.close()

    def test_inactive_window_attention_is_throttled_and_ignores_own_echo(self) -> None:
        window = ClientWindow(
            "127.0.0.1",
            65535,
            "Alice",
            auto_connect=False,
            security_dir=Path(self.temporary.name) / "client-security",
        )
        window.session_id = "self-id"
        window._request_attention = Mock()
        window.hide()

        incoming = {
            "type": "message",
            "scope": "room",
            "room_id": MAIN_ROOM_ID,
            "sender": "Bob",
            "sender_id": "bob-id",
            "text": "hello",
        }
        window._receive_message(incoming)
        window._receive_message(incoming)
        window._receive_message({**incoming, "sender_id": "self-id"})
        window._request_attention.assert_called_once_with()

        window._last_attention_at = 0.0
        window._receive_file_shared(
            {
                "type": "file_shared",
                "room_id": MAIN_ROOM_ID,
                "file": {
                    "id": "file-3",
                    "name": "notes.txt",
                    "size": 8,
                    "sender_id": "bob-id",
                },
            }
        )
        self.assertEqual(window._request_attention.call_count, 2)
        window.close()

    def test_client_decrypts_verified_messages_and_files(self) -> None:
        window = ClientWindow(
            "127.0.0.1",
            65535,
            "Alice",
            auto_connect=False,
            security_dir=Path(self.temporary.name) / "client-security",
        )
        window.session_id = "alice-id"
        bob = Identity.generate()
        timestamp = 1_700_000_000.0
        envelope = message_envelope(
            bob,
            [window.identity.public_key, bob.public_key],
            "verified secret",
            scope="room",
            room_id=MAIN_ROOM_ID,
            timestamp=timestamp,
        )
        packet = {
            "type": "message",
            "id": envelope["metadata"]["id"],
            "scope": "room",
            "room_id": MAIN_ROOM_ID,
            "sender": "Bob",
            "sender_id": "bob-id",
            "kind": "chat",
            "timestamp": timestamp,
            "envelope": envelope,
            **identity_fields(bob, sender=True),
        }
        decoded = window._decrypt_message_packet(packet)
        self.assertEqual(decoded["text"], "verified secret")
        self.assertTrue(decoded["encrypted"])

        encrypted_file = encrypt_file(
            b"verified file bytes",
            "verified.txt",
            bob.private_key,
            [window.identity.public_key, bob.public_key],
            authenticated_metadata={
                "room_id": MAIN_ROOM_ID,
                "timestamp": timestamp,
            },
        )
        file_metadata = {
            "id": encrypted_file.envelope["file_id"],
            "name": "Encrypted file",
            "room_id": MAIN_ROOM_ID,
            "sender": "Bob",
            "sender_id": "bob-id",
            "timestamp": timestamp,
            "envelope": encrypted_file.envelope,
            **identity_fields(bob, sender=True),
        }
        normalized = window._decrypt_file_metadata(file_metadata)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["name"], "verified.txt")

        destination = Path(self.temporary.name) / "verified.txt"
        window.pending_downloads[file_metadata["id"]] = destination
        window._receive_file_data(
            {
                "type": "file_data",
                "file": file_metadata,
                "data": encode_file_ciphertext(encrypted_file.ciphertext),
            }
        )
        self.assertEqual(destination.read_bytes(), b"verified file bytes")
        window.close()

    @staticmethod
    def _nonblank(widget) -> bool:
        image = widget.grab().toImage()
        colors: set[int] = set()
        x_step = max(1, image.width() // 12)
        y_step = max(1, image.height() // 10)
        for x in range(0, image.width(), x_step):
            for y in range(0, image.height(), y_step):
                colors.add(QColor(image.pixel(x, y)).rgb())
        return len(colors) > 1


if __name__ == "__main__":
    unittest.main()
