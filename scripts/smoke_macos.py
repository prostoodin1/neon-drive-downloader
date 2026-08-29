"""Exercise the actual .app launcher, not only the inner executable."""
import os
from pathlib import Path
import subprocess
import tempfile

with tempfile.TemporaryDirectory(prefix="neon-launch-test-") as temporary:
    env = dict(os.environ, NEON_DRIVE_DISABLE_NETWORK="1", NEON_DRIVE_DISABLE_AUTO_UPDATE="1",
               NEON_DRIVE_SETTINGS_DIR=temporary, NEON_DRIVE_DATA_DIR=temporary)
    env.pop("QT_QPA_PLATFORM", None)
    for name in ("Neon Drive.app", "Neon Drive Installer.app"):
        bundle = (Path("dist") / name).resolve()
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundle)], check=True)
        executable = "NeonDriveInstaller" if "Installer" in name else "NeonDriveDownloader"
        subprocess.run([str(bundle / "Contents/MacOS" / executable), "--smoke-test"],
                       env=env, check=True, timeout=60)
        subprocess.run(["open", "-W", "-n", str(bundle), "--args", "--smoke-test"],
                       env=env, check=True, timeout=60)
    if list(Path(temporary).rglob("crash-*.log")):
        raise RuntimeError("macOS smoke test wrote a crash log")
print("Native macOS GUI and LaunchServices checks passed.")
