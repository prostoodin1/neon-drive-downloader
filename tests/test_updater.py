from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from neon_drive import __version__
from neon_drive import updater


def release_payload(tag: str, assets: list[str], prerelease: bool = False) -> dict:
    return {
        "tag_name": tag,
        "name": tag,
        "prerelease": prerelease,
        "assets": [
            {
                "name": name,
                "browser_download_url": f"https://example.invalid/{name}",
            }
            for name in assets
        ],
    }


class UpdaterTests(unittest.TestCase):
    def test_prerelease_orders_before_matching_stable_version(self) -> None:
        self.assertLess(
            updater.version_tuple("v5.4.0-beta.1"),
            updater.version_tuple("v5.4.0"),
        )
        self.assertLess(
            updater.version_tuple("v5.4.0-beta.1"),
            updater.version_tuple("v5.4.0-beta.2"),
        )

    def test_beta_release_is_marked_for_manual_history(self) -> None:
        release = updater._normalize_release(
            release_payload(
                "v5.4.0-beta.1",
                [updater.SETUP_ASSET_NAME],
                prerelease=True,
            ),
            "public",
        )
        self.assertTrue(release["prerelease"])
        self.assertEqual(release["version"], "5.4.0-beta.1")

    def test_release_prefers_installer_over_legacy_onefile(self) -> None:
        release = updater._normalize_release(
            release_payload(
                "v99.0.0",
                [updater.LEGACY_ASSET_NAME, updater.SETUP_ASSET_NAME],
            ),
            "public",
        )
        self.assertEqual(release["asset_name"], updater.SETUP_ASSET_NAME)
        self.assertTrue(release["available"])

    def test_same_version_offers_migration_from_onefile(self) -> None:
        with (
            patch.object(updater.sys, "frozen", True, create=True),
            patch.object(updater.sys, "_MEIPASS", r"C:\Temp\_MEI123456", create=True),
        ):
            release = updater._normalize_release(
                release_payload(f"v{__version__}", [updater.SETUP_ASSET_NAME]),
                "public",
            )
        self.assertTrue(release["migration"])
        self.assertTrue(release["available"])

    def test_setup_update_uses_silent_installer_after_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            installer = root / updater.SETUP_ASSET_NAME
            installer.touch()
            current = root / "Portable" / updater.LEGACY_ASSET_NAME
            current.parent.mkdir()
            current.touch()

            with (
                patch.object(updater.sys, "frozen", True, create=True),
                patch.object(updater.sys, "_MEIPASS", r"C:\Temp\_MEI654321", create=True),
                patch.object(updater, "bootloader_parent_pid", return_value=4242),
                patch.object(updater.subprocess, "Popen") as popen,
            ):
                updater.launch_replacement(installer, current)

            script = root / "apply-setup-update.ps1"
            contents = script.read_text(encoding="utf-8-sig")
            self.assertIn("/VERYSILENT", contents)
            self.assertIn("NeonDriveDownloader.exe", contents)
            popen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
