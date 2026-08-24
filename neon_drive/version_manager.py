from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import psutil
from PySide6.QtCore import QSettings, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
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
from .single_instance import send_request
from .updater import (
    LEGACY_ASSET_NAME,
    REPOSITORY,
    SETUP_ASSET_NAMES,
    ReleaseHistoryThread,
    UpdateDownloadThread,
    version_tuple,
)


APP_ID = "{E6B76B7F-32F0-4C41-89B1-5A1694D1C7E4}_is1"


def _installed_registry_values() -> dict[str, str]:
    if os.name != "nt":
        return {}
    import winreg

    key_path = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}"
    views = (0, winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY)
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in views:
            try:
                with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view) as key:
                    values: dict[str, str] = {}
                    for name in (
                        "DisplayVersion",
                        "InstallLocation",
                        "UninstallString",
                        "QuietUninstallString",
                    ):
                        try:
                            values[name] = str(winreg.QueryValueEx(key, name)[0]).strip()
                        except OSError:
                            continue
                    if values:
                        return values
            except OSError:
                continue
    return {}


def installed_details() -> tuple[str, Path]:
    default = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    default /= "Programs/Neon Drive"
    values = _installed_registry_values()
    version = values.get("DisplayVersion", "")
    install_location = values.get("InstallLocation", "")
    if not values:
        return "не установлена", default
    return version or "неизвестна", Path(install_location or default)


def installed_uninstaller() -> Path | None:
    values = _installed_registry_values()
    raw = values.get("QuietUninstallString") or values.get("UninstallString") or ""
    if raw.startswith('"'):
        candidate = raw.split('"', 2)[1]
    else:
        marker = raw.casefold().find(".exe")
        candidate = raw[: marker + 4] if marker >= 0 else raw.split(" ", 1)[0]
    path = Path(candidate).expanduser() if candidate else Path()
    return path if candidate and path.is_file() else None


def same_version(left: str, right: str) -> bool:
    return version_tuple(left) == version_tuple(right)


def main_app_processes() -> list[psutil.Process]:
    processes: list[psutil.Process] = []
    for process in psutil.process_iter(("name",)):
        try:
            if str(process.info.get("name") or "").casefold() == "neondrivedownloader.exe":
                processes.append(process)
        except psutil.Error:
            continue
    return processes


def main_app_running() -> bool:
    return bool(main_app_processes())


def close_main_app() -> None:
    processes = main_app_processes()
    if not processes:
        return
    send_request({"command": "shutdown", "reason": "version-install"}, timeout_ms=3000)
    _gone, alive = psutil.wait_procs(processes, timeout=5)
    for process in alive:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as exc:
            raise RuntimeError(f"Не удалось закрыть Neon Drive: {exc}") from exc
    _gone, alive = psutil.wait_procs(alive, timeout=5)
    if alive:
        raise RuntimeError("Neon Drive не завершился. Закройте приложение вручную.")


class VersionManagerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.releases: list[dict] = []
        self.history_thread: ReleaseHistoryThread | None = None
        self.download_thread: UpdateDownloadThread | None = None
        self.settings = QSettings("NeonTools", "Neon Drive Installer")
        self.dark_mode = self.settings.value("dark_mode", False, type=bool)
        self.installed_version, self.install_directory = installed_details()
        self.uninstaller_path = installed_uninstaller()
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
        self.uninstall_button = QPushButton("Удалить установленную", objectName="danger")
        self.uninstall_button.clicked.connect(self.uninstall_installed)
        self.uninstall_button.setEnabled(self.uninstaller_path is not None)
        self.install_button = QPushButton("Скачать и установить", objectName="primary")
        self.install_button.clicked.connect(self.install_selected_release)
        self.install_button.setEnabled(False)
        actions.addWidget(self.github_button)
        actions.addWidget(self.uninstall_button)
        actions.addStretch()
        actions.addWidget(self.install_button)
        details_layout.addLayout(actions)
        content.addWidget(details_card, 7)
        outer.addLayout(content, 1)

        footer = QHBoxLayout()
        self.status = QLabel("Получение списка версий…", objectName="muted")
        footer.addWidget(self.status)
        footer.addStretch()
        self.theme_button = QPushButton()
        self.theme_button.clicked.connect(self.toggle_theme)
        footer.addWidget(self.theme_button)
        footer.addWidget(QLabel(f"Installer для Neon Drive {__version__}", objectName="muted"))
        outer.addLayout(footer)

    def _apply_style(self) -> None:
        if self.dark_mode:
            colors = {
                "root": "#08131f",
                "card": "#101e2d",
                "input": "#0b1724",
                "text": "#eaf2fa",
                "muted": "#91a5b8",
                "border": "#263a4e",
                "button": "#17273a",
                "disabled": "#62768a",
                "selection": "#123d49",
            }
            self.theme_button.setText("☀  Светлый фон")
        else:
            colors = {
                "root": "#f3f6f9",
                "card": "#ffffff",
                "input": "#f8fafc",
                "text": "#17212b",
                "muted": "#657585",
                "border": "#d9e2ea",
                "button": "#edf2f6",
                "disabled": "#9aa7b2",
                "selection": "#dff9fa",
            }
            self.theme_button.setText("☾  Тёмный фон")
        self.setStyleSheet(
            f"""
            * {{ font-family: 'Segoe UI Variable', 'Segoe UI'; color: {colors['text']}; }}
            #root {{ background: {colors['root']}; }}
            #title {{ font-size: 28px; font-weight: 750; color: {colors['text']}; }}
            #muted {{ color: {colors['muted']}; }}
            #badge {{ background: #e6fbfc; color: #087f86; border: 1px solid #36cbd2; border-radius: 14px; padding: 7px 12px; font-weight: 650; }}
            #card {{ background: {colors['card']}; border: 1px solid {colors['border']}; border-radius: 18px; }}
            #sectionTitle {{ font-size: 14px; font-weight: 700; padding: 4px 0; }}
            #releaseTitle {{ font-size: 23px; font-weight: 750; color: {colors['text']}; }}
            QListWidget, QTextBrowser {{ background: {colors['input']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 12px; padding: 6px; }}
            QListWidget::item {{ padding: 11px 10px; margin: 2px; border-radius: 9px; }}
            QListWidget::item:selected {{ background: {colors['selection']}; color: #22c8cf; }}
            QPushButton {{ background: {colors['button']}; border: 1px solid {colors['border']}; border-radius: 10px; padding: 8px 13px; font-weight: 650; }}
            QPushButton:hover {{ border-color: #22c8cf; color: #087f86; }}
            QPushButton#primary {{ background: #24d1d8; border-color: #24d1d8; color: #07161a; padding: 9px 17px; }}
            QPushButton#danger {{ color: #d93025; border-color: #f2b8b5; }}
            QPushButton:disabled {{ color: {colors['disabled']}; background: {colors['button']}; }}
            QProgressBar {{ border: 1px solid {colors['border']}; border-radius: 7px; background: {colors['button']}; min-height: 12px; }}
            QProgressBar::chunk {{ background: #24d1d8; border-radius: 6px; }}
            QMessageBox {{ background: {colors['card']}; }}
            QMessageBox QLabel {{ color: {colors['text']}; min-width: 280px; }}
            """
        )
        self.release_notes.document().setDefaultStyleSheet(
            f"body {{ color: {colors['text']}; background: {colors['input']}; }} "
            f"a {{ color: #18bfc7; }} code {{ color: {colors['text']}; }}"
        )

    def toggle_theme(self) -> None:
        self.dark_mode = not self.dark_mode
        self.settings.setValue("dark_mode", self.dark_mode)
        self._apply_style()

    def set_busy(self, busy: bool, text: str) -> None:
        self.refresh_button.setEnabled(not busy)
        self.install_button.setEnabled(not busy and self.version_list.currentRow() >= 0)
        self.version_list.setEnabled(not busy)
        self.uninstall_button.setEnabled(not busy and self.uninstaller_path is not None)
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
            current = (
                "  • установлена"
                if same_version(str(release.get("version") or ""), self.installed_version)
                else ""
            )
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
        same = same_version(str(release.get("version") or ""), self.installed_version)
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
        answer = QMessageBox.question(
            self,
            "Neon Drive Installer",
            f"Скачать и установить {release.get('tag')}?\n\n"
            "Для старой версии ваши пользовательские настройки сохранятся.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if main_app_running():
            self.set_busy(True, "Закрытие запущенного Neon Drive…")
            try:
                close_main_app()
            except Exception as exc:
                self.operation_failed(str(exc))
                return
        self.set_busy(True, f"Скачивание {release.get('tag')}…")
        thread = UpdateDownloadThread(release, self)
        self.download_thread = thread
        thread.succeeded.connect(lambda path: self.download_succeeded(Path(path), release))
        thread.failed.connect(self.operation_failed)
        thread.finished.connect(lambda: setattr(self, "download_thread", None))
        thread.start()

    def uninstall_installed(self) -> None:
        if self.uninstaller_path is None:
            QMessageBox.information(
                self,
                "Neon Drive Installer",
                "Зарегистрированная установка Neon Drive не найдена.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Neon Drive Installer",
            f"Удалить установленную версию {self.installed_version}?\n\n"
            "Пользовательские настройки будут сохранены для будущей установки.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if main_app_running():
            try:
                close_main_app()
            except Exception as exc:
                self.operation_failed(str(exc))
                return
        try:
            subprocess.Popen([str(self.uninstaller_path)], close_fds=True)
        except OSError as exc:
            self.operation_failed(f"Не удалось запустить удаление: {exc}")
            return
        self.status.setText("Программа удаления запущена. Менеджер версий закрывается…")
        QTimer.singleShot(400, QApplication.quit)

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
