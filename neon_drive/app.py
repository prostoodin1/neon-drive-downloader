from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import psutil
from PySide6.QtCore import (
    QEasingCurve,
    QProcess,
    QProcessEnvironment,
    QPropertyAnimation,
    QSettings,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPen, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .updater import (
    REPOSITORY,
    UpdateCheckThread,
    UpdateDownloadThread,
    launch_replacement,
)


APP_NAME = "Neon Drive Downloader"
PERCENT_RE = re.compile(r"(?<!\d)(?P<pct>\d{1,3}(?:[.,]\d+)?)%")


def app_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "NeonDriveDownloader"


def console_encoding() -> str:
    try:
        import ctypes

        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except Exception:
        return "utf-8"


def human_size(value: int) -> str:
    size = float(value)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024 or unit == "ТБ":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} Б"


def path_size(path: Path) -> int:
    """Return logical bytes without reading file contents from Google Drive."""
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


ROBOCOPY_CODES = {
    0: "Копировать было нечего: исходник и назначение уже совпадают.",
    1: "Файлы успешно скопированы.",
    2: "В назначении обнаружены дополнительные файлы; ошибок копирования нет.",
    3: "Файлы скопированы, в назначении есть дополнительные файлы.",
    4: "Обнаружены несовпадения файлов или папок; фатальных ошибок нет.",
    5: "Файлы скопированы, также обнаружены несовпадения.",
    6: "Дополнительные файлы и несовпадения; новых файлов не скопировано.",
    7: "Файлы скопированы, есть дополнительные файлы и несовпадения.",
    8: "Как минимум один файл скопировать не удалось.",
    16: "Критическая ошибка Robocopy: копирование не началось.",
}


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / name


def format_seconds(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "—"
    value = int(seconds)
    hours, value = divmod(value, 3600)
    minutes, secs = divmod(value, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


class Ring(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.value = 0
        self.setFixedSize(86, 86)

    def setValue(self, value: int) -> None:
        self.value = max(0, min(100, value))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(8, 8, -8, -8)
        painter.setPen(QPen(QColor("#17242b"), 7, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, 0, 360 * 16)
        painter.setPen(QPen(QColor("#00f0ff"), 7, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, 90 * 16, -int(360 * 16 * self.value / 100))
        painter.setPen(QColor("#e8fdff"))
        painter.setFont(QFont("Segoe UI", 13, QFont.DemiBold))
        painter.drawText(rect, Qt.AlignCenter, f"{self.value}%")


class Downloader(QProcess):
    log = Signal(str)
    progress = Signal(str, float, float)
    item_done = Signal(bool, str)
    command_started = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProcessChannelMode(QProcess.MergedChannels)
        self.readyReadStandardOutput.connect(self._read)
        self.finished.connect(self._finished)
        self.errorOccurred.connect(self._process_error)
        self.buffer = ""
        self.current = ""
        self.destination = Path()
        self.expected_target: Path | None = None
        self._done_emitted = False
        self.encoding = console_encoding()
        self._last_logged_percent = -1
        self._user_stopped = False
        self._item_completed_bytes = 0
        self._active_file_bytes = 0

    def start_item(self, source: str, destination: Path) -> None:
        self.current = source
        self.destination = destination
        self._done_emitted = False
        self._last_logged_percent = -1
        self._user_stopped = False
        self._item_completed_bytes = 0
        self._active_file_bytes = 0
        path = Path(source)
        common = [
            "/Z", "/J", "/R:20", "/W:10", "/COPY:DAT", "/DCOPY:DAT",
            "/XJ", "/V", "/FP", "/TS", "/BYTES", "/ETA",
        ]
        if path.is_dir():
            target = destination / (path.name or path.drive.rstrip(":\\"))
            self.expected_target = target
            args = [str(path), str(target), "/E", *common]
        else:
            self.expected_target = destination / path.name
            args = [str(path.parent), str(destination), path.name, *common]
        command = subprocess.list2cmdline(["robocopy.exe", *args])
        self.log.emit(f"\n▶ ИСХОДНИК: {source}\n▶ НАЗНАЧЕНИЕ: {self.expected_target}\n▶ КОМАНДА: {command}\n")
        self.command_started.emit(command)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        self.setProcessEnvironment(env)
        self.start("robocopy.exe", args)

    def _read(self) -> None:
        text = bytes(self.readAllStandardOutput()).decode(self.encoding, errors="replace")
        self.buffer += text
        # tqdm redraws a single line with carriage returns.
        for line in re.split(r"[\r\n]+", text):
            if not line.strip():
                continue
            file_match = re.search(r"\s(?P<size>\d+)\s+\d{4}/\d{2}/\d{2}\s+", line)
            if file_match:
                if self._active_file_bytes:
                    self._item_completed_bytes += self._active_file_bytes
                self._active_file_bytes = int(file_match.group("size"))
            match = PERCENT_RE.search(line)
            if match:
                pct = float(match.group("pct").replace(",", "."))
                pct = min(100.0, pct)
                item_bytes = self._item_completed_bytes + int(self._active_file_bytes * pct / 100)
                self.progress.emit(self.current, pct, float(item_bytes))
                whole = int(pct)
                if whole != self._last_logged_percent:
                    self._last_logged_percent = whole
                    self.log.emit(f"Прогресс текущего файла: {pct:.1f}%\n")
            else:
                self.log.emit(line.rstrip() + "\n")

    def _finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        if self._done_emitted:
            return
        self._done_emitted = True
        description = ROBOCOPY_CODES.get(exit_code, "Robocopy сообщил комбинированный код ошибки.")
        if self._user_stopped:
            description = "Процесс остановлен пользователем; частичный файл оставлен для продолжения."
        ok = exit_code < 8 and status == QProcess.NormalExit
        if ok and self.expected_target is not None and not self.expected_target.exists():
            ok = False
            description += " Но ожидаемый файл или каталог в назначении не найден."
        self.log.emit(f"\nКОД ROBOCOPY: {exit_code}. {description}\n")
        self.item_done.emit(ok, self.current)

    def _process_error(self, error: QProcess.ProcessError) -> None:
        self.log.emit(f"\nОШИБКА ЗАПУСКА ПРОЦЕССА: {error.name}. {self.errorString()}\n")
        if error == QProcess.FailedToStart and not self._done_emitted:
            self._done_emitted = True
            self.item_done.emit(False, self.current)

    def suspend(self) -> None:
        if self.processId():
            psutil.Process(self.processId()).suspend()

    def resume(self) -> None:
        if self.processId():
            psutil.Process(self.processId()).resume()

    def stop(self) -> None:
        if not self.processId():
            return
        self._user_stopped = True
        proc = psutil.Process(self.processId())
        for child in proc.children(recursive=True):
            child.terminate()
        proc.terminate()


class FileRow(QFrame):
    def __init__(self, source: str, destination: Path, compact: bool = False) -> None:
        super().__init__(objectName="fileRow")
        self.source = source
        self.destination = destination
        self.size = 0
        self.downloaded = 0
        self.setMinimumHeight(82 if compact else 106)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(7)
        top = QHBoxLayout()
        self.path_button = QPushButton(source)
        self.path_button.setObjectName("pathButton")
        self.path_button.setToolTip(source)
        self.path_button.clicked.connect(self.open_location)
        self.status = QLabel("ОЖИДАНИЕ")
        self.status.setObjectName("fileStatus")
        top.addWidget(self.path_button, 1)
        top.addWidget(self.status)
        layout.addLayout(top)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.info = QLabel("Размер определяется…")
        self.info.setObjectName("fileInfo")
        self.info.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.info)

    def target_path(self) -> Path:
        path = Path(self.source)
        return self.destination / (path.name or path.drive.rstrip(":\\"))

    def open_location(self) -> None:
        target = self.target_path()
        path = target if target.exists() else Path(self.source)
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            QProcess.startDetached("explorer.exe", ["/select,", str(path)])

    def update_data(
        self,
        size: int,
        downloaded: int,
        speed: float,
        elapsed: float,
        state: str,
    ) -> None:
        self.size = size
        self.downloaded = min(downloaded, size) if size else downloaded
        percent = self.downloaded / size if size else 0
        self.progress.setValue(round(percent * 1000))
        self.status.setText(state)
        remaining = max(0, size - self.downloaded)
        eta = remaining / speed if speed > 0 else None
        speed_text = f"{speed / (1024 * 1024):.1f} МБ/с" if speed > 0 else "—"
        self.info.setText(
            f"Скачано {human_size(self.downloaded)}  ·  Осталось {human_size(remaining)}  ·  "
            f"{speed_text}  ·  В работе {format_seconds(elapsed)}  ·  ETA {format_seconds(eta)}"
        )


@dataclass
class TaskInfo:
    source: str
    size: int
    downloaded: int = 0
    speed: float = 0.0
    status: str = "ОЖИДАНИЕ"
    started_at: float | None = None
    finished_at: float | None = None
    samples: deque[tuple[float, int]] = field(default_factory=deque)
    row: FileRow | None = None

    def elapsed(self, now: float | None = None) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at or now or time.monotonic()
        return max(0.0, end - self.started_at)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings("NeonTools", APP_NAME)
        self.queue: deque[str] = deque()
        self.workers: dict[str, Downloader] = {}
        self.tasks: dict[str, TaskInfo] = {}
        self.file_rows: dict[str, FileRow] = {}
        self.total_items = 0
        self.completed_items = 0
        self.failed_items = 0
        self.total_bytes = 0
        self.measured_done_bytes = 0
        self.speed_bps = 0.0
        self.speed_samples: deque[tuple[float, int]] = deque()
        self.metrics_started = False
        self.running = False
        self.stopping = False
        self.stop_after_file = False
        self.paused = False
        self.started_at = 0.0
        self.log_dir = app_data_dir() / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path: Path | None = None
        self.latest_update: dict | None = None
        self.update_check_thread: UpdateCheckThread | None = None
        self.update_download_thread: UpdateDownloadThread | None = None
        self._animations: list[QPropertyAnimation] = []
        self.metrics_timer = QTimer(self)
        self.metrics_timer.setInterval(1000)
        self.metrics_timer.timeout.connect(self.update_metrics)
        self.build_ui()
        self.restore_settings()
        QTimer.singleShot(4000, self.auto_check_updates)

    @staticmethod
    def card() -> QFrame:
        frame = QFrame(objectName="card")
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return frame

    @staticmethod
    def label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("caption")
        return label

    def build_ui(self) -> None:
        self.setWindowTitle(APP_NAME)
        self.resize(1260, 850)
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(28, 22, 28, 20)
        outer.setSpacing(14)

        title = QLabel("NEON <span style='color:#00f0ff'>DRIVE</span>")
        title.setObjectName("title")
        subtitle = QLabel("GOOGLE DRIVE COPY CONSOLE")
        subtitle.setObjectName("subtitle")
        outer.addWidget(title)
        outer.addWidget(subtitle)

        self.tabs = QTabWidget(objectName="navTabs")
        self.tabs.addTab(self.build_download_tab(), "ЗАГРУЗКА")
        self.tabs.addTab(self.build_files_tab(), "ФАЙЛЫ")
        self.tabs.addTab(self.build_settings_tab(), "НАСТРОЙКИ")
        self.tabs.addTab(self.build_interface_tab(), "ИНТЕРФЕЙС")
        self.tabs.currentChanged.connect(self.animate_tab)
        outer.addWidget(self.tabs, 1)

        outer.addWidget(self.build_overall_status())

        self.start_button = QPushButton("НАЧАТЬ СКАЧИВАНИЕ")
        self.start_button.setObjectName("primary")
        self.start_button.setMinimumHeight(56)
        self.start_button.clicked.connect(self.start_downloads)
        outer.addWidget(self.start_button)

        footer = QHBoxLayout()
        self.state_label = QLabel("●  ГОТОВО")
        self.state_label.setObjectName("state")
        self.footer_info = QLabel("Ожидание задачи")
        self.footer_info.setObjectName("footerInfo")
        footer.addWidget(self.state_label)
        footer.addStretch()
        footer.addWidget(self.footer_info)
        outer.addLayout(footer)
        self.apply_theme()

    def build_overall_status(self) -> QFrame:
        status_card = self.card()
        status = QHBoxLayout(status_card)
        status.setContentsMargins(22, 12, 22, 12)
        self.ring = Ring()
        status.addWidget(self.ring)
        progress_box = QVBoxLayout()
        self.progress_text = QLabel("ОБЩИЙ ПРОГРЕСС · 0 ИЗ 0", objectName="progressText")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        progress_box.addWidget(self.progress_text)
        progress_box.addWidget(self.progress)
        status.addLayout(progress_box, 1)
        eta_box = QVBoxLayout()
        eta_box.addWidget(self.label("ПРИМЕРНО ОСТАЛОСЬ"))
        self.eta = QLabel("—", objectName="eta")
        eta_box.addWidget(self.eta)
        status.addLayout(eta_box)
        speed_box = QVBoxLayout()
        speed_box.addWidget(self.label("СКОРОСТЬ"))
        self.speed = QLabel("—", objectName="speed")
        speed_box.addWidget(self.speed)
        status.addLayout(speed_box)
        return status_card

    def build_download_tab(self) -> QWidget:
        page = QWidget()
        content = QHBoxLayout(page)
        content.setContentsMargins(0, 12, 0, 4)
        content.setSpacing(18)
        form_card = self.card()
        form = QVBoxLayout(form_card)
        form.setContentsMargins(22, 20, 22, 22)
        form.setSpacing(12)
        form.addWidget(self.label("ВЫБРАННЫЕ ФАЙЛЫ И ПАПКИ"))
        self.sources = QPlainTextEdit()
        self.sources.setPlaceholderText("Нажмите «Выбрать файлы» или «Выбрать папку»…")
        form.addWidget(self.sources, 1)
        source_buttons = QHBoxLayout()
        self.choose_files_button = QPushButton("ВЫБРАТЬ ФАЙЛЫ")
        self.choose_files_button.clicked.connect(self.choose_files)
        self.choose_folder_button = QPushButton("ВЫБРАТЬ ПАПКУ / ДИСК")
        self.choose_folder_button.clicked.connect(self.choose_source_folder)
        self.clear_button = QPushButton("ОЧИСТИТЬ")
        self.clear_button.clicked.connect(self.sources.clear)
        source_buttons.addWidget(self.choose_files_button)
        source_buttons.addWidget(self.choose_folder_button)
        source_buttons.addWidget(self.clear_button)
        form.addLayout(source_buttons)
        form.addWidget(self.label("ПАПКА ЗАГРУЗКИ"))
        destination_row = QHBoxLayout()
        self.destination = QLineEdit()
        self.destination.setPlaceholderText("D:\\Downloads\\Google Drive")
        self.browse_button = QPushButton("ОБЗОР")
        self.browse_button.clicked.connect(self.choose_destination)
        destination_row.addWidget(self.destination, 1)
        destination_row.addWidget(self.browse_button)
        form.addLayout(destination_row)
        content.addWidget(form_card, 5)

        terminal_card = self.card()
        terminal_layout = QVBoxLayout(terminal_card)
        terminal_layout.setContentsMargins(20, 16, 20, 18)
        terminal_layout.addWidget(self.label("LIVE TERMINAL"))
        self.terminal = QPlainTextEdit(objectName="terminal")
        self.terminal.setReadOnly(True)
        self.terminal.setPlaceholderText("Ожидание запуска…")
        terminal_layout.addWidget(self.terminal, 1)
        controls = QHBoxLayout()
        self.pause_button = QPushButton("ПАУЗА")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.after_button = QPushButton("СТОП ПОСЛЕ ФАЙЛА")
        self.after_button.clicked.connect(self.toggle_stop_after)
        self.stop_button = QPushButton("ОСТАНОВИТЬ", objectName="danger")
        self.stop_button.clicked.connect(self.stop_now)
        open_logs = QPushButton("ОТКРЫТЬ ЛОГИ")
        open_logs.clicked.connect(self.open_logs)
        for button in (self.pause_button, self.after_button, self.stop_button, open_logs):
            controls.addWidget(button)
        terminal_layout.addLayout(controls)
        content.addWidget(terminal_card, 7)
        return page

    def build_files_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 4)
        header = QHBoxLayout()
        header.addWidget(self.label("ФАЙЛЫ · НАЖМИТЕ НА ПУТЬ, ЧТОБЫ ОТКРЫТЬ ЕГО В ПРОВОДНИКЕ"))
        header.addStretch()
        layout.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.file_list_widget = QWidget()
        self.file_list_layout = QVBoxLayout(self.file_list_widget)
        self.file_list_layout.setContentsMargins(0, 0, 0, 0)
        self.file_list_layout.setSpacing(10)
        self.file_list_layout.addStretch()
        scroll.setWidget(self.file_list_widget)
        layout.addWidget(scroll, 1)
        return page

    def build_settings_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 4)
        card = self.card()
        box = QVBoxLayout(card)
        box.setContentsMargins(26, 24, 26, 24)
        box.addWidget(self.label("МНОГОПОТОЧНОСТЬ"))
        self.parallel_check = QCheckBox("Скачивать все выбранные файлы одновременно")
        self.parallel_check.setObjectName("settingCheck")
        self.parallel_check.stateChanged.connect(self.save_preferences)
        box.addWidget(self.parallel_check)
        description = QLabel(
            "Снято: файлы скачиваются строго один за другим — стабильнее для Google Drive.\n"
            "Установлено: для каждого файла запускается отдельный процесс Robocopy. "
            "Суммарная скорость может вырасти, но нагрузка на сеть и диск будет выше."
        )
        description.setObjectName("settingDescription")
        description.setWordWrap(True)
        box.addWidget(description)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setObjectName("separator")
        box.addWidget(separator)
        box.addWidget(self.label("ОБНОВЛЕНИЯ ЧЕРЕЗ GITHUB RELEASES"))
        self.auto_update_check = QCheckBox("Автоматически проверять обновления при запуске")
        self.auto_update_check.setObjectName("settingCheck")
        self.auto_update_check.stateChanged.connect(self.save_preferences)
        box.addWidget(self.auto_update_check)
        update_row = QHBoxLayout()
        self.update_status = QLabel(f"Текущая версия: {__version__}")
        self.update_status.setObjectName("settingDescription")
        self.check_update_button = QPushButton("ПРОВЕРИТЬ ОБНОВЛЕНИЯ")
        self.check_update_button.clicked.connect(lambda: self.check_updates(silent=False))
        self.install_update_button = QPushButton("СКАЧАТЬ И УСТАНОВИТЬ")
        self.install_update_button.setObjectName("updateButton")
        self.install_update_button.setVisible(False)
        self.install_update_button.clicked.connect(self.install_update)
        repo_button = QPushButton("ОТКРЫТЬ GITHUB")
        repo_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(f"https://github.com/{REPOSITORY}"))
        )
        update_row.addWidget(self.update_status, 1)
        update_row.addWidget(self.check_update_button)
        update_row.addWidget(self.install_update_button)
        update_row.addWidget(repo_button)
        box.addLayout(update_row)
        box.addStretch()
        layout.addWidget(card)
        layout.addStretch()
        return page

    def build_interface_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 4)
        card = self.card()
        box = QVBoxLayout(card)
        box.setContentsMargins(26, 24, 26, 24)
        box.addWidget(self.label("ОТОБРАЖЕНИЕ"))
        self.animations_check = QCheckBox("Небольшие анимации при переходах между вкладками")
        self.animations_check.setObjectName("settingCheck")
        self.animations_check.stateChanged.connect(self.save_preferences)
        box.addWidget(self.animations_check)
        self.compact_check = QCheckBox("Компактные карточки файлов")
        self.compact_check.setObjectName("settingCheck")
        self.compact_check.stateChanged.connect(self.interface_changed)
        box.addWidget(self.compact_check)
        description = QLabel(
            "Во вкладке «Файлы» каждый путь является кнопкой. Справа отображаются "
            "скачанный и оставшийся объём, скорость, время работы и ETA."
        )
        description.setObjectName("settingDescription")
        description.setWordWrap(True)
        box.addWidget(description)
        box.addStretch()
        layout.addWidget(card)
        layout.addStretch()
        return page

    def apply_theme(self) -> None:
        self.setStyleSheet("""
            * { font-family: 'Segoe UI'; color: #d9edf0; }
            #root { background: #030607; }
            #title { font-size: 28px; font-weight: 800; letter-spacing: 2px; }
            #subtitle, #caption { color: #60777d; font-size: 10px; font-weight: 700; letter-spacing: 1px; }
            #state { color: #00f0ff; background: #07181b; border: 1px solid #0b464d; border-radius: 13px; padding: 6px 12px; }
            #footerInfo { color: #60777d; }
            #card, #fileRow { background: #080d0f; border: 1px solid #142429; border-radius: 14px; }
            #fileRow:hover { border-color: #1a555e; }
            QPlainTextEdit, QLineEdit { background: #030708; border: 1px solid #18343a; border-radius: 9px; padding: 11px; selection-background-color: #00a7b5; }
            QPlainTextEdit:focus, QLineEdit:focus { border: 1px solid #00cbd8; }
            #terminal { color: #7ef9ff; font-family: 'Cascadia Mono', Consolas; font-size: 11px; background: #020506; }
            QPushButton { background: #10191c; border: 1px solid #263a40; border-radius: 8px; padding: 10px 14px; font-weight: 700; }
            QPushButton:hover { border-color: #00dce8; color: #7cfcff; }
            QPushButton:pressed { background: #071215; }
            #pathButton { text-align: left; color: #77f8ff; background: transparent; border: 0; padding: 2px; font-weight: 600; }
            #pathButton:hover { color: white; text-decoration: underline; }
            #fileStatus { color: #86ff9d; font-weight: 800; }
            #fileInfo { color: #8aa0a5; font-family: 'Cascadia Mono', Consolas; font-size: 11px; }
            #danger:hover { border-color: #ff426d; color: #ff6c8d; }
            #primary { background: #00d7e5; color: #001012; border: 1px solid #51f6ff; border-radius: 11px; font-size: 14px; letter-spacing: 1px; }
            #primary:hover { background: #46f4ff; }
            #primary:disabled { background: #12383c; color: #5f7a7e; border-color: #1b4b50; }
            QProgressBar { background: #101a1d; border: 0; border-radius: 4px; height: 8px; }
            QProgressBar::chunk { background: #00e8f5; border-radius: 4px; }
            #progressText { font-size: 12px; font-weight: 700; }
            #eta { color: #00efff; font-size: 22px; font-weight: 700; }
            #speed { color: #86ff9d; font-size: 22px; font-weight: 700; min-width: 145px; }
            #navTabs::pane { border: 0; }
            QTabBar::tab { background: #080d0f; color: #688087; border: 1px solid #142429; padding: 10px 22px; margin-right: 5px; border-radius: 8px; font-weight: 700; }
            QTabBar::tab:selected { color: #00efff; background: #07181b; border-color: #17616a; }
            QTabBar::tab:hover { color: #b7fbff; border-color: #28525a; }
            #settingCheck { font-size: 14px; spacing: 12px; padding: 12px 0; }
            #settingDescription { color: #83979c; line-height: 1.5; padding: 8px 0; }
            #separator { color: #153038; margin: 12px 0; }
            #updateButton { color: #86ff9d; border-color: #347b48; }
            QCheckBox::indicator { width: 21px; height: 21px; border: 1px solid #28515a; border-radius: 5px; background: #030708; }
            QCheckBox::indicator:checked { background: #00d7e5; border-color: #5ff7ff; }
            QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
            QScrollBar:vertical { background: transparent; width: 8px; }
            QScrollBar::handle:vertical { background: #1a3a40; border-radius: 4px; }
        """)

    def restore_settings(self) -> None:
        self.parallel_check.setChecked(self.settings.value("parallel_downloads", False, type=bool))
        self.auto_update_check.setChecked(self.settings.value("auto_updates", True, type=bool))
        self.animations_check.setChecked(self.settings.value("animations", True, type=bool))
        self.compact_check.setChecked(self.settings.value("compact_rows", False, type=bool))
        self.destination.setText(self.settings.value("destination", str(Path.home() / "Downloads")))
        self.sources.setPlainText(self.settings.value("sources", ""))
        self.sources.textChanged.connect(self.refresh_file_rows)
        self.destination.textChanged.connect(self.refresh_file_rows)
        self.refresh_file_rows()

    @Slot(int)
    def save_preferences(self, _state: int = 0) -> None:
        self.settings.setValue("parallel_downloads", self.parallel_check.isChecked())
        self.settings.setValue("animations", self.animations_check.isChecked())
        self.settings.setValue("auto_updates", self.auto_update_check.isChecked())

    @Slot(int)
    def interface_changed(self, _state: int = 0) -> None:
        self.settings.setValue("compact_rows", self.compact_check.isChecked())
        self.refresh_file_rows()

    @Slot(int)
    def animate_tab(self, index: int) -> None:
        if not hasattr(self, "animations_check") or not self.animations_check.isChecked():
            return
        page = self.tabs.widget(index)
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(180)
        animation.setStartValue(0.35)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animations.append(animation)

        def cleanup() -> None:
            page.setGraphicsEffect(None)
            if animation in self._animations:
                self._animations.remove(animation)

        animation.finished.connect(cleanup)
        animation.start()

    def choose_destination(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку", self.destination.text())
        if folder:
            self.destination.setText(folder)

    def _append_sources(self, paths: list[str]) -> None:
        existing = [line.strip() for line in self.sources.toPlainText().splitlines() if line.strip()]
        seen = {os.path.normcase(os.path.normpath(item)) for item in existing}
        for path in paths:
            normalized = os.path.normcase(os.path.normpath(path))
            if normalized not in seen:
                existing.append(path)
                seen.add(normalized)
        self.sources.setPlainText("\n".join(existing))

    def choose_files(self) -> None:
        start = self.settings.value("last_source_dir", "")
        files, _ = QFileDialog.getOpenFileNames(self, "Выберите файлы", start, "Все файлы (*)")
        if files:
            self.settings.setValue("last_source_dir", str(Path(files[0]).parent))
            self._append_sources(files)

    def choose_source_folder(self) -> None:
        start = self.settings.value("last_source_dir", "")
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку или диск", start)
        if folder:
            self.settings.setValue("last_source_dir", folder)
            self._append_sources([folder])

    def clear_file_rows(self) -> None:
        while self.file_list_layout.count():
            item = self.file_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.file_rows.clear()

    def refresh_file_rows(self) -> None:
        if self.running:
            return
        items = [line.strip() for line in self.sources.toPlainText().splitlines() if line.strip()]
        destination = Path(self.destination.text().strip() or Path.home() / "Downloads")
        self.clear_file_rows()
        for source in items:
            row = FileRow(source, destination, self.compact_check.isChecked())
            try:
                size = path_size(Path(source)) if Path(source).exists() else 0
            except OSError:
                size = 0
            row.update_data(size, 0, 0, 0, "ОЖИДАНИЕ")
            self.file_rows[source] = row
            self.file_list_layout.addWidget(row)
        if not items:
            empty = QLabel("Выбранные файлы появятся здесь")
            empty.setObjectName("settingDescription")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.file_list_layout.addWidget(empty)
        self.file_list_layout.addStretch()

    def set_inputs_enabled(self, enabled: bool) -> None:
        for widget in (
            self.sources,
            self.destination,
            self.choose_files_button,
            self.choose_folder_button,
            self.clear_button,
            self.browse_button,
            self.parallel_check,
        ):
            widget.setEnabled(enabled)

    def start_downloads(self) -> None:
        items = [line.strip() for line in self.sources.toPlainText().splitlines() if line.strip()]
        destination = Path(self.destination.text().strip()).expanduser()
        if not items:
            QMessageBox.warning(self, APP_NAME, "Выберите хотя бы один файл или папку.")
            return
        missing = [item for item in items if not Path(item).exists()]
        if missing:
            QMessageBox.warning(self, APP_NAME, "Не найдены выбранные пути:\n" + "\n".join(missing[:5]))
            return
        try:
            destination.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(destination)
        except OSError as exc:
            QMessageBox.critical(self, APP_NAME, f"Не удалось подготовить папку назначения:\n{exc}")
            return
        robocopy = shutil.which("robocopy.exe")
        if not robocopy:
            QMessageBox.critical(self, APP_NAME, "Не найден системный robocopy.exe.")
            return

        self.settings.setValue("destination", str(destination))
        self.settings.setValue("sources", self.sources.toPlainText())
        self.save_preferences()
        self.set_state("●  АНАЛИЗ ФАЙЛОВ")
        QApplication.processEvents()
        self.queue = deque(items)
        self.tasks = {}
        self.total_bytes = 0
        for source in items:
            try:
                size = path_size(Path(source))
            except OSError:
                size = 0
            self.tasks[source] = TaskInfo(source=source, size=size)
            self.total_bytes += size
        self.total_items = len(items)
        self.completed_items = 0
        self.failed_items = 0
        self.measured_done_bytes = 0
        self.speed_bps = 0.0
        self.speed_samples.clear()
        self.metrics_started = False
        self.started_at = time.monotonic()
        self.stop_after_file = False
        self.stopping = False
        self.paused = False
        self.running = True
        self.terminal.clear()
        self.log_path = self.log_dir / f"session-{datetime.now():%Y%m%d-%H%M%S}.log"
        self.set_inputs_enabled(False)
        self.start_button.setEnabled(False)
        self.set_state("●  ЗАГРУЗКА")
        self.footer_info.setText("Параллельно" if self.parallel_check.isChecked() else "Последовательно")
        self.speed.setText("ИЗМЕРЕНИЕ…")
        self.eta.setText("ИЗМЕРЕНИЕ…")
        self.metrics_timer.start()
        self.rebuild_task_rows(destination)
        mode = "все одновременно" if self.parallel_check.isChecked() else "один за другим"
        self.append_log(
            f"{APP_NAME}\nСеанс: {datetime.now():%Y-%m-%d %H:%M:%S}\nRobocopy: {robocopy}\n"
            f"Режим: {mode}\nОчередь: {len(items)}\nОбщий объём: {human_size(self.total_bytes)}\n"
            f"Назначение: {destination}\nСвободно: {human_size(usage.free)} из {human_size(usage.total)}\n"
            f"Лог: {self.log_path}\n"
        )
        if self.parallel_check.isChecked():
            while self.queue:
                self.start_task(self.queue.popleft())
        else:
            self.start_next()

    def rebuild_task_rows(self, destination: Path) -> None:
        self.clear_file_rows()
        for source, task in self.tasks.items():
            row = FileRow(source, destination, self.compact_check.isChecked())
            row.update_data(task.size, 0, 0, 0, "ОЖИДАНИЕ")
            task.row = row
            self.file_rows[source] = row
            self.file_list_layout.addWidget(row)
        self.file_list_layout.addStretch()

    def start_next(self) -> None:
        if self.stop_after_file or not self.queue:
            if not self.workers:
                self.finish_queue(stopped=self.stop_after_file)
            return
        self.start_task(self.queue.popleft())

    def start_task(self, source: str) -> None:
        task = self.tasks[source]
        task.status = "ЗАГРУЗКА"
        task.started_at = task.started_at or time.monotonic()
        if task.row:
            task.row.update_data(task.size, task.downloaded, task.speed, task.elapsed(), task.status)
        worker = Downloader(self)
        worker.log.connect(self.append_log)
        worker.progress.connect(self.on_progress)
        worker.item_done.connect(self.on_item_done)
        self.workers[source] = worker
        worker.start_item(source, Path(self.destination.text()))

    @Slot(str, float, float)
    def on_progress(self, source: str, percent: float, item_bytes: float) -> None:
        task = self.tasks.get(source)
        if task is None:
            return
        task.downloaded = min(int(item_bytes), task.size) if task.size else int(item_bytes)
        now = time.monotonic()
        if not task.samples:
            task.samples.append((now, task.downloaded))
        self.measured_done_bytes = sum(item.downloaded for item in self.tasks.values())
        if not self.metrics_started:
            self.metrics_started = True
            self.speed_samples.append((now, self.measured_done_bytes))
        self.update_overall_progress(percent)

    def update_overall_progress(self, current_percent: float = 0.0) -> None:
        if self.total_bytes:
            overall = min(1.0, self.measured_done_bytes / self.total_bytes)
        else:
            overall = self.completed_items / max(1, self.total_items)
        self.progress.setValue(round(overall * 1000))
        self.ring.setValue(round(overall * 100))
        self.progress_text.setText(
            f"{human_size(self.measured_done_bytes)} ИЗ {human_size(self.total_bytes)} · "
            f"ГОТОВО {self.completed_items} ИЗ {self.total_items}"
        )

    @Slot()
    def update_metrics(self) -> None:
        now = time.monotonic()
        for task in self.tasks.values():
            if task.started_at is None:
                continue
            task.samples.append((now, task.downloaded))
            while len(task.samples) > 2 and now - task.samples[0][0] > 20:
                task.samples.popleft()
            if len(task.samples) >= 2:
                elapsed = task.samples[-1][0] - task.samples[0][0]
                delta = task.samples[-1][1] - task.samples[0][1]
                task.speed = max(0.0, delta / elapsed) if elapsed >= 1 else 0.0
            if task.row:
                task.row.update_data(task.size, task.downloaded, task.speed, task.elapsed(now), task.status)
        if not self.metrics_started:
            return
        self.measured_done_bytes = sum(item.downloaded for item in self.tasks.values())
        self.speed_samples.append((now, self.measured_done_bytes))
        while len(self.speed_samples) > 2 and now - self.speed_samples[0][0] > 30:
            self.speed_samples.popleft()
        first_time, first_bytes = self.speed_samples[0]
        elapsed = now - first_time
        delta = self.measured_done_bytes - first_bytes
        self.speed_bps = max(0.0, delta / elapsed) if elapsed >= 1 else 0.0
        if self.speed_bps > 0:
            self.speed.setText(f"{self.speed_bps / (1024 * 1024):.1f} МБ/с")
            remaining = max(0, self.total_bytes - self.measured_done_bytes)
            self.eta.setText(format_seconds(remaining / self.speed_bps))
        elif elapsed >= 3:
            self.speed.setText("0.0 МБ/с")
            self.eta.setText("ОЖИДАНИЕ…")
        self.footer_info.setText(
            f"Активно: {len(self.workers)} · В очереди: {len(self.queue)} · Ошибок: {self.failed_items}"
        )

    @Slot(bool, str)
    def on_item_done(self, ok: bool, source: str) -> None:
        task = self.tasks.get(source)
        worker = self.workers.pop(source, None)
        if worker:
            QTimer.singleShot(0, worker.deleteLater)
        if task:
            task.finished_at = time.monotonic()
            if ok:
                task.downloaded = task.size
                task.status = "ГОТОВО"
                self.completed_items += 1
                self.append_log(f"✓ Завершено: {source}\n")
            else:
                task.status = "ОШИБКА" if not self.stopping else "ОСТАНОВЛЕНО"
                if not self.stopping:
                    self.failed_items += 1
                self.append_log(f"✕ Не завершено: {source}\n")
            if task.row:
                task.row.update_data(task.size, task.downloaded, task.speed, task.elapsed(), task.status)
        self.measured_done_bytes = sum(item.downloaded for item in self.tasks.values())
        self.update_overall_progress()
        if self.stopping:
            if not self.workers:
                self.finish_queue(stopped=True)
            return
        if self.parallel_check.isChecked():
            if not self.workers:
                self.finish_queue(stopped=self.stop_after_file)
        elif self.stop_after_file:
            self.finish_queue(stopped=True)
        elif self.queue:
            self.start_next()
        else:
            self.finish_queue(stopped=False)

    def toggle_pause(self) -> None:
        if not self.workers:
            return
        try:
            if self.paused:
                for worker in self.workers.values():
                    worker.resume()
                self.paused = False
                self.pause_button.setText("ПАУЗА")
                self.set_state("●  ЗАГРУЗКА")
                self.append_log("▶ Все активные загрузки продолжены.\n")
            else:
                for worker in self.workers.values():
                    worker.suspend()
                self.paused = True
                self.pause_button.setText("ПРОДОЛЖИТЬ")
                self.set_state("●  ПАУЗА")
                self.append_log("Ⅱ Все активные загрузки приостановлены.\n")
        except psutil.Error as exc:
            self.append_log(f"Не удалось изменить состояние процесса: {exc}\n")

    def toggle_stop_after(self) -> None:
        if not self.workers:
            return
        self.stop_after_file = not self.stop_after_file
        self.after_button.setText("ОТМЕНИТЬ СТОП" if self.stop_after_file else "СТОП ПОСЛЕ ФАЙЛА")
        if self.parallel_check.isChecked():
            message = "Новые задачи не запустятся; уже активные параллельные загрузки будут завершены.\n"
        else:
            message = "Текущий файл будет последним.\n"
        self.append_log(message if self.stop_after_file else "Очередь снова будет продолжена.\n")

    def stop_now(self) -> None:
        if not self.workers:
            return
        self.stopping = True
        self.stop_after_file = True
        self.queue.clear()
        if self.paused:
            for worker in list(self.workers.values()):
                try:
                    worker.resume()
                except psutil.Error:
                    pass
        for worker in list(self.workers.values()):
            worker.stop()
        self.append_log("■ Получена команда немедленной остановки всех загрузок.\n")

    def finish_queue(self, stopped: bool = False) -> None:
        if not self.running:
            return
        self.running = False
        self.metrics_timer.stop()
        self.start_button.setEnabled(True)
        self.set_inputs_enabled(True)
        self.pause_button.setText("ПАУЗА")
        self.after_button.setText("СТОП ПОСЛЕ ФАЙЛА")
        if stopped:
            self.set_state("●  ОСТАНОВЛЕНО")
            self.append_log("\nОчередь остановлена. Частичные файлы оставлены для продолжения.\n")
        elif self.failed_items:
            self.set_state("●  ЗАВЕРШЕНО С ОШИБКАМИ")
            self.append_log(f"\nЗавершено с ошибками: {self.failed_items}.\n")
        else:
            self.set_state("●  ГОТОВО")
            self.progress.setValue(1000)
            self.ring.setValue(100)
            self.eta.setText("00:00")
            self.append_log("\n✓ Вся очередь успешно загружена.\n")
        self.footer_info.setText(
            f"Готово: {self.completed_items}/{self.total_items} · Ошибок: {self.failed_items}"
        )

    def set_state(self, text: str) -> None:
        self.state_label.setText(text)

    def append_log(self, text: str) -> None:
        if self.log_path is not None:
            try:
                with self.log_path.open("a", encoding="utf-8", newline="") as stream:
                    stream.write(text)
            except OSError:
                pass
        cursor = self.terminal.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.terminal.setTextCursor(cursor)
        self.terminal.ensureCursorVisible()

    def open_logs(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_dir)))

    def auto_check_updates(self) -> None:
        if self.auto_update_check.isChecked():
            self.check_updates(silent=True)

    def check_updates(self, silent: bool = False) -> None:
        if self.update_check_thread and self.update_check_thread.isRunning():
            return
        self.update_status.setText("Проверка GitHub Releases…")
        self.check_update_button.setEnabled(False)
        thread = UpdateCheckThread(self)
        self.update_check_thread = thread
        thread.succeeded.connect(lambda release: self.update_check_succeeded(release, silent))
        thread.failed.connect(lambda message: self.update_check_failed(message, silent))
        thread.finished.connect(lambda: self.check_update_button.setEnabled(True))
        thread.start()

    def update_check_succeeded(self, release: dict, silent: bool) -> None:
        self.latest_update = release
        if release.get("available"):
            self.update_status.setText(
                f"Доступна версия {release['version']} · установлена {release['current_version']}"
            )
            self.install_update_button.setVisible(True)
            if not silent:
                QMessageBox.information(
                    self,
                    APP_NAME,
                    f"Доступно обновление {release['version']}.\n"
                    "Нажмите «Скачать и установить» в настройках.",
                )
        else:
            self.install_update_button.setVisible(False)
            self.update_status.setText(f"Установлена актуальная версия {__version__}")
            if not silent:
                QMessageBox.information(self, APP_NAME, "У вас установлена актуальная версия.")

    def update_check_failed(self, message: str, silent: bool) -> None:
        self.update_status.setText("Не удалось проверить обновления")
        self.append_log(f"Проверка обновлений: {message}\n")
        if not silent:
            QMessageBox.warning(self, APP_NAME, f"Не удалось проверить обновления:\n{message}")

    def install_update(self) -> None:
        if not self.latest_update or not self.latest_update.get("available"):
            return
        if self.running:
            QMessageBox.warning(
                self,
                APP_NAME,
                "Сначала завершите или остановите текущие загрузки.",
            )
            return
        answer = QMessageBox.question(
            self,
            APP_NAME,
            f"Скачать версию {self.latest_update['version']} и перезапустить приложение?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.install_update_button.setEnabled(False)
        self.check_update_button.setEnabled(False)
        self.update_status.setText("Скачивание обновления…")
        thread = UpdateDownloadThread(self.latest_update, self)
        self.update_download_thread = thread
        thread.succeeded.connect(self.update_download_succeeded)
        thread.failed.connect(self.update_download_failed)
        thread.start()

    def update_download_succeeded(self, downloaded: str) -> None:
        try:
            launch_replacement(Path(downloaded), Path(sys.executable))
        except Exception as exc:
            self.update_download_failed(str(exc))
            return
        self.update_status.setText("Обновление скачано. Перезапуск…")
        QTimer.singleShot(300, QApplication.instance().quit)

    def update_download_failed(self, message: str) -> None:
        self.install_update_button.setEnabled(True)
        self.check_update_button.setEnabled(True)
        self.update_status.setText("Ошибка загрузки обновления")
        self.append_log(f"Загрузка обновления: {message}\n")
        QMessageBox.critical(self, APP_NAME, f"Не удалось установить обновление:\n{message}")

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.workers:
            answer = QMessageBox.question(self, APP_NAME, "Остановить загрузки и закрыть приложение?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.stop_now()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("NeonTools")
    app.setStyle("Fusion")

    def report_unhandled(exc_type, exc_value, exc_tb) -> None:
        details = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        crash_dir = app_data_dir() / "logs"
        crash_dir.mkdir(parents=True, exist_ok=True)
        crash_path = crash_dir / f"crash-{datetime.now():%Y%m%d-%H%M%S}.log"
        try:
            crash_path.write_text(details, encoding="utf-8")
        except OSError:
            pass
        QMessageBox.critical(
            None,
            APP_NAME,
            "В приложении произошла ошибка. Она сохранена в журнале:\n"
            f"{crash_path}\n\n{exc_type.__name__}: {exc_value}",
        )

    sys.excepthook = report_unhandled
    window = MainWindow()
    window.show()
    return app.exec()
