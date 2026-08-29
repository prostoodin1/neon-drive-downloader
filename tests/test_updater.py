from __future__ import annotations

import io
import tempfile
import unittest
import urllib.error
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
    def setUp(self) -> None:
        # Windows fixtures stay deterministic on macOS; native cases override this.
        platform_patch = patch.object(updater, "is_macos", return_value=False)
        platform_patch.start()
        self.addCleanup(platform_patch.stop)

    def test_beta_update_check_includes_newer_prereleases(self) -> None:
        releases = [
            release_payload("v5.5.0-beta.4", [updater.SETUP_ASSET_NAME], prerelease=True),
            release_payload("v5.6.0-beta.2", [updater.SETUP_ASSET_NAME], prerelease=True),
            release_payload("v5.4.0", [updater.SETUP_ASSET_NAME]),
        ]
        with patch.object(updater, "_release_data", return_value=(releases, "public")) as lookup:
            release = updater.latest_release()

        lookup.assert_called_once_with(latest=False)
        self.assertEqual(release["version"], "5.6.0-beta.2")
        self.assertTrue(release["available"])

    def test_public_release_lookup_never_requires_github_login(self) -> None:
        with patch.object(
            updater,
            "_public_json",
            side_effect=urllib.error.URLError("offline"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Вход в GitHub.*не требуются"):
                updater._release_data()

    def test_last_downloaded_release_is_kept_in_single_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            release = {
                "tag": "v5.4.0-beta.4",
                "version": "5.4.0-beta.4",
                "asset_name": updater.SETUP_ASSET_NAME,
                "asset_url": "https://example.invalid/setup.exe",
                "method": "public",
            }
            payload = io.BytesIO(b"n" * 1_000_001)
            with (
                patch.object(updater, "app_data_dir", return_value=root),
                patch.object(updater.urllib.request, "urlopen", return_value=payload),
            ):
                downloaded = updater.download_release(release)
                cached = updater.last_downloaded_release()

            self.assertEqual(downloaded.parent.name, updater.LAST_DOWNLOAD_DIRECTORY)
            self.assertTrue(downloaded.is_file())
            self.assertIsNotNone(cached)
            self.assertEqual(cached["version"], "5.4.0-beta.4")
            self.assertEqual(Path(cached["path"]), downloaded)

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

    def test_macos_release_selects_dmg_instead_of_windows_installer(self) -> None:
        with patch.object(updater, "is_macos", return_value=True):
            release = updater._normalize_release(
                release_payload(
                    "v99.0.0",
                    [updater.SETUP_ASSET_NAME, updater.MACOS_ASSET_NAME],
                ),
                "public",
            )
        self.assertEqual(release["asset_name"], updater.MACOS_ASSET_NAME)

    def test_macos_update_opens_dmg_without_powershell(self) -> None:
        with (
            patch.object(updater, "is_macos", return_value=True),
            patch.object(updater.sys, "frozen", True, create=True),
            patch.object(updater.subprocess, "Popen") as popen,
        ):
            downloaded = Path(updater.MACOS_ASSET_NAME)
            updater.launch_replacement(downloaded, Path("NeonDriveDownloader"))
        self.assertEqual(popen.call_args.args[0], ["open", str(downloaded)])

    def test_history_accepts_previous_setup_filename(self) -> None:
        release = updater._normalize_release(
            release_payload("v5.4.0-beta.13", [updater.PREVIOUS_SETUP_ASSET_NAME]),
            "public",
        )

        self.assertEqual(release["asset_name"], updater.PREVIOUS_SETUP_ASSET_NAME)
        self.assertEqual(release["version"], "5.4.0-beta.13")

    def test_same_version_offers_migration_from_onefile(self) -> None:
        with (
            patch.object(updater.sys, "frozen", True, create=True),
            patch.object(updater.sys, "_MEIPASS", str(Path("temp") / "_MEI123456"), create=True),
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
            self.assertNotIn("Remove-Item -LiteralPath $Source", contents)
            popen.assert_called_once()

    def test_onefile_update_uses_copy_and_keeps_cached_exe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            downloaded = root / updater.LEGACY_ASSET_NAME
            downloaded.write_bytes(b"cached-update")
            current = root / "Portable" / updater.LEGACY_ASSET_NAME
            current.parent.mkdir()
            current.write_bytes(b"current")

            with (
                patch.object(updater.sys, "frozen", True, create=True),
                patch.object(updater, "bootloader_parent_pid", return_value=4242),
                patch.object(updater.subprocess, "Popen") as popen,
            ):
                updater.launch_replacement(downloaded, current)

            replacement = root / f"apply-{updater.LEGACY_ASSET_NAME}"
            self.assertTrue(downloaded.is_file())
            self.assertEqual(replacement.read_bytes(), downloaded.read_bytes())
            arguments = popen.call_args.args[0]
            self.assertEqual(arguments[arguments.index("-Source") + 1], str(replacement))


if __name__ == "__main__":
    unittest.main()
