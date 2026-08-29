from __future__ import annotations

import os
import plistlib
import shlex
import sys
from pathlib import Path


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Neon Drive"
MACOS_LAUNCH_AGENT = "com.neontools.neondrive"


def macos_launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MACOS_LAUNCH_AGENT}.plist"


def startup_enabled() -> bool:
    if sys.platform == "darwin":
        path = macos_launch_agent_path()
        try:
            with path.open("rb") as stream:
                data = plistlib.load(stream)
            return data.get("Label") == MACOS_LAUNCH_AGENT and bool(
                data.get("ProgramArguments")
            )
        except (OSError, plistlib.InvalidFileException):
            return False
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value = str(winreg.QueryValueEx(key, RUN_VALUE)[0]).strip()
        return bool(value)
    except OSError:
        return False


def set_startup_enabled(enabled: bool, command: str) -> None:
    if sys.platform == "darwin":
        path = macos_launch_agent_path()
        if not enabled:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "Label": MACOS_LAUNCH_AGENT,
            "ProgramArguments": shlex.split(command),
            "RunAtLoad": True,
            "ProcessType": "Interactive",
        }
        temporary = path.with_suffix(".plist.download")
        try:
            with temporary.open("wb") as stream:
                plistlib.dump(data, stream)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return
    if os.name != "nt":
        return
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
