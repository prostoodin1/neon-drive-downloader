"""Read-only Google Drive browser using the bundled, OAuth-aware Rclone."""
from __future__ import annotations

import json
import re
import subprocess
import threading
from dataclasses import dataclass

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton

from .google_drive import GOOGLE_DRIVE_ROOT, managed_rclone_config_path


ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
MANAGED_PATH = re.compile(r"^NeonGoogleDrive(?:,(?:team_drive|root_folder_id)=[A-Za-z0-9_-]*)*:")
SHARED_NAMES = {"shared drives", "unidades compartidas", "общие диски", "drives partagés"}
MY_NAMES = {"my drive", "мой диск", "mi unidad", "mon drive"}


def is_managed_drive_path(value: str) -> bool:
    return bool(MANAGED_PATH.match(value.strip()))


def virtual_drive_parts(value: str) -> tuple[str, str, list[str]] | None:
    parts = [part for part in value.replace("\\", "/").split("/") if part]
    for index, part in enumerate(parts):
        lowered = part.casefold()
        if lowered in SHARED_NAMES:
            remaining = parts[index + 1:]
            return ("shared", remaining[0], remaining[1:]) if remaining else ("shared", "", [])
        if lowered in MY_NAMES:
            return "my", "", parts[index + 1:]
    return None


@dataclass(frozen=True)
class DriveFolder:
    name: str
    folder_id: str
    drive_id: str = ""
    label: str = ""

    @property
    def remote(self) -> str:
        if not ID_PATTERN.fullmatch(self.folder_id) or (self.drive_id and not ID_PATTERN.fullmatch(self.drive_id)):
            raise ValueError("Некорректный ID папки Google Drive.")
        return f"NeonGoogleDrive,team_drive={self.drive_id},root_folder_id={self.folder_id}:"


class DriveClient:
    def __init__(self, executable: str) -> None:
        self.executable = executable
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None

    def cancel(self) -> None:
        self.cancelled.set()
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                try:
                    self._process.terminate()
                except ProcessLookupError:
                    pass

    def query(self, arguments: list[str]) -> list[dict]:
        with self._lock:
            if self.cancelled.is_set():
                raise RuntimeError("Выбор папки отменён.")
            process = subprocess.Popen(
                [self.executable, *arguments, f"--config={managed_rclone_config_path()}",
                 "--contimeout=15s", "--timeout=30s", "--retries=1", "--low-level-retries=2"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._process = process
        try:
            try:
                stdout, stderr = process.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise RuntimeError("Google Drive не ответил за 60 секунд. Проверьте сеть и доступ.")
            if self.cancelled.is_set():
                raise RuntimeError("Выбор папки отменён.")
            if process.returncode:
                raise RuntimeError(stderr.decode("utf-8", errors="replace")[-2000:] or "Не удалось прочитать Google Drive.")
            result = json.loads(stdout.decode("utf-8"))
            if not isinstance(result, list):
                raise ValueError("Google Drive вернул неожиданный список папок.")
            return result
        finally:
            with self._lock:
                self._process = None

    def roots(self) -> list[DriveFolder]:
        roots = [DriveFolder("Мой диск", "root", label="Мой диск")]
        for drive in self.query(["backend", "drives", GOOGLE_DRIVE_ROOT]):
            name, identifier = str(drive.get("name", "")), str(drive.get("id", ""))
            if name and ID_PATTERN.fullmatch(identifier):
                roots.append(DriveFolder(name, identifier, identifier, "Общие диски / " + name))
        return roots

    def folders(self, parent: DriveFolder) -> list[DriveFolder]:
        result = []
        for item in self.query(["lsjson", parent.remote, "--dirs-only"]):
            identifier = str(item.get("ID", ""))
            if item.get("IsDir") and ID_PATTERN.fullmatch(identifier):
                name = str(item.get("Name", item.get("Path", "")))
                result.append(DriveFolder(name, identifier, parent.drive_id, parent.label + " / " + name))
        return sorted(result, key=lambda folder: folder.name.casefold())

    def resolve_virtual(self, value: str, roots: list[DriveFolder]) -> list[DriveFolder]:
        parts = virtual_drive_parts(value)
        managed = MANAGED_PATH.match(value)
        if managed:
            options = dict(part.split("=", 1) for part in managed.group(0).rstrip(":").split(",")[1:])
            drive_id = options.get("team_drive", "")
            folder_id = options.get("root_folder_id") or drive_id or "root"
            base = next((folder for folder in roots if folder.drive_id == drive_id), None)
            if base is None:
                raise ValueError("Выбранный общий диск недоступен текущему аккаунту.")
            trail = [base] if folder_id in (base.folder_id, "root") else [
                DriveFolder("Выбранная папка", folder_id, drive_id, base.label + " / папка " + folder_id)
            ]
            names = [name for name in value[managed.end():].split("/") if name]
            for name in names:
                matches = [folder for folder in self.folders(trail[-1]) if folder.name == name]
                if len(matches) != 1:
                    raise ValueError("Облачный путь неоднозначен или недоступен. Выберите папку вручную.")
                trail.append(matches[0])
            return trail
        if not parts:
            return []
        kind, drive_name, names = parts
        matches = [folder for folder in roots if (not folder.drive_id if kind == "my" else folder.drive_id and folder.name == drive_name)]
        if len(matches) != 1:
            raise ValueError("Общий диск не найден или его имя неоднозначно. Выберите диск вручную. Исходный путь сохранён.")
        trail = [matches[0]]
        for name in names:
            matches = [folder for folder in self.folders(trail[-1]) if folder.name == name]
            if len(matches) != 1:
                raise ValueError(f"Папка «{name}» не найдена или имеет дубликаты. Выберите её вручную. Исходный путь сохранён.")
            trail.append(matches[0])
        return trail


class BrowseThread(QThread):
    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.result = None
        self.error = ""

    def run(self):
        try:
            self.result = self.operation()
        except Exception as exc:
            self.error = str(exc)


class DriveFolderDialog(QDialog):
    def __init__(self, executable: str, original: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Google Drive · папка назначения")
        self.resize(660, 520)
        self.setMinimumSize(480, 360)
        self.client = DriveClient(executable)
        self.trail: list[DriveFolder] = []
        self.roots: list[DriveFolder] = []
        self.selected_folder: DriveFolder | None = None
        self.thread: BrowseThread | None = None
        self.closing = False
        layout = QVBoxLayout(self)
        self.path_label = QLabel("Мой диск и общие диски")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)
        if original:
            original_label = QLabel("Было выбрано: " + original)
            original_label.setWordWrap(True)
            layout.addWidget(original_label)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self.enter_folder)
        layout.addWidget(self.list, 1)
        self.status = QLabel("Подключение…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.back = QPushButton("Назад")
        self.back.clicked.connect(self.go_back)
        self.choose = QPushButton("Выбрать эту папку", objectName="primary")
        self.choose.clicked.connect(self.choose_current)
        self.cancel = QPushButton("Отмена")
        self.cancel.clicked.connect(self.reject)
        for button in (self.back, self.choose, self.cancel):
            actions.addWidget(button)
        layout.addLayout(actions)

        def initial():
            roots = self.client.roots()
            try:
                trail = self.client.resolve_virtual(original, roots)
                children = self.client.folders(trail[-1]) if trail else roots
                return roots, trail, children, ""
            except ValueError as exc:
                return roots, [], roots, str(exc)
        self.run_query(initial, self.initial_loaded)

    def run_query(self, operation, callback):
        if self.thread is not None:
            return
        self.list.setEnabled(False)
        self.back.setEnabled(False)
        self.choose.setEnabled(False)
        self.status.setText("Чтение папок Google Drive…")
        thread = BrowseThread(operation, self)
        self.thread = thread
        def finished():
            self.thread = None
            if self.closing:
                super(DriveFolderDialog, self).reject()
            elif thread.error:
                self.status.setText(thread.error + "\nНазначение не изменено. Можно отменить выбор.")
                self.list.setEnabled(True)
                self.back.setEnabled(bool(self.trail))
            else:
                callback(thread.result)
            thread.deleteLater()
        thread.finished.connect(finished)
        thread.start()

    def initial_loaded(self, result):
        self.roots, self.trail, folders, warning = result
        self.show_folders(folders)
        if warning:
            self.status.setText(warning)

    def show_folders(self, folders):
        self.list.clear()
        for folder in folders:
            item = QListWidgetItem(folder.name)
            item.setToolTip(folder.label + "\nID: " + folder.folder_id)
            item.setData(Qt.ItemDataRole.UserRole, folder)
            self.list.addItem(item)
        self.path_label.setText(self.trail[-1].label if self.trail else "Мой диск и общие диски")
        self.status.setText("Двойной щелчок — открыть папку. Кнопка ниже — подтвердить назначение.")
        self.list.setEnabled(True)
        self.back.setEnabled(bool(self.trail))
        self.choose.setEnabled(bool(self.trail))

    def enter_folder(self, item):
        folder = item.data(Qt.ItemDataRole.UserRole)
        def loaded(children):
            self.trail.append(folder)
            self.show_folders(children)
        self.run_query(lambda: self.client.folders(folder), loaded)

    def go_back(self):
        if len(self.trail) <= 1:
            self.trail = []
            self.show_folders(self.roots)
        else:
            target = self.trail[-2]
            def loaded(children):
                self.trail.pop()
                self.show_folders(children)
            self.run_query(lambda: self.client.folders(target), loaded)

    def choose_current(self):
        if self.thread is None and self.trail:
            self.selected_folder = self.trail[-1]
            self.accept()

    def reject(self):
        if self.thread is not None:
            self.closing = True
            self.client.cancel()
            self.status.setText("Отмена запроса…")
            self.cancel.setEnabled(False)
        else:
            super().reject()

    def closeEvent(self, event):
        if self.thread is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
