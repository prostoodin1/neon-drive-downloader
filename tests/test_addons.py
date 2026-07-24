from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from neon_drive.addons import (
    UPLOAD_ADDON_FILE,
    install_upload_addon,
    is_beta_build,
    remove_upload_addon,
    upload_addon_github_url,
    upload_addon_installed,
)


class UploadAddonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_addon_dir = os.environ.get("NEON_DRIVE_ADDON_DIR")
        os.environ["NEON_DRIVE_ADDON_DIR"] = self.temp_dir.name

    def tearDown(self) -> None:
        if self.previous_addon_dir is None:
            os.environ.pop("NEON_DRIVE_ADDON_DIR", None)
        else:
            os.environ["NEON_DRIVE_ADDON_DIR"] = self.previous_addon_dir
        self.temp_dir.cleanup()

    def test_beta_detection_excludes_stable_versions(self) -> None:
        self.assertTrue(is_beta_build("5.4.0-beta.4"))
        self.assertFalse(is_beta_build("5.4.0"))

    def test_install_and_remove_manifest(self) -> None:
        manifest = {
            "schema": 1,
            "id": "neon-uploader",
            "name": "Выгрузка на Google Drive",
            "version": "1.0.0-beta.1",
            "entry": "builtin:upload",
            "compatible_app_prefix": "5.4",
        }
        response = io.BytesIO(json.dumps(manifest).encode("utf-8"))
        with patch("neon_drive.addons.urllib.request.urlopen", return_value=response):
            installed_path = install_upload_addon("5.4.0-beta.4")

        self.assertEqual(installed_path.name, UPLOAD_ADDON_FILE)
        self.assertTrue(upload_addon_installed("5.4.0-beta.4"))
        self.assertTrue(remove_upload_addon())
        self.assertFalse(upload_addon_installed("5.4.0-beta.4"))

    def test_github_link_targets_current_beta_tag(self) -> None:
        url = upload_addon_github_url("5.4.0-beta.4")
        self.assertIn("/v5.4.0-beta.4/", url)
        self.assertTrue(url.endswith(f"/addons/{UPLOAD_ADDON_FILE}"))


if __name__ == "__main__":
    unittest.main()
