from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from neon_drive.app import Downloader


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


if __name__ == "__main__":
    unittest.main()
