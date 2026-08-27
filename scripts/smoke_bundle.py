from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> int:
    bundle = Path(sys.argv[1]).resolve()
    macos = sys.platform == "darwin"
    binaries = bundle / "Contents" / "MacOS" if macos else bundle
    suffix = "" if macos else ".exe"
    with tempfile.TemporaryDirectory(prefix="neon-smoke-") as temp:
        env = {
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "NEON_DRIVE_DISABLE_AUTO_UPDATE": "1",
            "NEON_DRIVE_SETTINGS_DIR": temp,
            "NEON_DRIVE_DATA_DIR": str(Path(temp) / "data"),
            "NEON_DRIVE_ADDON_DIR": str(Path(temp) / "addons"),
        }
        flags = 0 if macos else getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for name, arguments in (
            ("NeonDriveCLI", ["--help"]),
            ("NeonDriveDownloader", ["--smoke-test"]),
        ):
            result = subprocess.run(
                [str(binaries / (name + suffix)), *arguments],
                env=env, timeout=45, check=False, creationflags=flags,
            )
            if result.returncode:
                raise RuntimeError(f"{name} exited with {result.returncode}")
        if list((Path(temp) / "data").rglob("crash-*.log")):
            raise RuntimeError("Application generated a crash log")
        print("Packaged GUI and hidden CLI smoke tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
