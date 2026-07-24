from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path


REPOSITORY = "prostoodin1/neon-drive-downloader"
UPLOAD_ADDON_ID = "neon-uploader"
UPLOAD_ADDON_FILE = "NeonUploaderAddon.json"
MAX_MANIFEST_BYTES = 128 * 1024


def is_beta_build(version: str) -> bool:
    return "beta" in version.casefold()


def addon_directory() -> Path:
    override = os.environ.get("NEON_DRIVE_ADDON_DIR")
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "NeonDriveDownloader" / "addons"


def upload_addon_path() -> Path:
    return addon_directory() / UPLOAD_ADDON_FILE


def version_tag(version: str) -> str:
    return version if version.lower().startswith("v") else f"v{version}"


def upload_addon_source_url(version: str) -> str:
    override = os.environ.get("NEON_DRIVE_ADDON_SOURCE_URL")
    if override:
        return override
    tag = version_tag(version)
    return f"https://raw.githubusercontent.com/{REPOSITORY}/{tag}/addons/{UPLOAD_ADDON_FILE}"


def upload_addon_github_url(version: str) -> str:
    tag = version_tag(version)
    return f"https://github.com/{REPOSITORY}/blob/{tag}/addons/{UPLOAD_ADDON_FILE}"


def validate_upload_addon(data: object, app_version: str) -> dict:
    if not isinstance(data, dict):
        raise RuntimeError("GitHub вернул некорректный пакет дополнения.")
    if data.get("id") != UPLOAD_ADDON_ID or data.get("entry") != "builtin:upload":
        raise RuntimeError("Пакет не является дополнением «Выгрузка».")
    compatible_prefix = str(data.get("compatible_app_prefix") or "")
    if compatible_prefix and not app_version.lstrip("vV").startswith(compatible_prefix):
        raise RuntimeError(
            f"Дополнение предназначено для Neon Drive {compatible_prefix}.x, "
            f"а установлена версия {app_version}."
        )
    return data


def read_upload_addon(app_version: str) -> dict | None:
    if not is_beta_build(app_version):
        return None
    path = upload_addon_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return validate_upload_addon(data, app_version)
    except (OSError, json.JSONDecodeError, RuntimeError):
        return None


def upload_addon_installed(app_version: str) -> bool:
    return read_upload_addon(app_version) is not None


def install_upload_addon(app_version: str) -> Path:
    if not is_beta_build(app_version):
        raise RuntimeError("Дополнение «Выгрузка» доступно только в BETA-версиях.")
    request = urllib.request.Request(
        upload_addon_source_url(app_version),
        headers={"Accept": "application/json", "User-Agent": "NeonDriveDownloader"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read(MAX_MANIFEST_BYTES + 1)
    if not payload or len(payload) > MAX_MANIFEST_BYTES:
        raise RuntimeError("Пакет дополнения пустой или имеет неожиданный размер.")
    try:
        data = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Не удалось прочитать пакет дополнения с GitHub.") from exc
    validate_upload_addon(data, app_version)
    target = upload_addon_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".download")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def remove_upload_addon() -> bool:
    path = upload_addon_path()
    existed = path.is_file()
    path.unlink(missing_ok=True)
    return existed
