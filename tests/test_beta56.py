from __future__ import annotations

import os
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NEON_DRIVE_DISABLE_AUTO_UPDATE", "1")
os.environ.setdefault("NEON_DRIVE_DISABLE_NETWORK", "1")

from PySide6.QtWidgets import QApplication

from neon_drive.app import MainWindow, SpeedGraph, TaskInfo, create_settings, robocopy_arguments
from neon_drive.copy_engines import RcloneOptions, copy_engine_for_source, rclone_arguments


class Beta56Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def window(self) -> MainWindow:
        window = MainWindow()
        window.auto_health_timer.stop()
        window.notifications_check.setChecked(False)
        self.addCleanup(self.close_window, window)
        return window

    def close_window(self, window: MainWindow) -> None:
        window.running = False
        window.workers.clear()
        window.force_exit = True
        window.close()
        window.deleteLater()
        self.app.processEvents()

    def test_rclone_remote_download_keeps_remote_path_and_stable_flags(self) -> None:
        source = "NeonGoogleDrive:Clients/movie.mkv"
        args, target = rclone_arguments(
            source,
            Path("D:/Downloads"),
            RcloneOptions(),
            source_is_dir=False,
        )

        self.assertEqual(args[0], "copyto")
        self.assertEqual(args[1], source)
        self.assertEqual(Path(target).name, "movie.mkv")
        self.assertIn("--retries-sleep=3s", args)
        self.assertIn("--drive-pacer-min-sleep=10ms", args)
        self.assertIn("--partial-suffix=.neon-partial", args)
        self.assertEqual(copy_engine_for_source("robocopy", source), "rclone")

    def test_every_robocopy_template_keeps_restartable_mode(self) -> None:
        for profile in ("stable", "optimized", "maximum", "turbo"):
            args, _ = robocopy_arguments("D:/source.bin", Path("E:/Target"), profile)
            self.assertIn("/Z", args, profile)

    def test_templates_offer_sequential_or_full_parallel_rclone(self) -> None:
        window = self.window()
        window.copy_engine_combo.setCurrentIndex(
            window.copy_engine_combo.findData("rclone")
        )
        window.total_items = 12
        window.profile_queue_combo.setCurrentIndex(
            window.profile_queue_combo.findData("all")
        )

        self.assertEqual(window.download_mode_combo.currentData(), "all")
        self.assertEqual(window.max_concurrent_downloads(), 10)

        window.profile_queue_combo.setCurrentIndex(
            window.profile_queue_combo.findData("sequential")
        )
        self.assertEqual(window.download_mode_combo.currentData(), "sequential")
        self.assertEqual(window.max_concurrent_downloads(), 1)

    def test_beta2_defaults_to_direct_google_and_has_no_bandwidth_limit(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, {"NEON_DRIVE_SETTINGS_DIR": temporary.name})
        environment.start()
        self.addCleanup(environment.stop)
        window = self.window()

        self.assertEqual(window.google_route_combo.currentData(), "direct")
        self.assertEqual(window.drive_chunk_combo.currentData(), 128)
        args, _target = rclone_arguments(
            "D:/movie.mkv",
            "NeonGoogleDrive:Video",
            window.selected_rclone_options(),
        )
        self.assertFalse(any(argument.startswith("--bwlimit") for argument in args))

    def test_beta2_migrates_ask_but_preserves_explicit_filesystem_route(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, {"NEON_DRIVE_SETTINGS_DIR": temporary.name})
        environment.start()
        self.addCleanup(environment.stop)
        settings = create_settings("Neon Drive Downloader")
        settings.setValue("google_drive_route", "ask")
        settings.sync()
        window = self.window()
        self.assertEqual(window.google_route_combo.currentData(), "direct")

        window.google_route_combo.setCurrentIndex(
            window.google_route_combo.findData("filesystem")
        )
        window.persist_settings()
        self.assertEqual(window.settings.value("google_drive_route"), "filesystem")

    def test_blue_start_button_continues_the_same_worker(self) -> None:
        window = self.window()
        panel = window.bind_transfer_panel("download")
        worker = MagicMock()
        window.workers = {"movie.mkv": worker}
        window.running = True
        window.set_transfer_controls_enabled(True)

        panel.visible_stop_button.click()
        self.assertTrue(window.paused)
        self.assertEqual(panel.start_button.text(), "Продолжить")
        self.assertTrue(panel.start_button.isEnabled())

        panel.start_button.click()
        worker.resume.assert_called_once_with()
        self.assertFalse(window.paused)
        self.assertEqual(panel.start_button.text(), "Начать передачу")

    def test_remote_sources_skip_local_source_gate_and_have_file_graph(self) -> None:
        window = self.window()
        source = "NeonGoogleDrive:Clients/movie.mkv"
        task = TaskInfo(source, 100, speed_history=deque([2.0, 20.0, 70.0], maxlen=600))
        window.tasks = {source: task}

        self.assertTrue(window.source_ready_for_transfer(task))
        panel = window.bind_transfer_panel("download")
        panel.graph_selector.addItem("movie.mkv", source)
        panel.graph_selector.setCurrentIndex(panel.graph_selector.count() - 1)
        self.assertEqual(list(panel.speed_graph.values), [2.0, 20.0, 70.0])

        graph = SpeedGraph()
        self.assertEqual(graph.traffic_color(2).name(), "#ea4335")
        self.assertEqual(graph.traffic_color(20).name(), "#f9ab00")
        self.assertEqual(graph.traffic_color(70).name(), "#34a853")

    def test_explorer_google_source_auto_starts_as_direct_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.window()
            source = "H:/My Drive/Clients/movie.mkv"
            remote = "NeonGoogleDrive:Clients/movie.mkv"
            panel = window.bind_transfer_panel("download")
            panel.sources.setPlainText(source)
            panel.destination.setText(temp_dir)
            window.auto_direction_check.setChecked(True)
            window.download_buffer_check.setChecked(False)

            with (
                patch.object(window, "google_drive_is_connected", return_value=True),
                patch.object(
                    window,
                    "resolve_explorer_google_destination",
                    return_value=remote,
                ),
                patch.object(window, "resolved_rclone_executable", return_value="rclone.exe"),
                patch.object(window, "fill_worker_slots") as fill,
            ):
                window.start_transfers("download")

            self.assertTrue(window.running)
            self.assertEqual(window.active_transfer, "download")
            self.assertEqual(window.copy_engine_combo.currentData(), "rclone")
            self.assertIn(remote, window.tasks)
            self.assertFalse(window.tasks[remote].is_directory)
            fill.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
