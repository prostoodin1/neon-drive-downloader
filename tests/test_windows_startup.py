from __future__ import annotations

import sys
import tempfile
import unittest
import plistlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from neon_drive import windows_startup
from neon_drive.windows_startup import RUN_KEY, RUN_VALUE, set_startup_enabled, startup_enabled


class WindowsStartupTests(unittest.TestCase):
    def test_macos_launch_agent_can_be_enabled_and_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plist_path = Path(temp_dir) / "com.neontools.neondrive.plist"
            with (
                patch.object(windows_startup.sys, "platform", "darwin"),
                patch.object(windows_startup, "macos_launch_agent_path", return_value=plist_path),
            ):
                set_startup_enabled(True, "'/Applications/Neon Drive.app/Contents/MacOS/NeonDriveDownloader' --startup")
                self.assertTrue(startup_enabled())
                with plist_path.open("rb") as stream:
                    payload = plistlib.load(stream)
                self.assertEqual(payload["Label"], windows_startup.MACOS_LAUNCH_AGENT)
                self.assertEqual(payload["ProgramArguments"][-1], "--startup")
                set_startup_enabled(False, "")
                self.assertFalse(plist_path.exists())

    def test_reads_and_updates_current_user_run_value(self) -> None:
        key = MagicMock()
        opened = MagicMock()
        opened.__enter__.return_value = key
        created = MagicMock()
        created.__enter__.return_value = key
        winreg = SimpleNamespace(
            HKEY_CURRENT_USER=object(),
            REG_SZ=1,
            OpenKey=MagicMock(return_value=opened),
            CreateKey=MagicMock(return_value=created),
            QueryValueEx=MagicMock(return_value=('"C:\\Neon Drive\\NeonDriveDownloader.exe" --startup', 1)),
            SetValueEx=MagicMock(),
            DeleteValue=MagicMock(),
        )

        with patch.dict(sys.modules, {"winreg": winreg}):
            self.assertTrue(startup_enabled())
            set_startup_enabled(True, '"C:\\Neon Drive\\NeonDriveDownloader.exe" --startup')
            set_startup_enabled(False, "")

        winreg.OpenKey.assert_called_once_with(winreg.HKEY_CURRENT_USER, RUN_KEY)
        winreg.QueryValueEx.assert_called_once_with(key, RUN_VALUE)
        winreg.SetValueEx.assert_called_once()
        winreg.DeleteValue.assert_called_once_with(key, RUN_VALUE)


if __name__ == "__main__":
    unittest.main()
