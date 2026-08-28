from __future__ import annotations

import os
import ctypes
from pathlib import Path

import psutil

from .copy_engines import is_rclone_remote_path


def network_location(value: str) -> bool:
    if not value.strip():
        return False
    if is_rclone_remote_path(value) or value.startswith(("\\\\", "//")):
        return True
    normalized = value.replace("\\", "/").casefold().rstrip("/") + "/"
    if any(part in normalized for part in (
        "/cloudstorage/", "/google drive/", "/googledrive-", "/мой диск/",
        "/my drive/", "/shared drives/", "/общие диски/",
        "/unidades compartidas/", "/mi unidad/", "/drives partagés/", "/mon drive/",
    )):
        return True
    try:
        target = os.path.normcase(os.path.abspath(Path(value).expanduser()))
        if os.name == "nt":
            # Query this drive only; enumerating all mounted disks can stall the
            # UI on an unrelated disconnected network drive.
            get_type = ctypes.windll.kernel32.GetDriveTypeW
            get_type.argtypes = [ctypes.c_wchar_p]
            get_type.restype = ctypes.c_uint
            return get_type(Path(target).anchor) == 4  # DRIVE_REMOTE
        for part in psutil.disk_partitions(all=True):
            mount = os.path.normcase(os.path.abspath(part.mountpoint))
            if target == mount or target.startswith(mount.rstrip("/\\") + os.sep):
                if "remote" in part.opts or part.fstype.casefold() in {"smbfs", "cifs", "nfs", "nfs4", "fuse.drivefs"}:
                    return True
    except (OSError, ValueError):
        pass
    return False


def detect_direction(sources: list[str], destination: str) -> tuple[str | None, str]:
    if not sources or not destination.strip():
        return None, "Выберите источник и назначение."
    source_remote = [network_location(source) for source in sources]
    target_remote = network_location(destination)
    if target_remote and not any(source_remote):
        mode = "Google Drive напрямую" if is_rclone_remote_path(destination) else "сетевой / синхронизируемый диск"
        return "upload", "Выгрузка → " + mode
    if all(source_remote) and not target_remote:
        return "download", "Загрузка → локальная папка"
    return None, "Локальное / смешанное копирование · направление выбирается вручную"
