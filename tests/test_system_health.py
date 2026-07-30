from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from neon_drive.system_health import (
    HealthCheckItem,
    check_sources,
    is_drive_root,
    probe_directory,
    run_system_health_check,
)


class SystemHealthTests(unittest.TestCase):
    def test_probe_directory_creates_missing_folder_and_cleans_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "missing" / "logs"

            result = probe_directory(target, "Журналы", repair_missing=True)

            self.assertEqual(result.status, "fixed")
            self.assertTrue(target.is_dir())
            self.assertEqual(list(target.iterdir()), [])

    def test_source_check_reports_partial_and_missing_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            partial = root / "movie.mp4.crdownload"
            partial.write_bytes(b"unfinished")

            result = check_sources([str(partial), str(root / "missing.bin")])

            self.assertIsNotNone(result)
            self.assertEqual(result.status, "warning")
            self.assertIn("ещё загружается: 1", result.details)
            self.assertIn("не найдено: 1", result.details)

    def test_windows_drive_root_is_not_a_valid_upload_folder(self) -> None:
        self.assertTrue(is_drive_root(Path("G:\\")))
        self.assertFalse(is_drive_root(Path("G:\\Mi unidad")))

    def test_full_check_repairs_directories_destination_and_rclone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_root = root / "app-data"
            destination = root / "downloads"
            managed_rclone = root / "managed" / "rclone.exe"
            source = root / "ready.bin"
            source.write_bytes(b"ready")

            with (
                patch("neon_drive.system_health.shutil.which", return_value="robocopy.exe"),
                patch(
                    "neon_drive.system_health.check_rclone_executable",
                    side_effect=[(False, "Rclone не найден."), (True, "rclone v1.2.3")],
                ),
                patch(
                    "neon_drive.system_health.download_and_install_rclone",
                    return_value=(managed_rclone, "v1.2.3"),
                ),
                patch(
                    "neon_drive.system_health.check_online",
                    return_value=HealthCheckItem("Интернет", "ok", "Доступен"),
                ),
            ):
                report = run_system_health_check(
                    app_root=app_root,
                    rclone_candidate=None,
                    download_destination=str(destination),
                    upload_destination="",
                    sources=[str(source)],
                )

            self.assertGreaterEqual(report.fixed_count, 5)
            self.assertEqual(report.error_count, 0)
            self.assertEqual(report.rclone_path, str(managed_rclone))
            self.assertEqual(report.rclone_version, "v1.2.3")
            self.assertTrue(destination.is_dir())


if __name__ == "__main__":
    unittest.main()
