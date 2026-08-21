from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Download and verify the Rclone bundled with Neon Drive.")
    parser.add_argument("--destination", default="vendor/rclone")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    destination = Path(args.destination).resolve()
    executable = destination / "rclone.exe"
    metadata = destination / "install.json"
    if executable.is_file() and metadata.is_file() and not args.force:
        print(f"Bundled Rclone already exists: {executable}")
        return 0

    staging = destination.with_name(destination.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    os.environ["NEON_DRIVE_RCLONE_DIR"] = str(staging)
    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

    from neon_drive.rclone_manager import download_and_install_rclone

    path, version = download_and_install_rclone(
        lambda percent, message: print(f"[{percent:3d}%] {message}")
    )
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, executable)
    shutil.copy2(staging / "install.json", metadata)
    shutil.rmtree(staging)
    print(f"Bundled Rclone {version}: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
