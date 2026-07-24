from __future__ import annotations

import os
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NEON_DRIVE_DISABLE_AUTO_UPDATE", "1")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLabel, QTabWidget

from neon_drive.addons import UPLOAD_ADDON_FILE
from neon_drive.app import (
    MAX_CONCURRENT_DOWNLOADS,
    MAX_TURBO_THREADS,
    Downloader,
    MainWindow,
    RcloneDownloader,
    TaskInfo,
    TurboFileDownloader,
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

    def test_rclone_progress_is_converted_to_item_bytes(self) -> None:
        downloader = RcloneDownloader()
        downloader.current = r"G:\Drive\large.bin"
        downloader.expected_bytes = 1_000
        samples: list[int] = []
        downloader.progress.connect(lambda _source, _percent, size: samples.append(int(size)))

        downloader._handle_output_line("Transferred: 500 B / 1000 B, 50%, 5 MiB/s, ETA 0s")

        self.assertEqual(samples, [500])


class StopAfterCurrentFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_settings = tempfile.TemporaryDirectory()
        cls.previous_addon_dir = os.environ.get("NEON_DRIVE_ADDON_DIR")
        cls.addon_dir = Path(cls.temp_settings.name) / "addons"
        cls.addon_dir.mkdir()
        manifest = Path(__file__).parents[1] / "addons" / UPLOAD_ADDON_FILE
        (cls.addon_dir / UPLOAD_ADDON_FILE).write_text(
            manifest.read_text(encoding="utf-8"), encoding="utf-8"
        )
        os.environ["NEON_DRIVE_ADDON_DIR"] = str(cls.addon_dir)
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            str(Path(cls.temp_settings.name)),
        )
        isolated_settings = QSettings("NeonTools", "Neon Drive Downloader")
        isolated_settings.clear()
        isolated_settings.sync()
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.previous_addon_dir is None:
            os.environ.pop("NEON_DRIVE_ADDON_DIR", None)
        else:
            os.environ["NEON_DRIVE_ADDON_DIR"] = cls.previous_addon_dir
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
        self.assertTrue(window.manual_update_card.isEnabled())
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

            turbo_folder, _ = robocopy_arguments(str(source), destination, "turbo", 16)
            self.assertNotIn("/Z", turbo_folder)
            self.assertIn("/MT:16", turbo_folder)

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
        self.assertFalse(window.turbo_threads_controls.isEnabled())

        window.copy_profile_combo.setCurrentIndex(
            window.copy_profile_combo.findData("turbo")
        )
        window.update_settings_visibility()
        self.assertTrue(window.turbo_threads_controls.isEnabled())
        self.assertEqual(window.turbo_threads_slider.maximum(), MAX_TURBO_THREADS)

        window.force_exit = True
        window.close()

    def test_advanced_mode_adds_tab_and_reveals_terminal(self) -> None:
        window = MainWindow()
        window.notifications_check.setChecked(False)
        window.advanced_mode_check.setChecked(False)
        window.update_settings_visibility()
        self.assertEqual(window.tabs.indexOf(window.advanced_page), -1)
        self.assertFalse(window.transfer_panels["download"].terminal_card.isVisible())

        window.advanced_mode_check.setChecked(True)
        window.update_settings_visibility()
        self.assertGreaterEqual(window.tabs.indexOf(window.advanced_page), 0)
        self.assertFalse(window.transfer_panels["download"].terminal_card.isHidden())

        window.advanced_mode_check.setChecked(False)
        window.force_exit = True
        window.close()

    def test_navigation_can_move_left_and_collapse_without_changing_page(self) -> None:
        window = MainWindow()
        window.notifications_check.setChecked(False)
        window.navigation_mode_combo.setCurrentIndex(
            window.navigation_mode_combo.findData("top")
        )
        window.update_settings_visibility()
        self.assertEqual(window.tabs.tabPosition(), QTabWidget.TabPosition.North)
        self.assertTrue(window.navigation_toggle_button.isHidden())

        window.navigation_mode_combo.setCurrentIndex(
            window.navigation_mode_combo.findData("side")
        )
        window.update_settings_visibility()
        page = window.tabs.currentWidget()
        self.assertEqual(window.tabs.tabPosition(), QTabWidget.TabPosition.West)
        self.assertFalse(window.navigation_toggle_button.isHidden())
        self.assertGreater(
            window.tabs.tabBar().tabSizeHint(0).width(),
            window.tabs.tabBar().tabSizeHint(0).height(),
        )

        window.set_navigation_panel_expanded(False, animate=False)
        self.assertTrue(window.tabs.tabBar().isHidden())
        self.assertIs(window.tabs.currentWidget(), page)
        window.set_navigation_panel_expanded(True, animate=False)
        self.assertFalse(window.tabs.tabBar().isHidden())
        self.assertIs(window.tabs.currentWidget(), page)

        window.navigation_mode_combo.setCurrentIndex(
            window.navigation_mode_combo.findData("top")
        )
        window.force_exit = True
        window.close()

    def test_design_modes_are_compact_by_default_and_switch_live(self) -> None:
        window = MainWindow()
        window.notifications_check.setChecked(False)

        self.assertEqual(window.design_mode_combo.currentData(), "compact")
        self.assertEqual(window.start_button.minimumHeight(), 42)
        modes = {
            window.design_mode_combo.itemData(index)
            for index in range(window.design_mode_combo.count())
        }
        self.assertEqual(modes, {"compact", "comfortable", "minimal"})

        window.design_mode_combo.setCurrentIndex(
            window.design_mode_combo.findData("comfortable")
        )
        self.assertEqual(window.start_button.minimumHeight(), 52)
        window.force_exit = True
        window.close()

    def test_stable_build_hides_upload_addon_controls_and_tab(self) -> None:
        with patch("neon_drive.app.is_beta_build", return_value=False):
            window = MainWindow()
        window.notifications_check.setChecked(False)

        self.assertEqual(window.tabs.indexOf(window.upload_page), -1)
        self.assertFalse(hasattr(window, "addon_install_button"))
        window.force_exit = True
        window.close()

    def test_turbo_profile_uses_segmented_worker_for_one_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "large.bin"
            source.write_bytes(b"neon")
            destination = root / "downloads"
            destination.mkdir()

            window = MainWindow()
            window.notifications_check.setChecked(False)
            window.destination.setText(str(destination))
            window.tasks = {str(source): TaskInfo(str(source), source.stat().st_size)}
            window.download_mode_combo.setCurrentIndex(
                window.download_mode_combo.findData("sequential")
            )
            window.copy_profile_combo.setCurrentIndex(
                window.copy_profile_combo.findData("turbo")
            )
            window.turbo_threads_slider.setValue(12)

            with patch.object(TurboFileDownloader, "start_item") as start_item:
                window.start_task(str(source))

            worker = window.workers.pop(str(source))
            self.assertIsInstance(worker, TurboFileDownloader)
            start_item.assert_called_once_with(str(source), destination, 12)
            worker.deleteLater()
            window.force_exit = True
            window.close()

    def test_rclone_mode_uses_rclone_worker_and_selected_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "large.bin"
            source.write_bytes(b"neon-rclone")
            destination = root / "downloads"
            destination.mkdir()

            window = MainWindow()
            window.notifications_check.setChecked(False)
            window.destination.setText(str(destination))
            window.tasks = {str(source): TaskInfo(str(source), source.stat().st_size)}
            window.copy_engine_combo.setCurrentIndex(
                window.copy_engine_combo.findData("rclone")
            )
            window.rclone_executable = "rclone.exe"
            options = window.selected_rclone_options()

            with patch.object(RcloneDownloader, "start_item") as start_item:
                window.start_task(str(source))

            worker = window.workers.pop(str(source))
            self.assertIsInstance(worker, RcloneDownloader)
            start_item.assert_called_once_with(
                "rclone.exe",
                str(source),
                destination,
                options,
                source.stat().st_size,
            )
            window.copy_engine_combo.setCurrentIndex(
                window.copy_engine_combo.findData("robocopy")
            )
            worker.deleteLater()
            window.force_exit = True
            window.close()

    def test_manual_release_list_marks_beta_versions(self) -> None:
        window = MainWindow()
        window.notifications_check.setChecked(False)
        window.update_mode_combo.setCurrentIndex(
            window.update_mode_combo.findData("manual")
        )
        window.update_settings_visibility()
        window.release_history_succeeded(
            [
                {
                    "tag": "v5.4.0-beta.1",
                    "version": "5.4.0-beta.1",
                    "published_at": "2026-07-23T12:00:00Z",
                    "prerelease": True,
                }
            ]
        )

        self.assertIn("BETA", window.release_combo.itemText(0))
        self.assertTrue(window.install_selected_button.isEnabled())
        window.force_exit = True
        window.close()

    def test_upload_tab_uses_explorer_paths_and_robocopy_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "local-video.mkv"
            source.write_bytes(b"neon-upload")
            drive_destination = root / "Google Drive"
            drive_destination.mkdir()

            window = MainWindow()
            window.notifications_check.setChecked(False)
            window.tabs.setCurrentIndex(window.upload_tab_index)
            self.assertEqual(window.tabs.tabText(window.upload_tab_index), "Выгрузка")
            self.assertEqual(window.active_transfer, "upload")
            self.assertEqual(window.start_button.text(), "Начать выгрузку")
            self.assertIn("1.0.0-beta.1", window.addon_status_badge.text())

            window.upload_sources.setPlainText(str(source))
            window.upload_destination.setText(str(drive_destination))
            window.tasks = {str(source): TaskInfo(str(source), source.stat().st_size)}
            window.download_mode_combo.setCurrentIndex(
                window.download_mode_combo.findData("sequential")
            )
            window.copy_profile_combo.setCurrentIndex(
                window.copy_profile_combo.findData("turbo")
            )

            with patch.object(Downloader, "start_item") as start_item:
                window.start_task(str(source))

            worker = window.workers.pop(str(source))
            self.assertIsInstance(worker, Downloader)
            start_item.assert_called_once_with(
                str(source),
                drive_destination,
                "maximum",
                window.effective_directory_threads(),
            )
            worker.deleteLater()
            window.force_exit = True
            window.close()

    def test_start_upload_initializes_independent_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "local.bin"
            source.write_bytes(b"upload-queue")
            destination = root / "Mounted Google Drive"

            window = MainWindow()
            window.notifications_check.setChecked(False)
            window.upload_sources.setPlainText(str(source))
            window.upload_destination.setText(str(destination))

            with (
                patch("neon_drive.app.shutil.which", return_value="robocopy.exe"),
                patch.object(window, "fill_worker_slots") as fill_worker_slots,
            ):
                window.start_uploads()

            self.assertTrue(window.running)
            self.assertEqual(window.active_transfer, "upload")
            self.assertEqual(list(window.queue), [str(source)])
            self.assertEqual(window.total_bytes, source.stat().st_size)
            self.assertIn(
                "Операция: выгрузка",
                window.current_transfer_panel().terminal.toPlainText(),
            )
            fill_worker_slots.assert_called_once()

            window.running = False
            window.metrics_timer.stop()
            window.force_exit = True
            window.close()


if __name__ == "__main__":
    unittest.main()
