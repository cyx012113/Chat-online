from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from chat_online.client_window import ClientWindow
from chat_online.launcher import LauncherDialog
from chat_online.server_window import ServerWindow
from chat_online.storage import AppStorage
from chat_online.theme import apply_theme, configure_application_font


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
