from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NEON_DRIVE_DISABLE_AUTO_UPDATE", "1")
os.environ.setdefault("NEON_DRIVE_DISABLE_NETWORK", "1")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog

from neon_drive.app import MainWindow
from neon_drive.drive_browser import DriveClient, DriveEntry, DriveFolder


class Generation6Beta1Tests(unittest.TestCase):
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

    def test_only_download_and_upload_are_primary_navigation(self) -> None:
        window = self.window()
        visible_names = [
            window.tabs.tabText(index)
            for index in range(window.tabs.count())
            if window.tabs.isTabVisible(index)
        ]
        self.assertEqual(visible_names, ["Скачать", "Выгрузить"])
        self.assertEqual(
            [button.text().strip() for button in window.sidebar_page_buttons.values() if not button.isHidden()],
            ["↓   Скачать", "↑   Выгрузить"],
        )
        self.assertFalse(window.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint)
        self.assertEqual(window.theme_combo.currentData(), "google_drive")
        self.assertTrue(window.transfer_panels["download"].sources.isReadOnly())
        self.assertTrue(window.transfer_panels["upload"].source_display.acceptDrops())

    def test_google_drive_browser_adds_several_cloud_items(self) -> None:
        window = self.window()
        root = DriveFolder("Мой диск", "root", label="Мой диск")
        movie = DriveEntry("movie.mp4", "movie-id", root, False, 1024)
        folder = DriveEntry("Project", "folder-id", root, True)
        dialog = MagicMock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.selected_items = [movie, folder]
        with patch.object(window, "google_drive_is_connected", return_value=True), patch.object(
            window, "resolved_rclone_executable", return_value="rclone"
        ), patch("neon_drive.app.DriveItemDialog", return_value=dialog):
            window.choose_google_drive_items()

        selected = window.transfer_panels["download"].sources.toPlainText().splitlines()
        self.assertEqual(selected, [movie.remote, folder.remote])
        self.assertTrue(window.source_directory_flags[folder.remote])
        self.assertIn("movie.mp4", window.transfer_panels["download"].source_display.toPlainText())
        self.assertNotIn("root_folder_id", window.transfer_panels["download"].source_display.toPlainText())

    def test_upload_destination_uses_cloud_folder_browser(self) -> None:
        window = self.window()
        folder = DriveFolder("Материалы", "folder-id", label="Мой диск / Материалы")
        dialog = MagicMock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.selected_folder = folder
        with patch.object(window, "google_drive_is_connected", return_value=True), patch.object(
            window, "resolved_rclone_executable", return_value="rclone"
        ), patch("neon_drive.app.DriveFolderDialog", return_value=dialog):
            self.assertTrue(window.choose_destination_for("upload"))

        panel = window.transfer_panels["upload"]
        self.assertEqual(panel.destination.text(), folder.remote)
        self.assertEqual(panel.destination_display.text(), folder.label)
        self.assertEqual(window.copy_engine_combo.currentData(), "rclone")

    def test_drive_client_returns_folders_before_files(self) -> None:
        parent = DriveFolder("Мой диск", "root", label="Мой диск")
        client = DriveClient("rclone")
        client.query = MagicMock(
            return_value=[
                {"Name": "zeta.txt", "ID": "file-id", "IsDir": False, "Size": 12},
                {"Name": "Alpha", "ID": "folder-id", "IsDir": True, "Size": -1},
            ]
        )
        items = client.items(parent)
        self.assertEqual([item.name for item in items], ["Alpha", "zeta.txt"])
        self.assertTrue(items[0].is_directory)
        self.assertEqual(items[1].size, 12)


if __name__ == "__main__":
    unittest.main()
