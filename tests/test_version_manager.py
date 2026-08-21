from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["NEON_DRIVE_DISABLE_NETWORK"] = "1"

from PySide6.QtWidgets import QApplication

from neon_drive.version_manager import VersionManagerWindow, main_app_running


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


if __name__ == "__main__":
    unittest.main()
