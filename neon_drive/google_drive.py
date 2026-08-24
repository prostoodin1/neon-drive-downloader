from __future__ import annotations

import configparser
import json
import os
import re
from pathlib import Path


GOOGLE_DRIVE_REMOTE = "NeonGoogleDrive"
GOOGLE_DRIVE_ROOT = f"{GOOGLE_DRIVE_REMOTE}:"

OAUTH_COMPLETION_TEMPLATE = """<!doctype html>
<html lang=\"ru\"><head><meta charset=\"utf-8\"><title>Neon Drive</title>
<style>body{font:16px system-ui;background:#f4f7fb;color:#172033;display:grid;place-items:center;height:100vh;margin:0}.card{background:white;padding:32px;border-radius:18px;box-shadow:0 12px 40px #0002;text-align:center}h1{color:#1a73e8}</style></head>
<body><div class=\"card\"><h1>Google Drive подключён</h1><p>Можно вернуться в Neon Drive. Эта вкладка закроется автоматически.</p></div>
<script>setTimeout(function(){window.open('','_self');window.close();},900);</script></body></html>
"""


def managed_rclone_config_path() -> Path:
    override = os.environ.get("NEON_DRIVE_RCLONE_CONFIG")
    if override:
        return Path(override).expanduser()
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local_app_data / "NeonDriveDownloader" / "rclone" / "rclone.conf"


def oauth_completion_template_path() -> Path:
    path = managed_rclone_config_path().parent / "oauth-complete.html"
    if not path.is_file() or path.read_text(encoding="utf-8") != OAUTH_COMPLETION_TEMPLATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".html.download")
        try:
            temporary.write_text(OAUTH_COMPLETION_TEMPLATE, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


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
