from __future__ import annotations

import configparser
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .platform_support import app_data_directory


GOOGLE_DRIVE_REMOTE = "NeonGoogleDrive"
GOOGLE_DRIVE_ROOT = f"{GOOGLE_DRIVE_REMOTE}:"
GOOGLE_DRIVE_REMOTE_PREFIX = GOOGLE_DRIVE_REMOTE + "_"
ACCOUNT_KINDS = {"personal", "workspace", "team"}


@dataclass(frozen=True)
class GoogleDriveAccount:
    remote_name: str
    label: str
    kind: str = "personal"
    email: str = ""
    display_name: str = ""
    identity_verified: bool = False


def verified_google_email(value: object) -> str:
    """Return a usable Google identity email and reject test placeholders."""
    email = str(value or "").strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        return ""
    domain = email.rsplit("@", 1)[1].lower().rstrip(".")
    if domain in {
        "example.com", "example.net", "example.org", "example.invalid", "localhost"
    }:
        return ""
    if domain.endswith((".example", ".invalid", ".test", ".localhost")):
        return ""
    return email


def google_drive_root(remote_name: str = GOOGLE_DRIVE_REMOTE) -> str:
    if not re.fullmatch(r"NeonGoogleDrive(?:_[A-Za-z0-9_-]+)?", remote_name):
        raise ValueError("Некорректное имя подключения Google Drive.")
    return remote_name + ":"


def new_google_drive_remote_name() -> str:
    return GOOGLE_DRIVE_REMOTE_PREFIX + uuid4().hex[:12]

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
    return app_data_directory() / "rclone" / "rclone.conf"


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
    token: dict[str, object],
    config_path: Path | None = None,
    remote_name: str = GOOGLE_DRIVE_REMOTE,
    label: str = "",
    kind: str = "personal",
    identity: dict[str, str] | None = None,
) -> Path:
    if not token.get("access_token"):
        raise ValueError("В ответе Google отсутствует access token.")
    path = config_path or managed_rclone_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    config = _read_config(path)
    google_drive_root(remote_name)
    if not config.has_section(remote_name):
        config.add_section(remote_name)
    config.set(remote_name, "type", "drive")
    config.set(remote_name, "scope", "drive")
    config.set(
        remote_name,
        "token",
        json.dumps(token, ensure_ascii=False, separators=(",", ":")),
    )
    profile = identity or {}
    account_kind = kind if kind in ACCOUNT_KINDS else "personal"
    email = verified_google_email(profile.get("email", ""))
    display_name = str(profile.get("display_name", "")).strip()
    resolved_label = label.strip() or email or display_name or "Google Drive"
    config.set(remote_name, "neon_label", resolved_label)
    config.set(remote_name, "neon_kind", account_kind)
    if email:
        config.set(remote_name, "neon_email", email)
        config.set(remote_name, "neon_identity_verified", "true")
    else:
        # Never leave a stale or test address attached to a newly authorized token.
        config.remove_option(remote_name, "neon_email")
        config.set(remote_name, "neon_identity_verified", "false")
    if display_name:
        config.set(remote_name, "neon_display_name", display_name)
    temporary = path.with_suffix(path.suffix + ".download")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            config.write(stream, space_around_delimiters=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def google_drive_accounts(config_path: Path | None = None) -> list[GoogleDriveAccount]:
    path = config_path or managed_rclone_config_path()
    try:
        config = _read_config(path)
    except (OSError, configparser.Error):
        return []
    accounts: list[GoogleDriveAccount] = []
    for section in config.sections():
        if section != GOOGLE_DRIVE_REMOTE and not section.startswith(GOOGLE_DRIVE_REMOTE_PREFIX):
            continue
        try:
            token = json.loads(config.get(section, "token", fallback="{}"))
        except json.JSONDecodeError:
            continue
        if config.get(section, "type", fallback="") != "drive" or not isinstance(token, dict) or not token.get("access_token"):
            continue
        email = verified_google_email(config.get(section, "neon_email", fallback=""))
        display_name = config.get(section, "neon_display_name", fallback="").strip()
        label = config.get(section, "neon_label", fallback="").strip()
        if "@" in label and not verified_google_email(label):
            label = ""
        if not label:
            label = email or display_name or ("Основной Google Drive" if section == GOOGLE_DRIVE_REMOTE else section)
        kind = config.get(section, "neon_kind", fallback="personal")
        accounts.append(
            GoogleDriveAccount(
                section,
                label,
                kind if kind in ACCOUNT_KINDS else "personal",
                email,
                display_name,
                bool(email) and config.getboolean(
                    section, "neon_identity_verified", fallback=True
                ),
            )
        )
    return accounts


def google_drive_connected(
    config_path: Path | None = None, remote_name: str = GOOGLE_DRIVE_REMOTE
) -> bool:
    path = config_path or managed_rclone_config_path()
    try:
        config = _read_config(path)
        if config.get(remote_name, "type", fallback="") != "drive":
            return False
        token = json.loads(config.get(remote_name, "token", fallback="{}"))
        return isinstance(token, dict) and bool(token.get("access_token"))
    except (OSError, configparser.Error, json.JSONDecodeError):
        return False


def disconnect_google_drive(
    config_path: Path | None = None, remote_name: str = GOOGLE_DRIVE_REMOTE
) -> bool:
    path = config_path or managed_rclone_config_path()
    config = _read_config(path)
    if not config.remove_section(remote_name):
        return False
    temporary = path.with_suffix(path.suffix + ".download")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            config.write(stream, space_around_delimiters=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def fetch_google_drive_identity(token: dict[str, object]) -> dict[str, str]:
    """Read the signed-in Drive identity without exposing the OAuth token."""
    access_token = str(token.get("access_token", ""))
    if not access_token:
        return {}
    request = urllib.request.Request(
        "https://www.googleapis.com/drive/v3/about?fields=user(displayName,emailAddress)",
        headers={"Authorization": "Bearer " + access_token, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    user = payload.get("user", {}) if isinstance(payload, dict) else {}
    if not isinstance(user, dict):
        return {}
    return {
        "email": verified_google_email(user.get("emailAddress", "")),
        "display_name": str(user.get("displayName", "")).strip(),
    }
