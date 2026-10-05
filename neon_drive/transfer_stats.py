"""Persistent transfer-volume statistics for Neon Drive."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QSettings

from .platform_support import app_data_directory


STATS_PREFIX = "transfer_statistics"


@dataclass(frozen=True)
class TransferStatsSnapshot:
    total_bytes: int
    download_bytes: int
    upload_bytes: int
    period_start: datetime
    period_days: int
    auto_monthly_reset: bool


class TransferStats:
    """Store successful bytes outside the versioned application settings.

    ``settings`` is retained as a migration source for installations that used
    the old in-settings counter.  The dedicated INI file lives in application
    data, so replacing the executable or changing the settings namespace during
    an upgrade cannot reset the lifetime totals.
    """

    def __init__(self, settings: QSettings, storage_path: Path | None = None) -> None:
        self.legacy_settings = settings
        self.storage_path = storage_path or (
            app_data_directory() / "transfer-statistics.ini"
        )
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings = QSettings(str(self.storage_path), QSettings.Format.IniFormat)
        self._migrate_legacy_values()

    def _migrate_legacy_values(self) -> None:
        if self.settings.contains(f"{STATS_PREFIX}/schema_version"):
            return
        keys = (
            "total_bytes",
            "download_bytes",
            "upload_bytes",
            "period_start",
            "month",
            "auto_monthly_reset",
        )
        for key in keys:
            legacy_key = f"{STATS_PREFIX}/{key}"
            if self.legacy_settings.contains(legacy_key):
                self.settings.setValue(legacy_key, self.legacy_settings.value(legacy_key))
        self.settings.setValue(f"{STATS_PREFIX}/schema_version", 1)
        self.settings.sync()

    @staticmethod
    def _now(value: datetime | None = None) -> datetime:
        current = value or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    @staticmethod
    def _month_key(value: datetime) -> str:
        return value.strftime("%Y-%m")

    def _period_start(self, now: datetime) -> datetime:
        raw = str(self.settings.value(f"{STATS_PREFIX}/period_start", "") or "")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            parsed = now
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def auto_monthly_reset_enabled(self) -> bool:
        return self.settings.value(
            f"{STATS_PREFIX}/auto_monthly_reset", False, type=bool
        )

    def set_auto_monthly_reset(self, enabled: bool, now: datetime | None = None) -> None:
        current = self._now(now)
        self.settings.setValue(f"{STATS_PREFIX}/auto_monthly_reset", bool(enabled))
        self.settings.setValue(f"{STATS_PREFIX}/month", self._month_key(current))
        if not self.settings.contains(f"{STATS_PREFIX}/period_start"):
            self.settings.setValue(f"{STATS_PREFIX}/period_start", current.isoformat())
        self.settings.sync()

    def _reset_if_new_month(self, now: datetime) -> None:
        if not self.auto_monthly_reset_enabled():
            return
        stored_month = str(
            self.settings.value(f"{STATS_PREFIX}/month", self._month_key(now))
        )
        if stored_month != self._month_key(now):
            self.reset(now)

    def record(self, byte_count: int, direction: str, now: datetime | None = None) -> None:
        transferred = max(0, int(byte_count))
        if transferred == 0:
            return
        current = self._now(now)
        self._reset_if_new_month(current)
        if not self.settings.contains(f"{STATS_PREFIX}/period_start"):
            self.settings.setValue(f"{STATS_PREFIX}/period_start", current.isoformat())
            self.settings.setValue(f"{STATS_PREFIX}/month", self._month_key(current))
        direction_key = "upload_bytes" if direction == "upload" else "download_bytes"
        total = int(self.settings.value(f"{STATS_PREFIX}/total_bytes", 0) or 0)
        directional = int(self.settings.value(f"{STATS_PREFIX}/{direction_key}", 0) or 0)
        self.settings.setValue(f"{STATS_PREFIX}/total_bytes", total + transferred)
        self.settings.setValue(
            f"{STATS_PREFIX}/{direction_key}", directional + transferred
        )
        self.settings.sync()

    def reset(self, now: datetime | None = None) -> None:
        current = self._now(now)
        self.settings.setValue(f"{STATS_PREFIX}/total_bytes", 0)
        self.settings.setValue(f"{STATS_PREFIX}/download_bytes", 0)
        self.settings.setValue(f"{STATS_PREFIX}/upload_bytes", 0)
        self.settings.setValue(f"{STATS_PREFIX}/period_start", current.isoformat())
        self.settings.setValue(f"{STATS_PREFIX}/month", self._month_key(current))
        self.settings.sync()

    def snapshot(self, now: datetime | None = None) -> TransferStatsSnapshot:
        current = self._now(now)
        self._reset_if_new_month(current)
        start = self._period_start(current)
        period_days = max(1, (current.date() - start.date()).days + 1)
        return TransferStatsSnapshot(
            total_bytes=max(
                0, int(self.settings.value(f"{STATS_PREFIX}/total_bytes", 0) or 0)
            ),
            download_bytes=max(
                0,
                int(self.settings.value(f"{STATS_PREFIX}/download_bytes", 0) or 0),
            ),
            upload_bytes=max(
                0, int(self.settings.value(f"{STATS_PREFIX}/upload_bytes", 0) or 0)
            ),
            period_start=start,
            period_days=period_days,
            auto_monthly_reset=self.auto_monthly_reset_enabled(),
        )
