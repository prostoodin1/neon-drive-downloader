from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NEON_DRIVE_DISABLE_AUTO_UPDATE", "1")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from neon_drive.app import MainWindow
from neon_drive.cli import build_parser, request_from_args
from neon_drive.rclone_manager import bundled_rclone_path, bundled_rclone_version


class Beta13Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.settings_dir = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            cls.settings_dir.name,
        )
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.settings_dir.cleanup()

    def setUp(self) -> None:
        settings = QSettings("NeonTools", "Neon Drive Downloader")
        settings.clear()
        settings.sync()

    def test_dark_dashboard_and_bottom_settings_button_are_defaults(self) -> None:
        window = MainWindow()
        window.notifications_check.setChecked(False)
        self.assertEqual(window.theme_combo.currentData(), "dark")
        self.assertTrue(window.tabs.tabBar().isHidden())
        self.assertFalse(window.sidebar.isHidden())
        self.assertFalse(window.tabs.isTabVisible(window.settings_tab_index))
        tab_names = [window.tabs.tabText(i) for i in range(window.tabs.count())]
        self.assertNotIn("AI CLI", tab_names)
        self.assertNotIn("Выгрузка", tab_names)
        self.assertIn("Главная", tab_names)
        self.assertIs(window.system_bar.itemAt(0).widget(), window.settings_gear_button)
        self.assertFalse(window.settings_gear_button.isHidden())
        window.set_upload_addon_enabled(True)
        window.show_transfer_direction("upload")
        self.assertIs(window.home_transfer_stack.currentWidget(), window.upload_page)
        self.assertIs(window.tabs.currentWidget(), window.home_page)
        window.toggle_settings_page()
        self.assertIs(window.tabs.currentWidget(), window.settings_page)
        window.force_exit = True
        window.close()

    def test_simple_profile_configures_both_copy_engines(self) -> None:
        window = MainWindow()
        window.notifications_check.setChecked(False)
        window.apply_transfer_preset("maximum")
        self.assertEqual(window.copy_profile_combo.currentData(), "maximum")
        self.assertEqual(window.rclone_performance_combo.currentData(), "extreme")
        self.assertEqual(window.rclone_streams_combo.currentData(), 32)
        self.assertEqual(window.transfer_panels["download"].preset_combo.currentData(), "maximum")
        self.assertEqual(window.transfer_panels["upload"].preset_combo.currentData(), "maximum")
        window.force_exit = True
        window.close()

    def test_google_drive_theme_and_transfer_counter_are_available(self) -> None:
        window = MainWindow()
        window.notifications_check.setChecked(False)
        drive_theme = window.theme_combo.findData("google_drive")

        self.assertGreaterEqual(drive_theme, 0)
        window.theme_combo.setCurrentIndex(drive_theme)
        window.record_transfer_statistics(2 * 1024**3, "download")

        self.assertEqual(window.theme_combo.currentData(), "google_drive")
        self.assertIn("#1a73e8", self.app.styleSheet())
        self.assertIn("2.0 ГБ", window.sidebar_transfer_stats.text())
        self.assertIn("2.0 ГБ", window.transfer_stats_summary.text())
        self.assertIn("Скачивание: 2.0 ГБ", window.transfer_stats_details.text())
        window.force_exit = True
        window.close()

    def test_agent_cli_is_json_request_not_a_gui_tab(self) -> None:
        args = build_parser().parse_args(
            [
                "add",
                "--source",
                r"G:\My Drive\movie.mkv",
                "--destination",
                r"D:\Media",
                "--profile",
                "maximum",
                "--start",
            ]
        )
        request = request_from_args(args)
        self.assertEqual(request["command"], "add")
        self.assertEqual(request["profile"], "maximum")
        self.assertTrue(request["start"])

    def test_bundled_rclone_is_resolved_from_frozen_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tools = root / "tools"
            tools.mkdir()
            executable = tools / "rclone.exe"
            executable.write_bytes(b"MZ")
            (tools / "install.json").write_text(
                '{"version":"v1.2.3"}', encoding="utf-8"
            )
            with patch.object(sys, "_MEIPASS", str(root), create=True):
                self.assertEqual(bundled_rclone_path(), executable)
                self.assertEqual(bundled_rclone_version(), "v1.2.3")


if __name__ == "__main__":
    unittest.main()
