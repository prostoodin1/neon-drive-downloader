from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


def is_macos() -> bool:
    return sys.platform == "darwin"


def app_data_directory() -> Path:
    override = os.environ.get("NEON_DRIVE_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if is_macos():
        return Path.home() / "Library" / "Application Support" / "NeonDriveDownloader"
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "NeonDriveDownloader"


def rclone_executable_name() -> str:
    requested_platform = os.environ.get("NEON_DRIVE_RCLONE_PLATFORM", "").casefold()
    if requested_platform == "windows":
        return "rclone.exe"
    if requested_platform == "osx":
        return "rclone"
    return "rclone.exe" if os.name == "nt" else "rclone"


def rclone_package_platform() -> tuple[str, str]:
    requested_platform = os.environ.get("NEON_DRIVE_RCLONE_PLATFORM", "").casefold()
    requested_arch = os.environ.get("NEON_DRIVE_RCLONE_ARCH", "").casefold()
    target_platform = requested_platform or ("osx" if is_macos() else "windows")
    machine = requested_arch or platform.machine().casefold()
    target_arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
    return target_platform, target_arch


def macos_version_supported(minimum_major: int = 11) -> bool:
    if not is_macos():
        return True
    try:
        major = int((platform.mac_ver()[0] or "0").split(".", 1)[0])
    except ValueError:
        return False
    return major >= minimum_major
