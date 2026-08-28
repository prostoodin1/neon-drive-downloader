from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
import hashlib
import ssl
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NEON_DRIVE_DISABLE_AUTO_UPDATE", "1")
os.environ.setdefault("NEON_DRIVE_DISABLE_NETWORK", "1")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QFrame
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from shiboken6 import isValid
from neon_drive.app import MainWindow
from neon_drive.copy_engines import RcloneOptions, rclone_arguments, is_rclone_remote_path
from neon_drive.drive_browser import DriveClient, DriveFolder, DriveFolderDialog, is_managed_drive_path, virtual_drive_parts
from neon_drive import updater
from neon_drive.version_manager import VersionManagerWindow
from neon_drive.transfer_direction import detect_direction
from neon_drive.settings_store import create_settings
from neon_drive.network import https_context


class Beta8Tests(unittest.TestCase):
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

    def test_shared_virtual_path_resolves_to_exact_folder_id(self):
        client = DriveClient("rclone")
        parent = DriveFolder("Clients Materials", "drive123", "drive123", "Общие диски / Clients Materials")
        child = DriveFolder("Test carpet", "folder456", "drive123", parent.label + " / Test carpet")
        with patch.object(client, "folders", return_value=[child]):
            trail = client.resolve_virtual("H:/Unidades compartidas/Clients Materials/Test carpet", [parent])
        self.assertEqual(trail[-1].folder_id, "folder456")
        self.assertIn("team_drive=drive123", trail[-1].remote)
        self.assertIn("root_folder_id=folder456", trail[-1].remote)
        self.assertTrue(is_rclone_remote_path(child.remote))
        self.assertTrue(is_managed_drive_path(child.remote))
        self.assertFalse(is_managed_drive_path("NeonGoogleDrive,config=/bad:"))
        args, target = rclone_arguments("movie.mp4", child.remote)
        self.assertEqual(target, child.remote + "movie.mp4")
        self.assertEqual(args[2], target)

    def test_missing_or_duplicate_folder_does_not_fall_back_to_root(self):
        client = DriveClient("rclone")
        drive = DriveFolder("Clients Materials", "drive123", "drive123", "Shared")
        child = DriveFolder("Test carpet", "id1", "drive123", "Shared / Test carpet")
        for entries in ([], [child, child]):
            with patch.object(client, "folders", return_value=entries):
                with self.assertRaises(ValueError):
                    client.resolve_virtual("H:/Unidades compartidas/Clients Materials/Test carpet", [drive])

    def test_my_drive_and_saved_cloud_folder_resolve(self):
        client = DriveClient("rclone")
        root = DriveFolder("Мой диск", "root", label="Мой диск")
        child = DriveFolder("Video", "video123", label="Мой диск / Video")
        with patch.object(client, "folders", return_value=[child]):
            self.assertEqual(client.resolve_virtual("G:/My Drive/Video", [root])[-1], child)
            self.assertEqual(client.resolve_virtual("NeonGoogleDrive:Video", [root])[-1], child)
        self.assertEqual(client.resolve_virtual(child.remote, [root])[-1].folder_id, "video123")
        self.assertEqual(virtual_drive_parts("H:/Unidades compartidas/Clients Materials/Test carpet"),
                         ("shared", "Clients Materials", ["Test carpet"]))

    def test_browser_uses_public_rclone_listing_without_console(self):
        client = DriveClient("rclone")
        with patch("neon_drive.drive_browser.subprocess.Popen") as popen:
            popen.return_value.communicate.return_value = (b'[{"id":"drive1","name":"Shared"}]', b"")
            popen.return_value.returncode = 0
            roots = client.roots()
        self.assertEqual(roots[1].drive_id, "drive1")
        self.assertIn("drives", popen.call_args.args[0])
        self.assertFalse(popen.call_args.kwargs.get("shell", False))

    def test_actual_dialog_navigates_and_confirms_folder(self):
        root = DriveFolder("Мой диск", "root", label="Мой диск")
        child = DriveFolder("Video", "video123", label="Мой диск / Video")
        def wait_ready(dialog):
            deadline = time.monotonic() + 3
            while dialog.thread is not None and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertIsNone(dialog.thread)
        with patch.object(DriveClient, "roots", return_value=[root]), patch.object(DriveClient, "folders", side_effect=lambda folder: [child] if folder == root else []):
            dialog = DriveFolderDialog("unused", "")
            wait_ready(dialog)
            self.assertFalse(dialog.choose.isEnabled())
            dialog.enter_folder(dialog.list.item(0))
            wait_ready(dialog)
            self.assertEqual(dialog.trail[-1], root)
            dialog.enter_folder(dialog.list.item(0))
            wait_ready(dialog)
            self.assertEqual(dialog.path_label.text(), child.label)
            dialog.choose.click()
            self.assertEqual(dialog.selected_folder, child)
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
            dialog.deleteLater()

    def test_client_cancel_terminates_only_own_query(self):
        client = DriveClient("unused")
        process = MagicMock()
        process.poll.return_value = None
        client._process = process
        client.cancel()
        process.terminate.assert_called_once()
        with self.assertRaisesRegex(RuntimeError, "отменён"):
            client.query(["lsjson", "NeonGoogleDrive:"])

    def test_spanish_shared_drive_is_detected_as_upload(self):
        self.assertEqual(detect_direction(["C:/movie.mp4"], "H:/Unidades compartidas/Clients Materials/Test carpet")[0], "upload")

    def test_visible_stop_cancels_waiting_queue(self):
        window = self.window()
        window.running = True
        window.queue = ["waiting.bin"]
        window.set_transfer_controls_enabled(True)
        window.transfer_panels["download"].visible_stop_button.click()
        self.assertFalse(window.running)
        self.assertEqual(window.queue, [])
        self.assertTrue(window.stopping)

    def test_visible_stop_ends_real_rclone_without_modifying_source(self):
        executable = Path("vendor/rclone/rclone.exe" if os.name == "nt" else "vendor/rclone/rclone")
        if not executable.is_file():
            self.skipTest("Bundled Rclone is fetched in the build step.")
        def throttled(*args, **kwargs):
            command, target = rclone_arguments(*args, **kwargs)
            # APFS cloning can finish before Stop is clicked and bypasses the
            # bandwidth limiter. Exercise a real streamed copy in this test.
            return [*command, "--bwlimit=1M", "--local-no-clone"], target
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bin"
            source.write_bytes(b"neon" * 4 * 1024 * 1024)
            digest = hashlib.sha256(source.read_bytes()).digest()
            window = self.window()
            window.auto_health_timer.stop()
            window.upload_addon_enabled = True
            window.copy_engine_combo.setCurrentIndex(window.copy_engine_combo.findData("rclone"))
            for direction, buffered in (("download", False), ("download", True), ("upload", False)):
                window.download_buffer_check.setChecked(buffered)
                panel = window.transfer_panels[direction]
                panel.sources.setPlainText(str(source))
                destination = root / f"{direction}-{buffered}"
                panel.destination.setText(str(destination))
                with patch("neon_drive.app.rclone_arguments", side_effect=throttled):
                    window.start_transfers(direction)
                    deadline = time.monotonic() + 12
                    while not any(worker.processId() for worker in window.workers.values()) and time.monotonic() < deadline:
                        QTest.qWait(30)
                    self.assertTrue(window.workers, window.terminal.toPlainText())
                    QTest.qWait(200)
                    workers = list(window.workers.values())
                    panel.visible_stop_button.click()
                    deadline = time.monotonic() + 8
                    while window.running and time.monotonic() < deadline:
                        QTest.qWait(30)
                    for worker in workers:
                        if isValid(worker) and worker.processId():
                            worker.kill()
                            worker.waitForFinished(2000)
                    self.assertFalse(window.running, window.terminal.toPlainText())
                    self.assertFalse(window.workers)
                    self.assertEqual(window.completed_items, 0)
                    self.assertFalse(list(destination.glob(".neon-buffer-*")))
                    self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), digest)

    def test_cancel_cloud_picker_keeps_original_explorer_path(self):
        window = self.window()
        original = "H:/Unidades compartidas/Clients Materials/Test carpet"
        window.upload_destination.setText(original)
        with patch("neon_drive.app.google_drive_connected", return_value=True), patch.object(window, "resolved_rclone_executable", return_value="rclone"), patch("neon_drive.app.DriveFolderDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            window.use_or_connect_google_drive()
        self.assertEqual(window.upload_destination.text(), original)
        self.assertIsNone(window.cloud_browser)

    def test_cloud_picker_commits_only_confirmed_folder(self):
        window = self.window()
        selected = DriveFolder("Test carpet", "folder123", "drive456", "Clients Materials / Test carpet")
        with patch("neon_drive.app.google_drive_connected", return_value=True), patch.object(window, "resolved_rclone_executable", return_value="rclone"), patch("neon_drive.app.DriveFolderDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog.return_value.selected_folder = selected
            self.assertTrue(window.choose_cloud_destination("upload", "H:/Unidades compartidas/Clients Materials/Test carpet"))
        self.assertEqual(window.upload_destination.text(), selected.remote)
        self.assertEqual(window.settings.value("cloud_label/" + selected.remote), selected.label)

    def test_oauth_success_does_not_erase_destination(self):
        window = self.window()
        path = "H:/Unidades compartidas/Clients Materials/Test carpet"
        window.upload_destination.setText(path)
        with patch("neon_drive.app.QMessageBox.information"):
            window.google_drive_oauth_succeeded("config")
        self.assertEqual(window.upload_destination.text(), path)

    def test_route_preference_filesystem_never_opens_cloud_browser(self):
        window = self.window()
        window.google_route_combo.setCurrentIndex(window.google_route_combo.findData("filesystem"))
        path = "H:/Unidades compartidas/Clients Materials/Test carpet"
        with patch.object(window, "choose_cloud_destination") as browser:
            self.assertTrue(window.accept_destination_folder("upload", path))
            browser.assert_not_called()
        self.assertEqual(window.upload_destination.text(), path)
        window.google_route_combo.setCurrentIndex(window.google_route_combo.findData("direct"))
        with patch.object(window, "choose_cloud_destination", return_value=False) as browser:
            self.assertFalse(window.accept_destination_folder("upload", path))
            browser.assert_called_once_with("upload", path)
        self.assertEqual(window.upload_destination.text(), path)

    def test_single_file_replaces_list_and_stop_is_outside_terminal(self):
        window = self.window()
        panel = window.transfer_panels["download"]
        panel.sources.setPlainText("old1\nold2")
        with patch("neon_drive.app.QFileDialog.getOpenFileName", return_value=("new.bin", "")):
            window.choose_single_file_for("download")
        self.assertEqual(panel.sources.toPlainText(), "new.bin")
        self.assertEqual(panel.choose_files_button.text(), "Добавить файлы")
        self.assertFalse(panel.terminal_card.isAncestorOf(panel.visible_stop_button))
        window.set_transfer_controls_enabled(True)
        self.assertTrue(panel.visible_stop_button.isEnabled())

    def test_extreme_upload_chunk_and_saved_settings(self):
        window = self.window()
        window.apply_transfer_preset("extreme")
        options = window.selected_rclone_options()
        self.assertEqual(options.drive_chunk_size_mib, 1024)
        args, _ = rclone_arguments("movie.bin", "NeonGoogleDrive:", options)
        self.assertIn("--drive-chunk-size=1024Mi", args)
        self.assertIn("--transfers=1", args)
        self.assertEqual(len([arg for arg in args if arg.startswith("--transfers=")]), 1)
        window.persist_settings()
        self.assertEqual(window.settings.value("drive_chunk_mib", type=int), 1024)
        with self.assertRaises(ValueError):
            rclone_arguments("movie", "NeonGoogleDrive:", RcloneOptions(drive_chunk_size_mib=1000))

    def test_installer_falls_back_to_public_catalog_without_gh(self):
        catalog = [{"tag_name": "v5.5.0-beta.8", "prerelease": True, "assets": [{"name": updater.SETUP_ASSET_NAME}]}]
        with tempfile.TemporaryDirectory() as temporary, patch.object(updater, "app_data_dir", return_value=Path(temporary)), patch.object(updater, "_public_json", side_effect=[urllib.error.URLError("API unavailable"), catalog]), patch.object(updater.subprocess, "run", side_effect=AssertionError("CLI must not be invoked")):
            data, method = updater._release_data(False)
            self.assertEqual(data, catalog)
            self.assertEqual(method, "public-catalog")
            with patch.object(updater, "_public_json", side_effect=urllib.error.URLError("offline")):
                self.assertEqual(updater._release_data(False), (catalog, "cached"))

    def test_settings_override_uses_explicit_ini_file(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"NEON_DRIVE_SETTINGS_DIR": temporary}):
            settings = create_settings("Neon Drive Downloader")
            settings.setValue("test", "isolated")
            settings.sync()
            self.assertEqual(settings.format(), QSettings.Format.IniFormat)
            self.assertEqual(Path(settings.fileName()).parent, Path(temporary))
            self.assertTrue(Path(settings.fileName()).is_file())

    def test_https_bundle_keeps_certificate_and_hostname_verification(self):
        context = https_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertGreater(context.cert_store_stats()["x509_ca"], 0)

    def test_delayed_animation_is_compatible_with_macos_qt(self):
        window = self.window()
        window.animations_check.setChecked(True)
        card = QFrame(window)
        window.animate_appearance(card, duration=15, delay=10)
        deadline = time.monotonic() + 3
        while card.graphicsEffect() is not None and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertIsNone(card.graphicsEffect())

    def test_download_digest_mismatch_preserves_previous_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "updates/last-download"
            cache.mkdir(parents=True)
            previous = cache / updater.SETUP_ASSET_NAME
            previous.write_bytes(b"old installer")
            release = {"asset_name": updater.SETUP_ASSET_NAME, "asset_url": "https://example.invalid/setup.exe", "digest": "sha256:" + "0" * 64}
            with patch.object(updater, "app_data_dir", return_value=root), patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(b"x" * 1_000_001)):
                with self.assertRaisesRegex(RuntimeError, "Контрольная сумма"):
                    updater.download_release(release)
            self.assertEqual(previous.read_bytes(), b"old installer")

    def test_installer_can_launch_local_setup_without_network(self):
        manager = VersionManagerWindow()
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "NeonDrive-Setup.exe"
            package.write_bytes(b"test fixture")
            with patch("neon_drive.version_manager.is_macos", return_value=False), patch("neon_drive.version_manager.QFileDialog.getOpenFileName", return_value=(str(package), "")), patch("neon_drive.version_manager.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), patch("neon_drive.version_manager.main_app_running", return_value=False), patch.object(manager, "download_succeeded") as install:
                manager.install_local_package()
                install.assert_called_once_with(package, {"tag": package.name, "local_package": True})
        self.assertTrue(manager.release_notes.openExternalLinks())
        manager.close()


if __name__ == "__main__":
    unittest.main()
