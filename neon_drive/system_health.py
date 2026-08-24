from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .rclone_manager import VERSION_URL, download_and_install_rclone
from .copy_engines import is_rclone_remote_path
from .google_drive import google_drive_connected


ProgressCallback = Callable[[int, str], None]
INCOMPLETE_ENDINGS = (
    ".neon-partial",
    ".partial",
    ".crdownload",
    ".download",
    ".part",
    ".tmp",
)


@dataclass(frozen=True)
class HealthCheckItem:
    name: str
    status: str
    details: str


@dataclass(frozen=True)
class SystemHealthReport:
    items: tuple[HealthCheckItem, ...]
    rclone_path: str = ""
    rclone_version: str = ""

    @property
    def fixed_count(self) -> int:
        return sum(item.status == "fixed" for item in self.items)

    @property
    def warning_count(self) -> int:
        return sum(item.status == "warning" for item in self.items)

    @property
    def error_count(self) -> int:
        return sum(item.status == "error" for item in self.items)


def _emit(progress: ProgressCallback | None, percent: int, message: str) -> None:
    if progress:
        progress(max(0, min(100, percent)), message)


def probe_directory(path: Path, label: str, repair_missing: bool) -> HealthCheckItem:
    created = False
    try:
        if not path.exists():
            if not repair_missing:
                return HealthCheckItem(label, "warning", f"Папка не найдена: {path}")
            path.mkdir(parents=True, exist_ok=True)
            created = True
        if not path.is_dir():
            return HealthCheckItem(label, "error", f"Путь не является папкой: {path}")
        token = uuid4().hex
        temporary = path / f".neon-health-{token}.tmp"
        renamed = path / f".neon-health-{token}.ok"
        try:
            with temporary.open("xb") as stream:
                stream.write(b"Neon Drive system health check")
            temporary.replace(renamed)
        finally:
            temporary.unlink(missing_ok=True)
            renamed.unlink(missing_ok=True)
    except OSError as exc:
        return HealthCheckItem(label, "error", f"Нет безопасной записи в {path}: {exc}")
    if created:
        return HealthCheckItem(label, "fixed", f"Папка создана и проверена: {path}")
    return HealthCheckItem(label, "ok", f"Создание и переименование файлов работает: {path}")


def is_drive_root(path: Path) -> bool:
    raw = os.path.normcase(os.path.normpath(str(path)))
    anchor = os.path.normcase(os.path.normpath(path.anchor))
    return bool(path.drive and anchor and raw == anchor)


def check_rclone_executable(executable: str | None) -> tuple[bool, str]:
    if not executable:
        return False, "Rclone не найден."
    path = Path(executable).expanduser()
    if not path.is_file():
        return False, f"Файл Rclone не найден: {path}"
    creation_flags = 0x08000000 if os.name == "nt" else 0
    try:
        result = subprocess.run(
            [str(path), "version"],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Rclone не запускается: {exc}"
    output = (result.stdout or result.stderr or "").strip().splitlines()
    version = output[0] if output else "неизвестная версия"
    if result.returncode != 0 or not version.casefold().startswith("rclone v"):
        return False, f"Rclone вернул некорректный результат: {version}"
    return True, version


def check_online() -> HealthCheckItem:
    request = urllib.request.Request(
        VERSION_URL,
        headers={"User-Agent": "NeonDriveDownloader-SystemCheck"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read(128)
        if b"rclone" not in payload.lower():
            raise RuntimeError("сервер вернул неожиданный ответ")
    except Exception as exc:
        return HealthCheckItem(
            "Интернет и сервер Rclone",
            "warning",
            f"Сейчас недоступна проверка обновлений и установка Rclone: {exc}",
        )
    return HealthCheckItem(
        "Интернет и сервер Rclone",
        "ok",
        "Официальный сервер Rclone доступен.",
    )


def check_sources(sources: Iterable[str]) -> HealthCheckItem | None:
    selected = [Path(value).expanduser() for value in sources if value.strip()]
    if not selected:
        return None
    missing: list[str] = []
    incomplete: list[str] = []
    unreadable: list[str] = []
    for path in selected:
        name = path.name.casefold()
        if not path.exists():
            missing.append(str(path))
            continue
        if name.startswith("~$") or name.endswith(INCOMPLETE_ENDINGS):
            incomplete.append(str(path))
            continue
        try:
            path.stat()
        except OSError:
            unreadable.append(str(path))
    problems = missing + incomplete + unreadable
    if problems:
        details: list[str] = []
        if missing:
            details.append(f"не найдено: {len(missing)}")
        if incomplete:
            details.append(f"ещё загружается: {len(incomplete)}")
        if unreadable:
            details.append(f"нет доступа: {len(unreadable)}")
        preview = ", ".join(Path(value).name or value for value in problems[:3])
        return HealthCheckItem(
            "Выбранные исходники",
            "warning",
            f"Обнаружены проблемы ({'; '.join(details)}): {preview}",
        )
    return HealthCheckItem(
        "Выбранные исходники",
        "ok",
        f"Доступно выбранных путей: {len(selected)}.",
    )


def run_system_health_check(
    *,
    app_root: Path,
    rclone_candidate: str | None,
    download_destination: str,
    upload_destination: str,
    sources: Iterable[str],
    repair: bool = True,
    progress: ProgressCallback | None = None,
) -> SystemHealthReport:
    items: list[HealthCheckItem] = []
    _emit(progress, 3, "Проверка папок приложения…")
    for label, path in (
        ("Папка журналов", app_root / "logs"),
        ("Папка обновлений", app_root / "updates"),
        ("Папка инструментов", app_root / "tools"),
    ):
        items.append(probe_directory(path, label, repair))

    _emit(progress, 18, "Проверка свободного места…")
    try:
        free = shutil.disk_usage(app_root).free
        free_gib = free / (1024**3)
        items.append(
            HealthCheckItem(
                "Свободное место",
                "warning" if free_gib < 5 else "ok",
                f"На системном диске свободно {free_gib:.1f} ГиБ."
                + (" Для больших файлов желательно освободить место." if free_gib < 5 else ""),
            )
        )
    except OSError as exc:
        items.append(HealthCheckItem("Свободное место", "warning", str(exc)))

    _emit(progress, 26, "Проверка Robocopy…")
    robocopy = shutil.which("robocopy.exe") or shutil.which("robocopy")
    items.append(
        HealthCheckItem(
            "Robocopy",
            "ok" if robocopy else "error",
            f"Системный Robocopy найден: {robocopy}"
            if robocopy
            else "Robocopy не найден в Windows. Автоматическая установка системного компонента невозможна.",
        )
    )

    _emit(progress, 34, "Проверка Rclone…")
    rclone_ok, rclone_details = check_rclone_executable(rclone_candidate)
    final_rclone_path = rclone_candidate or ""
    final_rclone_version = rclone_details if rclone_ok else ""
    if rclone_ok:
        items.append(HealthCheckItem("Rclone", "ok", f"{rclone_details} запускается корректно."))
    elif repair:
        _emit(progress, 40, "Rclone повреждён или отсутствует — автоматическое восстановление…")
        try:
            def install_progress(percent: int, message: str) -> None:
                _emit(progress, 40 + int(percent * 0.38), message)

            installed_path, version = download_and_install_rclone(install_progress)
            valid, validation = check_rclone_executable(str(installed_path))
            if not valid:
                raise RuntimeError(validation)
            final_rclone_path = str(installed_path)
            final_rclone_version = version
            items.append(
                HealthCheckItem(
                    "Rclone",
                    "fixed",
                    f"Rclone {version} скачан, проверен по SHA-256 и подключён заново.",
                )
            )
        except Exception as exc:
            items.append(
                HealthCheckItem(
                    "Rclone",
                    "error",
                    f"{rclone_details} Автоматическое восстановление не удалось: {exc}",
                )
            )
    else:
        items.append(HealthCheckItem("Rclone", "warning", rclone_details))

    _emit(progress, 80, "Проверка интернет-соединения…")
    items.append(check_online())

    if download_destination.strip():
        _emit(progress, 86, "Проверка папки загрузки…")
        items.append(
            probe_directory(
                Path(download_destination).expanduser(),
                "Папка загрузки",
                repair,
            )
        )
    if upload_destination.strip():
        _emit(progress, 91, "Проверка папки Google Drive…")
        if is_rclone_remote_path(upload_destination):
            connected = google_drive_connected()
            items.append(
                HealthCheckItem(
                    "Прямое подключение Google Drive",
                    "ok" if connected else "error",
                    "OAuth2-подключение Neon доступно."
                    if connected
                    else "OAuth2-подключение не найдено. Подключите Google Drive в настройках Rclone.",
                )
            )
        else:
            upload_path = Path(upload_destination).expanduser()
            if is_drive_root(upload_path):
                items.append(
                    HealthCheckItem(
                        "Папка выгрузки Google Drive",
                        "error",
                        "Выбран виртуальный корень диска. Укажите My Drive / Мой диск / Mi unidad или вложенную папку.",
                    )
                )
            else:
                items.append(
                    probe_directory(upload_path, "Папка выгрузки Google Drive", False)
                )

    _emit(progress, 96, "Проверка выбранных файлов…")
    source_result = check_sources(sources)
    if source_result:
        items.append(source_result)
    _emit(progress, 100, "Диагностика завершена")
    return SystemHealthReport(
        tuple(items),
        rclone_path=final_rclone_path,
        rclone_version=final_rclone_version,
    )
