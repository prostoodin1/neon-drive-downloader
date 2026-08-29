from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NEON_DRIVE_DISABLE_AUTO_UPDATE", "1")
os.environ.setdefault("NEON_DRIVE_DISABLE_NETWORK", "1")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from neon_drive.app import MainWindow
from neon_drive.drive_browser import (
    DriveClient,
    DriveFolder,
    DriveFolderDialog,
    is_managed_drive_path,
    managed_options,
    virtual_drive_parts,
)
from neon_drive.transfer_direction import detect_direction, location_kind, location_label


class Beta9Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        window = MainWindow()
        window.notifications_check.setChecked(False)
        window.auto_start_check.setChecked(False)

        def cleanup():
            window.force_exit = True
            window.close()
            window.deleteLater()
            self.app.processEvents()

        self.addCleanup(cleanup)
        return window

    def wait_ready(self, dialog):
        deadline = time.monotonic() + 3
        while dialog.thread is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNone(dialog.thread)

    def test_picker_has_explicit_light_high_contrast_style(self):
        root = DriveFolder("Мой диск", "root", label="Мой диск")
        with patch.object(DriveClient, "roots", return_value=[root]), patch.object(
            DriveClient, "folders", return_value=[]
        ):
            dialog = DriveFolderDialog("unused")
            self.addCleanup(dialog.deleteLater)
            self.wait_ready(dialog)
        style = dialog.styleSheet().casefold()
        self.assertIn("background: #ffffff", style)
        self.assertIn("color: #202124", style)
        self.assertIn("qlistwidget::item:selected", style)

    def test_shared_with_me_is_listed_and_exact_folder_becomes_normal_remote(self):
        client = DriveClient("rclone")
        with patch.object(client, "query", side_effect=[[], [{"ID": "folder123", "Name": "Проект", "IsDir": True}]]):
            roots = client.roots()
            shared = next(folder for folder in roots if folder.shared_with_me)
            children = client.folders(shared)
        self.assertEqual(shared.remote, "NeonGoogleDrive,shared_with_me:")
        self.assertIn("Доступные мне", [folder.name for folder in roots])
        self.assertEqual(children[0].remote, "NeonGoogleDrive,root_folder_id=folder123:")
        self.assertFalse(children[0].shared_with_me)
        self.assertTrue(is_managed_drive_path(shared.remote))
        self.assertEqual(managed_options(shared.remote), {"shared_with_me": "true"})

    def test_shared_root_cannot_be_destination_but_shared_folder_can(self):
        shared = DriveFolder("Доступные мне", "root", label="Доступные мне", shared_with_me=True)
        child = DriveFolder("Upload here", "folder123", label="Доступные мне / Upload here")
        with patch.object(DriveClient, "roots", return_value=[shared]), patch.object(
            DriveClient, "folders", side_effect=lambda folder: [child] if folder == shared else []
        ):
            dialog = DriveFolderDialog("unused")
            self.addCleanup(dialog.deleteLater)
            self.wait_ready(dialog)
            dialog.enter_folder(dialog.list.item(0))
            self.wait_ready(dialog)
            self.assertFalse(dialog.choose.isEnabled())
            self.assertIn("корень", dialog.status.text())
            dialog.enter_folder(dialog.list.item(0))
            self.wait_ready(dialog)
            self.assertTrue(dialog.choose.isEnabled())

    def test_shared_with_me_localized_path_is_understood(self):
        self.assertEqual(
            virtual_drive_parts("G:/Доступные мне/Проект/Видео"),
            ("shared_with_me", "", ["Проект", "Видео"]),
        )

    def test_physical_cloud_and_network_paths_are_auto_detected(self):
        self.assertEqual(location_kind("C:/Video/movie.mp4"), "physical")
        self.assertEqual(location_kind("//NAS/media/movie.mp4"), "network")
        self.assertEqual(location_kind("NeonGoogleDrive:Video"), "cloud")
        self.assertEqual(
            detect_direction(["C:/Video/movie.mp4"], "H:/Unidades compartidas/Clients/Video")[0],
            "upload",
        )
        self.assertEqual(detect_direction(["NeonGoogleDrive:Video"], "D:/Downloads")[0], "download")
        direction, text = detect_direction(["C:/Video/movie.mp4"], "D:/Archive")
        self.assertIsNone(direction)
        self.assertIn("локальное копирование", text)
        self.assertIn("ФИЗИЧЕСКИЙ ДИСК", location_label("C:/Video/movie.mp4"))

    def test_transfer_headings_follow_entered_paths(self):
        window = self.window()
        panel = window.transfer_panels["upload"]
        panel.sources.setPlainText("C:/Video/movie.mp4")
        panel.destination.setText("NeonGoogleDrive:Video")
        QTest.qWait(10)
        headings = [label.text() for label in panel.page.findChildren(QLabel)]
        self.assertTrue(any(text.startswith("ОТКУДА · ФИЗИЧЕСКИЙ ДИСК") for text in headings))
        self.assertIn("КУДА · GOOGLE DRIVE", headings)


if __name__ == "__main__":
    unittest.main()
