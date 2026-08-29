from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from neon_drive.google_drive import (
    GOOGLE_DRIVE_REMOTE,
    disconnect_google_drive,
    extract_authorize_token,
    google_drive_connected,
    oauth_completion_template_path,
    store_google_drive_token,
)


class GoogleDriveTests(unittest.TestCase):
    def test_oauth_completion_page_requests_automatic_tab_close(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ", {"NEON_DRIVE_RCLONE_CONFIG": str(Path(temp_dir) / "rclone.conf")}
        ):
            path = oauth_completion_template_path()
            contents = path.read_text(encoding="utf-8")

        self.assertIn("window.close()", contents)
        self.assertIn("Google Drive подключён", contents)

    def test_extracts_token_without_exposing_surrounding_output(self) -> None:
        token = {
            "access_token": "secret-access",
            "token_type": "Bearer",
            "refresh_token": "secret-refresh",
        }
        output = "Open your browser\n" + json.dumps(token) + "\nEnd paste\n"

        self.assertEqual(extract_authorize_token(output), token)

    def test_stores_detects_and_disconnects_managed_remote(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "rclone.conf"
            token = {
                "access_token": "secret-access",
                "token_type": "Bearer",
                "refresh_token": "secret-refresh",
            }

            stored = store_google_drive_token(token, config_path)

            self.assertEqual(stored, config_path)
            self.assertTrue(google_drive_connected(config_path))
            contents = config_path.read_text(encoding="utf-8")
            self.assertIn(f"[{GOOGLE_DRIVE_REMOTE}]", contents)
            self.assertIn("type=drive", contents)
            self.assertTrue(disconnect_google_drive(config_path))
            self.assertFalse(google_drive_connected(config_path))


if __name__ == "__main__":
    unittest.main()
