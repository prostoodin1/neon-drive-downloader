from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NEON_DRIVE_DISABLE_AUTO_UPDATE", "1")
os.environ.setdefault("NEON_DRIVE_DISABLE_NETWORK", "1")

from PySide6.QtWidgets import QApplication, QFileDialog

from neon_drive.app import MainWindow, style_local_file_dialog


class Generation6Beta2Tests(unittest.TestCase):
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

    def test_download_page_offers_drive_files_and_folders(self) -> None:
        window = self.window()
        panel = window.transfer_panels["download"]
        self.assertEqual(panel.google_source_button.text(), "Google Drive")
        self.assertEqual(panel.choose_files_button.text(), "Выбрать файлы")
        self.assertEqual(panel.choose_folder_button.text(), "Выбрать папки")
        self.assertFalse(panel.google_source_button.isHidden())
        self.assertTrue(panel.source_display.acceptDrops())

    def test_download_files_are_selected_from_explorer_without_drive_dialog(self) -> None:
        window = self.window()
        first = str(Path(self.temporary.name) / "one.bin")
        second = str(Path(self.temporary.name) / "two.bin")
        with patch(
            "neon_drive.app.QFileDialog.getOpenFileNames",
            return_value=([first, second], ""),
        ), patch.object(window, "choose_google_drive_items") as cloud_picker:
            window.choose_files_for("download")

        self.assertEqual(
            window.transfer_panels["download"].sources.toPlainText().splitlines(),
            [first, second],
        )
        cloud_picker.assert_not_called()

    def test_download_folder_is_selected_from_explorer(self) -> None:
        window = self.window()
        folder = str(Path(self.temporary.name) / "Project")
        with patch("neon_drive.app.select_source_folders", return_value=[folder]):
            window.choose_source_folder_for("download")
        self.assertEqual(window.transfer_panels["download"].sources.toPlainText(), folder)

    def test_multi_folder_picker_has_readable_light_object_list(self) -> None:
        dialog = QFileDialog()
        self.addCleanup(dialog.deleteLater)
        style_local_file_dialog(dialog)
        style = dialog.styleSheet().casefold()
        self.assertIn("background: #ffffff", style)
        self.assertIn("color: #202124", style)
        self.assertIn("qtreeview::item:selected", style)
        self.assertIn("selection-color: #174ea6", style)
        self.assertGreaterEqual(dialog.minimumWidth(), 680)


if __name__ == "__main__":
    unittest.main()
