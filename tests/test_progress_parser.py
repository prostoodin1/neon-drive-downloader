from __future__ import annotations

import os
import tempfile
import unittest
from collections import deque
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLabel

from neon_drive.app import (
    MAX_CONCURRENT_DOWNLOADS,
    Downloader,
    MainWindow,
    TaskInfo,
    destination_collisions,
    robocopy_arguments,
)


class FakeWorker:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True

    def deleteLater(self) -> None:  # noqa: N802 - mirrors QObject API
        return


class ProgressParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_counts_inline_and_wrapped_robocopy_paths(self) -> None:
        downloader = Downloader()
        downloader.current = r"G:\Drive\Folder"
        samples: list[int] = []
        downloader.progress.connect(lambda _source, _percent, size: samples.append(int(size)))

        downloader._handle_output_line("\tНовый файл  100 2026/07/18 12:00:00")
        downloader._handle_output_line(r"G:\Drive\Folder\a-long-file-name.bin")
        downloader._handle_output_line("100.0%")
        downloader._handle_output_line(
            "\tНовый файл  50 2026/07/18 12:00:01  G:\\Drive\\Folder\\b.bin"
        )
        downloader._handle_output_line("100.0%")

        self.assertEqual(samples, [100, 150])

    def test_retry_of_same_path_does_not_double_count(self) -> None:
        downloader = Downloader()
        downloader.current = r"G:\Drive\large.bin"
        samples: list[int] = []
        downloader.progress.connect(lambda _source, _percent, size: samples.append(int(size)))
        header = "\tНовый файл  1000 2026/07/18 12:00:00  G:\\Drive\\large.bin"

        downloader._handle_output_line(header)
        downloader._handle_output_line("40.0%")
        downloader._handle_output_line(header)
        downloader._handle_output_line("41.0%")

        self.assertEqual(samples, [400, 410])


class StopAfterCurrentFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_settings = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            str(Path(cls.temp_settings.name)),
        )
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_settings.cleanup()

    def test_selected_current_file_stops_remaining_parallel_worker(self) -> None:
        window = MainWindow()
        window.notifications_check.setChecked(False)
        window.running = True
        window.total_items = 3
        window.total_bytes = 300
        window.queue = deque(["third.bin"])
        window.tasks = {
            "first.bin": TaskInfo("first.bin", 100, started_at=1.0),
            "second.bin": TaskInfo("second.bin", 100, started_at=2.0),
            "third.bin": TaskInfo("third.bin", 100),
        }
        first_worker = FakeWorker()
        second_worker = FakeWorker()
        window.workers = {
            "first.bin": first_worker,
            "second.bin": second_worker,
        }

        window.toggle_stop_after()
        self.assertEqual(window.stop_after_source, "first.bin")
        self.assertTrue(window.stop_after_file)

        window.on_item_done(True, "first.bin")
        self.assertTrue(second_worker.stopped)
        self.assertTrue(window.stopping)
        self.assertFalse(window.queue)

        window.on_item_done(False, "second.bin")
        self.assertFalse(window.running)
        self.assertEqual(window.completed_items, 1)
        self.assertEqual(window.failed_items, 0)
        window.force_exit = True
        window.close()

    def test_all_mode_never_starts_more_than_ten_workers(self) -> None:
        window = MainWindow()
        window.notifications_check.setChecked(False)
        all_index = window.download_mode_combo.findData("all")
        window.download_mode_combo.setCurrentIndex(all_index)
        window.running = True
        window.total_items = 12
        window.queue = deque(f"file-{index}.bin" for index in range(12))
        started: list[str] = []

        def start_fake(source: str) -> None:
            started.append(source)
            window.workers[source] = FakeWorker()

        window.start_task = start_fake
        window.fill_worker_slots()

        self.assertEqual(MAX_CONCURRENT_DOWNLOADS, 10)
        self.assertEqual(len(started), 10)
        self.assertEqual(len(window.workers), 10)
        self.assertEqual(len(window.queue), 2)

        window.workers.pop(started[0])
        window.fill_worker_slots()
        self.assertEqual(len(window.workers), 10)
        self.assertEqual(len(window.queue), 1)

        window.running = False
        window.workers.clear()
        window.force_exit = True
        window.close()

    def test_mode_specific_settings_are_visible_but_disabled(self) -> None:
        window = MainWindow()
        window.notifications_check.setChecked(False)

        window.download_mode_combo.setCurrentIndex(
            window.download_mode_combo.findData("sequential")
        )
        window.update_settings_visibility()
        self.assertFalse(window.concurrency_controls.isEnabled())

        window.download_mode_combo.setCurrentIndex(
            window.download_mode_combo.findData("limited")
        )
        window.update_settings_visibility()
        self.assertTrue(window.concurrency_controls.isEnabled())
        self.assertEqual(window.concurrency_spin.maximum(), 10)

        window.tray_check.setChecked(False)
        window.cleanup_logs_check.setChecked(False)
        window.update_mode_combo.setCurrentIndex(
            window.update_mode_combo.findData("automatic")
        )
        window.update_settings_visibility()
        self.assertFalse(window.continue_in_tray_check.setting_container.isEnabled())
        self.assertFalse(window.log_retention_controls.isEnabled())
        self.assertFalse(window.manual_update_card.isEnabled())
        self.assertFalse(window.manual_update_card.isHidden())

        window.force_exit = True
        window.close()

    def test_duplicate_destination_names_are_rejected_before_parallel_copy(self) -> None:
        collisions = destination_collisions(
            [r"G:\Drive A\movie.mkv", r"H:\Drive B\movie.mkv"],
            Path(r"D:\Downloads"),
        )
        self.assertEqual(len(collisions), 1)
        self.assertEqual(len(next(iter(collisions.values()))), 2)

    def test_performance_profiles_change_real_robocopy_flags(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir)
            destination = Path(target_dir)

            optimized, _ = robocopy_arguments(
                str(source), destination, "optimized", directory_threads=8
            )
            self.assertIn("/Z", optimized)
            self.assertIn("/J", optimized)
            self.assertIn("/MT:8", optimized)

            stable, _ = robocopy_arguments(str(source), destination, "stable", 8)
            self.assertIn("/Z", stable)
            self.assertFalse(any(argument.startswith("/MT:") for argument in stable))

            maximum, _ = robocopy_arguments(str(source), destination, "maximum", 12)
            self.assertNotIn("/Z", maximum)
            self.assertIn("/MT:12", maximum)
            self.assertIn("/R:8", maximum)
            self.assertIn("/W:2", maximum)

    def test_interface_has_no_preview_and_dialog_style_is_global(self) -> None:
        window = MainWindow()
        headings = [label.text() for label in window.findChildren(QLabel)]
        self.assertNotIn("ПРЕДПРОСМОТР", headings)
        self.assertIn("QMessageBox QLabel", QApplication.instance().styleSheet())

        window.copy_profile_combo.setCurrentIndex(
            window.copy_profile_combo.findData("stable")
        )
        window.update_settings_visibility()
        self.assertFalse(window.directory_threads_controls.isEnabled())

        window.copy_profile_combo.setCurrentIndex(
            window.copy_profile_combo.findData("optimized")
        )
        window.update_settings_visibility()
        self.assertTrue(window.directory_threads_controls.isEnabled())

        window.force_exit = True
        window.close()


if __name__ == "__main__":
    unittest.main()
