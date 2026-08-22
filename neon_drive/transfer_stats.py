"""Persistent transfer-volume statistics for Neon Drive."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from PySide6.QtCore import QSettings


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
    """Store successful transfer bytes in the application's QSettings file."""

    def __init__(self, settings: QSettings) -> None:
        self.settings = settings

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
