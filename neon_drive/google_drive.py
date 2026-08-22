from __future__ import annotations

import configparser
import json
import os
import re
from pathlib import Path


GOOGLE_DRIVE_REMOTE = "NeonGoogleDrive"
GOOGLE_DRIVE_ROOT = f"{GOOGLE_DRIVE_REMOTE}:"


def managed_rclone_config_path() -> Path:
    override = os.environ.get("NEON_DRIVE_RCLONE_CONFIG")
    if override:
        return Path(override).expanduser()
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local_app_data / "NeonDriveDownloader" / "rclone" / "rclone.conf"


def _read_config(path: Path) -> configparser.ConfigParser:
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    if path.is_file():
        config.read(path, encoding="utf-8")
    return config


def extract_authorize_token(output: str) -> dict[str, object]:
    """Extract the OAuth token printed by ``rclone authorize drive``."""
    candidates = [line.strip() for line in output.splitlines() if line.strip().startswith("{")]
    if not candidates:
        candidates = re.findall(r"\{[^\r\n]*\"access_token\"[^\r\n]*\}", output)
    for candidate in reversed(candidates):
        try:
            token = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(token, dict) and token.get("access_token"):
            return token
    raise ValueError("Rclone не вернул токен Google Drive.")


def store_google_drive_token(
    token: dict[str, object], config_path: Path | None = None
) -> Path:
    if not token.get("access_token"):
        raise ValueError("В ответе Google отсутствует access token.")
    path = config_path or managed_rclone_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    config = _read_config(path)
    if not config.has_section(GOOGLE_DRIVE_REMOTE):
        config.add_section(GOOGLE_DRIVE_REMOTE)
    config.set(GOOGLE_DRIVE_REMOTE, "type", "drive")
    config.set(GOOGLE_DRIVE_REMOTE, "scope", "drive")
    config.set(
        GOOGLE_DRIVE_REMOTE,
        "token",
        json.dumps(token, ensure_ascii=False, separators=(",", ":")),
    )
    temporary = path.with_suffix(path.suffix + ".download")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            config.write(stream, space_around_delimiters=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def google_drive_connected(config_path: Path | None = None) -> bool:
    path = config_path or managed_rclone_config_path()
    try:
        config = _read_config(path)
        if config.get(GOOGLE_DRIVE_REMOTE, "type", fallback="") != "drive":
            return False
        token = json.loads(config.get(GOOGLE_DRIVE_REMOTE, "token", fallback="{}"))
        return isinstance(token, dict) and bool(token.get("access_token"))
    except (OSError, configparser.Error, json.JSONDecodeError):
        return False


def disconnect_google_drive(config_path: Path | None = None) -> bool:
    path = config_path or managed_rclone_config_path()
    config = _read_config(path)
    if not config.remove_section(GOOGLE_DRIVE_REMOTE):
        return False
    temporary = path.with_suffix(path.suffix + ".download")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            config.write(stream, space_around_delimiters=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True
