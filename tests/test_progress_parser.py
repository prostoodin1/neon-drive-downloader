from __future__ import annotations

import os
import tempfile
import unittest
from collections import deque
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from neon_drive.app import Downloader, MainWindow, TaskInfo


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


if __name__ == "__main__":
    unittest.main()
