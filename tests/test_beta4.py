from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NEON_DRIVE_DISABLE_AUTO_UPDATE", "1")
os.environ.setdefault("NEON_DRIVE_DISABLE_NETWORK", "1")

from PySide6.QtWidgets import QApplication

from neon_drive.app import MainWindow
from neon_drive.drive_browser import DriveClient, GoogleDriveAuthError


class Beta4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def window(self) -> MainWindow:
        window = MainWindow()
        window.auto_health_timer.stop()
        window.notifications_check.setChecked(False)
        window.auto_start_check.setChecked(False)

        def cleanup() -> None:
            window.running = False
            window.workers.clear()
            window.queue.clear()
            window.force_exit = True
            window.close()
            window.deleteLater()
            self.app.processEvents()

        self.addCleanup(cleanup)
        return window

    def test_rclone_401_becomes_safe_reconnect_error(self) -> None:
        process = MagicMock()
        process.returncode = 1
        process.communicate.return_value = (
            b"",
            b"googleapi: Error 401: Invalid Credentials, authError",
        )
        with patch("neon_drive.drive_browser.subprocess.Popen", return_value=process):
            with self.assertRaises(GoogleDriveAuthError) as raised:
                DriveClient("rclone").shared_drive_ids()

        self.assertIn("Переподключите", str(raised.exception))
        self.assertNotIn("googleapi", str(raised.exception))

    def test_invalid_destination_token_offers_reconnect_and_keeps_path(self) -> None:
        window = self.window()
        window.upload_addon_enabled = True
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "movie.bin"
            source.write_bytes(b"neon")
            path = "H:/Unidades compartidas/Clients Materials/Test carpet"
            window.upload_sources.setPlainText(str(source))
            window.upload_destination.setText(path)
            with patch("neon_drive.app.google_drive_connected", return_value=True), patch.object(
                window,
                "resolve_explorer_google_destination",
                side_effect=GoogleDriveAuthError(),
            ), patch.object(window, "offer_google_reconnect") as reconnect:
                window.start_transfers("upload")

        reconnect.assert_called_once_with(
            "upload", path, preserve_cloud_destination=True
        )
        self.assertEqual(window.upload_destination.text(), path)
        self.assertFalse(window.running)

    def test_reconnect_action_retries_transfer_after_oauth(self) -> None:
        window = self.window()
        path = "H:/Unidades compartidas/Clients Materials/Test carpet"
        prompt = MagicMock()
        reconnect_button = object()
        prompt.addButton.side_effect = (reconnect_button, object())
        prompt.clickedButton.return_value = reconnect_button
        with patch("neon_drive.app.QMessageBox", return_value=prompt), patch.object(
            window, "start_google_drive_oauth"
        ) as oauth:
            window.offer_google_reconnect(
                "upload", path, preserve_cloud_destination=True
            )

        self.assertEqual(window._cloud_picker_request, ("upload", path))
        self.assertEqual(window._start_after_google_oauth, "upload")
        oauth.assert_called_once_with()

    def test_public_preview_uses_anonymous_path(self) -> None:
        preview = (
            Path(__file__).resolve().parents[1] / "scripts" / "preview_beta8.py"
        ).read_text(encoding="utf-8")
        self.assertIn('destination.setText("C:/Downloads")', preview)
        self.assertNotIn('destination.setText("C:/Users/', preview)

    def test_compact_transfer_buttons_are_not_clipped(self) -> None:
        window = self.window()
        window.resize(900, 640)
        window.show()
        self.app.processEvents()
        panel = window.transfer_panels["download"]
        for button in (
            panel.choose_file_button,
            panel.choose_files_button,
            panel.choose_folder_button,
            panel.start_button,
            panel.visible_stop_button,
            panel.hard_stop_button,
        ):
            self.assertGreater(button.width(), 100, button.text())
        self.assertFalse(
            panel.choose_file_button.geometry().intersects(
                panel.choose_folder_button.geometry()
            )
        )
        self.assertFalse(
            panel.start_button.geometry().intersects(
                panel.hard_stop_button.geometry()
            )
        )
        self.assertGreater(panel.hard_stop_button.width(), 150)


if __name__ == "__main__":
    unittest.main()
