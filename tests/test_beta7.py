from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NEON_DRIVE_DISABLE_AUTO_UPDATE", "1")

from PySide6.QtCore import QProcess, QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from neon_drive.app import MainWindow, RcloneDownloader, TaskInfo
from neon_drive.copy_engines import RcloneOptions, rclone_arguments
from neon_drive.transfer_buffer import TransferBuffer
from neon_drive.transfer_direction import detect_direction
from neon_drive import updater, macos_installer


class Beta7Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_google_dark_and_new_settings_persist(self):
        window = MainWindow()
        window.theme_combo.setCurrentIndex(window.theme_combo.findData("google_drive_dark"))
        window.download_buffer_check.setChecked(True)
        window.auto_direction_check.setChecked(False)
        window.persist_settings()
        self.assertIn("#1b1b1f", self.app.styleSheet())
        self.assertIn('colorRole="upload"', self.app.styleSheet())
        self.assertTrue(window.settings.value("download_buffer", False, type=bool))
        self.assertFalse(window.settings.value("auto_direction", True, type=bool))
        self.assertFalse(window.auto_rclone_monitor_check.isChecked())
        window.force_exit = True
        window.close()

    def test_old_auto_monitor_preference_is_disabled(self):
        settings = QSettings("NeonTools", "Neon Drive Downloader")
        settings.setValue("auto_rclone_monitor", True)
        settings.sync()
        window = MainWindow()
        self.assertFalse(window.auto_rclone_monitor_check.isChecked())
        window.force_exit = True
        window.close()

    def test_json_progress_before_one_percent_of_25_gib(self):
        worker = RcloneDownloader()
        worker.current = "movie.mp4"
        worker.expected_bytes = 25 * 1024**3
        events = []
        worker.progress.connect(lambda *args: events.append(args))
        worker._handle_output_line(json.dumps({"stats": {"bytes": 1048576, "speed": 1048576}}))
        self.assertEqual(events[-1][2], 1048576)
        self.assertGreater(events[-1][1], 0)
        self.assertLess(events[-1][1], 1)
        worker._handle_output_line('{"level":"error","msg":"quota exceeded"}')
        self.assertIn("quota exceeded", worker._error_lines)

    def test_drive_memory_does_not_scale_with_2gib_chunk(self):
        args, _ = rclone_arguments("movie.mp4", "NeonGoogleDrive:", RcloneOptions(chunk_size_mib=2048, transfers=32))
        self.assertIn("--drive-chunk-size=64Mi", args)
        self.assertIn("--max-buffer-memory=512Mi", args)
        self.assertIn("--use-json-log", args)
        self.assertNotIn("--progress", args)

    def test_direction_detection_preserves_ambiguous_paths(self):
        with patch("neon_drive.transfer_direction.psutil.disk_partitions", return_value=[]):
            self.assertEqual(detect_direction(["/tmp/movie"], "NeonGoogleDrive:Videos")[0], "upload")
            self.assertEqual(detect_direction(["//NAS/share/movie"], "/tmp/destination")[0], "download")
            self.assertEqual(detect_direction(["/tmp/movie"], "/tmp/destination")[0], None)
            self.assertEqual(detect_direction(["/Users/me/Library/CloudStorage/GoogleDrive-me/movie"], "/tmp/destination")[0], "download")

    def test_buffer_commits_and_removes_its_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / "movie").write_bytes(b"old")
            buffer = TransferBuffer(target, 4)
            (buffer.root / "movie").write_bytes(b"neon")
            buffer.commit("movie", 4)
            self.assertEqual((target / "movie").read_bytes(), b"neon")
            self.assertFalse(buffer.root.exists())

    def test_failed_buffer_keeps_existing_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / "movie").write_bytes(b"old")
            buffer = TransferBuffer(target, 4)
            (buffer.root / "movie").write_bytes(b"no")
            with self.assertRaises(OSError):
                buffer.commit("movie", 4)
            self.assertEqual((target / "movie").read_bytes(), b"old")
            buffer.discard()
            self.assertFalse(buffer.root.exists())

    def test_buffer_refuses_insufficient_space(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch("neon_drive.transfer_buffer.shutil.disk_usage") as usage:
                usage.return_value.free = 0
                with self.assertRaises(OSError):
                    TransferBuffer(Path(temporary), 100)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_native_arm_release_is_selected(self):
        data = {"tag_name": "v5.5.0-beta.7", "assets": [
            {"name": updater.MACOS_ASSET_NAME}, {"name": updater.MACOS_ARM_ASSET_NAME},
        ]}
        with patch.object(updater, "is_macos", return_value=True), patch.object(updater.platform, "machine", return_value="arm64"):
            release = updater._normalize_release(data, "public")
            self.assertEqual(release["asset_name"], updater.MACOS_ARM_ASSET_NAME)

    def test_mac_uninstall_is_recoverable_and_rejects_other_targets(self):
        with tempfile.TemporaryDirectory() as temporary, patch("pathlib.Path.home", return_value=Path(temporary)):
            app = Path(temporary) / "Applications/Neon Drive.app"
            app.mkdir(parents=True)
            (app / "data").write_text("app")
            trash = macos_installer.uninstall_to_trash(app)
            self.assertTrue((trash / "data").exists())
            self.assertFalse(app.exists())
            with self.assertRaises(RuntimeError):
                macos_installer.validate_target(Path(temporary) / "Other.app")

    def test_source_analysis_does_not_block_gui(self):
        window = MainWindow()
        window.notifications_check.setChecked(False)
        window.running = True
        window.tasks = {"source": TaskInfo("source", 1)}
        from collections import deque
        window.queue = deque(["source"])
        def slow_snapshot(_):
            time.sleep(0.3)
            return (1, 1, 1), None
        with patch("neon_drive.app.source_snapshot", side_effect=slow_snapshot):
            start = time.monotonic()
            window.fill_worker_slots()
            self.assertLess(time.monotonic() - start, 0.2)
            window.running = False
            for _ in range(30):
                QTest.qWait(30)
                if not window.snapshot_threads:
                    break
        self.assertFalse(window.snapshot_threads)
        window.force_exit = True
        window.close()

    def test_real_rclone_copy_reports_bytes_and_preserves_hash(self):
        executable = Path("vendor/rclone/rclone.exe" if os.name == "nt" else "vendor/rclone/rclone")
        if not executable.is_file():
            self.skipTest("Bundled Rclone is fetched in the build step.")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sample.bin"
            source.write_bytes(bytes(range(256)) * 8192)
            destination = root / "destination"
            destination.mkdir()
            buffer = TransferBuffer(destination, source.stat().st_size)
            worker = RcloneDownloader()
            events, done = [], []
            worker.progress.connect(lambda *args: events.append(args))
            worker.item_done.connect(lambda *args: done.append(args))
            worker.start_item(str(executable.resolve()), str(source), buffer.root, RcloneOptions(), source.stat().st_size)
            deadline = time.monotonic() + 20
            while not done and time.monotonic() < deadline:
                QTest.qWait(30)
            if not done:
                worker.kill()
                worker.waitForFinished(3000)
            self.assertTrue(done and done[0][0], worker.failure_reason)
            self.assertTrue(any(event[2] > 0 for event in events))
            buffer.commit(source.name, source.stat().st_size)
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),
                             hashlib.sha256((destination / source.name).read_bytes()).digest())
            self.assertFalse(buffer.root.exists())


if __name__ == "__main__":
    unittest.main()
