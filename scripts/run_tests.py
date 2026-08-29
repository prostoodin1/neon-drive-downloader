"""Run every test in an isolated Qt process and temporary user-data directory."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["NEON_DRIVE_DISABLE_AUTO_UPDATE"] = "1"


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item.id()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case")
    parser.add_argument("modules", nargs="*")
    args = parser.parse_args()
    if args.case:
        from PySide6.QtCore import QSettings

        with tempfile.TemporaryDirectory(prefix="neon-test-") as temp_dir:
            os.environ["NEON_DRIVE_SETTINGS_DIR"] = temp_dir
            os.environ["NEON_DRIVE_DATA_DIR"] = str(Path(temp_dir) / "data")
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(
                QSettings.Format.IniFormat, QSettings.Scope.UserScope, temp_dir
            )
            suite = unittest.defaultTestLoader.loadTestsFromName(args.case)
            return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    suite = (
        unittest.defaultTestLoader.loadTestsFromNames(args.modules)
        if args.modules
        else unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    )
    test_ids = list(cases(suite))
    failures = []
    for index, test_id in enumerate(test_ids, 1):
        print(f"[{index}/{len(test_ids)}] {test_id}", flush=True)
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--case", test_id],
                cwd=ROOT, timeout=180, check=False,
            )
            if result.returncode:
                failures.append(test_id)
        except subprocess.TimeoutExpired:
            failures.append(test_id)
            print(f"TIMEOUT: {test_id}", flush=True)
    print(f"Tests: {len(test_ids)}; failures: {len(failures)}", flush=True)
    for failure in failures:
        print(f"FAILED: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
