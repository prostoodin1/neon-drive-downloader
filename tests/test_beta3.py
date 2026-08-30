from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NEON_DRIVE_DISABLE_AUTO_UPDATE", "1")
os.environ.setdefault("NEON_DRIVE_DISABLE_NETWORK", "1")

from PySide6.QtWidgets import QApplication

from neon_drive.app import MainWindow, existing_local_targets
from neon_drive.google_drive import (
    GOOGLE_DRIVE_REMOTE,
    managed_rclone_config_path,
    store_google_drive_token,
)


class Beta3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def window(self) -> MainWindow:
        window = MainWindow()
        window.auto_health_timer.stop()
        window.notifications_check.setChecked(False)
        window.auto_start_check.setChecked(False)

        def cleanup() -> None:
            window.running = False
            window.workers.clear()
            window.queue.clear()
            window.force_exit = True
            window.close()
            window.deleteLater()
            self.app.processEvents()

        self.addCleanup(cleanup)
        return window

    def test_existing_destination_check_classifies_files_and_folder_merges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "source"
            destination = root / "destination"
            source_root.mkdir()
            destination.mkdir()
            same = source_root / "same.bin"
            changed = source_root / "changed.bin"
            folder = source_root / "album"
            same.write_bytes(b"same")
            changed.write_bytes(b"new")
            folder.mkdir()
            shutil.copy2(same, destination / same.name)
            (destination / changed.name).write_bytes(b"old")
            (destination / folder.name).mkdir()

            identical, different, folders = existing_local_targets(
                [str(same), str(changed), str(folder)], destination
            )

            self.assertEqual([Path(source).name for source, _ in identical], ["same.bin"])
            self.assertEqual([Path(source).name for source, _ in different], ["changed.bin"])
            self.assertEqual([Path(source).name for source, _ in folders], ["album"])

    def test_identical_file_is_skipped_before_any_worker_starts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "movie.bin"
            destination = root / "destination"
            destination.mkdir()
            source.write_bytes(b"already copied")
            shutil.copy2(source, destination / source.name)
            window = self.window()
            panel = window.bind_transfer_panel("download")
            panel.sources.setPlainText(str(source))
            panel.destination.setText(str(destination))

            with patch("neon_drive.app.QMessageBox.information") as notice, patch.object(
                window, "fill_worker_slots"
            ) as fill:
                window.start_transfers("download")

            notice.assert_called_once()
            fill.assert_not_called()
            self.assertFalse(window.running)
            self.assertEqual(panel.sources.toPlainText(), "")
            self.assertIn("УЖЕ СКОПИРОВАНО", panel.state_label.text())

    def test_visible_full_stop_closes_every_worker_and_clears_queue(self) -> None:
        window = self.window()
        panel = window.bind_transfer_panel("download")
        first = MagicMock()
        second = MagicMock()
        window.workers = {"one": first, "two": second}
        window.queue.extend(("three", "four"))
        window.running = True
        window.set_transfer_controls_enabled(True)

        panel.hard_stop_button.click()

        first.stop.assert_called_once_with()
        second.stop.assert_called_once_with()
        self.assertTrue(window.stopping)
        self.assertEqual(list(window.queue), [])
        self.assertEqual(panel.hard_stop_button.text(), "Полностью остановить")

    def test_transfer_tab_clear_removes_both_lists_but_not_statistics(self) -> None:
        window = self.window()
        before = window.transfer_stats.snapshot().total_bytes
        window.transfer_panels["download"].sources.setPlainText("D:/one.bin")
        window.transfer_panels["upload"].sources.setPlainText("E:/two.bin")
        window.refresh_files_overview()

        window.clear_transfers_button.click()

        self.assertEqual(window.transfer_panels["download"].sources.toPlainText(), "")
        self.assertEqual(window.transfer_panels["upload"].sources.toPlainText(), "")
        self.assertEqual(window.files_summary_label.text(), "ФАЙЛОВ: 0 · ЗАГРУЗКА: 0 · ВЫГРУЗКА: 0")
        self.assertEqual(window.transfer_stats.snapshot().total_bytes, before)

    def test_whole_local_folder_can_be_copied_between_ordinary_disks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "disk-a" / "folder"
            destination = root / "disk-b"
            source.mkdir(parents=True)
            (source / "file.txt").write_text("Neon", encoding="utf-8")
            window = self.window()
            panel = window.bind_transfer_panel("download")
            panel.sources.setPlainText(str(source))
            panel.destination.setText(str(destination))
            window.copy_engine_combo.setCurrentIndex(
                window.copy_engine_combo.findData("rclone")
            )

            with patch.object(window, "resolved_rclone_executable", return_value="rclone"), patch.object(
                window, "fill_worker_slots"
            ) as fill:
                window.start_transfers("download")

            self.assertTrue(window.running)
            self.assertTrue(window.tasks[str(source)].is_directory)
            self.assertEqual(Path(window.active_destination), destination)
            self.assertEqual(panel.choose_folder_button.text(), "Добавить папки")
            fill.assert_called_once_with()

    def test_every_google_account_shows_its_email(self) -> None:
        config = managed_rclone_config_path()
        token = {"access_token": "access", "refresh_token": "refresh"}
        store_google_drive_token(
            token,
            config,
            remote_name=GOOGLE_DRIVE_REMOTE,
            kind="personal",
            identity={"email": "personal.user@gmail.com"},
        )
        store_google_drive_token(
            token,
            config,
            remote_name="NeonGoogleDrive_team",
            kind="team",
            identity={"email": "team.user@company.com"},
        )
        window = self.window()
        window.refresh_google_drive_status()

        entries = [window.google_account_combo.itemText(index) for index in range(2)]
        self.assertTrue(any("personal.user@gmail.com" in entry for entry in entries))
        self.assertTrue(any("team.user@company.com" in entry for entry in entries))
        team_index = window.google_account_combo.findData("NeonGoogleDrive_team")
        window.google_account_combo.setCurrentIndex(team_index)
        self.assertIn("team.user@company.com", window.google_account_identity.text())


if __name__ == "__main__":
    unittest.main()
