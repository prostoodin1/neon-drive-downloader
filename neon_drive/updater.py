from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import psutil
from PySide6.QtCore import QThread, Signal

from . import __version__


REPOSITORY = "prostoodin1/neon-drive-downloader"
SETUP_ASSET_NAME = "NeonDriveDownloader-Setup.exe"
LEGACY_ASSET_NAME = "NeonDriveDownloader.exe"
ASSET_NAMES = (SETUP_ASSET_NAME, LEGACY_ASSET_NAME)
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=20"


def app_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "NeonDriveDownloader"


def version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value.lstrip("vV"))
    return tuple(int(number) for number in numbers[:4]) or (0,)


def running_onefile() -> bool:
    bundle_dir = Path(str(getattr(sys, "_MEIPASS", "")))
    return bool(getattr(sys, "frozen", False) and bundle_dir.name.upper().startswith("_MEI"))


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


def _public_json(url: str) -> object:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "NeonDriveDownloader"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data


def _private_json(endpoint: str) -> object:
    executable = gh_path()
    if not executable:
        raise RuntimeError(
            "Приватный репозиторий требует GitHub CLI. Установите gh и выполните gh auth login."
        )
    result = subprocess.run(
        [executable, "api", endpoint],
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
    return json.loads(result.stdout)


def _release_data(latest: bool = True) -> tuple[object, str]:
    url = API_URL if latest else RELEASES_URL
    endpoint = f"repos/{REPOSITORY}/releases/latest" if latest else f"repos/{REPOSITORY}/releases?per_page=20"
    try:
        return _public_json(url), "public"
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return _private_json(endpoint), "gh"


def _normalize_release(data: dict, method: str) -> dict:
    tag = str(data.get("tag_name", ""))
    assets = data.get("assets") or []
    asset = next(
        (item for name in ASSET_NAMES for item in assets if item.get("name") == name),
        None,
    )
    if not tag or not asset:
        raise RuntimeError(
            f"Релиз {tag or 'без версии'} не содержит установщик или совместимый EXE."
        )
    asset_name = str(asset.get("name") or LEGACY_ASSET_NAME)
    migration = (
        asset_name == SETUP_ASSET_NAME
        and running_onefile()
        and version_tuple(tag) == version_tuple(__version__)
    )
    return {
        "tag": tag,
        "version": tag.lstrip("vV"),
        "name": data.get("name") or tag,
        "notes": data.get("body") or "",
        "published_at": data.get("published_at") or data.get("created_at") or "",
        "asset_url": asset.get("browser_download_url") or "",
        "asset_name": asset_name,
        "method": method,
        "available": version_tuple(tag) > version_tuple(__version__) or migration,
        "migration": migration,
        "current_version": __version__,
    }


def latest_release() -> dict:
    data, method = _release_data(latest=True)
    if not isinstance(data, dict):
        raise RuntimeError("GitHub вернул некорректные данные последнего релиза.")
    return _normalize_release(data, method)


def release_history() -> list[dict]:
    data, method = _release_data(latest=False)
    if not isinstance(data, list):
        raise RuntimeError("GitHub вернул некорректный список релизов.")
    releases: list[dict] = []
    for item in data:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        try:
            releases.append(_normalize_release(item, method))
        except RuntimeError:
            continue
    if not releases:
        raise RuntimeError("Подходящие GitHub Releases не найдены.")
    return releases


def download_release(release: dict) -> Path:
    update_dir = app_data_dir() / "updates"
    update_dir.mkdir(parents=True, exist_ok=True)
    asset_name = str(release.get("asset_name") or LEGACY_ASSET_NAME)
    if asset_name not in ASSET_NAMES:
        raise RuntimeError("GitHub Release содержит неподдерживаемый формат обновления.")
    destination = update_dir / asset_name
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
                asset_name,
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


def bootloader_parent_pid(current_executable: Path) -> int:
    """Wait for the PyInstaller onefile parent so it can finish _MEI cleanup."""
    current_pid = os.getpid()
    try:
        parent = psutil.Process(current_pid).parent()
        if parent is None:
            return current_pid
        parent_executable = Path(parent.exe()).resolve()
        if os.path.normcase(str(parent_executable)) == os.path.normcase(
            str(current_executable.resolve())
        ):
            return parent.pid
    except (OSError, psutil.Error):
        pass
    return current_pid


def launch_replacement(downloaded: Path, current_executable: Path) -> None:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Автоустановка доступна только в собранной EXE-версии.")
    pid_to_wait = bootloader_parent_pid(current_executable)
    if downloaded.name.casefold() == SETUP_ASSET_NAME.casefold():
        bundle_dir = Path(str(getattr(sys, "_MEIPASS", "")))
        if bundle_dir.name == "_internal":
            install_dir = current_executable.parent
        else:
            install_dir = (
                Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
                / "Programs"
                / "Neon Drive Downloader"
            )
        script = downloaded.parent / "apply-setup-update.ps1"
        script.write_text(
            """param([string]$Source,[string]$InstallDir,[int]$PidToWait,[string]$ScriptPath)
$ErrorActionPreference='Stop'
Wait-Process -Id $PidToWait -ErrorAction SilentlyContinue
$arguments = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CLOSEAPPLICATIONS',('/DIR="' + $InstallDir + '"'))
$installer = Start-Process -FilePath $Source -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
if($installer.ExitCode -ne 0){exit $installer.ExitCode}
$target = Join-Path $InstallDir 'NeonDriveDownloader.exe'
if(!(Test-Path -LiteralPath $target)){exit 2}
Start-Process -FilePath $target
Remove-Item -LiteralPath $Source -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $ScriptPath -Force -ErrorAction SilentlyContinue
exit 0
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
                "-InstallDir",
                str(install_dir),
                "-PidToWait",
                str(pid_to_wait),
                "-ScriptPath",
                str(script),
            ],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
        return
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
            str(pid_to_wait),
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


class ReleaseHistoryThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.succeeded.emit(release_history())
        except Exception as exc:
            self.failed.emit(str(exc))
