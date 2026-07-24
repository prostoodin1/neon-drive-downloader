from __future__ import annotations

import hashlib
import io
import json
import os
import re
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path


DOWNLOADS_ROOT = "https://downloads.rclone.org"
VERSION_URL = f"{DOWNLOADS_ROOT}/version.txt"
MAX_TEXT_BYTES = 256 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXECUTABLE_BYTES = 80 * 1024 * 1024
ProgressCallback = Callable[[int, str], None]


def rclone_install_directory() -> Path:
    override = os.environ.get("NEON_DRIVE_RCLONE_DIR")
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "NeonDriveDownloader" / "tools" / "rclone"


def installed_rclone_path() -> Path | None:
    path = rclone_install_directory() / "rclone.exe"
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
) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/octet-stream", "User-Agent": "NeonDriveDownloader"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
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


def _release_details(progress: ProgressCallback | None = None) -> tuple[str, str, str]:
    if progress:
        progress(2, "Проверка последней стабильной версии Rclone…")
    version_text = _fetch_bytes(VERSION_URL, MAX_TEXT_BYTES).decode("utf-8-sig", errors="replace")
    match = re.search(r"v\d+(?:\.\d+){2}(?:[-\w.]*)?", version_text)
    if not match:
        raise RuntimeError("Не удалось определить последнюю версию Rclone.")
    version = match.group(0)
    filename = f"rclone-{version}-windows-amd64.zip"
    base_url = f"{DOWNLOADS_ROOT}/{version}"
    sums = _fetch_bytes(f"{base_url}/SHA256SUMS", MAX_TEXT_BYTES).decode(
        "utf-8-sig", errors="replace"
    )
    checksum_match = re.search(
        rf"(?im)^([0-9a-f]{{64}})\s+\*?{re.escape(filename)}\s*$",
        sums,
    )
    if not checksum_match:
        raise RuntimeError("Официальная контрольная сумма Windows-архива Rclone не найдена.")
    return version, filename, checksum_match.group(1).lower()


def download_and_install_rclone(
    progress: ProgressCallback | None = None,
) -> tuple[Path, str]:
    version, filename, expected_sha256 = _release_details(progress)
    archive_url = f"{DOWNLOADS_ROOT}/{version}/{filename}"
    archive = _fetch_bytes(archive_url, MAX_ARCHIVE_BYTES, progress, (8, 82))
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
            and item.filename.replace("\\", "/").casefold().endswith("/rclone.exe")
        ]
        if len(candidates) != 1:
            raise RuntimeError("В официальном архиве не найден единственный rclone.exe.")
        executable_info = candidates[0]
        if executable_info.file_size <= 0 or executable_info.file_size > MAX_EXECUTABLE_BYTES:
            raise RuntimeError("rclone.exe имеет неожиданный размер.")
        target_dir = rclone_install_directory()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "rclone.exe"
        temporary = target.with_suffix(".download")
        try:
            if progress:
                progress(92, "Подключение rclone.exe к Neon Drive…")
            with package.open(executable_info) as source, temporary.open("wb") as destination:
                while True:
                    chunk = source.read(256 * 1024)
                    if not chunk:
                        break
                    destination.write(chunk)
            if temporary.stat().st_size != executable_info.file_size:
                raise RuntimeError("rclone.exe извлечён не полностью.")
            with temporary.open("rb") as executable:
                if executable.read(2) != b"MZ":
                    raise RuntimeError("Извлечённый файл не является Windows-приложением.")
            temporary.replace(target)
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
