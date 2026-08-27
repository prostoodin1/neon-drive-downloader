from __future__ import annotations

import hashlib
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from neon_drive import rclone_manager


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}


def rclone_archive(payload: bytes = b"MZ-neon-rclone") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("rclone-v1.2.3-windows-amd64/rclone.exe", payload)
    return output.getvalue()


def macos_rclone_archive(payload: bytes = b"\xcf\xfa\xed\xfe-neon-rclone") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("rclone-v1.2.3-osx-amd64/rclone", payload)
    return output.getvalue()


class RcloneManagerTests(unittest.TestCase):
    def test_macos_archive_is_verified_and_installed_as_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = macos_rclone_archive()
            filename = "rclone-v1.2.3-osx-amd64.zip"
            responses = {
                rclone_manager.VERSION_URL: b"rclone v1.2.3\n",
                f"{rclone_manager.DOWNLOADS_ROOT}/v1.2.3/SHA256SUMS": (
                    f"{hashlib.sha256(archive).hexdigest()}  {filename}\n".encode()
                ),
                f"{rclone_manager.DOWNLOADS_ROOT}/v1.2.3/{filename}": archive,
            }

            def open_url(request, timeout=0):
                return FakeResponse(responses[request.full_url])

            with (
                patch.dict(
                    os.environ,
                    {
                        "NEON_DRIVE_RCLONE_DIR": temp_dir,
                        "NEON_DRIVE_RCLONE_PLATFORM": "osx",
                        "NEON_DRIVE_RCLONE_ARCH": "amd64",
                    },
                ),
                patch.object(rclone_manager, "is_macos", return_value=True),
                patch.object(rclone_manager.urllib.request, "urlopen", side_effect=open_url),
            ):
                path, version = rclone_manager.download_and_install_rclone()

            self.assertEqual(version, "v1.2.3")
            self.assertEqual(path.name, "rclone")
            self.assertEqual(path.read_bytes(), b"\xcf\xfa\xed\xfe-neon-rclone")
            if os.name != "nt":
                self.assertTrue(path.stat().st_mode & 0o100)

    def test_locked_executable_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temporary = Path(temp_dir) / "rclone.download"
            target = Path(temp_dir) / "rclone.exe"
            temporary.write_bytes(b"MZ-new")
            target.write_bytes(b"MZ-running")

            with (
                patch.object(Path, "replace", side_effect=PermissionError(13, "Access denied")),
                patch.object(rclone_manager.time, "sleep"),
                self.assertRaisesRegex(RuntimeError, "Rclone сейчас используется"),
            ):
                rclone_manager._replace_executable(temporary, target)

    def test_download_verifies_checksum_and_installs_only_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = rclone_archive()
            filename = "rclone-v1.2.3-windows-amd64.zip"
            responses = {
                rclone_manager.VERSION_URL: b"rclone v1.2.3\n",
                f"{rclone_manager.DOWNLOADS_ROOT}/v1.2.3/SHA256SUMS": (
                    f"{hashlib.sha256(archive).hexdigest()}  {filename}\n".encode()
                ),
                f"{rclone_manager.DOWNLOADS_ROOT}/v1.2.3/{filename}": archive,
            }

            def open_url(request, timeout=0):
                return FakeResponse(responses[request.full_url])

            previous = os.environ.get("NEON_DRIVE_RCLONE_DIR")
            os.environ["NEON_DRIVE_RCLONE_DIR"] = temp_dir
            try:
                with patch.object(rclone_manager.urllib.request, "urlopen", side_effect=open_url):
                    path, version = rclone_manager.download_and_install_rclone()
            finally:
                if previous is None:
                    os.environ.pop("NEON_DRIVE_RCLONE_DIR", None)
                else:
                    os.environ["NEON_DRIVE_RCLONE_DIR"] = previous

            self.assertEqual(version, "v1.2.3")
            self.assertEqual(path.read_bytes(), b"MZ-neon-rclone")
            self.assertTrue((Path(temp_dir) / "install.json").is_file())

    def test_checksum_mismatch_keeps_existing_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "rclone.exe"
            target.write_bytes(b"MZ-existing")
            archive = rclone_archive()
            filename = "rclone-v1.2.3-windows-amd64.zip"
            responses = {
                rclone_manager.VERSION_URL: b"rclone v1.2.3\n",
                f"{rclone_manager.DOWNLOADS_ROOT}/v1.2.3/SHA256SUMS": (
                    f"{'0' * 64}  {filename}\n".encode()
                ),
                f"{rclone_manager.DOWNLOADS_ROOT}/v1.2.3/{filename}": archive,
            }

            def open_url(request, timeout=0):
                return FakeResponse(responses[request.full_url])

            previous = os.environ.get("NEON_DRIVE_RCLONE_DIR")
            os.environ["NEON_DRIVE_RCLONE_DIR"] = temp_dir
            try:
                with (
                    patch.object(rclone_manager.urllib.request, "urlopen", side_effect=open_url),
                    self.assertRaisesRegex(RuntimeError, "SHA-256"),
                ):
                    rclone_manager.download_and_install_rclone()
            finally:
                if previous is None:
                    os.environ.pop("NEON_DRIVE_RCLONE_DIR", None)
                else:
                    os.environ["NEON_DRIVE_RCLONE_DIR"] = previous

            self.assertEqual(target.read_bytes(), b"MZ-existing")


if __name__ == "__main__":
    unittest.main()
