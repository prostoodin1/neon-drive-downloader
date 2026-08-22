from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["NEON_DRIVE_DISABLE_NETWORK"] = "1"

from PySide6.QtWidgets import QApplication

from neon_drive.version_manager import (
    VersionManagerWindow,
    close_main_app,
    main_app_running,
)


class VersionManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_release_list_shows_channel_changelog_and_installed_marker(self) -> None:
        with patch(
            "neon_drive.version_manager.installed_details",
            return_value=("5.5.0-beta.1", os.path.abspath("installed")),
        ):
            window = VersionManagerWindow()
        releases = [
            {
                "tag": "v5.5.0-beta.1",
                "version": "5.5.0-beta.1",
                "name": "Neon Drive 5.5 Beta 1",
                "notes": "- Новый интерфейс\n- Менеджер версий",
                "published_at": "2026-08-22T12:00:00Z",
                "asset_name": "NeonDrive-Setup.exe",
                "prerelease": True,
            }
        ]

        window.versions_loaded(releases)

        self.assertIn("BETA", window.version_list.item(0).text())
        self.assertIn("установлена", window.version_list.item(0).text())
        self.assertIn("Менеджер версий", window.release_notes.toPlainText())
        self.assertIn("Переустановить", window.install_button.text())
        window.close()

    def test_running_check_uses_only_main_application_process(self) -> None:
        process = type("Process", (), {"info": {"name": "NeonDriveInstaller.exe"}})()
        with patch("neon_drive.version_manager.psutil.process_iter", return_value=[process]):
            self.assertFalse(main_app_running())

    def test_theme_button_switches_to_dark_background(self) -> None:
        window = VersionManagerWindow()
        original = window.dark_mode

        window.toggle_theme()

        self.assertNotEqual(window.dark_mode, original)
        expected = "Светлый фон" if window.dark_mode else "Тёмный фон"
        self.assertIn(expected, window.theme_button.text())
        window.close()

    def test_installer_requests_graceful_main_app_shutdown(self) -> None:
        process = MagicMock()
        with (
            patch(
                "neon_drive.version_manager.main_app_processes",
                return_value=[process],
            ),
            patch("neon_drive.version_manager.send_request") as request,
            patch(
                "neon_drive.version_manager.psutil.wait_procs",
                return_value=([process], []),
            ),
        ):
            close_main_app()

        request.assert_called_once_with(
            {"command": "shutdown", "reason": "version-install"},
            timeout_ms=3000,
        )
        process.terminate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
