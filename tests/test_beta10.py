from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NEON_DRIVE_DISABLE_AUTO_UPDATE", "1")
os.environ.setdefault("NEON_DRIVE_DISABLE_NETWORK", "1")

from PySide6.QtWidgets import QApplication

from neon_drive.app import MainWindow
from neon_drive.google_drive import (
    GOOGLE_DRIVE_REMOTE,
    managed_rclone_config_path,
    store_google_drive_token,
)


class Beta10Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def window(self) -> MainWindow:
        window = MainWindow()
        window.auto_health_timer.stop()
        window.notifications_check.setChecked(False)
        window.auto_start_check.setChecked(False)

        def cleanup() -> None:
            window.force_exit = True
            window.close()
            window.deleteLater()
            self.app.processEvents()

        self.addCleanup(cleanup)
        return window

    def test_account_selector_switches_managed_rclone_remote(self) -> None:
        config = managed_rclone_config_path()
        token = {"access_token": "access", "refresh_token": "refresh"}
        store_google_drive_token(
            token,
            config,
            remote_name=GOOGLE_DRIVE_REMOTE,
            kind="personal",
            identity={"email": "personal.user@gmail.com"},
        )
        store_google_drive_token(
            token,
            config,
            remote_name="NeonGoogleDrive_work",
            kind="workspace",
            identity={"email": "work.user@company.com"},
        )
        window = self.window()
        window.refresh_google_drive_status()

        self.assertEqual(window.google_account_combo.count(), 2)
        work_index = window.google_account_combo.findData("NeonGoogleDrive_work")
        window.google_account_combo.setCurrentIndex(work_index)

        self.assertEqual(
            window.resolve_explorer_google_destination("G:/My Drive/Video"),
            "NeonGoogleDrive_work:Video",
        )
        self.assertEqual(
            window.settings.value("active_google_drive_remote"),
            "NeonGoogleDrive_work",
        )

    def test_visible_stop_and_continue_reuse_same_worker(self) -> None:
        window = self.window()
        panel = window.bind_transfer_panel("upload")
        worker = MagicMock()
        window.workers = {"movie.mkv": worker}
        window.running = True
        window.set_transfer_controls_enabled(True)

        panel.visible_stop_button.click()

        worker.suspend.assert_called_once_with()
        self.assertTrue(window.paused)
        self.assertEqual(panel.visible_stop_button.text(), "Продолжить")

        panel.visible_stop_button.click()

        worker.resume.assert_called_once_with()
        self.assertFalse(window.paused)
        self.assertEqual(panel.visible_stop_button.text(), "Остановить")


if __name__ == "__main__":
    unittest.main()
