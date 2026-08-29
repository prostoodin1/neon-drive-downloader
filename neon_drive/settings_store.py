"""Shared settings location; test/preview overrides never touch the real profile."""
import os
from pathlib import Path
from PySide6.QtCore import QSettings


def create_settings(application: str) -> QSettings:
    override = os.environ.get("NEON_DRIVE_SETTINGS_DIR")
    if override:
        return QSettings(str(Path(override) / (application + ".ini")), QSettings.Format.IniFormat)
    return QSettings(QSettings.defaultFormat(), QSettings.Scope.UserScope, "NeonTools", application)
