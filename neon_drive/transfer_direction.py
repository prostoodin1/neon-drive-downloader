from __future__ import annotations

import os
import ctypes
from pathlib import Path

import psutil

from .copy_engines import is_rclone_remote_path


CLOUD_PATH_MARKERS = (
    "/cloudstorage/", "/google drive/", "/googledrive-", "/мой диск/",
    "/my drive/", "/shared drives/", "/общие диски/", "/доступные мне/",
    "/shared with me/", "/compartido conmigo/", "/partagés avec moi/",
    "/unidades compartidas/", "/mi unidad/", "/drives partagés/", "/mon drive/",
)


def location_kind(value: str) -> str:
    """Classify a path without enumerating unrelated mounted drives."""
    if not value.strip():
        return "unknown"
    if is_rclone_remote_path(value):
        return "cloud"
    if value.startswith(("\\\\", "//")):
        return "network"
    normalized = value.replace("\\", "/").casefold().rstrip("/") + "/"
    if any(part in normalized for part in CLOUD_PATH_MARKERS):
        return "cloud"
    try:
        target = os.path.normcase(os.path.abspath(Path(value).expanduser()))
        if os.name == "nt":
            get_type = ctypes.windll.kernel32.GetDriveTypeW
            get_type.argtypes = [ctypes.c_wchar_p]
            get_type.restype = ctypes.c_uint
            drive_type = get_type(Path(target).anchor)
            if drive_type == 4:  # DRIVE_REMOTE
                return "network"
            if drive_type in {2, 3, 5, 6}:  # removable, fixed, optical, RAM disk
                return "physical"
        else:
            for part in psutil.disk_partitions(all=True):
                mount = os.path.normcase(os.path.abspath(part.mountpoint))
                if target == mount or target.startswith(mount.rstrip("/\\") + os.sep):
                    if "remote" in part.opts or part.fstype.casefold() in {
                        "smbfs", "cifs", "nfs", "nfs4", "fuse.drivefs"
                    }:
                        return "network"
                    return "physical"
    except (OSError, ValueError):
        pass
    return "physical"


def location_label(value: str) -> str:
    kind = location_kind(value)
    if kind == "cloud":
        return "GOOGLE DRIVE" if (
            is_rclone_remote_path(value) or "google" in value.casefold() or
            any(marker in value.replace("\\", "/").casefold() for marker in (
                "мой диск", "my drive", "mi unidad", "mon drive", "общие диски",
                "shared drives", "unidades compartidas", "доступные мне", "shared with me"
            ))
        ) else "ОБЛАЧНЫЙ ДИСК"
    if kind == "network":
        return "СЕТЕВОЙ ДИСК"
    if kind == "physical":
        anchor = Path(os.path.abspath(value)).anchor.rstrip("\\/")
        return "ФИЗИЧЕСКИЙ ДИСК" + (" " + anchor if anchor else "")
    return "ПУТЬ"


def network_location(value: str) -> bool:
    return location_kind(value) in {"cloud", "network"}


def detect_direction(sources: list[str], destination: str) -> tuple[str | None, str]:
    if not sources or not destination.strip():
        return None, "Выберите источник и назначение."
    source_kinds = [location_kind(source) for source in sources]
    target_kind = location_kind(destination)
    source_remote = [kind in {"cloud", "network"} for kind in source_kinds]
    target_remote = target_kind in {"cloud", "network"}
    source_label = location_label(sources[0]) if len(set(source_kinds)) == 1 else "СМЕШАННЫЕ ИСТОЧНИКИ"
    target_label = location_label(destination)
    if target_remote and not any(source_remote):
        mode = "Google Drive напрямую" if is_rclone_remote_path(destination) else "сетевой / синхронизируемый диск"
        return "upload", f"Авто: {source_label} → {target_label} · выгрузка через {mode}"
    if all(source_remote) and not target_remote:
        return "download", f"Авто: {source_label} → {target_label} · загрузка на локальный диск"
    if not any(source_remote) and not target_remote:
        return None, f"Авто: {source_label} → {target_label} · локальное копирование"
    return None, "Смешанные источники · направление выбирается вручную"
