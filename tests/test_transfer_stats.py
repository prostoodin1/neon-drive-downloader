from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone

from PySide6.QtCore import QSettings

from neon_drive.transfer_stats import TransferStats


class TransferStatsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.settings_dir = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            cls.settings_dir.name,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.settings_dir.cleanup()

    def setUp(self) -> None:
        self.settings = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, "NeonTools", "Transfer Stats Tests")
        self.settings.clear()
        self.settings.sync()
        self.stats = TransferStats(self.settings)

    def test_successful_bytes_are_accumulated_by_direction_and_period(self) -> None:
        start = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
        self.stats.record(2 * 1024**3, "download", start)
        self.stats.record(3 * 1024**3, "upload", start)

        snapshot = self.stats.snapshot(
            datetime(2026, 8, 22, 9, tzinfo=timezone.utc)
        )

        self.assertEqual(snapshot.total_bytes, 5 * 1024**3)
        self.assertEqual(snapshot.download_bytes, 2 * 1024**3)
        self.assertEqual(snapshot.upload_bytes, 3 * 1024**3)
        self.assertEqual(snapshot.period_days, 3)

    def test_monthly_mode_resets_on_first_access_in_new_month(self) -> None:
        january = datetime(2026, 1, 31, 20, tzinfo=timezone.utc)
        self.stats.set_auto_monthly_reset(True, january)
        self.stats.record(1024**4, "download", january)

        snapshot = self.stats.snapshot(
            datetime(2026, 2, 1, 8, tzinfo=timezone.utc)
        )

        self.assertEqual(snapshot.total_bytes, 0)
        self.assertEqual(snapshot.period_days, 1)
        self.assertTrue(snapshot.auto_monthly_reset)

    def test_manual_reset_starts_a_new_empty_period(self) -> None:
        self.stats.record(
            1000,
            "upload",
            datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        reset_at = datetime(2026, 7, 15, tzinfo=timezone.utc)

        self.stats.reset(reset_at)
        snapshot = self.stats.snapshot(reset_at)

        self.assertEqual(snapshot.total_bytes, 0)
        self.assertEqual(snapshot.download_bytes, 0)
        self.assertEqual(snapshot.upload_bytes, 0)
        self.assertEqual(snapshot.period_days, 1)


if __name__ == "__main__":
    unittest.main()
