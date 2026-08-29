from __future__ import annotations

from .network import https_context

import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from .platform_support import (
    app_data_directory,
    is_macos,
    rclone_executable_name,
    rclone_package_platform,
)


DOWNLOADS_ROOT = "https://downloads.rclone.org"
VERSION_URL = f"{DOWNLOADS_ROOT}/version.txt"
GITHUB_RELEASE_API = "https://api.github.com/repos/rclone/rclone/releases/latest"
GITHUB_RELEASE_ROOT = "https://github.com/rclone/rclone/releases/download"
MAX_TEXT_BYTES = 256 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXECUTABLE_BYTES = 140 * 1024 * 1024
ProgressCallback = Callable[[int, str], None]


def _replace_executable(temporary: Path, target: Path) -> None:
    last_error: OSError | None = None
    for attempt in range(6):
        try:
            temporary.replace(target)
            return
        except OSError as exc:
            locked = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (5, 32)
            if not locked:
                raise
            last_error = exc
            if attempt < 5:
                time.sleep(0.25)
    raise RuntimeError(
        "Rclone сейчас используется другим процессом. Остановите все загрузки и выгрузки, "
        "закройте оставшийся процесс Rclone и повторите обновление."
    ) from last_error


def rclone_install_directory() -> Path:
    override = os.environ.get("NEON_DRIVE_RCLONE_DIR")
    if override:
        return Path(override).expanduser()
    return app_data_directory() / "tools" / "rclone"


def bundled_rclone_directory() -> Path:
    """Return the read-only Rclone payload shipped inside Neon Drive beta.13."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root) / "tools"
    return Path(__file__).resolve().parents[1] / "vendor" / "rclone"


def bundled_rclone_path() -> Path | None:
    path = bundled_rclone_directory() / rclone_executable_name()
    return path if path.is_file() else None


def bundled_rclone_version() -> str | None:
    metadata = bundled_rclone_directory() / "install.json"
    try:
        data = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = str(data.get("version") or "") if isinstance(data, dict) else ""
    return version or None


def installed_rclone_path() -> Path | None:
    path = rclone_install_directory() / rclone_executable_name()
    return path if path.is_file() else None


def installed_rclone_version() -> str | None:
    metadata = rclone_install_directory() / "install.json"
    try:
        data = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = str(data.get("version") or "") if isinstance(data, dict) else ""
    return version or None


def _fetch_bytes(
    url: str,
    limit: int,
    progress: ProgressCallback | None = None,
    progress_range: tuple[int, int] = (0, 100),
    attempts: int = 4,
    timeout: int = 60,
    accept: str = "application/octet-stream",
) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "NeonDriveDownloader",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    last_error: BaseException | None = None
    attempts = max(1, int(attempts))
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=https_context()) as response:
                total_header = getattr(response, "headers", {}).get("Content-Length", "0")
                try:
                    total = int(total_header)
                except (TypeError, ValueError):
                    total = 0
                if total > limit:
                    raise RuntimeError("Архив Rclone имеет неожиданный размер.")
                payload = bytearray()
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    payload.extend(chunk)
                    if len(payload) > limit:
                        raise RuntimeError("Архив Rclone превышает допустимый размер.")
                    if progress and total:
                        start, end = progress_range
                        percent = start + int((end - start) * len(payload) / total)
                        progress(min(end, percent), "Скачивание официального архива Rclone…")
            if not payload:
                raise RuntimeError("Сервер Rclone вернул пустой ответ.")
            return bytes(payload)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            if progress:
                progress(
                    progress_range[0],
                    f"Сервер Rclone не ответил · повтор {attempt + 2} из {attempts}…",
                )
            time.sleep(2 ** attempt)
    raise RuntimeError(
        f"Сервер Rclone временно не отвечает после {attempts} попыток. Повторите позже."
    ) from last_error


def _checksum_from_text(sums: str, filename: str) -> str:
    checksum_match = re.search(
        rf"(?im)^([0-9a-f]{{64}})\s+\*?{re.escape(filename)}\s*$",
        sums,
    )
    if not checksum_match:
        raise RuntimeError("Официальная контрольная сумма архива Rclone не найдена.")
    return checksum_match.group(1).lower()


def _github_release_details(progress: ProgressCallback | None = None) -> tuple[str, str, str, str]:
    if progress:
        progress(3, "Основной сервер недоступен · используется официальный GitHub Rclone…")
    payload = json.loads(
        _fetch_bytes(
            GITHUB_RELEASE_API,
            MAX_TEXT_BYTES,
            attempts=3,
            timeout=30,
            accept="application/vnd.github+json",
        ).decode(
            "utf-8-sig", errors="replace"
        )
    )
    version = str(payload.get("tag_name") or "") if isinstance(payload, dict) else ""
    if not re.fullmatch(r"v\d+(?:\.\d+){2}(?:[-\w.]*)?", version):
        raise RuntimeError("GitHub Rclone вернул некорректную версию.")
    target_platform, target_arch = rclone_package_platform()
    filename = f"rclone-{version}-{target_platform}-{target_arch}.zip"
    assets = payload.get("assets", []) if isinstance(payload, dict) else []
    urls = {
        str(asset.get("name") or ""): str(asset.get("browser_download_url") or "")
        for asset in assets
        if isinstance(asset, dict)
    }
    archive_url = urls.get(filename) or f"{GITHUB_RELEASE_ROOT}/{version}/{filename}"
    sums_url = urls.get("SHA256SUMS") or f"{GITHUB_RELEASE_ROOT}/{version}/SHA256SUMS"
    sums = _fetch_bytes(sums_url, MAX_TEXT_BYTES, attempts=3, timeout=30).decode(
        "utf-8-sig", errors="replace"
    )
    return version, filename, _checksum_from_text(sums, filename), archive_url


def _release_details(progress: ProgressCallback | None = None) -> tuple[str, str, str, str]:
    if progress:
        progress(2, "Проверка последней стабильной версии Rclone…")
    try:
        version_text = _fetch_bytes(
            VERSION_URL, MAX_TEXT_BYTES, attempts=2, timeout=20
        ).decode("utf-8-sig", errors="replace")
    except RuntimeError:
        return _github_release_details(progress)
    match = re.search(r"v\d+(?:\.\d+){2}(?:[-\w.]*)?", version_text)
    if not match:
        raise RuntimeError("Не удалось определить последнюю версию Rclone.")
    version = match.group(0)
    target_platform, target_arch = rclone_package_platform()
    filename = f"rclone-{version}-{target_platform}-{target_arch}.zip"
    base_url = f"{DOWNLOADS_ROOT}/{version}"
    try:
        sums = _fetch_bytes(
            f"{base_url}/SHA256SUMS", MAX_TEXT_BYTES, attempts=2, timeout=20
        ).decode("utf-8-sig", errors="replace")
        checksum = _checksum_from_text(sums, filename)
    except RuntimeError:
        return _github_release_details(progress)
    return version, filename, checksum, f"{base_url}/{filename}"


def download_and_install_rclone(
    progress: ProgressCallback | None = None,
) -> tuple[Path, str]:
    version, filename, expected_sha256, archive_url = _release_details(progress)
    try:
        archive = _fetch_bytes(archive_url, MAX_ARCHIVE_BYTES, progress, (8, 82))
    except RuntimeError:
        github_url = f"{GITHUB_RELEASE_ROOT}/{version}/{filename}"
        if archive_url == github_url:
            raise
        if progress:
            progress(8, "Основной архив недоступен · скачивание с официального GitHub Rclone…")
        archive = _fetch_bytes(github_url, MAX_ARCHIVE_BYTES, progress, (8, 82))
    if progress:
        progress(85, "Проверка SHA-256…")
    actual_sha256 = hashlib.sha256(archive).hexdigest()
    if actual_sha256 != expected_sha256:
        raise RuntimeError("SHA-256 архива Rclone не совпадает с официальной контрольной суммой.")

    try:
        package = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile as exc:
        raise RuntimeError("Скачанный архив Rclone повреждён.") from exc
    with package:
        candidates = [
            item
            for item in package.infolist()
            if not item.is_dir()
            and item.filename.replace("\\", "/").casefold().endswith(
                "/" + rclone_executable_name().casefold()
            )
        ]
        if len(candidates) != 1:
            raise RuntimeError("В официальном архиве не найден единственный файл Rclone.")
        executable_info = candidates[0]
        if executable_info.file_size <= 0 or executable_info.file_size > MAX_EXECUTABLE_BYTES:
            raise RuntimeError("Rclone имеет неожиданный размер.")
        target_dir = rclone_install_directory()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / rclone_executable_name()
        temporary = target.with_suffix(".download")
        try:
            if progress:
                progress(92, "Подключение Rclone к Neon Drive…")
            with package.open(executable_info) as source, temporary.open("wb") as destination:
                while True:
                    chunk = source.read(256 * 1024)
                    if not chunk:
                        break
                    destination.write(chunk)
            if temporary.stat().st_size != executable_info.file_size:
                raise RuntimeError("Rclone извлечён не полностью.")
            with temporary.open("rb") as executable:
                header = executable.read(4)
                valid_header = (
                    header in {b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"}
                    if is_macos()
                    else header.startswith(b"MZ")
                )
                if not valid_header:
                    raise RuntimeError("Извлечённый файл Rclone имеет неверный формат.")
            _replace_executable(temporary, target)
            if is_macos():
                target.chmod(target.stat().st_mode | 0o755)
        finally:
            temporary.unlink(missing_ok=True)

    metadata = target_dir / "install.json"
    metadata_temporary = metadata.with_suffix(".download")
    metadata_temporary.write_text(
        json.dumps(
            {
                "version": version,
                "source": archive_url,
                "sha256": actual_sha256,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    metadata_temporary.replace(metadata)
    if progress:
        progress(100, f"Rclone {version} подключён.")
    return target, version
