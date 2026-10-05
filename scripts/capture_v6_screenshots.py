from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repository))
    output = repository / "docs" / "screenshots" / "neon-drive-6"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="neon-v6-screens-") as temporary:
        root = Path(temporary)
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["NEON_DRIVE_DISABLE_AUTO_UPDATE"] = "1"
        os.environ["NEON_DRIVE_DISABLE_NETWORK"] = "1"
        os.environ["NEON_DRIVE_SETTINGS_DIR"] = str(root / "settings")
        os.environ["NEON_DRIVE_DATA_DIR"] = str(root / "data")
        os.environ["NEON_DRIVE_RCLONE_CONFIG"] = str(root / "rclone.conf")

        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QFont, QFontDatabase
        from PySide6.QtWidgets import QApplication

        from neon_drive.app import MainWindow
        from neon_drive.google_drive import GOOGLE_DRIVE_REMOTE, store_google_drive_token
        from neon_drive.settings_store import create_settings

        preview_settings = create_settings("Neon Drive Downloader")
        preview_settings.setValue("language", "ru")
        preview_settings.sync()

        store_google_drive_token(
            {"access_token": "preview", "refresh_token": "preview"},
            root / "rclone.conf",
            remote_name=GOOGLE_DRIVE_REMOTE,
            identity={"email": "demo@neondrive.app", "display_name": "Neon Demo"},
        )
        app = QApplication.instance() or QApplication(sys.argv)
        font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf"
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
        app.setFont(QFont("Arial", 10))
        window = MainWindow()
        window.auto_health_timer.stop()
        window.notifications_check.setChecked(False)
        window.animations_check.setChecked(False)
        window.resize(1180, 760)

        download = window.transfer_panels["download"]
        cloud_file = "NeonGoogleDrive,root_folder_id=video_demo:Neon_Presentation.mp4"
        cloud_folder = "NeonGoogleDrive,root_folder_id=project_demo:"
        window.settings.setValue(
            "cloud_label/" + cloud_file,
            "Мой диск / Видео / Neon_Presentation.mp4",
        )
        window.settings.setValue(
            "cloud_label/" + cloud_folder,
            "Мой диск / Проекты / Материалы сайта",
        )
        window.source_directory_flags[cloud_folder] = True
        download.sources.setPlainText(cloud_file + "\n" + cloud_folder)
        download.destination.setText(r"D:\Neon Demo\Downloads")
        window.show_transfer_direction("download")
        window.show()
        app.processEvents()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        for index, row in enumerate(download.file_rows.values()):
            size = (18 + index * 7) * 1024**3
            row.update_data(size, int(size * (0.42 + index * 0.18)), 54 * 1024**2, 92, "СКАЧИВАНИЕ")
        download.file_mode_label.setText("2 объекта")
        download.speed.setText("54.0 МБ/с")
        download.progress_text.setText("ОБЩИЙ ПРОГРЕСС · 52%")
        download.progress.set_progress(520)
        download.ring.setValue(52)
        download.eta.setText("Осталось 06:18")
        download.state_label.setText("●  СКАЧИВАНИЕ")
        download.footer_info.setText("Активно: 2 · ошибок: 0")
        app.processEvents()
        window.grab().save(str(output / "download.png"))

        upload = window.transfer_panels["upload"]
        local_file = r"D:\Neon Demo\Media\Project_final.mp4"
        local_folder = r"D:\Neon Demo\Projects\Campaign"
        cloud_target = "NeonGoogleDrive,root_folder_id=website_assets:"
        window.settings.setValue(
            "cloud_label/" + cloud_target,
            "Мой диск / Команда / Материалы сайта",
        )
        upload.sources.setPlainText(local_file + "\n" + local_folder)
        upload.destination.setText(cloud_target)
        window.refresh_destination_display("upload")
        window.show_transfer_direction("upload")
        app.processEvents()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        for index, row in enumerate(upload.file_rows.values()):
            size = (9 + index * 4) * 1024**3
            row.update_data(size, int(size * (0.68 - index * 0.2)), 47 * 1024**2, 76, "ВЫГРУЗКА")
        upload.file_mode_label.setText("2 объекта")
        upload.speed.setText("47.0 МБ/с")
        upload.progress_text.setText("ОБЩИЙ ПРОГРЕСС · 48%")
        upload.progress.set_progress(480)
        upload.ring.setValue(48)
        upload.eta.setText("Осталось 04:42")
        upload.state_label.setText("●  ВЫГРУЗКА")
        upload.footer_info.setText("Активно: 2 · ошибок: 0")
        app.processEvents()
        window.grab().save(str(output / "upload.png"))

        window.toggle_settings_page()
        app.processEvents()
        window.grab().save(str(output / "settings.png"))
        window.v6_settings_scroll.verticalScrollBar().setValue(
            window.v6_settings_scroll.verticalScrollBar().maximum()
        )
        app.processEvents()
        window.grab().save(str(output / "about.png"))
        window.force_exit = True
        window.close()
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
