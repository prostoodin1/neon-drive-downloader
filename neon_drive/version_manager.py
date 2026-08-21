from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import psutil
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .updater import (
    LEGACY_ASSET_NAME,
    REPOSITORY,
    SETUP_ASSET_NAMES,
    ReleaseHistoryThread,
    UpdateDownloadThread,
)


APP_ID = "{E6B76B7F-32F0-4C41-89B1-5A1694D1C7E4}_is1"


def installed_details() -> tuple[str, Path]:
    default = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    default /= "Programs/Neon Drive"
    if os.name != "nt":
        return "не установлена", default
    try:
        import winreg

        key_path = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            version = str(winreg.QueryValueEx(key, "DisplayVersion")[0])
            install_location = str(winreg.QueryValueEx(key, "InstallLocation")[0])
        return version or "неизвестна", Path(install_location or default)
    except OSError:
        return "не установлена", default


def main_app_running() -> bool:
    for process in psutil.process_iter(("name",)):
        try:
            if str(process.info.get("name") or "").casefold() == "neondrivedownloader.exe":
                return True
        except psutil.Error:
            continue
    return False


class VersionManagerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.releases: list[dict] = []
        self.history_thread: ReleaseHistoryThread | None = None
        self.download_thread: UpdateDownloadThread | None = None
        self.installed_version, self.install_directory = installed_details()
        self.setWindowTitle("Neon Drive Installer")
        self.setMinimumSize(900, 620)
        self.resize(1040, 720)
        self._build_ui()
        self._apply_style()
        if os.environ.get("NEON_DRIVE_DISABLE_NETWORK") != "1":
            self.refresh_versions()

    def _build_ui(self) -> None:
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 20, 24, 22)
        outer.setSpacing(16)

        header = QHBoxLayout()
        brand = QVBoxLayout()
        title = QLabel("Neon Drive Installer", objectName="title")
        subtitle = QLabel(
            "Установка новых и предыдущих версий с подробным списком изменений",
            objectName="muted",
        )
        brand.addWidget(title)
        brand.addWidget(subtitle)
        header.addLayout(brand)
        header.addStretch()
        self.installed_badge = QLabel(
            f"Установлена: {self.installed_version}", objectName="badge"
        )
        header.addWidget(self.installed_badge)
        outer.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(16)
        versions_card = QFrame(objectName="card")
        versions_layout = QVBoxLayout(versions_card)
        versions_layout.setContentsMargins(16, 16, 16, 16)
        versions_header = QHBoxLayout()
        versions_header.addWidget(QLabel("Версии", objectName="sectionTitle"))
        versions_header.addStretch()
        self.refresh_button = QPushButton("Обновить список")
        self.refresh_button.clicked.connect(self.refresh_versions)
        versions_header.addWidget(self.refresh_button)
        versions_layout.addLayout(versions_header)
        self.version_list = QListWidget(objectName="versionList")
        self.version_list.currentRowChanged.connect(self.show_release)
        versions_layout.addWidget(self.version_list, 1)
        content.addWidget(versions_card, 4)

        details_card = QFrame(objectName="card")
        details_layout = QVBoxLayout(details_card)
        details_layout.setContentsMargins(20, 18, 20, 18)
        self.release_title = QLabel("Выберите версию", objectName="releaseTitle")
        self.release_meta = QLabel("", objectName="muted")
        details_layout.addWidget(self.release_title)
        details_layout.addWidget(self.release_meta)
        details_layout.addWidget(QLabel("Что изменилось", objectName="sectionTitle"))
        self.release_notes = QTextBrowser(objectName="notes")
        self.release_notes.setOpenExternalLinks(False)
        details_layout.addWidget(self.release_notes, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        details_layout.addWidget(self.progress)
        actions = QHBoxLayout()
        self.github_button = QPushButton("Открыть на GitHub")
        self.github_button.clicked.connect(self.open_selected_release)
        self.install_button = QPushButton("Скачать и установить", objectName="primary")
        self.install_button.clicked.connect(self.install_selected_release)
        self.install_button.setEnabled(False)
        actions.addWidget(self.github_button)
        actions.addStretch()
        actions.addWidget(self.install_button)
        details_layout.addLayout(actions)
        content.addWidget(details_card, 7)
        outer.addLayout(content, 1)

        footer = QHBoxLayout()
        self.status = QLabel("Получение списка версий…", objectName="muted")
        footer.addWidget(self.status)
        footer.addStretch()
        footer.addWidget(QLabel(f"Installer для Neon Drive {__version__}", objectName="muted"))
        outer.addLayout(footer)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            * { font-family: 'Segoe UI Variable', 'Segoe UI'; color: #17212b; }
            #root { background: #f3f6f9; }
            #title { font-size: 28px; font-weight: 750; color: #101820; }
            #muted { color: #657585; }
            #badge { background: #e6fbfc; color: #087f86; border: 1px solid #36cbd2; border-radius: 14px; padding: 7px 12px; font-weight: 650; }
            #card { background: #ffffff; border: 1px solid #d9e2ea; border-radius: 18px; }
            #sectionTitle { font-size: 14px; font-weight: 700; padding: 4px 0; }
            #releaseTitle { font-size: 23px; font-weight: 750; color: #101820; }
            QListWidget, QTextBrowser { background: #f8fafc; border: 1px solid #dce5ec; border-radius: 12px; padding: 6px; }
            QListWidget::item { padding: 11px 10px; margin: 2px; border-radius: 9px; }
            QListWidget::item:selected { background: #dff9fa; color: #087f86; }
            QPushButton { background: #edf2f6; border: 1px solid #d3dde5; border-radius: 10px; padding: 8px 13px; font-weight: 650; }
            QPushButton:hover { border-color: #22c8cf; color: #087f86; }
            QPushButton#primary { background: #24d1d8; border-color: #24d1d8; color: #07161a; padding: 9px 17px; }
            QPushButton:disabled { color: #9aa7b2; background: #eef2f5; }
            QProgressBar { border: 1px solid #d3dde5; border-radius: 7px; background: #edf2f6; min-height: 12px; }
            QProgressBar::chunk { background: #24d1d8; border-radius: 6px; }
            """
        )

    def set_busy(self, busy: bool, text: str) -> None:
        self.refresh_button.setEnabled(not busy)
        self.install_button.setEnabled(not busy and self.version_list.currentRow() >= 0)
        self.version_list.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.status.setText(text)

    def refresh_versions(self) -> None:
        if self.history_thread and self.history_thread.isRunning():
            return
        self.set_busy(True, "Получение версий с GitHub…")
        thread = ReleaseHistoryThread(self)
        self.history_thread = thread
        thread.succeeded.connect(self.versions_loaded)
        thread.failed.connect(self.operation_failed)
        thread.finished.connect(lambda: setattr(self, "history_thread", None))
        thread.start()

    def versions_loaded(self, releases: list[dict]) -> None:
        self.releases = releases
        self.version_list.clear()
        for release in releases:
            channel = "BETA" if release.get("prerelease") else "STABLE"
            current = "  • установлена" if release.get("version") == self.installed_version else ""
            item = QListWidgetItem(f"{release.get('tag')}   [{channel}]{current}")
            item.setToolTip(str(release.get("published_at") or ""))
            self.version_list.addItem(item)
        self.set_busy(False, f"Доступно версий: {len(releases)}")
        if releases:
            self.version_list.setCurrentRow(0)

    def selected_release(self) -> dict | None:
        row = self.version_list.currentRow()
        return self.releases[row] if 0 <= row < len(self.releases) else None

    def show_release(self, row: int) -> None:
        release = self.selected_release()
        if release is None:
            self.install_button.setEnabled(False)
            return
        self.release_title.setText(str(release.get("name") or release.get("tag")))
        published = str(release.get("published_at") or "")[:10]
        channel = "Предварительная версия" if release.get("prerelease") else "Стабильная версия"
        self.release_meta.setText(
            f"{channel}  ·  {published or 'дата не указана'}  ·  {release.get('asset_name')}"
        )
        notes = str(release.get("notes") or "Изменения для этой версии не описаны.")
        self.release_notes.setMarkdown(notes)
        same = release.get("version") == self.installed_version
        self.install_button.setText("Переустановить выбранную" if same else "Скачать и установить")
        self.install_button.setEnabled(True)

    def open_selected_release(self) -> None:
        release = self.selected_release()
        if release:
            QDesktopServices.openUrl(
                QUrl(f"https://github.com/{REPOSITORY}/releases/tag/{release.get('tag')}")
            )

    def install_selected_release(self) -> None:
        release = self.selected_release()
        if release is None or (self.download_thread and self.download_thread.isRunning()):
            return
        if main_app_running():
            QMessageBox.warning(
                self,
                "Neon Drive Installer",
                "Закройте Neon Drive перед установкой другой версии.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Neon Drive Installer",
            f"Скачать и установить {release.get('tag')}?\n\n"
            "Для старой версии ваши пользовательские настройки сохранятся.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.set_busy(True, f"Скачивание {release.get('tag')}…")
        thread = UpdateDownloadThread(release, self)
        self.download_thread = thread
        thread.succeeded.connect(lambda path: self.download_succeeded(Path(path), release))
        thread.failed.connect(self.operation_failed)
        thread.finished.connect(lambda: setattr(self, "download_thread", None))
        thread.start()

    def download_succeeded(self, downloaded: Path, release: dict) -> None:
        try:
            if downloaded.name in SETUP_ASSET_NAMES:
                subprocess.Popen([str(downloaded)], close_fds=True)
                self.status.setText("Установщик запущен. Менеджер версий закрывается…")
                QTimer.singleShot(300, QApplication.quit)
                return
            if downloaded.name != LEGACY_ASSET_NAME:
                raise RuntimeError("Эта версия использует неизвестный формат пакета.")
            self.install_directory.mkdir(parents=True, exist_ok=True)
            target = self.install_directory / "NeonDriveDownloader.exe"
            shutil.copy2(downloaded, target)
            subprocess.Popen([str(target)], close_fds=True)
            self.set_busy(False, f"Версия {release.get('tag')} установлена и запущена.")
        except Exception as exc:
            self.operation_failed(str(exc))

    def operation_failed(self, message: str) -> None:
        self.set_busy(False, "Операция завершилась ошибкой")
        QMessageBox.critical(self, "Neon Drive Installer", message)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Neon Drive Installer")
    app.setOrganizationName("NeonTools")
    app.setStyle("Fusion")
    window = VersionManagerWindow()
    window.show()
    if "--smoke-test" in sys.argv:
        QTimer.singleShot(1200, app.quit)
    return app.exec()
