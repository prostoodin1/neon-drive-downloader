from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from neon_drive.windows_startup import RUN_KEY, RUN_VALUE, set_startup_enabled, startup_enabled


class WindowsStartupTests(unittest.TestCase):
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
