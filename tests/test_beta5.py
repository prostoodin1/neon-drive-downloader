from __future__ import annotations

import os
import tempfile
import unittest
from collections import deque
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NEON_DRIVE_DISABLE_AUTO_UPDATE", "1")
os.environ.setdefault("NEON_DRIVE_DISABLE_NETWORK", "1")

from PySide6.QtWidgets import QApplication

from neon_drive.app import MainWindow, TaskInfo
from neon_drive.google_drive import google_drive_accounts, store_google_drive_token


class Beta5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def window(self) -> MainWindow:
        window = MainWindow()
        window.auto_health_timer.stop()
        window.notifications_check.setChecked(False)
        window.auto_start_check.setChecked(False)
        self.addCleanup(self._close, window)
        return window

    def _close(self, window: MainWindow) -> None:
        window.running = False
        window.workers.clear()
        window.queue.clear()
        window.force_exit = True
        window.close()
        window.deleteLater()
        self.app.processEvents()

    def test_placeholder_email_is_never_presented_as_oauth_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "rclone.conf"
            store_google_drive_token(
                {"access_token": "access", "refresh_token": "refresh"},
                config,
                identity={"email": "team@example.com"},
            )
            account = google_drive_accounts(config)[0]
            self.assertEqual(account.email, "")
            self.assertNotIn("example.com", account.label)
            self.assertFalse(account.identity_verified)

    def test_settings_state_exactly_names_verified_oauth_email(self) -> None:
        from neon_drive.google_drive import managed_rclone_config_path

        store_google_drive_token(
            {"access_token": "access", "refresh_token": "refresh"},
            managed_rclone_config_path(),
            identity={"email": "owner@gmail.com", "display_name": "Owner"},
        )
        window = self.window()
        window.refresh_google_drive_status()
        self.assertIn("OAuth2 подключён к: owner@gmail.com", window.google_account_identity.text())
        self.assertIn("OAuth2: owner@gmail.com", window.google_drive_status.text())

    def test_direct_google_queue_runs_every_file_one_after_another(self) -> None:
        window = self.window()
        window.running = True
        window.active_destination = "NeonGoogleDrive:Uploads"
        window.total_items = 3
        names = (
            "NeonGoogleDriveSource:one.bin",
            "NeonGoogleDriveSource:two.bin",
            "NeonGoogleDriveSource:three.bin",
        )
        window.tasks = {name: TaskInfo(source=name, size=100) for name in names}
        window.queue = deque(window.tasks)
        started: list[str] = []

        def start_fake(source: str) -> None:
            started.append(source)
            window.workers[source] = object()

        window.start_task = start_fake
        window.fill_worker_slots()
        self.assertEqual(started, [names[0]])
        self.assertEqual(list(window.queue), [names[1], names[2]])

        window.workers.clear()
        window.fill_worker_slots()
        self.assertEqual(started, [names[0], names[1]])

    def test_sent_bytes_wait_for_google_confirmation_before_100_percent(self) -> None:
        window = self.window()
        panel = window.bind_transfer_panel("upload")
        window.total_items = 3
        window.completed_items = 1
        window.total_bytes = 300
        window.measured_done_bytes = 300
        window.update_overall_progress()
        self.assertEqual(panel.ring.value, 99)
        self.assertIn("ГОТОВО 1 ИЗ 3", panel.progress_text.text())


if __name__ == "__main__":
    unittest.main()
