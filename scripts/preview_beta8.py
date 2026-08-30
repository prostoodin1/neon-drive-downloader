"""Capture real Beta 8 widgets with isolated settings and no cloud writes."""
import os
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["NEON_DRIVE_DISABLE_AUTO_UPDATE"] = "1"
os.environ["NEON_DRIVE_DISABLE_NETWORK"] = "1"
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
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    window.animations_check.setChecked(False)
    window.auto_health_timer.stop()
    window.resize(1100, 740)
    window.theme_combo.setCurrentIndex(window.theme_combo.findData("google_drive_dark"))
    # Public screenshots must never expose the local Windows account name.
    window.transfer_panels["download"].destination.setText("C:/Downloads")
    window.show()
    output = root / "docs/images"
    output.mkdir(parents=True, exist_ok=True)
    for width, height, name in ((1100, 740, "beta8-dark.png"), (900, 640, "beta8-compact.png")):
        window.resize(width, height)
        app.processEvents()
        QTest.qWait(300)
        window.grab().save(str(output / name))
        panel = window.transfer_panels["download"]
        for button in (panel.choose_file_button, panel.choose_files_button, panel.start_button, panel.visible_stop_button):
            assert button.isVisible(), button.text()
            point = button.mapTo(window, button.rect().topLeft())
            assert window.rect().contains(point), button.text()
    window.upload_addon_enabled = True
    window.show_transfer_direction("upload")
    app.processEvents()
    QTest.qWait(300)
    window.grab().save(str(output / "beta8-upload.png"))
    window.apply_transfer_preset("extreme")
    window.tabs.setCurrentWidget(window.profiles_page)
    app.processEvents()
    QTest.qWait(300)
    window.grab().save(str(output / "beta8-profiles.png"))
    window.force_exit = True
    window.close()
