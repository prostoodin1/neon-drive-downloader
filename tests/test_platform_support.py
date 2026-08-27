from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from neon_drive import platform_support


class PlatformSupportTests(unittest.TestCase):
    def test_data_directory_override_is_shared_by_all_platforms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"NEON_DRIVE_DATA_DIR": temp_dir}
        ):
            self.assertEqual(platform_support.app_data_directory(), Path(temp_dir))

    def test_macos_uses_application_support(self) -> None:
        with (
            patch.object(platform_support, "is_macos", return_value=True),
            patch.object(Path, "home", return_value=Path("/Users/neon")),
            patch.dict(os.environ, {}, clear=True),
        ):
            self.assertEqual(
                platform_support.app_data_directory(),
                Path("/Users/neon/Library/Application Support/NeonDriveDownloader"),
            )

    def test_rclone_package_supports_intel_and_apple_silicon(self) -> None:
        with patch.dict(
            os.environ,
            {"NEON_DRIVE_RCLONE_PLATFORM": "osx", "NEON_DRIVE_RCLONE_ARCH": "arm64"},
        ):
            self.assertEqual(platform_support.rclone_package_platform(), ("osx", "arm64"))
        with patch.dict(
            os.environ,
            {"NEON_DRIVE_RCLONE_PLATFORM": "osx", "NEON_DRIVE_RCLONE_ARCH": "amd64"},
        ):
            self.assertEqual(platform_support.rclone_package_platform(), ("osx", "amd64"))

    def test_macos_11_is_minimum_supported_version(self) -> None:
        with patch.object(platform_support, "is_macos", return_value=True):
            with patch.object(platform_support.platform, "mac_ver", return_value=(("10.15.7"), (), "")):
                self.assertFalse(platform_support.macos_version_supported())
            with patch.object(platform_support.platform, "mac_ver", return_value=(("11.0"), (), "")):
                self.assertTrue(platform_support.macos_version_supported())


if __name__ == "__main__":
    unittest.main()
