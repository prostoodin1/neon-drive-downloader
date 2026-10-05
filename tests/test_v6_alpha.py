from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NEON_DRIVE_DISABLE_AUTO_UPDATE", "1")
os.environ.setdefault("NEON_DRIVE_DISABLE_NETWORK", "1")

from PySide6.QtWidgets import QApplication

from neon_drive import __version__
from neon_drive.addons import is_beta_build
from neon_drive.app import MainWindow
from neon_drive.settings_store import create_settings


class Generation6AlphaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.environment = patch.dict(
            os.environ,
            {
                "NEON_DRIVE_SETTINGS_DIR": str(root / "settings"),
                "NEON_DRIVE_DATA_DIR": str(root / "data"),
                "NEON_DRIVE_RCLONE_CONFIG": str(root / "rclone.conf"),
            },
        )
        self.environment.start()

    def tearDown(self) -> None:
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, MainWindow):
                widget.auto_health_timer.stop()
                widget.force_exit = True
                widget.close()
                widget.deleteLater()
        self.app.processEvents()
        self.environment.stop()
        self.temporary.cleanup()

    def window(self) -> MainWindow:
        window = MainWindow()
        window.auto_health_timer.stop()
        window.notifications_check.setChecked(False)
        return window

    def test_alpha_version_and_modern_icon_assets_are_packaged(self) -> None:
        self.assertEqual(__version__, "6.0.4-alpha")
        self.assertTrue(is_beta_build(__version__))
        assets = Path(__file__).resolve().parents[1] / "assets"
        for name in ("neon-drive-v3.png", "neon-drive-v3.ico", "neon-drive-v3.icns"):
            self.assertGreater((assets / name).stat().st_size, 10_000)

    def test_language_switch_updates_main_ui_and_persists(self) -> None:
        window = self.window()
        self.assertEqual(window.language, "ru")
        self.assertEqual(
            [window.language_combo.itemData(index) for index in range(window.language_combo.count())],
            ["ru", "en", "es"],
        )
        window.language_combo.setCurrentIndex(window.language_combo.findData("en"))
        self.assertEqual(window.tabs.tabText(window.download_tab_index), "Download")
        self.assertEqual(window.transfer_panels["upload"].start_button.text(), "Upload")
        self.assertIn("Application language", window.v6_language_label.text())
        settings = create_settings("Neon Drive Downloader")
        self.assertEqual(settings.value("language"), "en")

    def test_about_card_contains_version_platforms_and_project_actions(self) -> None:
        window = self.window()
        self.assertIn(__version__, window.about_product.text())
        self.assertIn("Rclone", window.about_details.text())
        self.assertIn("Windows", window.about_details.text())
        self.assertIn("macOS", window.about_details.text())
        self.assertTrue(window.about_project_button.text())
        self.assertTrue(window.about_releases_button.text())
        self.assertTrue(window.about_issue_button.text())


if __name__ == "__main__":
    unittest.main()
