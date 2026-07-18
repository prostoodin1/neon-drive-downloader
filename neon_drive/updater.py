from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from . import __version__


REPOSITORY = "prostoodin1/neon-drive-downloader"
ASSET_NAME = "NeonDriveDownloader.exe"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"


def version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value.lstrip("vV"))
    return tuple(int(number) for number in numbers[:4]) or (0,)


def gh_path() -> str | None:
    found = shutil.which("gh")
    if found:
        return found
    candidates = (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "GitHub CLI" / "gh.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "GitHub CLI" / "gh.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "gh.exe",
    )
    return next((str(path) for path in candidates if path.is_file()), None)


def _public_release() -> dict:
    request = urllib.request.Request(
        API_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "NeonDriveDownloader"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.loads(response.read().decode("utf-8"))
    data["download_method"] = "public"
    return data


def _private_release() -> dict:
    executable = gh_path()
    if not executable:
        raise RuntimeError(
            "Приватный репозиторий требует GitHub CLI. Установите gh и выполните gh auth login."
        )
    result = subprocess.run(
        [executable, "api", f"repos/{REPOSITORY}/releases/latest"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "GitHub API недоступен"
        raise RuntimeError(message)
    data = json.loads(result.stdout)
    data["download_method"] = "gh"
    return data


def latest_release() -> dict:
    try:
        data = _public_release()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        data = _private_release()
    tag = str(data.get("tag_name", ""))
    assets = data.get("assets") or []
    asset = next((item for item in assets if item.get("name") == ASSET_NAME), None)
    if not tag:
        raise RuntimeError("Последний GitHub Release не содержит номера версии.")
    if not asset:
        raise RuntimeError(f"В релизе {tag} не найден файл {ASSET_NAME}.")
    return {
        "tag": tag,
        "version": tag.lstrip("vV"),
        "name": data.get("name") or tag,
        "notes": data.get("body") or "",
        "asset_url": asset.get("browser_download_url") or "",
        "method": data.get("download_method", "public"),
        "available": version_tuple(tag) > version_tuple(__version__),
        "current_version": __version__,
    }


def download_release(release: dict) -> Path:
    update_dir = Path(tempfile.gettempdir()) / "NeonDriveDownloader-update"
    update_dir.mkdir(parents=True, exist_ok=True)
    destination = update_dir / ASSET_NAME
    destination.unlink(missing_ok=True)
    if release.get("method") == "gh":
        executable = gh_path()
        if not executable:
            raise RuntimeError("GitHub CLI больше не доступен.")
        result = subprocess.run(
            [
                executable,
                "release",
                "download",
                release["tag"],
                "--repo",
                REPOSITORY,
                "--pattern",
                ASSET_NAME,
                "--dir",
                str(update_dir),
                "--clobber",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Не удалось скачать Release через GitHub CLI.")
    else:
        request = urllib.request.Request(
            release["asset_url"],
            headers={"User-Agent": "NeonDriveDownloader"},
        )
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as stream:
            shutil.copyfileobj(response, stream)
    if not destination.is_file() or destination.stat().st_size < 1_000_000:
        raise RuntimeError("Загруженный файл обновления отсутствует или повреждён.")
    return destination


def launch_replacement(downloaded: Path, current_executable: Path) -> None:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Автоустановка доступна только в собранной EXE-версии.")
    if not os.access(current_executable.parent, os.W_OK):
        raise RuntimeError("Нет прав на замену EXE в текущей папке.")
    script = downloaded.parent / "apply-update.ps1"
    script.write_text(
        """param([string]$Source,[string]$Target,[int]$PidToWait,[string]$ScriptPath)
$ErrorActionPreference='Stop'
Wait-Process -Id $PidToWait -ErrorAction SilentlyContinue
$backup = $Target + '.old'
for($i=0; $i -lt 120; $i++){
  try {
    if(Test-Path -LiteralPath $backup){Remove-Item -LiteralPath $backup -Force}
    if(Test-Path -LiteralPath $Target){Move-Item -LiteralPath $Target -Destination $backup -Force}
    Move-Item -LiteralPath $Source -Destination $Target -Force
    if(Test-Path -LiteralPath $backup){Remove-Item -LiteralPath $backup -Force}
    Start-Process -FilePath $Target
    Remove-Item -LiteralPath $ScriptPath -Force -ErrorAction SilentlyContinue
    exit 0
  } catch {
    if(!(Test-Path -LiteralPath $Target) -and (Test-Path -LiteralPath $backup)){
      Move-Item -LiteralPath $backup -Destination $Target -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
  }
}
exit 1
""",
        encoding="utf-8-sig",
    )
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Source",
            str(downloaded),
            "-Target",
            str(current_executable),
            "-PidToWait",
            str(os.getpid()),
            "-ScriptPath",
            str(script),
        ],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
    )


class UpdateCheckThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.succeeded.emit(latest_release())
        except Exception as exc:  # network and CLI errors are reported to the UI
            self.failed.emit(str(exc))


class UpdateDownloadThread(QThread):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, release: dict, parent=None) -> None:
        super().__init__(parent)
        self.release = release

    def run(self) -> None:
        try:
            self.succeeded.emit(str(download_release(self.release)))
        except Exception as exc:
            self.failed.emit(str(exc))
