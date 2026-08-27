"""User-scoped macOS installation, with replacement rollback and no sudo."""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4


APP_NAME = "Neon Drive.app"
BUNDLE_ID = "com.neontools.neondrive"


def installed_app() -> Path:
    for directory in (Path.home() / "Applications", Path("/Applications")):
        app = directory / APP_NAME
        if app.is_dir():
            return app
    return Path.home() / "Applications" / APP_NAME


def installed_version(app: Path) -> str:
    try:
        with (app / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        return str(info.get("CFBundleShortVersionString", "неизвестна"))
    except (OSError, ValueError, plistlib.InvalidFileException):
        return "не установлена"


def validate_target(target: Path) -> None:
    allowed = {(Path.home() / "Applications").resolve(), Path("/Applications").resolve()}
    if target.name != APP_NAME or target.is_symlink() or target.parent.resolve() not in allowed:
        raise RuntimeError("Небезопасный путь приложения macOS.")


def install_dmg(package: Path, target: Path) -> Path:
    validate_target(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not os.access(target.parent, os.W_OK):
        raise PermissionError("Нет доступа к папке Applications. Перенесите приложение через Finder.")
    with tempfile.TemporaryDirectory(prefix="neon-install-") as temporary:
        mount = Path(temporary) / "image"
        mount.mkdir()
        subprocess.run(
            ["hdiutil", "attach", str(package), "-readonly", "-nobrowse", "-mountpoint", str(mount)],
            check=True, capture_output=True, timeout=120,
        )
        staging = target.parent / (".neon-install-" + uuid4().hex + ".app")
        backup = target.parent / (".neon-previous-" + uuid4().hex + ".app")
        try:
            source = mount / APP_NAME
            with (source / "Contents/Info.plist").open("rb") as stream:
                info = plistlib.load(stream)
            if info.get("CFBundleIdentifier") != BUNDLE_ID:
                raise RuntimeError("DMG не содержит ожидаемое приложение Neon Drive.")
            subprocess.run(["ditto", str(source), str(staging)], check=True, capture_output=True, timeout=600)
            subprocess.run(["codesign", "--verify", "--deep", "--strict", str(staging)],
                           check=True, capture_output=True, timeout=120)
            if target.exists():
                target.rename(backup)
            try:
                staging.rename(target)
            except OSError:
                if backup.exists():
                    backup.rename(target)
                raise
            if backup.exists():
                shutil.rmtree(backup)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
            subprocess.run(["hdiutil", "detach", str(mount)], check=True, capture_output=True, timeout=60)
    return target


def uninstall_to_trash(target: Path) -> Path:
    validate_target(target)
    trash = Path.home() / ".Trash"
    trash.mkdir(exist_ok=True)
    destination = trash / ("Neon Drive-" + uuid4().hex + ".app")
    target.rename(destination)
    return destination
