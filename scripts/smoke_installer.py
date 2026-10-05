"""Read public releases with the frozen installer and no developer tools in PATH."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

executable = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix="neon-installer-smoke-") as temporary:
    root = Path(temporary)
    report = root / "report.json"
    env = dict(os.environ, NEON_DRIVE_SMOKE_REPORT=str(report),
               NEON_DRIVE_DATA_DIR=str(root / "data"),
               NEON_DRIVE_SETTINGS_DIR=str(root / "settings"),
               SSL_CERT_FILE=str(root / "no-system-cert.pem"),
               SSL_CERT_DIR=str(root / "no-system-certs"))
    env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32") if os.name == "nt" else "/usr/bin:/bin"
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "PYTHONPATH", "PYTHONHOME"):
        env.pop(name, None)
    assert shutil.which("gh", path=env["PATH"]) is None
    result = subprocess.run([str(executable), "--network-self-test"], env=env,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=90)
    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    assert result.returncode == 0 and data.get("ok"), data
    assert data["method"] in ("public", "public-catalog"), data
    assert data["trusted_cas"] > 0, data
    print("Frozen installer without GitHub CLI or login:", data)
