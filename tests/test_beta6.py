from __future__ import annotations

import os
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NEON_DRIVE_DISABLE_AUTO_UPDATE", "1")

from PySide6.QtCore import QEvent, QPointF, QSettings
from neon_drive.settings_store import create_settings
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication

from neon_drive import __version__
from neon_drive.app import MainWindow, ProfileCard, RcloneMonitorWindow, TaskInfo


class Beta6InterfaceTests(unittest.TestCase):
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
                widget.force_exit = True
                widget.close()
                widget.deleteLater()
        self.app.processEvents()
        settings = create_settings("Neon Drive Downloader")
        settings.clear()
        settings.sync()

    def make_window(self) -> MainWindow:
        window = MainWindow()
        window.notifications_check.setChecked(False)
        return window

    def test_automatic_theme_switches_at_day_boundaries(self) -> None:
        self.assertEqual(MainWindow.automatic_theme_for_hour(6), "dark")
        self.assertEqual(MainWindow.automatic_theme_for_hour(7), "light")
        self.assertEqual(MainWindow.automatic_theme_for_hour(18), "light")
        self.assertEqual(MainWindow.automatic_theme_for_hour(19), "dark")

    def test_templates_have_profile_colors_and_hover_glow(self) -> None:
        for key in ("slow", "optimal", "maximum"):
            card = ProfileCard(key)
            self.assertEqual(card.property("profileKey"), key)
            self.assertEqual(card.glow.color(), ProfileCard.COLORS[key])
            card.enterEvent(QEnterEvent(QPointF(), QPointF(), QPointF()))
            self.assertEqual(card.glow.blurRadius(), 28)
            card.leaveEvent(QEvent(QEvent.Type.Leave))
            self.assertEqual(card.glow.blurRadius(), 0)

    def test_rclone_monitor_has_graph_and_independent_terminal(self) -> None:
        monitor = RcloneMonitorWindow()
        monitor.reset_monitor()
        monitor.set_speed(42.5)
        monitor.append_text("rclone: transferred 50%\n")
        self.assertEqual(monitor.speed_label.text(), "42.5 МБ/с")
        self.assertIn("transferred 50%", monitor.terminal.toPlainText())
        self.assertGreaterEqual(len(monitor.graph.values), 2)
        monitor.close()

    def test_contextual_button_mode_is_saved_and_direction_toggle_remains(self) -> None:
        window = self.make_window()
        window.contextual_buttons_check.setChecked(True)
        window.persist_settings()
        self.assertEqual(
            window.transfer_panels["download"].start_button.property("colorRole"),
            "download",
        )
        self.assertEqual(
            window.transfer_panels["upload"].start_button.property("colorRole"),
            "upload",
        )
        self.assertFalse(
            window.transfer_panels["download"].direction_toggle_button.isHidden()
        )
        window.force_exit = True
        window.close()

        restored = self.make_window()
        self.assertTrue(restored.contextual_buttons_check.isChecked())
        restored.force_exit = True
        restored.close()

    def test_direct_google_all_mode_really_starts_multiple_workers(self) -> None:
        window = self.make_window()
        window.active_destination = "NeonGoogleDrive:Uploads"
        sources = [f"NeonGoogleDriveSource:file-{index}.bin" for index in range(4)]
        window.total_items = len(sources)
        window.tasks = {
            source: TaskInfo(source=source, size=100) for source in sources
        }
        window.queue = deque(sources)
        window.running = True
        window.download_mode_combo.setCurrentIndex(
            window.download_mode_combo.findData("all")
        )
        self.assertEqual(window.max_concurrent_downloads(), 4)
        started: list[str] = []

        def start_fake(source: str) -> None:
            started.append(source)
            window.workers[source] = object()

        window.start_task = start_fake
        window.fill_worker_slots()
        self.assertEqual(started, sources)
        self.assertFalse(window.queue)
        window.drive_chunk_combo.setCurrentIndex(
            window.drive_chunk_combo.findData(1024)
        )
        self.assertEqual(window.max_concurrent_downloads(), 1)
        window.running = False
        window.workers.clear()
        window.force_exit = True
        window.close()

    def test_welcome_is_shown_once_per_version_and_again_after_update(self) -> None:
        window = self.make_window()
        with patch("neon_drive.app.ReleaseWelcomeDialog.exec", return_value=1) as opened:
            self.assertTrue(window.show_release_welcome_once())
            self.assertEqual(window.settings.value("welcome_seen_version"), __version__)
            self.assertFalse(window.show_release_welcome_once())
            self.assertEqual(opened.call_count, 1)
            window.settings.setValue("welcome_seen_version", "older-version")
            self.assertTrue(window.show_release_welcome_once())
            self.assertEqual(opened.call_count, 2)
        window.force_exit = True
        window.close()

    def test_folder_button_adds_several_complete_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first-folder"
            second = Path(temporary) / "second-folder"
            first.mkdir()
            second.mkdir()
            window = self.make_window()
            with patch(
                "neon_drive.app.select_source_folders",
                return_value=[str(first), str(second)],
            ):
                window.choose_source_folder_for("upload")
            selected = window.transfer_panels["upload"].sources.toPlainText().splitlines()
            self.assertEqual(selected, [str(first), str(second)])
            self.assertEqual(
                window.transfer_panels["upload"].choose_folder_button.text(),
                "Добавить папки",
            )
            window.force_exit = True
            window.close()


if __name__ == "__main__":
    unittest.main()
