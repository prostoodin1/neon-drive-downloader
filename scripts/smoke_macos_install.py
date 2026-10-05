import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from neon_drive.macos_installer import install_dmg, installed_version, uninstall_to_trash
from neon_drive import __version__

with tempfile.TemporaryDirectory(prefix="neon-install-test-") as temporary:
    with patch("pathlib.Path.home", return_value=Path(temporary)):
        target = Path(temporary) / "Applications/Neon Drive.app"
        install_dmg(Path(sys.argv[1]).resolve(), target)
        assert installed_version(target) == __version__
        install_dmg(Path(sys.argv[1]).resolve(), target)
        assert installed_version(target) == __version__
        trash = uninstall_to_trash(target)
        assert trash.is_dir() and not target.exists()
print("DMG install, replacement and recoverable uninstall passed.")
