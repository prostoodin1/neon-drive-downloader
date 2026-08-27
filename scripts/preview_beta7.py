"""Render real widgets with isolated settings, without connecting to a cloud."""
import os
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["NEON_DRIVE_DISABLE_AUTO_UPDATE"] = "1"
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtTest import QTest
from neon_drive.app import MainWindow

with tempfile.TemporaryDirectory() as temporary:
    os.environ["NEON_DRIVE_DATA_DIR"] = str(Path(temporary) / "data")
    os.environ["NEON_DRIVE_SETTINGS_DIR"] = temporary
    app = QApplication([])
    for font in Path("C:/Windows/Fonts").glob("segoeui*.ttf"):
        QFontDatabase.addApplicationFont(str(font))
    for name in ("seguisym.ttf", "segmdl2.ttf"):
        QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    window.animations_check.setChecked(False)
    window.auto_health_timer.stop()
    window.resize(1100, 740)
    window.theme_combo.setCurrentIndex(window.theme_combo.findData("google_drive_dark"))
    window.show()
    app.processEvents()
    QTest.qWait(500)
    output = root / "docs/images"
    output.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(output / "beta7-dark.png"))
    window.force_exit = True
    window.close()
