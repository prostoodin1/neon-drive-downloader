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
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, MainWindow):
                widget.auto_health_timer.stop()
                widget.force_exit = True
                widget.close()
                widget.deleteLater()
        self.app.processEvents()
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
        self.assertFalse(
            window.transfer_panels["upload"].direction_toggle_button.isHidden()
        )
        window.transfer_panels["upload"].direction_toggle_button.click()
        self.assertIs(window.home_transfer_stack.currentWidget(), window.download_page)
        window.toggle_settings_page()
        self.assertIs(window.tabs.currentWidget(), window.settings_page)
        window.force_exit = True
        window.close()

    def test_beta5_behavior_settings_are_persisted(self) -> None:
        window = MainWindow()
        window.notifications_check.setChecked(False)
        with patch("neon_drive.app.set_startup_enabled") as set_startup:
            window.auto_system_health_check.setChecked(True)
            window.keep_open_after_finish_check.setChecked(True)
            window.windows_startup_check.setChecked(True)
            window.persist_settings()

        settings = QSettings("NeonTools", "Neon Drive Downloader")
        self.assertTrue(settings.value("auto_system_health", False, type=bool))
        self.assertTrue(settings.value("keep_open_after_finish", False, type=bool))
        self.assertTrue(settings.value("windows_startup", False, type=bool))
        set_startup.assert_called_once()

        window.close_when_idle = True
        window.maybe_close_when_idle()
        self.assertFalse(window.close_when_idle)
        window.force_exit = True
        window.close()

    def test_automatic_health_check_starts_silently(self) -> None:
        window = MainWindow()
        window.notifications_check.setChecked(False)
        window.auto_health_timer.stop()
        window.auto_system_health_check.setChecked(True)

        with patch.object(window, "start_system_health_check") as start_health:
            window.maybe_auto_system_health_check()

        start_health.assert_called_once_with(silent=True)
        window.force_exit = True
        window.close()

    def test_manual_transfer_tuning_survives_restart_without_preset_override(self) -> None:
        first = MainWindow()
        first.notifications_check.setChecked(False)
        first.copy_engine_combo.setCurrentIndex(first.copy_engine_combo.findData("rclone"))
        first.rclone_performance_combo.setCurrentIndex(
            first.rclone_performance_combo.findData("manual")
        )
        first.rclone_chunk_combo.setCurrentIndex(first.rclone_chunk_combo.findData(512))
        self.assertGreaterEqual(first.rclone_chunk_combo.findData(2048), 0)
        first.rclone_streams_combo.setCurrentIndex(first.rclone_streams_combo.findData(24))
        first.rclone_checksum_check.setChecked(True)
        first.persist_settings()
        first.force_exit = True
        first.close()

        restored = MainWindow()
        restored.notifications_check.setChecked(False)
        self.assertEqual(restored.copy_engine_combo.currentData(), "rclone")
        self.assertEqual(restored.rclone_performance_combo.currentData(), "manual")
        self.assertEqual(restored.rclone_chunk_combo.currentData(), 512)
        self.assertEqual(restored.rclone_streams_combo.currentData(), 24)
        self.assertTrue(restored.rclone_checksum_check.isChecked())
        restored.force_exit = True
        restored.close()

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
