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
    QEvent,
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
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QPainter,
    QPen,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .updater import (
    REPOSITORY,
    ReleaseHistoryThread,
    UpdateCheckThread,
    UpdateDownloadThread,
    launch_replacement,
)


APP_NAME = "Neon Drive Downloader"
MAX_CONCURRENT_DOWNLOADS = 10
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


def copy_target_path(source: str | Path, destination: Path) -> Path:
    path = Path(source)
    target_name = path.name or path.drive.rstrip(":\\/") or "drive"
    return destination / target_name


def destination_collisions(sources: list[str], destination: Path) -> dict[Path, list[str]]:
    targets: dict[str, tuple[Path, list[str]]] = {}
    for source in sources:
        target = copy_target_path(source, destination)
        key = os.path.normcase(os.path.normpath(str(target)))
        if key not in targets:
            targets[key] = (target, [])
        targets[key][1].append(source)
    return {target: items for target, items in targets.values() if len(items) > 1}


def format_seconds(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "—"
    value = int(seconds)
    hours, value = divmod(value, 3600)
    minutes, secs = divmod(value, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


class AnimatedProgressBar(QProgressBar):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._animation = QPropertyAnimation(self, b"value", self)
        self._animation.setDuration(260)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animations_enabled = True

    def set_progress(self, value: int) -> None:
        value = max(self.minimum(), min(self.maximum(), value))
        if not self.animations_enabled or abs(value - self.value()) > 300:
            self._animation.stop()
            self.setValue(value)
            return
        self._animation.stop()
        self._animation.setStartValue(self.value())
        self._animation.setEndValue(value)
        self._animation.start()


class Ring(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.value = 0
        self.track_color = QColor("#17242b")
        self.accent_color = QColor("#00f0ff")
        self.text_color = QColor("#e8fdff")
        self.setFixedSize(86, 86)

    def setValue(self, value: int) -> None:
        self.value = max(0, min(100, value))
        self.update()

    def set_colors(self, track: str, accent: str, text: str) -> None:
        self.track_color = QColor(track)
        self.accent_color = QColor(accent)
        self.text_color = QColor(text)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(8, 8, -8, -8)
        painter.setPen(QPen(self.track_color, 7, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, 0, 360 * 16)
        painter.setPen(QPen(self.accent_color, 7, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, 90 * 16, -int(360 * 16 * self.value / 100))
        painter.setPen(self.text_color)
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
        self._active_file_path = ""
        self._pending_file_bytes: int | None = None

    def start_item(self, source: str, destination: Path) -> None:
        self.current = source
        self.destination = destination
        self._done_emitted = False
        self._last_logged_percent = -1
        self._user_stopped = False
        self._item_completed_bytes = 0
        self._active_file_bytes = 0
        self._active_file_path = ""
        self._pending_file_bytes = None
        self.buffer = ""
        path = Path(source)
        common = [
            "/Z", "/J", "/R:20", "/W:10", "/COPY:DAT", "/DCOPY:DAT",
            "/XJ", "/V", "/FP", "/TS", "/BYTES", "/ETA",
        ]
        if path.is_dir():
            target = copy_target_path(path, destination)
            self.expected_target = target
            args = [str(path), str(target), "/E", *common]
        else:
            self.expected_target = copy_target_path(path, destination)
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
        # Robocopy uses both newlines and carriage returns for its ETA updates.
        lines = re.split(r"\r\n|\r|\n", self.buffer)
        self.buffer = lines.pop()
        for line in lines:
            self._handle_output_line(line)

    def _handle_output_line(self, line: str) -> None:
        if not line.strip():
            return
        stripped = line.strip()
        if self._pending_file_bytes is not None and re.match(r"^(?:[A-Za-z]:\\|\\\\)", stripped):
            self._activate_file(self._pending_file_bytes, stripped)
            self._pending_file_bytes = None
        file_match = re.search(
            r"\s(?P<size>\d+)\s+\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}(?:\s+(?P<path>.+))?$",
            line,
        )
        if file_match:
            file_bytes = int(file_match.group("size"))
            file_path = (file_match.group("path") or "").strip()
            if file_path:
                self._activate_file(file_bytes, file_path)
                self._pending_file_bytes = None
            else:
                self._pending_file_bytes = file_bytes
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

    def _activate_file(self, file_bytes: int, file_path: str) -> None:
        if self._active_file_bytes and file_path != self._active_file_path:
            self._item_completed_bytes += self._active_file_bytes
        self._active_file_bytes = file_bytes
        self._active_file_path = file_path

    def _finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        if self._done_emitted:
            return
        if self.bytesAvailable():
            self._read()
        if self.buffer.strip():
            self._handle_output_line(self.buffer)
            self.buffer = ""
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
        try:
            proc = psutil.Process(self.processId())
            for child in proc.children(recursive=True):
                try:
                    child.terminate()
                except psutil.Error:
                    continue
            proc.terminate()
        except psutil.Error:
            self.kill()


class FileRow(QFrame):
    def __init__(
        self,
        source: str,
        destination: Path,
        compact: bool = False,
        display_mode: str = "list",
        animations_enabled: bool = True,
        show_source_link: bool = True,
        show_destination_link: bool = True,
    ) -> None:
        super().__init__(objectName="fileRow")
        self.source = source
        self.destination = destination
        self.display_mode = display_mode
        self.size = 0
        self.downloaded = 0
        if display_mode == "shortcut":
            self.setMinimumHeight(150 if not compact else 125)
        else:
            self.setMinimumHeight(86 if compact else 116)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(7)
        top = QHBoxLayout()
        button_text = source if display_mode == "paths" else (Path(source).name or source)
        self.path_button = QPushButton(button_text)
        self.path_button.setObjectName("tilePathButton" if display_mode == "shortcut" else "pathButton")
        self.path_button.setToolTip(source)
        self.path_button.clicked.connect(self.open_source)
        self.path_button.setEnabled(show_source_link)
        if display_mode == "shortcut":
            icon_type = (
                QStyle.StandardPixmap.SP_DirIcon
                if Path(source).is_dir()
                else QStyle.StandardPixmap.SP_FileIcon
            )
            self.path_button.setIcon(QApplication.style().standardIcon(icon_type))
            self.path_button.setMinimumHeight(54)
        self.status = QLabel("ОЖИДАНИЕ")
        self.status.setObjectName("fileStatus")
        top.addWidget(self.path_button, 1)
        top.addWidget(self.status)
        layout.addLayout(top)
        self.progress = AnimatedProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.animations_enabled = animations_enabled
        layout.addWidget(self.progress)
        self.destination_button = QPushButton(f"Куда: {self.target_path()}")
        self.destination_button.setObjectName("folderLink")
        self.destination_button.setToolTip(str(self.target_path()))
        self.destination_button.clicked.connect(self.open_destination)
        self.destination_button.setVisible(show_destination_link)
        layout.addWidget(self.destination_button)
        self.info = QLabel("Размер определяется…")
        self.info.setObjectName("fileInfo")
        self.info.setAlignment(
            Qt.AlignmentFlag.AlignLeft if display_mode == "paths" else Qt.AlignmentFlag.AlignRight
        )
        layout.addWidget(self.info)

    def target_path(self) -> Path:
        return copy_target_path(self.source, self.destination)

    @staticmethod
    def reveal(path: Path) -> None:
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            QProcess.startDetached("explorer.exe", ["/select,", str(path)])

    def open_source(self) -> None:
        self.reveal(Path(self.source))

    def open_destination(self) -> None:
        target = self.target_path()
        self.reveal(target if target.exists() else self.destination)

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
        self.progress.set_progress(round(percent * 1000))
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
    fraction: float = 0.0
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
        self.stop_after_source: str | None = None
        self.paused = False
        self.started_at = 0.0
        self.log_dir = app_data_dir() / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path: Path | None = None
        self.latest_update: dict | None = None
        self.update_check_thread: UpdateCheckThread | None = None
        self.update_download_thread: UpdateDownloadThread | None = None
        self.release_history_thread: ReleaseHistoryThread | None = None
        self.release_history: list[dict] = []
        self.force_exit = False
        self.settings_dirty = False
        self.tray_icon: QSystemTrayIcon | None = None
        self._animations: list[QPropertyAnimation] = []
        self.restart_banners: list[QFrame] = []
        self.metrics_timer = QTimer(self)
        self.metrics_timer.setInterval(1000)
        self.metrics_timer.timeout.connect(self.update_metrics)
        self.build_ui()
        self.restore_settings()
        self.cleanup_old_logs()
        self.setup_tray()
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

        title_row = QHBoxLayout()
        title = QLabel("NEON")
        title.setObjectName("title")
        brand_accent = QLabel("DRIVE")
        brand_accent.setObjectName("brandAccent")
        version_badge = QLabel(f"· V{__version__}")
        version_badge.setObjectName("versionBadge")
        title_row.addWidget(title)
        title_row.addWidget(brand_accent)
        title_row.addWidget(version_badge)
        title_row.addStretch()
        subtitle = QLabel("GOOGLE DRIVE COPY CONSOLE")
        subtitle.setObjectName("subtitle")
        outer.addLayout(title_row)
        outer.addWidget(subtitle)

        self.tabs = QTabWidget(objectName="navTabs")
        self.tabs.addTab(self.build_download_tab(), "ЗАГРУЗКА")
        self.tabs.addTab(self.build_settings_tab(), "НАСТРОЙКИ")
        self.tabs.addTab(self.build_interface_tab(), "ИНТЕРФЕЙС")
        self.tabs.addTab(self.build_updates_tab(), "ОБНОВЛЕНИЯ")
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
        self.progress = AnimatedProgressBar()
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
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 12, 0, 4)
        page_layout.setSpacing(14)
        content = QHBoxLayout()
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
        self.choose_files_button = QPushButton("ФАЙЛЫ…")
        self.choose_files_button.setToolTip("Выбрать один или несколько файлов")
        self.choose_files_button.clicked.connect(self.choose_files)
        self.choose_folder_button = QPushButton("ПАПКА / ДИСК…")
        self.choose_folder_button.setToolTip("Выбрать папку или подключённый диск")
        self.choose_folder_button.clicked.connect(self.choose_source_folder)
        self.clear_button = QPushButton("СБРОС")
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
        self.show_destination_button = QPushButton("ОТКРЫТЬ")
        self.show_destination_button.setToolTip("Открыть папку загрузки")
        self.show_destination_button.clicked.connect(self.open_destination_folder)
        destination_row.addWidget(self.destination, 1)
        destination_row.addWidget(self.browse_button)
        destination_row.addWidget(self.show_destination_button)
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
        self.after_button = QPushButton("ПОСЛЕ ФАЙЛА")
        self.after_button.setToolTip("Остановить очередь после завершения активного файла")
        self.after_button.clicked.connect(self.toggle_stop_after)
        self.stop_button = QPushButton("СТОП", objectName="danger")
        self.stop_button.clicked.connect(self.stop_now)
        for button in (self.pause_button, self.after_button, self.stop_button):
            button.setEnabled(False)
        open_logs = QPushButton("ЛОГИ")
        open_logs.clicked.connect(self.open_logs)
        for button in (self.pause_button, self.after_button, self.stop_button, open_logs):
            controls.addWidget(button)
        terminal_layout.addLayout(controls)
        content.addWidget(terminal_card, 7)
        page_layout.addLayout(content, 3)

        files_card = self.card()
        files_layout = QVBoxLayout(files_card)
        files_layout.setContentsMargins(18, 14, 18, 14)
        files_header = QHBoxLayout()
        files_header.addWidget(self.label("ФАЙЛЫ В РАБОТЕ · ВИД МЕНЯЕТСЯ В НАСТРОЙКАХ"))
        files_header.addStretch()
        self.file_mode_label = QLabel("ПОДРОБНЫЙ СПИСОК")
        self.file_mode_label.setObjectName("fileStatus")
        files_header.addWidget(self.file_mode_label)
        files_layout.addLayout(files_header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.file_list_widget = QWidget()
        self.file_list_layout = QGridLayout(self.file_list_widget)
        self.file_list_layout.setContentsMargins(0, 0, 0, 0)
        self.file_list_layout.setSpacing(10)
        scroll.setWidget(self.file_list_widget)
        files_layout.addWidget(scroll, 1)
        page_layout.addWidget(files_card, 2)
        return page

    def create_restart_banner(self) -> QFrame:
        banner = QFrame(objectName="restartBanner")
        restart_layout = QHBoxLayout(banner)
        restart_layout.setContentsMargins(18, 10, 12, 10)
        restart_layout.addWidget(
            QLabel("↻  Настройки изменены — нужен перезапуск приложения"), 1
        )
        button = QPushButton("ПЕРЕЗАПУСТИТЬ СЕЙЧАС")
        button.setObjectName("primarySmall")
        button.clicked.connect(self.restart_app)
        restart_layout.addWidget(button)
        banner.setVisible(False)
        self.restart_banners.append(banner)
        if len(self.restart_banners) == 1:
            self.restart_banner = banner
            self.restart_button = button
        return banner

    def settings_section(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        card = self.card()
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 18, 20, 18)
        box.setSpacing(9)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        box.addWidget(heading)
        return card, box

    def add_setting_toggle(self, box: QVBoxLayout, text: str) -> QCheckBox:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(9)
        checkbox = QCheckBox()
        checkbox.setObjectName("settingToggle")
        label = QLabel(text)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row.addWidget(checkbox, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(label, 1)
        box.addWidget(container)
        checkbox.setting_container = container
        checkbox.setting_label = label
        return checkbox

    @staticmethod
    def set_toggle_available(checkbox: QCheckBox, available: bool, reason: str = "") -> None:
        container = getattr(checkbox, "setting_container", checkbox)
        container.setEnabled(available)
        container.setToolTip("" if available else reason)

    @staticmethod
    def settings_scroll(grid: QGridLayout) -> QScrollArea:
        content = QWidget()
        content.setLayout(grid)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        return scroll

    def build_settings_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 4)
        layout.setSpacing(10)
        layout.addWidget(self.create_restart_banner())
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 4, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        speed_card, speed_box = self.settings_section("ЗАГРУЗКА И УСКОРЕНИЕ")
        speed_box.addWidget(QLabel("Режим"))
        self.download_mode_combo = QComboBox()
        self.download_mode_combo.addItem("Один файл за другим · стабильнее", "sequential")
        self.download_mode_combo.addItem("Ограничить число одновременных", "limited")
        self.download_mode_combo.addItem("Все доступные · не более 10 одновременно", "all")
        speed_box.addWidget(self.download_mode_combo)

        self.concurrency_controls = QWidget()
        concurrency_box = QVBoxLayout(self.concurrency_controls)
        concurrency_box.setContentsMargins(0, 2, 0, 2)
        concurrency_box.setSpacing(6)
        self.concurrency_label = QLabel("Одновременных файлов: 3")
        concurrency_box.addWidget(self.concurrency_label)
        self.concurrency_spin = QSlider(Qt.Orientation.Horizontal)
        self.concurrency_spin.setRange(2, MAX_CONCURRENT_DOWNLOADS)
        self.concurrency_spin.setValue(3)
        self.concurrency_spin.valueChanged.connect(
            lambda value: self.concurrency_label.setText(f"Одновременных файлов: {value}")
        )
        concurrency_box.addWidget(self.concurrency_spin)
        speed_box.addWidget(self.concurrency_controls)
        self.auto_start_check = self.add_setting_toggle(
            speed_box, "Начинать загрузку сразу после добавления файлов"
        )
        speed_note = QLabel(
            "Если папка назначения не выбрана, приложение сначала откроет окно выбора. "
            "Жёсткий предел — 10 файлов. Для больших файлов рекомендуется 2–3 одновременные загрузки."
        )
        speed_note.setObjectName("settingDescription")
        speed_note.setWordWrap(True)
        speed_box.addWidget(speed_note)
        grid.addWidget(speed_card, 0, 0)

        files_card, files_box = self.settings_section("ФАЙЛЫ ВО ВКЛАДКЕ «ЗАГРУЗКА»")
        self.file_display_combo = QComboBox(page)
        self.file_display_combo.addItem("Подробный список", "list")
        self.file_display_combo.addItem("Видео-ярлыки", "shortcut")
        self.file_display_combo.addItem("Пути как в терминале", "paths")
        self.file_display_combo.hide()
        self.file_display_group = QButtonGroup(self)
        self.file_display_radios: list[QRadioButton] = []
        for index, text in enumerate(("Подробный список", "Видео-ярлыки", "Пути как в терминале")):
            radio = QRadioButton(text)
            radio.setObjectName("displayRadio")
            radio.toggled.connect(
                lambda checked, selected=index: self.file_display_combo.setCurrentIndex(selected)
                if checked else None
            )
            self.file_display_group.addButton(radio, index)
            self.file_display_radios.append(radio)
            files_box.addWidget(radio)
        self.file_display_radios[0].setChecked(True)
        self.file_display_combo.currentIndexChanged.connect(self.sync_file_display_radios)
        self.show_source_links_check = self.add_setting_toggle(
            files_box, "Показывать ссылку на исходный файл или папку"
        )
        self.show_destination_links_check = self.add_setting_toggle(
            files_box, "Показывать папку назначения под прогрессом"
        )
        self.compact_check = self.add_setting_toggle(files_box, "Компактные карточки файлов")
        grid.addWidget(files_card, 0, 1)

        behavior_card, behavior_box = self.settings_section("ФОНОВАЯ РАБОТА")
        self.tray_check = self.add_setting_toggle(
            behavior_box, "Сворачивать приложение в системный tray"
        )
        self.continue_in_tray_check = self.add_setting_toggle(
            behavior_box, "Продолжать загрузку после закрытия окна"
        )
        self.notifications_check = self.add_setting_toggle(
            behavior_box, "Windows-уведомление после завершения"
        )
        behavior_note = QLabel("Уведомление автоматически исчезнет через несколько секунд.")
        behavior_note.setObjectName("settingDescription")
        behavior_note.setWordWrap(True)
        behavior_box.addWidget(behavior_note)
        grid.addWidget(behavior_card, 1, 0)

        logs_card, logs_box = self.settings_section("ЛОГИ")
        log_actions = QHBoxLayout()
        open_logs_button = QPushButton("ОТКРЫТЬ ПАПКУ С ЛОГАМИ")
        open_logs_button.clicked.connect(self.open_logs)
        cleanup_now_button = QPushButton("ОЧИСТИТЬ СЕЙЧАС")
        cleanup_now_button.clicked.connect(lambda: self.cleanup_old_logs(force=True))
        log_actions.addWidget(open_logs_button)
        log_actions.addWidget(cleanup_now_button)
        logs_box.addLayout(log_actions)
        self.cleanup_logs_check = self.add_setting_toggle(
            logs_box, "Автоматически удалять старые логи"
        )
        self.log_retention_controls = QWidget()
        retention_box = QVBoxLayout(self.log_retention_controls)
        retention_box.setContentsMargins(0, 0, 0, 0)
        retention_box.setSpacing(6)
        self.log_retention_label = QLabel("Хранить логи")
        retention_box.addWidget(self.log_retention_label)
        self.log_retention_combo = QComboBox()
        self.log_retention_combo.addItem("1 неделя", 7)
        self.log_retention_combo.addItem("1 месяц", 30)
        self.log_retention_combo.addItem("3 месяца", 90)
        self.log_retention_combo.addItem("Всегда", 0)
        retention_box.addWidget(self.log_retention_combo)
        logs_box.addWidget(self.log_retention_controls)
        self.smart_terminal_check = self.add_setting_toggle(
            logs_box, "Не прокручивать терминал вниз, если читаю старые строки"
        )
        grid.addWidget(logs_card, 1, 1)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addWidget(self.settings_scroll(grid), 1)
        return page

    def build_interface_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 4)
        layout.setSpacing(10)
        layout.addWidget(self.create_restart_banner())
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 4, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        theme_card, theme_box = self.settings_section("ТЕМА ПРИЛОЖЕНИЯ")
        theme_box.addWidget(QLabel("Основная тема"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Чёрный OLED", "oled")
        self.theme_combo.addItem("Тёмная тема", "dark")
        self.theme_combo.addItem("Светлая тема", "light")
        theme_box.addWidget(self.theme_combo)
        theme_box.addWidget(QLabel("Цвет кнопок и акцентов"))
        accent_row = QHBoxLayout()
        self.accent_combo = QComboBox()
        self.accent_combo.addItem("Голубой неон", "#00e8f5")
        self.accent_combo.addItem("Фиолетовый", "#9b6cff")
        self.accent_combo.addItem("Зелёный", "#55e878")
        self.accent_combo.addItem("Розовый", "#ff4f9a")
        self.accent_combo.addItem("Оранжевый", "#ff9d3d")
        self.custom_accent_button = QPushButton("СВОЙ ЦВЕТ…")
        self.custom_accent_button.clicked.connect(self.choose_accent_color)
        accent_row.addWidget(self.accent_combo, 1)
        accent_row.addWidget(self.custom_accent_button)
        theme_box.addLayout(accent_row)
        self.accent_all_buttons_check = self.add_setting_toggle(
            theme_box, "Красить выбранным цветом все основные кнопки"
        )
        grid.addWidget(theme_card, 0, 0)

        motion_card, motion_box = self.settings_section("ПЛАВНОСТЬ И АНИМАЦИИ")
        self.animations_check = self.add_setting_toggle(
            motion_box, "Плавные вкладки, карточки, статусы и полосы прогресса"
        )
        motion_note = QLabel(
            "При включении файлы появляются последовательно, смена статуса мягко подсвечивается, "
            "а баннер перезапуска плавно раскрывается."
        )
        motion_note.setObjectName("settingDescription")
        motion_note.setWordWrap(True)
        motion_box.addWidget(motion_note)
        grid.addWidget(motion_card, 0, 1)

        preview_card, preview_box = self.settings_section("ПРЕДПРОСМОТР")
        preview_buttons = QHBoxLayout()
        preview_buttons.addWidget(QPushButton("ОБЫЧНАЯ КНОПКА"))
        preview_primary = QPushButton("ГЛАВНАЯ КНОПКА")
        preview_primary.setObjectName("primarySmall")
        preview_buttons.addWidget(preview_primary)
        preview_danger = QPushButton("ОСТАНОВИТЬ")
        preview_danger.setObjectName("danger")
        preview_buttons.addWidget(preview_danger)
        preview_box.addLayout(preview_buttons)
        preview_progress = AnimatedProgressBar()
        preview_progress.setRange(0, 1000)
        preview_progress.setValue(680)
        preview_progress.setTextVisible(False)
        preview_box.addWidget(preview_progress)
        grid.addWidget(preview_card, 1, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addWidget(self.settings_scroll(grid), 1)
        return page

    def build_updates_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 4)
        layout.setSpacing(10)
        layout.addWidget(self.create_restart_banner())

        update_card, update_box = self.settings_section("ОБНОВЛЕНИЯ ЧЕРЕЗ GITHUB RELEASES")
        update_box.addWidget(QLabel("Способ обновления"))
        self.update_mode_combo = QComboBox()
        self.update_mode_combo.addItem("Автоматически проверять при запуске", "automatic")
        self.update_mode_combo.addItem("Проверять и устанавливать вручную", "manual")
        update_box.addWidget(self.update_mode_combo)
        self.update_status = QLabel(f"Текущая версия: {__version__}")
        self.update_status.setObjectName("settingDescription")
        self.update_status.setWordWrap(True)
        update_box.addWidget(self.update_status)
        update_row = QHBoxLayout()
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
        update_row.addWidget(self.check_update_button)
        update_row.addWidget(self.install_update_button)
        update_row.addWidget(repo_button)
        update_row.addStretch()
        update_box.addLayout(update_row)
        layout.addWidget(update_card)

        history_card, history_box = self.settings_section("ПРЕДЫДУЩИЕ ВЕРСИИ")
        self.manual_update_card = history_card
        history_note = QLabel(
            "В ручном режиме можно скачать текущую или вернуться к любой опубликованной версии."
        )
        history_note.setObjectName("settingDescription")
        history_box.addWidget(history_note)
        self.manual_update_widget = QWidget()
        manual_box = QHBoxLayout(self.manual_update_widget)
        manual_box.setContentsMargins(0, 4, 0, 0)
        self.release_combo = QComboBox()
        self.release_combo.setMinimumWidth(240)
        self.load_releases_button = QPushButton("ЗАГРУЗИТЬ СПИСОК")
        self.load_releases_button.clicked.connect(self.load_release_history)
        self.install_selected_button = QPushButton("УСТАНОВИТЬ ВЫБРАННУЮ")
        self.install_selected_button.clicked.connect(self.install_selected_release)
        self.install_selected_button.setEnabled(False)
        manual_box.addWidget(self.release_combo, 1)
        manual_box.addWidget(self.load_releases_button)
        manual_box.addWidget(self.install_selected_button)
        history_box.addWidget(self.manual_update_widget)
        layout.addWidget(history_card)
        layout.addStretch()
        return page

    def sync_file_display_radios(self, index: int) -> None:
        if 0 <= index < len(self.file_display_radios):
            self.file_display_radios[index].setChecked(True)

    def apply_theme(self) -> None:
        theme = self.theme_combo.currentData() if hasattr(self, "theme_combo") else "oled"
        themes = {
            "oled": {
                "background": "#000000", "card": "#080b0d", "input": "#020405",
                "text": "#dcebed", "muted": "#71868b", "border": "#17282d",
                "button": "#10171a", "track": "#132024", "terminal": "#020405",
            },
            "dark": {
                "background": "#14181d", "card": "#1c2228", "input": "#10151a",
                "text": "#e7edf0", "muted": "#93a1a8", "border": "#34414a",
                "button": "#263039", "track": "#303b43", "terminal": "#0c1115",
            },
            "light": {
                "background": "#eef2f5", "card": "#ffffff", "input": "#f8fafb",
                "text": "#172127", "muted": "#66757d", "border": "#cdd8de",
                "button": "#e6edf1", "track": "#d9e3e8", "terminal": "#101820",
            },
        }
        colors = themes.get(str(theme), themes["oled"])
        selected_accent = self.accent_combo.currentData() if hasattr(self, "accent_combo") else "#00e8f5"
        accent = getattr(self, "accent_color", None) or str(selected_accent or "#00e8f5")
        accent_color = QColor(accent)
        if not accent_color.isValid():
            accent_color = QColor("#00e8f5")
        accent = accent_color.name()
        accent_hover = accent_color.lighter(122).name()
        accent_text = "#081012" if accent_color.lightness() > 145 else "#ffffff"
        green = "#42d56b" if theme != "light" else "#16843a"
        terminal_text = accent_color.lighter(135).name()
        all_buttons = bool(
            hasattr(self, "accent_all_buttons_check") and self.accent_all_buttons_check.isChecked()
        )
        general_button = (
            f"background: {accent}; color: {accent_text}; border-color: {accent_hover};"
            if all_buttons else
            f"background: {colors['button']}; color: {colors['text']}; border-color: {colors['border']};"
        )
        self.setStyleSheet(f"""
            * {{ font-family: 'Segoe UI'; color: {colors['text']}; }}
            #root {{ background: {colors['background']}; }}
            #title, #brandAccent {{ font-size: 28px; font-weight: 800; letter-spacing: 2px; }}
            #brandAccent {{ color: {accent}; }}
            #versionBadge {{ color: {colors['muted']}; font-size: 15px; font-weight: 700; padding-top: 7px; }}
            #subtitle, #caption {{ color: {colors['muted']}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
            #state {{ color: {accent}; background: {colors['card']}; border: 1px solid {accent}; border-radius: 13px; padding: 6px 12px; }}
            #footerInfo {{ color: {colors['muted']}; }}
            #card, #fileRow {{ background: {colors['card']}; border: 1px solid {colors['border']}; border-radius: 14px; }}
            #fileRow:hover {{ border-color: {accent}; }}
            #restartBanner {{ background: {colors['card']}; border: 1px solid {accent}; border-radius: 11px; }}
            QPlainTextEdit, QLineEdit, QComboBox {{ background: {colors['input']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 8px; padding: 8px 10px; selection-background-color: {accent}; }}
            QComboBox QAbstractItemView {{ background: {colors['card']}; color: {colors['text']}; border: 1px solid {colors['border']}; selection-background-color: {accent}; selection-color: {accent_text}; }}
            QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus {{ border-color: {accent}; }}
            #terminal {{ color: {terminal_text}; font-family: 'Cascadia Mono', Consolas; font-size: 11px; background: {colors['terminal']}; }}
            QPushButton {{ {general_button} border-width: 1px; border-style: solid; border-radius: 8px; padding: 9px 13px; font-weight: 700; }}
            QPushButton:hover {{ border-color: {accent_hover}; color: {accent if not all_buttons else accent_text}; }}
            QPushButton:pressed {{ background: {accent_color.darker(135).name()}; color: {accent_text}; }}
            QPushButton:disabled {{ color: {colors['muted']}; border-color: {colors['border']}; background: {colors['track']}; }}
            QLabel:disabled, QCheckBox:disabled, QRadioButton:disabled {{ color: {colors['muted']}; }}
            QPlainTextEdit:disabled, QLineEdit:disabled, QComboBox:disabled {{ color: {colors['muted']}; background: {colors['track']}; border-color: {colors['border']}; }}
            #pathButton, #folderLink {{ text-align: left; color: {accent}; background: transparent; border: 0; padding: 2px; font-weight: 600; }}
            #pathButton:hover, #folderLink:hover {{ color: {accent_hover}; text-decoration: underline; }}
            #tilePathButton {{ text-align: left; color: {accent}; background: {colors['input']}; border-color: {colors['border']}; }}
            #fileStatus {{ color: {green}; font-weight: 800; }}
            #fileInfo {{ color: {colors['muted']}; font-family: 'Cascadia Mono', Consolas; font-size: 11px; }}
            #danger:hover {{ border-color: #ff426d; color: #ff426d; }}
            #primary, #primarySmall {{ background: {accent}; color: {accent_text}; border: 1px solid {accent_hover}; border-radius: 10px; letter-spacing: 1px; }}
            #primary {{ font-size: 14px; }}
            #primary:hover, #primarySmall:hover {{ background: {accent_hover}; color: {accent_text}; }}
            #primary:disabled {{ background: {colors['track']}; color: {colors['muted']}; border-color: {colors['border']}; }}
            QProgressBar {{ background: {colors['track']}; border: 0; border-radius: 4px; height: 8px; }}
            QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}
            #progressText {{ font-size: 12px; font-weight: 700; }}
            #eta {{ color: {accent}; font-size: 22px; font-weight: 700; }}
            #speed {{ color: {green}; font-size: 22px; font-weight: 700; min-width: 145px; }}
            #navTabs::pane {{ border: 0; }}
            QTabBar::tab {{ background: {colors['card']}; color: {colors['muted']}; border: 1px solid {colors['border']}; padding: 10px 22px; margin-right: 5px; border-radius: 8px; font-weight: 700; }}
            QTabBar::tab:selected {{ color: {accent}; background: {colors['input']}; border-color: {accent}; }}
            QTabBar::tab:hover {{ color: {accent_hover}; border-color: {accent}; }}
            #settingCheck, #settingToggle {{ font-size: 13px; spacing: 10px; padding: 5px 0; }}
            #sectionTitle {{ font-size: 15px; font-weight: 750; padding-bottom: 7px; }}
            #settingDescription {{ color: {colors['muted']}; padding: 4px 0; }}
            #separator {{ color: {colors['border']}; margin: 10px 0; }}
            #updateButton {{ color: {green}; border-color: {green}; }}
            QCheckBox::indicator {{ width: 19px; height: 19px; border: 1px solid {colors['border']}; border-radius: 5px; background: {colors['input']}; }}
            QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent_hover}; }}
            QCheckBox::indicator:disabled {{ background: {colors['track']}; border-color: {colors['border']}; }}
            QRadioButton {{ spacing: 10px; padding: 5px 0; }}
            QRadioButton::indicator {{ width: 18px; height: 18px; border: 1px solid {colors['border']}; border-radius: 10px; background: {colors['input']}; }}
            QRadioButton::indicator:checked {{ background: {accent}; border: 5px solid {colors['card']}; }}
            QRadioButton::indicator:disabled {{ background: {colors['track']}; border-color: {colors['border']}; }}
            QSlider::groove:horizontal {{ background: {colors['track']}; height: 5px; border-radius: 2px; }}
            QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
            QSlider::handle:horizontal {{ background: {accent_hover}; border: 2px solid {colors['card']}; width: 17px; margin: -7px 0; border-radius: 10px; }}
            QSlider::sub-page:horizontal:disabled, QSlider::handle:horizontal:disabled {{ background: {colors['muted']}; }}
            QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
            QScrollBar:vertical {{ background: transparent; width: 8px; }}
            QScrollBar::handle:vertical {{ background: {colors['border']}; border-radius: 4px; min-height: 30px; }}
        """)
        self.ring.set_colors(colors["track"], accent, colors["text"])
        animations = not hasattr(self, "animations_check") or self.animations_check.isChecked()
        self.progress.animations_enabled = animations
        for row in self.file_rows.values():
            row.progress.animations_enabled = animations

    def restore_settings(self) -> None:
        def select(combo: QComboBox, value) -> None:
            index = combo.findData(value)
            combo.setCurrentIndex(index if index >= 0 else 0)

        old_parallel = self.settings.value("parallel_downloads", False, type=bool)
        select(self.download_mode_combo, self.settings.value(
            "download_mode", "all" if old_parallel else "sequential"
        ))
        self.concurrency_spin.setValue(self.settings.value("concurrency", 3, type=int))
        select(self.file_display_combo, self.settings.value("file_display", "list"))
        self.compact_check.setChecked(self.settings.value("compact_rows", False, type=bool))
        self.show_source_links_check.setChecked(
            self.settings.value("show_source_links", True, type=bool)
        )
        self.show_destination_links_check.setChecked(
            self.settings.value("show_destination_links", True, type=bool)
        )
        select(self.theme_combo, self.settings.value("theme", "oled"))
        stored_accent = str(self.settings.value("accent_color", "#00e8f5"))
        accent_index = self.accent_combo.findData(stored_accent)
        if accent_index < 0:
            self.accent_combo.addItem(f"Свой · {stored_accent.upper()}", stored_accent)
            accent_index = self.accent_combo.count() - 1
        self.accent_combo.setCurrentIndex(accent_index)
        self.accent_color = stored_accent
        self.accent_all_buttons_check.setChecked(
            self.settings.value("accent_all_buttons", False, type=bool)
        )
        self.animations_check.setChecked(self.settings.value("animations", True, type=bool))
        select(self.update_mode_combo, self.settings.value(
            "update_mode",
            "automatic" if self.settings.value("auto_updates", True, type=bool) else "manual",
        ))
        self.tray_check.setChecked(self.settings.value("tray_enabled", True, type=bool))
        self.continue_in_tray_check.setChecked(
            self.settings.value("continue_in_tray", True, type=bool)
        )
        self.notifications_check.setChecked(
            self.settings.value("notifications", True, type=bool)
        )
        self.auto_start_check.setChecked(self.settings.value("auto_start", False, type=bool))
        self.smart_terminal_check.setChecked(
            self.settings.value("smart_terminal", True, type=bool)
        )
        self.cleanup_logs_check.setChecked(
            self.settings.value("cleanup_logs", True, type=bool)
        )
        select(self.log_retention_combo, self.settings.value("log_retention_days", 30, type=int))
        self.destination.setText(self.settings.value("destination", str(Path.home() / "Downloads")))
        self.sources.setPlainText(self.settings.value("sources", ""))

        for signal in (
            self.download_mode_combo.currentIndexChanged,
            self.concurrency_spin.valueChanged,
            self.file_display_combo.currentIndexChanged,
            self.compact_check.stateChanged,
            self.show_source_links_check.stateChanged,
            self.show_destination_links_check.stateChanged,
            self.theme_combo.currentIndexChanged,
            self.accent_combo.currentIndexChanged,
            self.accent_all_buttons_check.stateChanged,
            self.animations_check.stateChanged,
            self.update_mode_combo.currentIndexChanged,
            self.tray_check.stateChanged,
            self.continue_in_tray_check.stateChanged,
            self.notifications_check.stateChanged,
            self.auto_start_check.stateChanged,
            self.smart_terminal_check.stateChanged,
            self.cleanup_logs_check.stateChanged,
            self.log_retention_combo.currentIndexChanged,
        ):
            signal.connect(self.settings_changed)
        self.sources.textChanged.connect(self.refresh_file_rows)
        self.destination.textChanged.connect(self.refresh_file_rows)
        self.update_settings_visibility()
        self.apply_theme()
        self.refresh_file_rows()

    def persist_settings(self) -> None:
        self.settings.setValue("download_mode", self.download_mode_combo.currentData())
        self.settings.setValue("concurrency", self.concurrency_spin.value())
        self.settings.setValue("file_display", self.file_display_combo.currentData())
        self.settings.setValue("compact_rows", self.compact_check.isChecked())
        self.settings.setValue("show_source_links", self.show_source_links_check.isChecked())
        self.settings.setValue(
            "show_destination_links", self.show_destination_links_check.isChecked()
        )
        self.settings.setValue("theme", self.theme_combo.currentData())
        self.settings.setValue("accent_color", self.accent_color)
        self.settings.setValue("accent_all_buttons", self.accent_all_buttons_check.isChecked())
        self.settings.setValue("animations", self.animations_check.isChecked())
        self.settings.setValue("update_mode", self.update_mode_combo.currentData())
        self.settings.setValue("tray_enabled", self.tray_check.isChecked())
        self.settings.setValue("continue_in_tray", self.continue_in_tray_check.isChecked())
        self.settings.setValue("notifications", self.notifications_check.isChecked())
        self.settings.setValue("auto_start", self.auto_start_check.isChecked())
        self.settings.setValue("smart_terminal", self.smart_terminal_check.isChecked())
        self.settings.setValue("cleanup_logs", self.cleanup_logs_check.isChecked())
        self.settings.setValue("log_retention_days", self.log_retention_combo.currentData())
        self.settings.setValue("destination", self.destination.text())
        self.settings.setValue("sources", self.sources.toPlainText())
        self.settings.sync()

    def settings_changed(self, *_args) -> None:
        if self.sender() is self.accent_combo:
            self.accent_color = str(self.accent_combo.currentData())
        self.persist_settings()
        self.settings_dirty = True
        for banner in self.restart_banners:
            if not banner.isVisible():
                banner.setVisible(True)
                self.animate_appearance(banner, duration=220)
        self.update_settings_visibility()
        self.apply_theme()
        self.refresh_file_rows()
        if self.sender() in (self.tray_check, self.notifications_check):
            self.setup_tray()

    def update_settings_visibility(self) -> None:
        limited = self.download_mode_combo.currentData() == "limited" and not self.running
        self.concurrency_controls.setEnabled(limited)
        self.concurrency_controls.setToolTip(
            "" if limited else "Число файлов задаётся только в ограниченном режиме."
        )
        manual = self.update_mode_combo.currentData() == "manual"
        self.manual_update_card.setEnabled(manual)
        self.manual_update_card.setToolTip(
            "" if manual else "Список версий доступен в ручном режиме обновлений."
        )
        keep_logs = self.cleanup_logs_check.isChecked()
        self.log_retention_controls.setEnabled(keep_logs)
        self.log_retention_controls.setToolTip(
            "" if keep_logs else "Сначала включите автоматическое удаление старых логов."
        )
        self.set_toggle_available(
            self.continue_in_tray_check,
            self.tray_check.isChecked(),
            "Сначала включите сворачивание приложения в системный tray.",
        )

    def choose_accent_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.accent_color), self, "Цвет кнопок и акцентов")
        if not color.isValid():
            return
        value = color.name()
        custom_index = self.accent_combo.findData(value)
        if custom_index < 0:
            if self.accent_combo.count() > 5:
                self.accent_combo.removeItem(self.accent_combo.count() - 1)
            self.accent_combo.addItem(f"Свой · {value.upper()}", value)
            custom_index = self.accent_combo.count() - 1
        self.accent_color = value
        self.accent_combo.setCurrentIndex(custom_index)
        self.settings_changed()

    def restart_app(self) -> None:
        if self.running:
            QMessageBox.warning(self, APP_NAME, "Сначала завершите или остановите загрузки.")
            return
        if getattr(sys, "frozen", False):
            program = sys.executable
            arguments = sys.argv[1:]
        else:
            program = sys.executable
            arguments = [str(resource_path("main.py")), *sys.argv[1:]]
        started = QProcess.startDetached(program, arguments, str(Path.cwd()))
        ok = started[0] if isinstance(started, tuple) else started
        if not ok:
            QMessageBox.critical(self, APP_NAME, "Не удалось перезапустить приложение.")
            return
        self.force_exit = True
        QApplication.instance().quit()

    def max_concurrent_downloads(self) -> int:
        mode = self.download_mode_combo.currentData()
        if mode == "sequential":
            return 1
        if mode == "limited":
            return min(MAX_CONCURRENT_DOWNLOADS, max(1, self.concurrency_spin.value()))
        return min(MAX_CONCURRENT_DOWNLOADS, max(1, self.total_items))

    @Slot(int)
    def animate_tab(self, index: int) -> None:
        if not hasattr(self, "animations_check") or not self.animations_check.isChecked():
            return
        page = self.tabs.widget(index)
        self.animate_appearance(page, duration=210, start_opacity=0.25)

    def animate_appearance(
        self,
        widget: QWidget,
        duration: int = 180,
        start_opacity: float = 0.35,
        delay: int = 0,
    ) -> None:
        if not hasattr(self, "animations_check") or not self.animations_check.isChecked():
            return
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(duration)
        animation.setStartValue(start_opacity)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animations.append(animation)

        def discard() -> None:
            if animation in self._animations:
                self._animations.remove(animation)

        def cleanup() -> None:
            widget.setGraphicsEffect(None)
            discard()

        def widget_destroyed() -> None:
            animation.stop()
            discard()

        animation.finished.connect(cleanup)
        widget.destroyed.connect(widget_destroyed)
        if delay:
            QTimer.singleShot(delay, widget, animation.start)
        else:
            animation.start()

    def choose_destination(self) -> bool:
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку", self.destination.text())
        if folder:
            self.destination.setText(folder)
            self.settings.setValue("destination", folder)
            return True
        return False

    def open_destination_folder(self) -> None:
        text = self.destination.text().strip()
        if not text:
            if not self.choose_destination():
                return
            text = self.destination.text().strip()
        folder = Path(text).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, f"Не удалось открыть папку:\n{exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

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
            self.maybe_auto_start()

    def choose_source_folder(self) -> None:
        start = self.settings.value("last_source_dir", "")
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку или диск", start)
        if folder:
            self.settings.setValue("last_source_dir", folder)
            self._append_sources([folder])
            self.maybe_auto_start()

    def maybe_auto_start(self) -> None:
        if self.running or not self.auto_start_check.isChecked():
            return
        if not self.destination.text().strip() and not self.choose_destination():
            return
        QTimer.singleShot(150, self.start_downloads)

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
        mode = str(self.file_display_combo.currentData() or "list")
        mode_names = {
            "list": "ПОДРОБНЫЙ СПИСОК",
            "shortcut": "ЯРЛЫКИ",
            "paths": "ПУТИ КАК В ТЕРМИНАЛЕ",
        }
        self.file_mode_label.setText(mode_names.get(mode, mode_names["list"]))
        self.clear_file_rows()
        for index, source in enumerate(items):
            row = FileRow(
                source,
                destination,
                self.compact_check.isChecked(),
                mode,
                self.animations_check.isChecked(),
                self.show_source_links_check.isChecked(),
                self.show_destination_links_check.isChecked(),
            )
            try:
                size = path_size(Path(source)) if Path(source).exists() else 0
            except OSError:
                size = 0
            row.update_data(size, 0, 0, 0, "ОЖИДАНИЕ")
            self.file_rows[source] = row
            self.place_file_row(row, index, mode)
        if not items:
            empty = QLabel("Выбранные файлы появятся здесь")
            empty.setObjectName("settingDescription")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.file_list_layout.addWidget(empty, 0, 0, 1, 3)

    def place_file_row(self, row: FileRow, index: int, mode: str) -> None:
        if mode == "shortcut":
            self.file_list_layout.addWidget(row, index // 3, index % 3)
        else:
            self.file_list_layout.addWidget(row, index, 0, 1, 3)
        self.animate_appearance(
            row,
            duration=240,
            start_opacity=0.15,
            delay=min(index * 35, 280),
        )

    def set_inputs_enabled(self, enabled: bool) -> None:
        for widget in (
            self.sources,
            self.destination,
            self.choose_files_button,
            self.choose_folder_button,
            self.clear_button,
            self.browse_button,
            self.download_mode_combo,
        ):
            widget.setEnabled(enabled)
        self.update_settings_visibility()

    def set_download_controls_enabled(self, enabled: bool) -> None:
        for button in (self.pause_button, self.after_button, self.stop_button):
            button.setEnabled(enabled)

    def start_downloads(self) -> None:
        if self.running or self.workers:
            return
        raw_items = [line.strip() for line in self.sources.toPlainText().splitlines() if line.strip()]
        items: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            normalized = os.path.normcase(os.path.normpath(item))
            if normalized not in seen:
                items.append(item)
                seen.add(normalized)
        if not items:
            QMessageBox.warning(self, APP_NAME, "Выберите хотя бы один файл или папку.")
            return
        if not self.destination.text().strip():
            if not self.choose_destination():
                return
        destination = Path(self.destination.text().strip()).expanduser()
        missing = [item for item in items if not Path(item).exists()]
        if missing:
            QMessageBox.warning(self, APP_NAME, "Не найдены выбранные пути:\n" + "\n".join(missing[:5]))
            return
        collisions = destination_collisions(items, destination)
        if collisions:
            details = []
            for target, sources in list(collisions.items())[:4]:
                details.append(f"{target}\n  ← " + "\n  ← ".join(sources))
            QMessageBox.critical(
                self,
                APP_NAME,
                "Несколько источников будут записываться в один и тот же путь. "
                "Параллельная загрузка остановлена, чтобы не повредить файлы:\n\n"
                + "\n\n".join(details),
            )
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
        self.persist_settings()
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
        self.stop_after_source = None
        self.stopping = False
        self.paused = False
        self.running = True
        self.terminal.clear()
        self.log_path = self.log_dir / f"session-{datetime.now():%Y%m%d-%H%M%S}.log"
        self.set_inputs_enabled(False)
        self.set_download_controls_enabled(True)
        self.start_button.setEnabled(False)
        self.set_state("●  ЗАГРУЗКА")
        mode_names = {
            "sequential": "Последовательно",
            "limited": f"До {self.concurrency_spin.value()} одновременно",
            "all": f"Все доступные · до {MAX_CONCURRENT_DOWNLOADS} одновременно",
        }
        selected_mode = str(self.download_mode_combo.currentData())
        self.footer_info.setText(mode_names.get(selected_mode, "Последовательно"))
        self.speed.setText("ИЗМЕРЕНИЕ…")
        self.eta.setText("ИЗМЕРЕНИЕ…")
        self.metrics_timer.start()
        self.rebuild_task_rows(destination)
        mode = mode_names.get(selected_mode, "Последовательно").lower()
        self.append_log(
            f"{APP_NAME}\nСеанс: {datetime.now():%Y-%m-%d %H:%M:%S}\nRobocopy: {robocopy}\n"
            f"Режим: {mode}\nЛимит процессов: {self.max_concurrent_downloads()}\n"
            f"Очередь: {len(items)}\nОбщий объём: {human_size(self.total_bytes)}\n"
            f"Назначение: {destination}\nСвободно: {human_size(usage.free)} из {human_size(usage.total)}\n"
            f"Лог: {self.log_path}\n"
        )
        self.fill_worker_slots()

    def rebuild_task_rows(self, destination: Path) -> None:
        self.clear_file_rows()
        mode = str(self.file_display_combo.currentData() or "list")
        for index, (source, task) in enumerate(self.tasks.items()):
            row = FileRow(
                source,
                destination,
                self.compact_check.isChecked(),
                mode,
                self.animations_check.isChecked(),
                self.show_source_links_check.isChecked(),
                self.show_destination_links_check.isChecked(),
            )
            row.update_data(task.size, 0, 0, 0, "ОЖИДАНИЕ")
            task.row = row
            self.file_rows[source] = row
            self.place_file_row(row, index, mode)

    def start_next(self) -> None:
        self.fill_worker_slots()

    def fill_worker_slots(self) -> None:
        limit = self.max_concurrent_downloads()
        while self.queue and len(self.workers) < limit and not self.stop_after_file:
            self.start_task(self.queue.popleft())
        if not self.workers and (not self.queue or self.stop_after_file):
            self.finish_queue(stopped=self.stop_after_file)

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
        measured = min(int(item_bytes), task.size) if task.size else int(item_bytes)
        task.downloaded = max(task.downloaded, measured)
        if task.size:
            task.fraction = min(1.0, task.downloaded / task.size)
        else:
            task.fraction = max(task.fraction, min(1.0, percent / 100.0))
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
            overall = sum(task.fraction for task in self.tasks.values()) / max(1, self.total_items)
        self.progress.set_progress(round(overall * 1000))
        self.ring.setValue(round(overall * 100))
        self.progress_text.setText(
            f"ОБЩИЙ ПРОГРЕСС {overall * 100:.1f}% · "
            f"{human_size(self.measured_done_bytes)} ИЗ {human_size(self.total_bytes)} · "
            f"ГОТОВО {self.completed_items} ИЗ {self.total_items}"
        )

    @Slot()
    def update_metrics(self) -> None:
        now = time.monotonic()
        if self.paused:
            self.speed.setText("ПАУЗА")
            self.eta.setText("ПАУЗА")
            return
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
        while len(self.speed_samples) > 2 and now - self.speed_samples[0][0] > 15:
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
                task.fraction = 1.0
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
        if (
            self.stop_after_file
            and source == self.stop_after_source
            and not self.stopping
        ):
            self.stopping = True
            self.set_download_controls_enabled(False)
            self.queue.clear()
            self.append_log(
                f"■ Выбранный текущий файл завершён: {source}\n"
                "Остальные активные загрузки останавливаются; частичные файлы сохранены.\n"
            )
            for active_worker in list(self.workers.values()):
                active_worker.stop()
            if not self.workers:
                self.finish_queue(stopped=True)
            return
        if self.stopping:
            if not self.workers:
                self.finish_queue(stopped=True)
            return
        self.fill_worker_slots()

    def toggle_pause(self) -> None:
        if not self.workers:
            return
        try:
            if self.paused:
                for worker in self.workers.values():
                    worker.resume()
                self.paused = False
                self.speed_samples.clear()
                self.metrics_started = False
                now = time.monotonic()
                for task in self.tasks.values():
                    task.samples.clear()
                    if task.started_at is not None and task.finished_at is None:
                        task.samples.append((now, task.downloaded))
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
        if self.stop_after_file:
            self.stop_after_file = False
            self.stop_after_source = None
            self.after_button.setText("ПОСЛЕ ФАЙЛА")
            self.after_button.setToolTip("Остановить очередь после завершения текущего файла")
            self.append_log("▶ Остановка после файла отменена. Очередь снова продолжается.\n")
            self.fill_worker_slots()
            return
        active_sources = [source for source in self.workers if source in self.tasks]
        if not active_sources:
            return
        self.stop_after_source = min(
            active_sources,
            key=lambda source: self.tasks[source].started_at or float("inf"),
        )
        self.stop_after_file = True
        current_name = Path(self.stop_after_source).name or self.stop_after_source
        self.after_button.setText("ОТМЕНИТЬ")
        self.after_button.setToolTip(f"Остановка после: {current_name}")
        self.append_log(
            f"■ Очередь остановится сразу после текущего файла: {self.stop_after_source}\n"
            "Новые файлы не запускаются. После него остальные активные процессы будут остановлены.\n"
        )

    def stop_now(self) -> None:
        if not self.workers:
            return
        self.stopping = True
        self.stop_after_file = True
        self.stop_after_source = None
        self.queue.clear()
        self.set_download_controls_enabled(False)
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
        self.set_download_controls_enabled(False)
        self.pause_button.setText("ПАУЗА")
        self.after_button.setText("ПОСЛЕ ФАЙЛА")
        self.after_button.setToolTip("Остановить очередь после завершения текущего файла")
        self.stop_after_source = None
        if stopped:
            self.set_state("●  ОСТАНОВЛЕНО")
            self.append_log("\nОчередь остановлена. Частичные файлы оставлены для продолжения.\n")
            notification = "Загрузка остановлена. Частичные файлы сохранены."
        elif self.failed_items:
            self.set_state("●  ЗАВЕРШЕНО С ОШИБКАМИ")
            self.append_log(f"\nЗавершено с ошибками: {self.failed_items}.\n")
            notification = f"Очередь завершена. Ошибок: {self.failed_items}."
        else:
            self.set_state("●  ГОТОВО")
            self.progress.set_progress(1000)
            self.ring.setValue(100)
            self.eta.setText("00:00")
            self.append_log("\n✓ Вся очередь успешно загружена.\n")
            notification = f"Все файлы загружены: {self.completed_items}."
        self.footer_info.setText(
            f"Готово: {self.completed_items}/{self.total_items} · Ошибок: {self.failed_items}"
        )
        self.notify(APP_NAME, notification)

    def set_state(self, text: str) -> None:
        self.state_label.setText(text)
        self.animate_appearance(
            self.state_label,
            duration=200,
            start_opacity=0.35,
        )

    def append_log(self, text: str) -> None:
        if self.log_path is not None:
            try:
                with self.log_path.open("a", encoding="utf-8", newline="") as stream:
                    stream.write(text)
            except OSError:
                pass
        scroll = self.terminal.verticalScrollBar()
        old_position = scroll.value()
        was_at_bottom = old_position >= scroll.maximum() - 3
        cursor = self.terminal.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.terminal.setTextCursor(cursor)
        smart_scroll = not hasattr(self, "smart_terminal_check") or self.smart_terminal_check.isChecked()
        if was_at_bottom or not smart_scroll:
            scroll.setValue(scroll.maximum())
        else:
            scroll.setValue(old_position)

    def open_logs(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_dir)))

    def cleanup_old_logs(self, force: bool = False) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if force:
            answer = QMessageBox.question(
                self,
                APP_NAME,
                "Удалить все старые журналы? Текущий журнал загрузки будет сохранён.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            cutoff = time.time() + 1
        else:
            if not self.cleanup_logs_check.isChecked():
                return
            days = int(self.log_retention_combo.currentData() or 0)
            if days <= 0:
                return
            cutoff = time.time() - days * 86400
        removed = 0
        for path in self.log_dir.glob("*.log"):
            if self.log_path is not None and path == self.log_path:
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        if force:
            QMessageBox.information(self, APP_NAME, f"Удалено журналов: {removed}.")

    def setup_tray(self) -> None:
        required = self.tray_check.isChecked() or self.notifications_check.isChecked()
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if self.tray_icon is None:
            icon = self.windowIcon()
            if icon.isNull():
                icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon)
                self.setWindowIcon(icon)
            tray = QSystemTrayIcon(icon, self)
            tray.setToolTip(f"{APP_NAME} · {__version__}")
            menu = QMenu(self)
            show_action = QAction("Открыть Neon Drive", self)
            show_action.triggered.connect(self.show_from_tray)
            destination_action = QAction("Открыть папку загрузки", self)
            destination_action.triggered.connect(self.open_destination_folder)
            pause_action = QAction("Пауза / продолжить", self)
            pause_action.triggered.connect(self.toggle_pause)
            exit_action = QAction("Выйти", self)
            exit_action.triggered.connect(self.exit_from_tray)
            menu.addAction(show_action)
            menu.addAction(destination_action)
            menu.addAction(pause_action)
            menu.addSeparator()
            menu.addAction(exit_action)
            tray.setContextMenu(menu)
            tray.activated.connect(self.tray_activated)
            self.tray_icon = tray
        if required:
            self.tray_icon.show()
        else:
            self.tray_icon.hide()

    def tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_from_tray()

    def show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def exit_from_tray(self) -> None:
        if self.running:
            answer = QMessageBox.question(
                self, APP_NAME, "Остановить загрузки и выйти из приложения?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.stop_now()
        self.force_exit = True
        QApplication.instance().quit()

    def notify(self, title: str, message: str) -> None:
        if not self.notifications_check.isChecked():
            return
        self.setup_tray()
        if self.tray_icon is not None:
            self.tray_icon.showMessage(
                title,
                message,
                QSystemTrayIcon.MessageIcon.Information,
                4500,
            )

    def auto_check_updates(self) -> None:
        if self.update_mode_combo.currentData() == "automatic":
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
        self.install_release(self.latest_update)

    def load_release_history(self) -> None:
        if self.release_history_thread and self.release_history_thread.isRunning():
            return
        self.load_releases_button.setEnabled(False)
        self.install_selected_button.setEnabled(False)
        self.update_status.setText("Загрузка списка GitHub Releases…")
        thread = ReleaseHistoryThread(self)
        self.release_history_thread = thread
        thread.succeeded.connect(self.release_history_succeeded)
        thread.failed.connect(self.release_history_failed)
        thread.finished.connect(lambda: self.load_releases_button.setEnabled(True))
        thread.start()

    def release_history_succeeded(self, releases: list[dict]) -> None:
        self.release_history = releases
        self.release_combo.clear()
        for index, release in enumerate(releases):
            published = str(release.get("published_at", ""))[:10]
            marker = " · установлена" if release.get("version") == __version__ else ""
            self.release_combo.addItem(
                f"{release.get('tag', release.get('version'))} · {published}{marker}", index
            )
        self.install_selected_button.setEnabled(bool(releases))
        self.update_status.setText(
            f"Найдено версий: {len(releases)} · текущая версия {__version__}"
        )

    def release_history_failed(self, message: str) -> None:
        self.update_status.setText("Не удалось получить список версий")
        self.append_log(f"Список версий: {message}\n")
        QMessageBox.warning(self, APP_NAME, f"Не удалось получить список версий:\n{message}")

    def install_selected_release(self) -> None:
        index = self.release_combo.currentData()
        if index is None:
            return
        try:
            release = self.release_history[int(index)]
        except (IndexError, TypeError, ValueError):
            return
        self.install_release(release)

    def install_release(self, release: dict) -> None:
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
            f"Скачать версию {release['version']} и перезапустить приложение?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.install_update_button.setEnabled(False)
        self.install_selected_button.setEnabled(False)
        self.check_update_button.setEnabled(False)
        self.update_status.setText("Скачивание обновления…")
        thread = UpdateDownloadThread(release, self)
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
        self.install_selected_button.setEnabled(bool(self.release_history))
        self.check_update_button.setEnabled(True)
        self.update_status.setText("Ошибка загрузки обновления")
        self.append_log(f"Загрузка обновления: {message}\n")
        QMessageBox.critical(self, APP_NAME, f"Не удалось установить обновление:\n{message}")

    def closeEvent(self, event) -> None:  # noqa: N802
        self.persist_settings()
        if self.force_exit:
            event.accept()
            return
        if (
            self.tray_check.isChecked()
            and self.continue_in_tray_check.isChecked()
            and QSystemTrayIcon.isSystemTrayAvailable()
        ):
            self.hide()
            self.notify(APP_NAME, "Приложение продолжает работать в фоновом режиме.")
            event.ignore()
            return
        if self.workers:
            answer = QMessageBox.question(self, APP_NAME, "Остановить загрузки и закрыть приложение?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.stop_now()
        event.accept()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if (
            event.type() == QEvent.Type.WindowStateChange
            and self.isMinimized()
            and self.tray_check.isChecked()
            and QSystemTrayIcon.isSystemTrayAvailable()
        ):
            QTimer.singleShot(0, self.hide)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("NeonTools")
    app.setStyle("Fusion")
    icon_path = resource_path("assets/neon-drive.png")
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

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
    if "--smoke-test" in sys.argv:
        QTimer.singleShot(900, app.quit)
    else:
        window.show()
    return app.exec()
