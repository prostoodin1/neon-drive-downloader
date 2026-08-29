from __future__ import annotations

import os
import json
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import psutil
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QProcess,
    QProcessEnvironment,
    QPropertyAnimation,
    QRect,
    QSettings,
    QSize,
    QThread,
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
    QPainterPath,
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
    QDialog,
    QGraphicsDropShadowEffect,
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
    QStackedWidget,
    QStyle,
    QStyleOptionTab,
    QStylePainter,
    QSystemTrayIcon,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .settings_store import create_settings
from .transfer_buffer import TransferBuffer
from .transfer_direction import detect_direction, location_label
from .drive_browser import (
    DriveClient,
    DriveFolderDialog,
    SharedDriveAccessError,
    explorer_shared_drive_target,
    is_managed_drive_path,
    remote_from_explorer_path,
    virtual_drive_parts,
)
from .addons import (
    install_upload_addon,
    is_beta_build,
    read_upload_addon,
    remove_upload_addon,
    upload_addon_github_url,
    upload_addon_installed,
)
from .copy_engines import (
    COPY_ENGINE_NAMES,
    RcloneOptions,
    copy_engine_for_source,
    is_rclone_remote_path,
    rclone_target_path,
    rclone_arguments,
)
from .google_drive import (
    GOOGLE_DRIVE_REMOTE,
    disconnect_google_drive,
    extract_authorize_token,
    fetch_google_drive_identity,
    google_drive_accounts,
    google_drive_connected,
    google_drive_root,
    managed_rclone_config_path,
    new_google_drive_remote_name,
    oauth_completion_template_path,
    store_google_drive_token,
)
from .platform_support import (
    app_data_directory,
    is_macos,
    macos_version_supported,
    rclone_executable_name,
)
from .rclone_manager import (
    bundled_rclone_path,
    bundled_rclone_version,
    download_and_install_rclone,
    installed_rclone_path,
    installed_rclone_version,
)
from .single_instance import InstanceServer, send_request
from .system_health import SystemHealthReport, run_system_health_check
from .transfer_stats import TransferStats
from .windows_startup import set_startup_enabled, startup_enabled
from .updater import (
    REPOSITORY,
    SETUP_ASSET_NAME,
    SETUP_ASSET_NAMES,
    ReleaseHistoryThread,
    UpdateCheckThread,
    UpdateDownloadThread,
    last_downloaded_release,
    launch_replacement,
    version_tuple,
)
from .turbo_copy import TurboCopyStopped, parallel_copy_file


APP_NAME = "Neon Drive"
SETTINGS_APP_NAME = "Neon Drive Downloader"
MAX_CONCURRENT_DOWNLOADS = 10
MAX_DIRECTORY_THREADS = 16
MAX_TURBO_THREADS = 16
SOURCE_STABLE_SECONDS = 3.0
SOURCE_RECHECK_INTERVAL_MS = 1200
INCOMPLETE_SOURCE_ENDINGS = (
    ".neon-partial",
    ".partial",
    ".crdownload",
    ".download",
    ".part",
    ".tmp",
)
WINDOW_SIZE_PRESETS = {
    "small": (900, 640),
    "standard": (1180, 760),
    "large": (1380, 880),
}
COPY_PROFILE_NAMES = {
    "stable": "Надёжный · докачка после обрыва",
    "optimized": "Ускоренный · многопоточные папки с докачкой",
    "maximum": "Максимальная скорость · без докачки текущего файла",
    "turbo": "Турбо · сегменты большого файла параллельно",
}
RCLONE_PERFORMANCE_PROFILES = {
    "balanced": {
        "chunk": 64, "cutoff": 256, "streams": 4, "transfers": 4,
        "checkers": 8, "buffer": 16, "write_buffer": 1,
    },
    "fast": {
        "chunk": 128, "cutoff": 256, "streams": 8, "transfers": 8,
        "checkers": 16, "buffer": 32, "write_buffer": 2,
    },
    "maximum": {
        "chunk": 256, "cutoff": 128, "streams": 16, "transfers": 12,
        "checkers": 32, "buffer": 64, "write_buffer": 4,
    },
    "extreme": {
        "chunk": 512, "cutoff": 64, "streams": 32, "transfers": 16,
        "checkers": 64, "buffer": 128, "write_buffer": 8,
    },
}
PERCENT_RE = re.compile(r"(?<!\d)(?P<pct>\d{1,3}(?:[.,]\d+)?)%")


def app_data_dir() -> Path:
    return app_data_directory()


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


def copy_target_path(source: str | Path, destination: str | Path) -> str | Path:
    return rclone_target_path(source, destination)


def destination_collisions(
    sources: list[str], destination: str | Path
) -> dict[str | Path, list[str]]:
    targets: dict[str, tuple[str | Path, list[str]]] = {}
    for source in sources:
        target = copy_target_path(source, destination)
        key = os.path.normcase(os.path.normpath(str(target)))
        if key not in targets:
            targets[key] = (target, [])
        targets[key][1].append(source)
    return {target: items for target, items in targets.values() if len(items) > 1}


def upload_destination_requirement(destination: Path) -> str | None:
    """Explain why an Explorer upload destination cannot be used safely."""
    raw = os.path.normcase(os.path.normpath(str(destination)))
    anchor = os.path.normcase(os.path.normpath(destination.anchor))
    if destination.drive and anchor and raw == anchor:
        return (
            f"Нельзя выгружать файлы прямо в корень {destination.anchor}. "
            "Выберите внутри Google Drive папку «My Drive / Мой диск / Mi unidad», "
            "«Shared drives/Общие диски» или любую вложенную папку."
        )
    return None


def destination_write_problem(destination: Path) -> str | None:
    """Probe the create-and-rename operations used by atomic file copies."""
    token = uuid4().hex
    temporary = destination / f".neon-write-test-{token}.tmp"
    renamed = destination / f".neon-write-test-{token}.ok"
    try:
        with temporary.open("xb") as stream:
            stream.write(b"Neon Drive destination test")
        temporary.replace(renamed)
    except OSError as exc:
        return (
            f"Папка {destination} не разрешает создать и переименовать файл: {exc}. "
            "Проверьте доступ, подключение Google Drive и выберите обычную вложенную папку."
        )
    finally:
        for candidate in (temporary, renamed):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass
    return None


def source_has_incomplete_name(path: Path) -> bool:
    name = path.name.casefold()
    return name.startswith("~$") or name.endswith(INCOMPLETE_SOURCE_ENDINGS)


def source_is_being_written(path: Path) -> bool:
    """Check Windows sharing locks without opening or hydrating file contents."""
    if os.name != "nt" or not path.is_file():
        return False
    try:
        import ctypes
        from ctypes import wintypes

        create_file = ctypes.windll.kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        handle = create_file(str(path), 0, 0x00000001, None, 3, 0x00000080, None)
        invalid = wintypes.HANDLE(-1).value
        if handle == invalid:
            return ctypes.windll.kernel32.GetLastError() in (32, 33)
        ctypes.windll.kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ValueError):
        return False
    return False


def source_snapshot(path: Path) -> tuple[tuple[int, int, int] | None, str | None]:
    """Return file count, logical bytes, latest mtime, or a transient wait reason."""
    if not path.exists():
        return None, "Источник пока недоступен или ещё не появился полностью."
    if path.is_file():
        if source_has_incomplete_name(path):
            return None, f"Файл {path.name} имеет временное расширение и ещё не готов."
        try:
            stat = path.stat()
        except OSError as exc:
            return None, f"Источник пока нельзя прочитать: {exc}"
        if source_is_being_written(path):
            return None, f"Файл {path.name} всё ещё открыт программой записи."
        return (1, int(stat.st_size), int(stat.st_mtime_ns)), None
    if not path.is_dir():
        return None, "Источник не является обычным файлом или папкой."

    count = 0
    total = 0
    latest_mtime = 0
    try:
        latest_mtime = int(path.stat().st_mtime_ns)
        for root, _, files in os.walk(path):
            for name in files:
                current = Path(root) / name
                if source_has_incomplete_name(current):
                    return None, f"В папке ещё создаётся временный файл: {current.name}"
                stat = current.stat()
                if source_is_being_written(current):
                    return None, f"В папке ещё записывается файл: {current.name}"
                count += 1
                total += int(stat.st_size)
                latest_mtime = max(latest_mtime, int(stat.st_mtime_ns))
    except OSError as exc:
        return None, f"Папка источника ещё не готова: {exc}"
    return (count, total, latest_mtime), None


def robocopy_arguments(
    source: str,
    destination: Path,
    profile: str = "optimized",
    directory_threads: int = 8,
) -> tuple[list[str], Path]:
    """Build the real Robocopy command for the selected performance profile."""
    path = Path(source)
    profile = profile if profile in COPY_PROFILE_NAMES else "optimized"
    robocopy_profile = "maximum" if profile == "turbo" else profile
    retry_count, retry_wait = (8, 2) if robocopy_profile == "maximum" else (20, 10)
    common = [
        "/J",
        f"/R:{retry_count}",
        f"/W:{retry_wait}",
        "/COPY:DAT",
        "/DCOPY:DAT",
        "/XJ",
        "/V",
        "/FP",
        "/TS",
        "/BYTES",
        "/ETA",
    ]
    if robocopy_profile != "maximum":
        common.insert(0, "/Z")
    target = copy_target_path(path, destination)
    if path.is_dir():
        folder_options = ["/E"]
        if robocopy_profile in ("optimized", "maximum"):
            threads = max(2, min(MAX_DIRECTORY_THREADS, int(directory_threads)))
            folder_options.append(f"/MT:{threads}")
        return [str(path), str(target), *folder_options, *common], target
    return [str(path.parent), str(destination), path.name, *common], target


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
        self._animation.setDuration(360)
        self._animation.setEasingCurve(QEasingCurve.Type.OutQuart)
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


class NavigationTabBar(QTabBar):
    """Keep sidebar labels horizontal while QTabWidget is positioned on the left."""

    SIDE_WIDTH = 204
    SIDE_HEIGHT = 46

    def tabSizeHint(self, index: int) -> QSize:
        if self.shape() in (QTabBar.Shape.RoundedWest, QTabBar.Shape.TriangularWest):
            return QSize(self.SIDE_WIDTH, self.SIDE_HEIGHT)
        return super().tabSizeHint(index)

    def paintEvent(self, event) -> None:
        if self.shape() not in (QTabBar.Shape.RoundedWest, QTabBar.Shape.TriangularWest):
            super().paintEvent(event)
            return
        painter = QStylePainter(self)
        for index in range(self.count()):
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            option.shape = QTabBar.Shape.RoundedNorth
            option.text = f"  {option.text}"
            painter.drawControl(QStyle.ControlElement.CE_TabBarTab, option)


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


class SpeedGraph(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.values: deque[float] = deque([0.0], maxlen=24)
        self.accent_color = QColor("#00e8f5")
        self.fill_color = QColor("#123b49")
        self.setMinimumHeight(86)

    def setValue(self, value: float) -> None:
        self.values.append(max(0.0, float(value)))
        self.update()

    def set_colors(self, accent: str) -> None:
        self.accent_color = QColor(accent)
        self.fill_color = QColor(accent)
        self.fill_color.setAlpha(35)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if len(self.values) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = self.rect().adjusted(4, 6, -4, -5)
        maximum = max(max(self.values), 1.0)
        step = bounds.width() / max(1, len(self.values) - 1)
        line = QPainterPath()
        for index, value in enumerate(self.values):
            x = bounds.left() + index * step
            y = bounds.bottom() - (value / maximum) * bounds.height()
            if index == 0:
                line.moveTo(x, y)
            else:
                line.lineTo(x, y)
        fill = QPainterPath(line)
        fill.lineTo(bounds.right(), bounds.bottom())
        fill.lineTo(bounds.left(), bounds.bottom())
        fill.closeSubpath()
        painter.fillPath(fill, self.fill_color)
        painter.setPen(QPen(self.accent_color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(line)


class ProfileCard(QFrame):
    COLORS = {
        "slow": QColor("#34a853"),
        "optimal": QColor("#f9ab00"),
        "maximum": QColor("#ea4335"),
        "extreme": QColor("#a142f4"),
    }

    def __init__(self, profile_key: str) -> None:
        super().__init__()
        self.profile_key = profile_key
        self.setObjectName("profileCard")
        self.setProperty("profileKey", profile_key)
        self.glow = QGraphicsDropShadowEffect(self)
        self.glow.setBlurRadius(0)
        self.glow.setOffset(0, 0)
        self.glow.setColor(self.COLORS.get(profile_key, QColor("#00e8f5")))
        self.setGraphicsEffect(self.glow)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.glow.setBlurRadius(28)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.glow.setBlurRadius(0)
        super().leaveEvent(event)


class SourceSnapshotThread(QThread):
    def __init__(self, source: str, parent=None) -> None:
        super().__init__(parent)
        self.source = source
        self.result = (None, "Источник ещё проверяется.")

    def run(self) -> None:
        try:
            self.result = source_snapshot(Path(self.source))
        except Exception as exc:
            self.result = (None, f"Ошибка проверки исходника: {exc}")


class RcloneMonitorWindow(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Neon Drive · Монитор Rclone")
        self.setMinimumSize(720, 480)
        self.resize(900, 610)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("RCLONE · СЕТЬ И ТЕРМИНАЛ", objectName="sectionTitle")
        self.speed_label = QLabel("0.0 МБ/с", objectName="speed")
        self.state_label = QLabel("● ОЖИДАНИЕ", objectName="state")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.speed_label)
        header.addWidget(self.state_label)
        layout.addLayout(header)
        self.graph = SpeedGraph()
        self.graph.setMinimumHeight(155)
        layout.addWidget(self.graph)
        self.terminal = QPlainTextEdit(objectName="terminal")
        self.terminal.setMaximumBlockCount(5000)
        self.terminal.setReadOnly(True)
        self.terminal.setPlaceholderText("Команды и вывод Rclone появятся здесь…")
        layout.addWidget(self.terminal, 1)

    def reset_monitor(self) -> None:
        self.graph.values.clear()
        self.graph.values.append(0.0)
        self.graph.update()
        self.terminal.clear()
        self.set_speed(0.0)
        self.state_label.setText("● ЗАПУСК")

    def set_speed(self, speed_mib: float) -> None:
        self.graph.setValue(speed_mib)
        self.speed_label.setText(f"{speed_mib:.1f} МБ/с")

    def append_text(self, text: str) -> None:
        cursor = self.terminal.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.terminal.setTextCursor(cursor)
        self.terminal.verticalScrollBar().setValue(
            self.terminal.verticalScrollBar().maximum()
        )


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
        self.failure_reason = ""
        self._error_lines: deque[str] = deque(maxlen=4)

    def start_item(
        self,
        source: str,
        destination: Path,
        profile: str = "optimized",
        directory_threads: int = 8,
    ) -> None:
        self.current = source
        self.destination = destination
        self._done_emitted = False
        self._last_logged_percent = -1
        self._user_stopped = False
        self._item_completed_bytes = 0
        self._active_file_bytes = 0
        self._active_file_path = ""
        self._pending_file_bytes = None
        self.failure_reason = ""
        self._error_lines.clear()
        self.buffer = ""
        args, self.expected_target = robocopy_arguments(
            source,
            destination,
            profile,
            directory_threads,
        )
        command = subprocess.list2cmdline(["robocopy.exe", *args])
        profile_name = COPY_PROFILE_NAMES.get(profile, COPY_PROFILE_NAMES["optimized"])
        self.log.emit(
            f"\n▶ ИСХОДНИК: {source}\n▶ НАЗНАЧЕНИЕ: {self.expected_target}\n"
            f"▶ ПРОФИЛЬ: {profile_name}\n▶ КОМАНДА: {command}\n"
        )
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
        if re.search(r"(?:^|\s)(?:ERROR|ОШИБКА)\s+\d+", stripped, re.IGNORECASE):
            self._error_lines.append(stripped)
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
        if not ok:
            self.failure_reason = "\n".join(self._error_lines) or description
        self.log.emit(f"\nКОД ROBOCOPY: {exit_code}. {description}\n")
        self.item_done.emit(ok, self.current)

    def _process_error(self, error: QProcess.ProcessError) -> None:
        self.failure_reason = f"{error.name}: {self.errorString()}"
        self.log.emit(f"\nОШИБКА ЗАПУСКА ПРОЦЕССА: {self.failure_reason}\n")
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
        self._user_stopped = True
        if not self.processId():
            if self.state() != QProcess.NotRunning:
                self.kill()
            return
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


class RcloneDownloader(QProcess):
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
        self.expected_target: str | Path | None = None
        self.expected_bytes = 0
        self._done_emitted = False
        self._user_stopped = False
        self.failure_reason = ""
        self._error_lines: deque[str] = deque(maxlen=4)
        self.disk_buffer: TransferBuffer | None = None
        self.last_bytes = 0
        self.last_byte_time = time.monotonic()
        self.heartbeat = QTimer(self)
        self.heartbeat.setInterval(10000)
        self.heartbeat.timeout.connect(self._heartbeat)
        self.started.connect(self.heartbeat.start)
        self.finished.connect(self.heartbeat.stop)

    def _heartbeat(self) -> None:
        if self.state() == QProcess.Running:
            idle = int(time.monotonic() - self.last_byte_time)
            if idle >= 10:
                self.log.emit(
                    f"⏳ Rclone работает · без новых байтов {idle} сек. "
                    "Возможны проверка хеша, чтение исходника или ожидание сервера.\n"
                )

    def start_item(
        self,
        executable: str,
        source: str,
        destination: str | Path,
        options: RcloneOptions,
        expected_bytes: int = 0,
    ) -> None:
        self.current = source
        self.expected_bytes = max(0, int(expected_bytes))
        self._done_emitted = False
        self._user_stopped = False
        self.failure_reason = ""
        self._error_lines.clear()
        self.buffer = ""
        self.last_bytes = 0
        self.last_byte_time = time.monotonic()
        args, self.expected_target = rclone_arguments(source, destination, options)
        command = subprocess.list2cmdline([executable, *args])
        self.log.emit(
            f"\n▶ ИСХОДНИК: {source}\n▶ НАЗНАЧЕНИЕ: {self.expected_target}\n"
            f"▶ ДВИЖОК: Rclone\n▶ КОМАНДА: {command}\n"
        )
        self.command_started.emit(command)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        self.setProcessEnvironment(env)
        self.start(executable, args)

    def _read(self) -> None:
        text = bytes(self.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.buffer += text
        lines = re.split(r"\r\n|\r|\n", self.buffer)
        self.buffer = lines.pop()
        for line in lines:
            self._handle_output_line(line)

    def _handle_output_line(self, line: str) -> None:
        stripped = line.strip()
        if not stripped:
            return
        if stripped.startswith("{"):
            try:
                record = json.loads(stripped)
                stats = record.get("stats")
                if isinstance(stats, dict):
                    copied = max(0, int(stats.get("bytes", 0)))
                    total = self.expected_bytes or max(0, int(stats.get("totalBytes", 0)))
                    if copied > self.last_bytes:
                        self.last_byte_time = time.monotonic()
                    self.last_bytes = copied
                    self.progress.emit(self.current, min(100.0, copied * 100 / total) if total else 0.0, float(copied))
                    self.log.emit(
                        f"Rclone · {human_size(copied)} / {human_size(total)} · "
                        f"{human_size(float(stats.get('speed', 0)))}/с · "
                        f"проверок {stats.get('checks', 0)} · ошибок {stats.get('errors', 0)}\n"
                    )
                    return
                message = str(record.get("msg", ""))
                if str(record.get("level", "")).lower() in ("error", "critical", "fatal"):
                    self._error_lines.append(message)
                self.log.emit(message + "\n")
                return
            except (ValueError, TypeError, AttributeError):
                pass
        if " ERROR " in f" {stripped.upper()} " or "NOTICE: FAILED" in stripped.upper():
            self._error_lines.append(stripped)
        match = PERCENT_RE.search(stripped)
        if match:
            percent = min(100.0, float(match.group("pct").replace(",", ".")))
            copied = int(self.expected_bytes * percent / 100) if self.expected_bytes else 0
            self.progress.emit(self.current, percent, float(copied))
        self.log.emit(stripped + "\n")

    def _finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        if self._done_emitted:
            return
        if self.bytesAvailable():
            self._read()
        if self.buffer.strip():
            self._handle_output_line(self.buffer)
            self.buffer = ""
        self._done_emitted = True
        ok = exit_code == 0 and status == QProcess.NormalExit and not self._user_stopped
        if ok:
            self.log.emit("\nКОД RCLONE: 0. Копирование успешно завершено.\n")
        elif self._user_stopped:
            self.log.emit("\nRclone остановлен пользователем.\n")
        else:
            self.log.emit(f"\nОШИБКА RCLONE: код {exit_code}.\n")
        if not ok:
            self.failure_reason = (
                "Операция остановлена пользователем."
                if self._user_stopped
                else "\n".join(self._error_lines) or f"Rclone завершился с кодом {exit_code}."
            )
        self.item_done.emit(ok, self.current)

    def _process_error(self, error: QProcess.ProcessError) -> None:
        self.failure_reason = f"{error.name}: {self.errorString()}"
        self.heartbeat.stop()
        self.log.emit(f"\nОШИБКА ЗАПУСКА RCLONE: {self.failure_reason}\n")
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
        self._user_stopped = True
        if not self.processId():
            if self.state() != QProcess.NotRunning:
                self.kill()
            return
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


class TurboFileDownloader(QThread):
    log = Signal(str)
    progress = Signal(str, float, float)
    item_done = Signal(bool, str)
    command_started = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.current = ""
        self.destination = Path()
        self.expected_target: Path | None = None
        self.workers = 8
        self._stop_event = threading.Event()
        self._run_event = threading.Event()
        self._run_event.set()
        self._user_stopped = False

    def start_item(self, source: str, destination: Path, workers: int = 8) -> None:
        self.current = source
        self.destination = destination
        self.expected_target = copy_target_path(source, destination)
        self.workers = max(2, min(MAX_TURBO_THREADS, int(workers)))
        self._stop_event.clear()
        self._run_event.set()
        self._user_stopped = False
        description = (
            f"turbo-copy --workers {self.workers} "
            f"{subprocess.list2cmdline([source, str(self.expected_target)])}"
        )
        self.log.emit(
            f"\n▶ ИСТОЧНИК: {source}\n▶ НАЗНАЧЕНИЕ: {self.expected_target}\n"
            f"▶ ПРОФИЛЬ: {COPY_PROFILE_NAMES['turbo']}\n"
            f"▶ ПАРАЛЛЕЛЬНЫХ СЕГМЕНТОВ: {self.workers}\n"
        )
        self.command_started.emit(description)
        self.start()

    def run(self) -> None:
        if self.expected_target is None:
            self.log.emit("\nОШИБКА ТУРБОРЕЖИМА: не задан путь назначения.\n")
            self.item_done.emit(False, self.current)
            return

        def report(copied: int, total: int) -> None:
            percent = copied * 100.0 / total if total else 100.0
            self.progress.emit(self.current, percent, float(copied))

        ok = False
        try:
            parallel_copy_file(
                self.current,
                self.expected_target,
                self.workers,
                stop_event=self._stop_event,
                run_event=self._run_event,
                progress=report,
            )
            ok = self.expected_target.exists()
            if ok:
                self.log.emit("\n✓ Турбокопирование завершено, все сегменты объединены.\n")
        except TurboCopyStopped:
            self.log.emit(
                "\n■ Турбокопирование остановлено. Завершённые сегменты сохранены для докачки.\n"
            )
        except Exception as exc:
            self.log.emit(f"\nОШИБКА ТУРБОРЕЖИМА: {exc}\n{traceback.format_exc()}\n")
        self.item_done.emit(ok, self.current)

    def suspend(self) -> None:
        self._run_event.clear()

    def resume(self) -> None:
        self._run_event.set()

    def stop(self) -> None:
        self._user_stopped = True
        self._stop_event.set()
        self._run_event.set()


class FileRow(QFrame):
    def __init__(
        self,
        source: str,
        destination: str | Path,
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

    def target_path(self) -> str | Path:
        return copy_target_path(self.source, self.destination)

    @staticmethod
    def reveal(path: Path) -> None:
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        elif is_macos():
            QProcess.startDetached("open", ["-R", str(path)])
        else:
            QProcess.startDetached("explorer.exe", ["/select,", str(path)])

    def open_source(self) -> None:
        self.reveal(Path(self.source))

    def open_destination(self) -> None:
        target = self.target_path()
        if is_rclone_remote_path(target):
            QDesktopServices.openUrl(QUrl("https://drive.google.com/drive/my-drive"))
            return
        target_path = Path(target)
        destination_path = Path(self.destination)
        self.reveal(target_path if target_path.exists() else destination_path)

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


class FilesOverviewRow(QFrame):
    def __init__(self, direction: str, source: str, destination: str | Path) -> None:
        super().__init__(objectName="fileRow")
        self.direction = direction
        self.source = source
        self.destination = destination
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(6)

        header = QHBoxLayout()
        direction_label = QLabel("ВЫГРУЗКА" if direction == "upload" else "ЗАГРУЗКА")
        direction_label.setObjectName("directionBadge")
        self.status = QLabel("ОЖИДАНИЕ", objectName="fileStatus")
        header.addWidget(direction_label)
        header.addStretch()
        header.addWidget(self.status)
        layout.addLayout(header)

        layout.addWidget(QLabel("ОТКУДА", objectName="caption"))
        self.source_button = QPushButton(source, objectName="pathButton")
        self.source_button.setToolTip(source)
        self.source_button.clicked.connect(lambda: FileRow.reveal(Path(self.source)))
        layout.addWidget(self.source_button)

        self.progress = AnimatedProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        metrics = QHBoxLayout()
        self.bytes_label = QLabel("0.0 Б ИЗ 0.0 Б", objectName="fileInfo")
        self.read_speed_label = QLabel("СКАЧИВАНИЕ / ЧТЕНИЕ  —", objectName="fileInfo")
        self.write_speed_label = QLabel("ЗАПИСЬ  —", objectName="fileInfo")
        self.write_speed_label.setToolTip(
            "Robocopy и Rclone сообщают общую эффективную скорость конвейера; "
            "скорость записи отображает тот же подтверждённый поток данных."
        )
        metrics.addWidget(self.bytes_label)
        metrics.addStretch()
        metrics.addWidget(self.read_speed_label)
        metrics.addWidget(self.write_speed_label)
        layout.addLayout(metrics)

        layout.addWidget(QLabel("КУДА", objectName="caption"))
        self.destination_button = QPushButton(str(self.target_path()), objectName="folderLink")
        self.destination_button.setToolTip(str(self.target_path()))
        self.destination_button.clicked.connect(self.open_destination)
        layout.addWidget(self.destination_button)

    def target_path(self) -> str | Path:
        return copy_target_path(self.source, self.destination)

    def open_destination(self) -> None:
        target = self.target_path()
        if is_rclone_remote_path(target):
            QDesktopServices.openUrl(QUrl("https://drive.google.com/drive/my-drive"))
            return
        target_path = Path(target)
        destination_path = Path(self.destination)
        FileRow.reveal(target_path if target_path.exists() else destination_path)

    def update_data(self, size: int, downloaded: int, speed: float, state: str) -> None:
        transferred = min(downloaded, size) if size else downloaded
        fraction = transferred / size if size else 0.0
        self.progress.set_progress(round(max(0.0, min(1.0, fraction)) * 1000))
        self.status.setText(state)
        self.bytes_label.setText(f"{human_size(transferred)} ИЗ {human_size(size)}")
        speed_text = f"{speed / (1024 * 1024):.1f} МБ/с" if speed > 0 else "—"
        self.read_speed_label.setText(f"СКАЧИВАНИЕ / ЧТЕНИЕ  {speed_text}")
        self.write_speed_label.setText(f"ЗАПИСЬ  {speed_text}")


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
    error_message: str = ""
    source_signature: tuple[int, int, int] | None = None
    source_stable_since: float | None = None
    source_wait_message: str = ""

    def elapsed(self, now: float | None = None) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at or now or time.monotonic()
        return max(0.0, end - self.started_at)


@dataclass
class TransferPanel:
    direction: str
    page: QWidget
    sources: QPlainTextEdit
    destination: QLineEdit
    choose_files_button: QPushButton
    choose_folder_button: QPushButton
    clear_button: QPushButton
    browse_button: QPushButton
    show_destination_button: QPushButton
    preset_combo: QComboBox
    terminal_card: QFrame
    terminal: QPlainTextEdit
    pause_button: QPushButton
    after_button: QPushButton
    stop_button: QPushButton
    file_mode_label: QLabel
    file_list_layout: QGridLayout
    status_card: QFrame
    ring: Ring
    progress_text: QLabel
    progress: AnimatedProgressBar
    eta: QLabel
    speed: QLabel
    start_button: QPushButton
    state_label: QLabel
    footer_info: QLabel
    file_rows: dict[str, FileRow] = field(default_factory=dict)


class AddonInstallThread(QThread):
    succeeded = Signal(str)
    failed = Signal(str)

    def run(self) -> None:
        try:
            path = install_upload_addon(__version__)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(str(path))


class RcloneInstallThread(QThread):
    progress = Signal(int, str)
    succeeded = Signal(str, str)
    failed = Signal(str)

    def run(self) -> None:
        try:
            path, version = download_and_install_rclone(
                lambda percent, message: self.progress.emit(percent, message)
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(str(path), version)


class GoogleDriveOAuthThread(QThread):
    progress = Signal(str)
    succeeded = Signal(str, str)
    failed = Signal(str)

    def __init__(
        self,
        executable: str,
        remote_name: str = GOOGLE_DRIVE_REMOTE,
        label: str = "",
        kind: str = "personal",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.executable = executable
        self.remote_name = remote_name
        self.label = label
        self.kind = kind
        self.process: subprocess.Popen[str] | None = None

    def run(self) -> None:
        self.progress.emit("Откройте браузер и подтвердите доступ к Google Drive…")
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW
        try:
            self.process = subprocess.Popen(
                [
                    self.executable,
                    "authorize",
                    "drive",
                    f"--template={oauth_completion_template_path()}",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
            output, _ = self.process.communicate(timeout=600)
            if self.process.returncode != 0:
                raise RuntimeError(
                    "Авторизация отменена или Google не выдал разрешение. Попробуйте ещё раз."
                )
            token = extract_authorize_token(output)
            identity = fetch_google_drive_identity(token)
            path = store_google_drive_token(
                token,
                remote_name=self.remote_name,
                label=self.label,
                kind=self.kind,
                identity=identity,
            )
        except subprocess.TimeoutExpired:
            if self.process is not None:
                self.process.kill()
            self.failed.emit("Время ожидания подтверждения Google истекло.")
            return
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        finally:
            self.process = None
        self.succeeded.emit(str(path), self.remote_name)

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()


class SystemHealthThread(QThread):
    progress = Signal(int, str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        *,
        app_root: Path,
        rclone_candidate: str | None,
        download_destination: str,
        upload_destination: str,
        sources: list[str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.app_root = app_root
        self.rclone_candidate = rclone_candidate
        self.download_destination = download_destination
        self.upload_destination = upload_destination
        self.sources = list(sources)

    def run(self) -> None:
        try:
            report = run_system_health_check(
                app_root=self.app_root,
                rclone_candidate=self.rclone_candidate,
                download_destination=self.download_destination,
                upload_destination=self.upload_destination,
                sources=self.sources,
                repair=True,
                progress=lambda percent, message: self.progress.emit(percent, message),
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(report)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        # Keep the beta.12 settings namespace so upgrades retain every preference.
        self.settings = create_settings(SETTINGS_APP_NAME)
        self.transfer_stats = TransferStats(self.settings)
        self.queue: deque[str] = deque()
        self.workers: dict[str, Downloader | RcloneDownloader | TurboFileDownloader] = {}
        self.turbo_workers: set[TurboFileDownloader] = set()
        self.tasks: dict[str, TaskInfo] = {}
        self.file_rows: dict[str, FileRow] = {}
        self.transfer_panels: dict[str, TransferPanel] = {}
        self.active_transfer = "download"
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
        self.addon_install_thread: AddonInstallThread | None = None
        self.rclone_install_thread: RcloneInstallThread | None = None
        self.google_drive_oauth_thread: GoogleDriveOAuthThread | None = None
        self.system_health_thread: SystemHealthThread | None = None
        self.system_health_silent = False
        self.rclone_monitor: RcloneMonitorWindow | None = None
        self.active_engines: set[str] = set()
        self.active_destination: str | Path | None = None
        self.cloud_browser: DriveFolderDialog | None = None
        self._cloud_picker_request: tuple[str, str] | None = None
        self._start_after_google_oauth: str | None = None
        self._shared_drive_ids_cache: dict[str, dict[str, str]] = {}
        self._refreshing_google_accounts = False
        self.snapshot_threads: dict[str, SourceSnapshotThread] = {}
        self.snapshot_results: dict[str, tuple] = {}
        self.robocopy_executable = "robocopy.exe"
        self.rclone_executable = rclone_executable_name()
        self.beta_build = is_beta_build(__version__)
        self.upload_addon_enabled = self.beta_build and upload_addon_installed(__version__)
        self.advanced_mode_visible = self.settings.value(
            "advanced_mode_visible", False, type=bool
        )
        self.files_tab_visible = self.settings.value("files_tab_visible", False, type=bool)
        self.sidebar_expanded = self.settings.value("sidebar_expanded", True, type=bool)
        self.sidebar_animation: QPropertyAnimation | None = None
        self.tab_slide_animation: QPropertyAnimation | None = None
        self._last_tab_index = 0
        self.force_exit = False
        self.close_when_idle = False
        self.should_restore_maximized = False
        self.settings_dirty = False
        self._applying_rclone_profile = False
        self.tray_icon: QSystemTrayIcon | None = None
        self._animations: list[QPropertyAnimation] = []
        self.restart_banners: list[QFrame] = []
        self.metrics_timer = QTimer(self)
        self.metrics_timer.setInterval(1000)
        self.metrics_timer.timeout.connect(self.update_metrics)
        self.source_check_timer = QTimer(self)
        self.source_check_timer.setSingleShot(True)
        self.source_check_timer.setInterval(SOURCE_RECHECK_INTERVAL_MS)
        self.source_check_timer.timeout.connect(self.fill_worker_slots)
        self.auto_health_timer = QTimer(self)
        self.auto_health_timer.setSingleShot(True)
        self.auto_health_timer.setInterval(1800)
        self.auto_health_timer.timeout.connect(self.maybe_auto_system_health_check)
        self.theme_timer = QTimer(self)
        self.theme_timer.setInterval(60_000)
        self.theme_timer.timeout.connect(self.refresh_automatic_theme)
        self.build_ui()
        self.restore_settings()
        if getattr(sys, "frozen", False) and "--smoke-test" not in sys.argv:
            self.apply_windows_startup_setting()
        self.cleanup_old_logs()
        self.setup_tray()
        if os.environ.get("NEON_DRIVE_DISABLE_AUTO_UPDATE") != "1":
            QTimer.singleShot(4000, self.auto_check_updates)
        self.auto_health_timer.start()
        self.theme_timer.start()

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
        self.setMinimumSize(900, 640)
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 800)
        self.resize(
            min(1280, max(900, int(available.width() * 0.88))),
            min(850, max(640, int(available.height() * 0.88))),
        )
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(14, 14, 14, 14)
        shell.setSpacing(18)

        self.sidebar = QFrame(objectName="dashboardSidebar")
        self.sidebar.setMinimumWidth(220)
        self.sidebar.setMaximumWidth(220)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(16, 18, 16, 16)
        sidebar_layout.setSpacing(10)

        brand_row = QHBoxLayout()
        self.sidebar_logo = QLabel("N")
        self.sidebar_logo.setObjectName("sidebarLogo")
        self.sidebar_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sidebar_logo.setFixedSize(40, 40)
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(0)
        self.sidebar_brand = QLabel("NEON", objectName="sidebarBrand")
        self.sidebar_version = QLabel(f"Drive {__version__}", objectName="sidebarVersion")
        brand_copy.addWidget(self.sidebar_brand)
        brand_copy.addWidget(self.sidebar_version)
        brand_row.addWidget(self.sidebar_logo)
        brand_row.addLayout(brand_copy, 1)
        sidebar_layout.addLayout(brand_row)

        self.new_transfer_button = QPushButton("＋ Новая передача")
        self.new_transfer_button.setObjectName("newTransferButton")
        self.new_transfer_button.setMinimumHeight(42)
        self.new_transfer_button.clicked.connect(self.open_new_transfer_menu)
        sidebar_layout.addWidget(self.new_transfer_button)
        sidebar_layout.addSpacing(8)

        self.sidebar_navigation = QVBoxLayout()
        self.sidebar_navigation.setSpacing(6)
        sidebar_layout.addLayout(self.sidebar_navigation)
        sidebar_layout.addStretch()

        content_shell = QWidget()
        outer = QVBoxLayout(content_shell)
        self.outer_layout = outer
        outer.setContentsMargins(4, 8, 0, 0)
        outer.setSpacing(12)

        header_row = QHBoxLayout()
        self.navigation_toggle_button = QPushButton("≡")
        self.navigation_toggle_button.setObjectName("navToggle")
        self.navigation_toggle_button.setFixedSize(36, 36)
        self.navigation_toggle_button.setToolTip("Свернуть боковую панель")
        self.navigation_toggle_button.clicked.connect(self.toggle_navigation_panel)
        header_copy = QVBoxLayout()
        header_copy.setSpacing(1)
        self.dashboard_title = QLabel("Главная", objectName="dashboardTitle")
        self.dashboard_subtitle = QLabel(
            "Одна очередь, один процесс Rclone и полный контроль скорости",
            objectName="dashboardSubtitle",
        )
        header_copy.addWidget(self.dashboard_title)
        header_copy.addWidget(self.dashboard_subtitle)
        self.header_ready_badge = QLabel("● Готово", objectName="headerReadyBadge")
        self.header_version_badge = QLabel(f"beta · {__version__}", objectName="headerVersionBadge")
        header_row.addWidget(self.navigation_toggle_button)
        header_row.addLayout(header_copy)
        header_row.addStretch()
        header_row.addWidget(self.header_ready_badge)
        header_row.addWidget(self.header_version_badge)
        outer.addLayout(header_row)

        self.tabs = QTabWidget(objectName="navTabs")
        self.tabs.setTabBar(NavigationTabBar(self.tabs))
        self.tabs.tabBar().setUsesScrollButtons(False)
        self.download_page = self.build_transfer_tab("download")
        self.upload_page = self.build_transfer_tab("upload")
        self.home_page = self.build_home_tab()
        self.download_tab_index = self.tabs.addTab(self.home_page, "Главная")
        self.upload_tab_index = -1
        self.files_page = self.build_files_tab()
        self.files_tab_index = -1
        if self.files_tab_visible:
            self.files_tab_index = self.tabs.addTab(self.files_page, "Файлы")
        self.profiles_page = self.build_profiles_tab()
        self.profiles_tab_index = self.tabs.addTab(self.profiles_page, "Шаблоны")
        self.interface_settings_page = self.build_interface_tab()
        self.advanced_page = self.build_settings_tab()
        self.settings_page = self.build_settings_hub()
        self.settings_tab_index = self.tabs.addTab(self.settings_page, "Настройки")
        self.advanced_tab_index = -1
        self.updates_page = self.build_updates_tab()
        self.updates_tab_index = self.tabs.addTab(self.updates_page, "Обновления")
        self.tabs.setTabVisible(self.settings_tab_index, False)
        self._last_tab_index = self.tabs.currentIndex()
        self.tabs.currentChanged.connect(self.animate_tab)
        self.tabs.currentChanged.connect(self.transfer_tab_changed)
        self.tabs.currentChanged.connect(self.remember_active_tab)
        self.tabs.currentChanged.connect(self.update_dashboard_navigation)
        self.tabs.tabBar().hide()
        outer.addWidget(self.tabs, 1)

        self.sidebar_page_buttons: dict[QWidget, QPushButton] = {}
        self.sidebar_button_specs: list[tuple[QWidget, str, str]] = []
        self.add_sidebar_page_button(self.home_page, "H", "Главная")
        self.sidebar_files_button = self.add_sidebar_page_button(
            self.files_page, "↕", "Передачи"
        )
        self.sidebar_files_button.setVisible(self.files_tab_visible)
        self.add_sidebar_page_button(self.profiles_page, "P", "Шаблоны")
        self.add_sidebar_page_button(self.updates_page, "U", "Обновления")

        system_bar = QVBoxLayout()
        self.system_bar = system_bar
        system_bar.setSpacing(8)
        self.global_system_status = QLabel("●  СИСТЕМА ГОТОВА")
        self.global_system_status.setObjectName("systemState")
        self.global_rclone_status = QLabel("Rclone · встроен")
        self.global_rclone_status.setObjectName("footerInfo")
        self.sidebar_transfer_stats = QLabel("Передано 0.0 Б · за 1 день")
        self.sidebar_transfer_stats.setObjectName("transferStats")
        self.settings_gear_button = QPushButton("⚙  Настройки")
        self.settings_gear_button.setObjectName("settingsGear")
        self.settings_gear_button.setMinimumHeight(42)
        self.settings_gear_button.setCheckable(True)
        self.settings_gear_button.setToolTip("Настройки Neon Drive")
        self.settings_gear_button.clicked.connect(self.toggle_settings_page)
        system_bar.addWidget(self.settings_gear_button)
        status_card = QFrame(objectName="sidebarStatusCard")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(12, 11, 12, 11)
        status_layout.addWidget(self.global_system_status)
        status_layout.addWidget(self.global_rclone_status)
        status_layout.addWidget(self.sidebar_transfer_stats)
        system_bar.addWidget(status_card)
        sidebar_layout.addLayout(system_bar)

        shell.addWidget(self.sidebar)
        shell.addWidget(content_shell, 1)
        self.bind_transfer_panel("download")
        self.update_dashboard_navigation(self.tabs.currentIndex())
        self.apply_theme()

    def add_sidebar_page_button(
        self, page: QWidget, icon: str, label: str
    ) -> QPushButton:
        button = QPushButton(f"{icon}   {label}")
        button.setObjectName("sidebarNavButton")
        button.setCheckable(True)
        button.setMinimumHeight(42)
        button.clicked.connect(lambda _checked=False, selected=page: self.tabs.setCurrentWidget(selected))
        self.sidebar_navigation.addWidget(button)
        self.sidebar_page_buttons[page] = button
        self.sidebar_button_specs.append((page, icon, label))
        return button

    def open_new_transfer_menu(self) -> None:
        menu = QMenu(self)
        download_action = menu.addAction("↓  Скачать на компьютер")
        upload_action = menu.addAction("↑  Выгрузить на сетевой диск")
        upload_action.setEnabled(self.upload_addon_enabled)
        selected = menu.exec(self.new_transfer_button.mapToGlobal(QPoint(0, self.new_transfer_button.height())))
        if selected is download_action:
            self.show_transfer_direction("download")
        elif selected is upload_action:
            self.show_transfer_direction("upload")

    @Slot(int)
    def update_dashboard_navigation(self, _index: int) -> None:
        page = self.tabs.currentWidget()
        metadata = {
            self.home_page: (
                "Главная",
                "Одна очередь, один процесс Rclone и полный контроль скорости",
            ),
            self.files_page: ("Передачи", "Все файлы, направления, скорость и статус"),
            self.profiles_page: (
                "Шаблоны скорости",
                "Готовые режимы для скачивания и выгрузки",
            ),
            self.settings_page: (
                "Настройки",
                "Простые параметры сверху, сложные — в Advanced mode",
            ),
            self.advanced_page: (
                "Advanced mode",
                "Потоки, чанки, буферы, движки и журналы",
            ),
            self.updates_page: (
                "Обновления",
                "Версии Neon Drive, Installer и дополнение выгрузки",
            ),
        }
        title, subtitle = metadata.get(page, ("Neon Drive", "Надёжная передача файлов"))
        self.dashboard_title.setText(title)
        self.dashboard_subtitle.setText(subtitle)
        for candidate, button in self.sidebar_page_buttons.items():
            button.setChecked(candidate is page)
        self.settings_gear_button.setChecked(page in (self.settings_page, self.advanced_page))

    def build_overall_status(
        self,
    ) -> tuple[QFrame, Ring, QLabel, AnimatedProgressBar, QLabel, QLabel]:
        status_card = self.card()
        status_card.setObjectName("statusCard")
        status_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        status = QHBoxLayout(status_card)
        status.setContentsMargins(16, 9, 16, 9)
        ring = Ring()
        status.addWidget(ring)
        progress_box = QVBoxLayout()
        progress_text = QLabel("ОБЩИЙ ПРОГРЕСС · 0 ИЗ 0", objectName="progressText")
        progress = AnimatedProgressBar()
        progress.setRange(0, 1000)
        progress.setTextVisible(False)
        progress_box.addWidget(progress_text)
        progress_box.addWidget(progress)
        status.addLayout(progress_box, 1)
        eta_box = QVBoxLayout()
        eta_box.addWidget(self.label("ПРИМЕРНО ОСТАЛОСЬ"))
        eta = QLabel("—", objectName="eta")
        eta_box.addWidget(eta)
        status.addLayout(eta_box)
        speed_box = QVBoxLayout()
        speed_box.addWidget(self.label("СКОРОСТЬ"))
        speed = QLabel("—", objectName="speed")
        speed_box.addWidget(speed)
        status.addLayout(speed_box)
        return status_card, ring, progress_text, progress, eta, speed

    def build_home_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)

        direction_card = QFrame(objectName="directionSwitch")
        direction_layout = QHBoxLayout(direction_card)
        direction_layout.setContentsMargins(12, 8, 12, 8)
        direction_layout.setSpacing(8)
        direction_layout.addWidget(QLabel("НОВАЯ ПЕРЕДАЧА", objectName="caption"))
        direction_layout.addStretch()
        self.download_direction_button = QPushButton("↓  НА КОМПЬЮТЕР")
        self.download_direction_button.setObjectName("directionButton")
        self.download_direction_button.setCheckable(True)
        self.download_direction_button.setChecked(True)
        self.download_direction_button.clicked.connect(
            lambda: self.show_transfer_direction("download")
        )
        self.upload_direction_button = QPushButton("↑  НА СЕТЕВОЙ ДИСК")
        self.upload_direction_button.setObjectName("directionButton")
        self.upload_direction_button.setCheckable(True)
        self.upload_direction_button.setEnabled(self.upload_addon_enabled)
        self.upload_direction_button.setToolTip(
            "Выгрузка через Проводник"
            if self.upload_addon_enabled
            else "Установите дополнение «Выгрузка» в разделе обновлений"
        )
        self.upload_direction_button.clicked.connect(
            lambda: self.show_transfer_direction("upload")
        )
        direction_layout.addWidget(self.download_direction_button)
        direction_layout.addWidget(self.upload_direction_button)
        direction_card.hide()
        self.direction_switch_card = direction_card

        self.home_transfer_stack = QStackedWidget()
        self.home_transfer_stack.addWidget(self.download_page)
        self.home_transfer_stack.addWidget(self.upload_page)
        layout.addWidget(self.home_transfer_stack, 1)
        return page

    def show_transfer_direction(self, direction: str, switch_to_home: bool = True) -> None:
        if direction == "upload" and not self.upload_addon_enabled:
            return
        direction = "upload" if direction == "upload" else "download"
        panel = self.bind_transfer_panel(direction)
        self.home_transfer_stack.setCurrentWidget(panel.page)
        self.download_direction_button.setChecked(direction == "download")
        self.upload_direction_button.setChecked(direction == "upload")
        self.settings.setValue("home_transfer_direction", direction)
        if switch_to_home and self.tabs.currentWidget() is not self.home_page:
            self.tabs.setCurrentWidget(self.home_page)
        self.update_start_button()

    def toggle_transfer_direction(self) -> None:
        target = "download" if self.active_transfer == "upload" else "upload"
        if target == "upload" and not self.upload_addon_enabled:
            QMessageBox.information(
                self,
                APP_NAME,
                "Сначала установите BETA-дополнение «Выгрузка» в разделе обновлений.",
            )
            return
        self.show_transfer_direction(target)

    def build_transfer_tab(self, direction: str) -> QWidget:
        upload = direction == "upload"
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 4, 0, 2)
        page_layout.setSpacing(12)

        form_card = self.card()
        form_card.setObjectName("heroCard")
        form_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        form = QVBoxLayout(form_card)
        form.setContentsMargins(18, 14, 18, 14)
        form.setSpacing(9)
        hero_row = QHBoxLayout()
        transfer_title = QLabel("Новая передача")
        transfer_title.setObjectName("transferTitle")
        preset_combo = QComboBox()
        preset_combo.addItem("Медленно · минимум нагрузки", "slow")
        preset_combo.addItem("Оптимально · рекомендуется", "optimal")
        preset_combo.addItem("Максимально · весь доступный канал", "maximum")
        preset_combo.addItem("Экстрим · выгрузка чанками 1 ГиБ", "extreme")
        preset_combo.setMinimumWidth(205)
        hero_row.addWidget(transfer_title)
        hero_row.addStretch()
        hero_row.addWidget(preset_combo)
        form.addLayout(hero_row)

        sources = QPlainTextEdit()
        sources.setPlaceholderText("Выберите файлы или папку через Проводник…")
        sources.setFixedHeight(58)
        source_buttons = QHBoxLayout()
        source_buttons.setContentsMargins(0, 0, 0, 0)
        choose_files_button = QPushButton("Выбрать файлы")
        choose_files_button.setProperty("colorRole", "download")
        choose_files_button.setMaximumWidth(150)
        choose_files_button.setToolTip("Выбрать один или несколько файлов через Проводник")
        choose_files_button.clicked.connect(
            lambda _checked=False, selected=direction: self.choose_files_for(selected)
        )
        choose_folder_button = QPushButton("Папка")
        choose_folder_button.setProperty("colorRole", "folder")
        choose_folder_button.setMaximumWidth(110)
        choose_folder_button.setToolTip("Выбрать папку или подключённый диск через Проводник")
        choose_folder_button.clicked.connect(
            lambda _checked=False, selected=direction: self.choose_source_folder_for(selected)
        )
        clear_button = QPushButton("×")
        clear_button.setProperty("colorRole", "danger")
        clear_button.setMaximumWidth(46)
        clear_button.clicked.connect(sources.clear)
        choose_file_button = QPushButton("Выбрать файл")
        choose_file_button.setProperty("colorRole", "download")
        choose_file_button.setToolTip("Заменить весь список одним выбранным файлом")
        choose_file_button.clicked.connect(
            lambda _checked=False, selected=direction: self.choose_single_file_for(selected)
        )
        choose_files_button.setText("Добавить файлы")
        source_buttons.addWidget(choose_file_button)
        source_buttons.addWidget(choose_files_button)
        source_buttons.addWidget(choose_folder_button)
        source_buttons.addWidget(clear_button)

        destination_row = QHBoxLayout()
        destination = QLineEdit()
        destination.setPlaceholderText(
            "G:\\Мой диск (или Mi unidad)" if upload else "D:\\Downloads\\Google Drive"
        )
        destination.setMinimumHeight(58)
        browse_button = QPushButton("…")
        browse_button.setFixedWidth(42)
        browse_button.clicked.connect(
            lambda _checked=False, selected=direction: self.choose_destination_for(selected)
        )
        show_destination_button = QPushButton("↗")
        show_destination_button.setFixedWidth(42)
        show_destination_button.setToolTip(
            "Открыть папку Google Drive" if upload else "Открыть локальную папку загрузки"
        )
        show_destination_button.clicked.connect(
            lambda _checked=False, selected=direction: self.open_destination_folder_for(selected)
        )
        google_drive_button = QPushButton("Google Drive")
        google_drive_button.setObjectName("primarySmall")
        google_drive_button.setToolTip(
            "Сначала выбрать конечную папку в Проводнике, затем использовать Neon Rclone"
        )
        google_drive_button.setVisible(upload)
        google_drive_button.clicked.connect(self.use_or_connect_google_drive)
        destination_row.addWidget(destination, 1)
        destination_row.addWidget(browse_button)
        destination_row.addWidget(show_destination_button)

        path_grid = QGridLayout()
        path_grid.setHorizontalSpacing(16)
        path_grid.setVerticalSpacing(6)
        source_heading_label = self.label(
            "ОТКУДА · ФИЗИЧЕСКИЙ ДИСК" if upload else "ОТКУДА · СЕТЕВОЙ ДИСК"
        )
        path_grid.addWidget(source_heading_label, 0, 0)
        destination_heading = QHBoxLayout()
        destination_heading_label = self.label(
            "КУДА · СЕТЕВОЙ ДИСК" if upload else "КУДА · ФИЗИЧЕСКИЙ ДИСК"
        )
        destination_heading.addWidget(destination_heading_label, 1)
        destination_heading.addWidget(google_drive_button)
        path_grid.addLayout(destination_heading, 0, 2)
        direction_toggle_button = QPushButton("⇅")
        direction_toggle_button.setObjectName("directionToggleButton")
        direction_toggle_button.setFixedSize(38, 30)
        direction_toggle_button.setToolTip(
            "Переключить на загрузку" if upload else "Переключить на выгрузку"
        )
        direction_toggle_button.setEnabled(upload or self.upload_addon_enabled)
        direction_toggle_button.clicked.connect(self.toggle_transfer_direction)
        path_grid.addWidget(direction_toggle_button, 0, 1, Qt.AlignmentFlag.AlignCenter)
        path_grid.addWidget(sources, 1, 0)
        arrow = QLabel("→", objectName="transferArrow")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        path_grid.addWidget(arrow, 1, 1)
        destination_container = QWidget()
        destination_container.setLayout(destination_row)
        path_grid.addWidget(destination_container, 1, 2)
        source_actions = QWidget()
        source_actions.setLayout(source_buttons)
        path_grid.addWidget(source_actions, 2, 0)
        start_button = QPushButton("Начать передачу")
        start_button.setObjectName("primary")
        start_button.setProperty("colorRole", "upload" if upload else "download")
        start_button.setMinimumHeight(42)
        start_button.clicked.connect(
            lambda _checked=False, selected=direction: self.start_transfers(selected)
        )
        transfer_actions = QHBoxLayout()
        transfer_actions.addWidget(start_button, 1)
        visible_stop = QPushButton("Остановить")
        visible_stop.setProperty("colorRole", "danger")
        visible_stop.setToolTip(
            "Приостановить текущие процессы без потери сессии; повторное нажатие продолжит с того же места"
        )
        visible_stop.setEnabled(False)
        visible_stop.clicked.connect(self.toggle_resumable_stop)
        transfer_actions.addWidget(visible_stop)
        path_grid.addLayout(transfer_actions, 2, 2)
        path_grid.setColumnStretch(0, 5)
        path_grid.setColumnStretch(2, 5)
        form.addLayout(path_grid)
        route_note = QLabel("Выберите источник и назначение.", objectName="settingDescription")
        route_note.setWordWrap(True)
        def update_route_note() -> None:
            source_items = [line.strip() for line in sources.toPlainText().splitlines() if line.strip()]
            _, description = detect_direction(
                source_items,
                destination.text(),
            )
            if source_items:
                source_labels = {location_label(item) for item in source_items}
                source_heading_label.setText(
                    "ОТКУДА · " + (next(iter(source_labels)) if len(source_labels) == 1 else "СМЕШАННЫЕ ПУТИ")
                )
            if destination.text().strip():
                destination_heading_label.setText("КУДА · " + location_label(destination.text()))
            selected_label = str(self.settings.value("cloud_label/" + destination.text(), ""))
            route_note.setText("Прямая выгрузка → " + selected_label if selected_label else description)
        sources.textChanged.connect(update_route_note)
        destination.textChanged.connect(update_route_note)
        form.addWidget(route_note)
        page_layout.addWidget(form_card)

        terminal_card = self.card()
        terminal_card.setObjectName("terminalCard")
        terminal_card.setVisible(self.advanced_mode_visible)
        terminal_layout = QVBoxLayout(terminal_card)
        terminal_layout.setContentsMargins(16, 12, 16, 13)
        terminal_layout.addWidget(self.label("LIVE TERMINAL"))
        terminal = QPlainTextEdit(objectName="terminal")
        terminal.setReadOnly(True)
        terminal.setPlaceholderText("Ожидание запуска…")
        terminal_layout.addWidget(terminal, 1)
        controls = QHBoxLayout()
        pause_button = QPushButton("ПАУЗА")
        pause_button.clicked.connect(self.toggle_pause)
        after_button = QPushButton("ПОСЛЕ ФАЙЛА")
        after_button.setToolTip("Остановить очередь после завершения активного файла")
        after_button.clicked.connect(self.toggle_stop_after)
        stop_button = QPushButton("СТОП", objectName="danger")
        stop_button.clicked.connect(self.stop_now)
        for button in (pause_button, after_button, stop_button):
            button.setEnabled(False)
        open_logs = QPushButton("ЛОГИ")
        open_logs.clicked.connect(self.open_logs)
        for button in (pause_button, after_button, stop_button, open_logs):
            controls.addWidget(button)
        terminal_layout.addLayout(controls)

        files_card = self.card()
        files_card.setObjectName("filesCard")
        files_layout = QVBoxLayout(files_card)
        files_layout.setContentsMargins(14, 10, 14, 10)
        files_header = QHBoxLayout()
        files_header.addWidget(QLabel("Текущие передачи", objectName="sectionTitle"))
        files_header.addStretch()
        file_mode_label = QLabel("0 активных")
        file_mode_label.setObjectName("fileStatus")
        files_header.addWidget(file_mode_label)
        files_layout.addLayout(files_header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        file_list_widget = QWidget()
        file_list_layout = QGridLayout(file_list_widget)
        file_list_layout.setContentsMargins(0, 0, 0, 0)
        file_list_layout.setSpacing(7)
        for column in range(3):
            file_list_layout.setColumnStretch(column, 1)
        scroll.setWidget(file_list_widget)
        files_layout.addWidget(scroll, 1)

        status_card = self.card()
        status_card.setObjectName("statusCard")
        status_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(18, 14, 18, 14)
        status_layout.setSpacing(4)
        performance_header = QHBoxLayout()
        performance_header.addWidget(QLabel("Производительность", objectName="sectionTitle"))
        performance_header.addStretch()
        rclone_monitor_button = QPushButton("Rclone ↗")
        rclone_monitor_button.setProperty("colorRole", "monitor")
        rclone_monitor_button.setToolTip("Открыть отдельное окно графика и терминала Rclone")
        rclone_monitor_button.clicked.connect(self.show_rclone_monitor)
        performance_header.addWidget(rclone_monitor_button)
        speed = QLabel("—", objectName="speed")
        status_layout.addLayout(performance_header)
        status_layout.addWidget(speed)
        speed_graph = SpeedGraph()
        speed_graph.setMinimumHeight(32)
        speed_graph.setMaximumHeight(70)
        status_layout.addWidget(speed_graph)
        transfer_stats_label = QLabel("Передано 0.0 Б · за 1 день")
        transfer_stats_label.setObjectName("transferStats")
        transfer_stats_label.setWordWrap(True)
        status_layout.addWidget(transfer_stats_label)
        ring_row = QHBoxLayout()
        ring = Ring()
        ring.setFixedSize(60, 60)
        ring_row.addWidget(ring)
        metrics = QVBoxLayout()
        progress_text = QLabel("ОБЩИЙ ПРОГРЕСС · 0 ИЗ 0", objectName="progressText")
        progress_text.setWordWrap(True)
        eta = QLabel("Ожидание", objectName="eta")
        metrics.addWidget(progress_text)
        metrics.addWidget(eta)
        ring_row.addLayout(metrics, 1)
        status_layout.addLayout(ring_row)
        progress = AnimatedProgressBar()
        progress.setRange(0, 1000)
        progress.setTextVisible(False)
        status_layout.addWidget(progress)
        status_layout.addStretch()
        state_label = QLabel("●  ГОТОВО")
        state_label.setObjectName("state")
        footer_info = QLabel("Ожидание задачи")
        footer_info.setWordWrap(True)
        footer_info.setObjectName("footerInfo")
        status_layout.addWidget(state_label)
        status_layout.addWidget(footer_info)

        middle = QHBoxLayout()
        middle.setSpacing(12)
        middle.addWidget(files_card, 7)
        middle.addWidget(status_card, 3)
        page_layout.addLayout(middle, 1)

        recent_card = self.card()
        recent_card.setObjectName("recentCard")
        recent_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        recent_layout = QVBoxLayout(recent_card)
        recent_layout.setContentsMargins(16, 10, 16, 10)
        recent_layout.addWidget(QLabel("Недавние", objectName="sectionTitle"))
        recent_empty = QLabel("Завершённые и проверенные передачи появятся здесь")
        recent_empty.setObjectName("settingDescription")
        recent_layout.addWidget(recent_empty)
        page_layout.addWidget(recent_card)
        page_layout.addWidget(terminal_card)

        panel = TransferPanel(
            direction=direction,
            page=page,
            sources=sources,
            destination=destination,
            choose_files_button=choose_files_button,
            choose_folder_button=choose_folder_button,
            clear_button=clear_button,
            browse_button=browse_button,
            show_destination_button=show_destination_button,
            preset_combo=preset_combo,
            terminal_card=terminal_card,
            terminal=terminal,
            pause_button=pause_button,
            after_button=after_button,
            stop_button=stop_button,
            file_mode_label=file_mode_label,
            file_list_layout=file_list_layout,
            status_card=status_card,
            ring=ring,
            progress_text=progress_text,
            progress=progress,
            eta=eta,
            speed=speed,
            start_button=start_button,
            state_label=state_label,
            footer_info=footer_info,
        )
        self.transfer_panels[direction] = panel
        panel.google_drive_button = google_drive_button
        panel.direction_toggle_button = direction_toggle_button
        panel.speed_graph = speed_graph
        panel.visible_stop_button = visible_stop
        panel.choose_file_button = choose_file_button
        panel.recent_card = recent_card
        panel.transfer_stats_label = transfer_stats_label
        panel.rclone_monitor_button = rclone_monitor_button
        preset_combo.currentIndexChanged.connect(
            lambda _index, selected=preset_combo: self.apply_transfer_preset(
                str(selected.currentData() or "optimal")
            )
        )
        if direction == "download":
            self.sources = sources
            self.destination = destination
            self.choose_files_button = choose_files_button
            self.choose_folder_button = choose_folder_button
            self.clear_button = clear_button
            self.browse_button = browse_button
            self.show_destination_button = show_destination_button
            self.terminal = terminal
            self.pause_button = pause_button
            self.after_button = after_button
            self.stop_button = stop_button
            self.file_mode_label = file_mode_label
            self.file_list_layout = file_list_layout
            self.file_rows = panel.file_rows
        else:
            self.upload_sources = sources
            self.upload_destination = destination
        return page

    def build_profiles_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 12, 0, 4)
        layout.setSpacing(14)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        intro = self.card()
        intro.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        intro_layout = QHBoxLayout(intro)
        intro_layout.setContentsMargins(18, 12, 18, 12)
        intro_copy = QVBoxLayout()
        title = QLabel("Выберите профиль для всех новых передач")
        title.setObjectName("sectionTitle")
        subtitle = QLabel(
            "Настройки можно изменить отдельно в Advanced mode. Во всех профилях "
            "«Остановить → Продолжить» сохраняет активный процесс и место передачи."
        )
        subtitle.setObjectName("transferSubtitle")
        subtitle.setWordWrap(True)
        intro_copy.addWidget(title)
        intro_copy.addWidget(subtitle)
        intro_layout.addLayout(intro_copy, 1)
        advanced_button = QPushButton("Advanced mode")
        advanced_button.clicked.connect(
            lambda: (
                self.tabs.setCurrentWidget(self.settings_page),
                self.show_settings_section("rclone"),
            )
        )
        intro_layout.addWidget(advanced_button)
        layout.addWidget(intro)

        cards = QGridLayout()
        cards.setSpacing(14)
        self.profile_buttons: dict[str, QPushButton] = {}
        profiles = (
            (
                "slow",
                "Медленно",
                "Фоновая работа",
                "4 Rclone-потока · 64 МиБ · последовательная очередь",
                "Для фоновой передачи, пока вы работаете. Меньше потоков и расход памяти; скорость зависит от сети и диска, жёсткого лимита нет.",
                (("Скорость", "щадящий режим"), ("Размер чанка", "64 МиБ"), ("Параллельность", "4 потока"), ("Проверка", "До и после")),
            ),
            (
                "optimal",
                "Оптимально",
                "Рекомендуется",
                "8 потоков · 128 МиБ · безопасная докачка",
                "Для ежедневных передач. Баланс скорости, стабильности и памяти; подходит для большинства подключений.",
                (("Скорость", "баланс"), ("Размер чанка", "128 МиБ"), ("Параллельность", "8 потоков"), ("Проверка", "До и после")),
            ),
            (
                "maximum",
                "Максимально",
                "Весь доступный канал",
                "32 Rclone-потока · до 10 задач Robocopy",
                "Для больших файлов, быстрого SSD и свободного канала. Повышенный расход RAM; скорость всё равно ограничена сетью, диском и сервисом.",
                (("Скорость", "без лимита"), ("Размер чанка", "512 МиБ"), ("Параллельность", "32 потока"), ("Проверка", "До и после")),
            ),
            (
                "extreme", "Экстрим", "Крупные облачные файлы",
                "Google Drive · чанк 1 ГиБ · один файл за раз",
                "Чанк выгрузки занимает до 1 ГиБ RAM. Используйте при достаточной памяти и стабильной сети; ускорение не гарантируется.",
                (("Чанк выгрузки", "1024 МиБ"), ("Файлов сразу", "1"), ("Локальные потоки", "32"), ("Проверка", "До и после")),
            ),
        )
        for profile_index, (key, name, badge, details, description, specs) in enumerate(profiles):
            card = ProfileCard(key)
            card.setMinimumWidth(0)
            box = QVBoxLayout(card)
            box.setContentsMargins(20, 20, 20, 20)
            box.setSpacing(12)
            heading = QLabel(name)
            heading.setObjectName("profileTitle")
            heading.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            tag = QLabel(badge)
            tag.setObjectName("directionBadge")
            tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tag.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            details_label = QLabel(details)
            details_label.setObjectName("performanceNotice")
            details_label.setWordWrap(True)
            details_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            description_label = QLabel(description)
            description_label.setObjectName("settingDescription")
            description_label.setWordWrap(True)
            description_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            button = QPushButton("ВЫБРАТЬ")
            button.setProperty("profileKey", key)
            button.clicked.connect(
                lambda _checked=False, selected=key: self.apply_transfer_preset(selected)
            )
            self.profile_buttons[key] = button
            box.addWidget(heading)
            box.addWidget(tag)
            box.addWidget(details_label)
            box.addWidget(description_label)
            separator = QFrame(objectName="separator")
            separator.setFrameShape(QFrame.Shape.HLine)
            box.addWidget(separator)
            for spec_name, spec_value in specs:
                spec_row = QHBoxLayout()
                spec_label = QLabel(spec_name, objectName="settingDescription")
                spec_data = QLabel(spec_value, objectName="profileValue")
                spec_label.setWordWrap(True)
                spec_data.setWordWrap(True)
                spec_data.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                spec_row.addWidget(spec_label, 1)
                spec_row.addWidget(spec_data)
                box.addLayout(spec_row)
            box.addStretch()
            box.addWidget(button)
            cards.addWidget(card, profile_index // 2, profile_index % 2)
        layout.addLayout(cards, 1)

        impact = self.card()
        impact.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        impact_layout = QVBoxLayout(impact)
        impact_layout.setContentsMargins(18, 13, 18, 13)
        impact_layout.addWidget(QLabel("Как профиль влияет на передачу", objectName="sectionTitle"))
        impact_metrics = QHBoxLayout()
        for title_text, value_text in (
            ("Канал", "до 100%"),
            ("CPU", "адаптивная нагрузка"),
            ("Диск", "крупные блоки"),
            ("Продолжение", "тот же процесс и сессия"),
        ):
            metric = QVBoxLayout()
            metric.addWidget(QLabel(title_text, objectName="settingDescription"))
            value = QLabel(value_text, objectName="profileValue")
            value.setWordWrap(True)
            value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            metric.addWidget(value)
            impact_metrics.addLayout(metric, 1)
        impact_layout.addLayout(impact_metrics)
        layout.addWidget(impact)
        return page

    def build_files_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 2)
        layout.setSpacing(10)

        intro = self.card()
        intro.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        intro_layout = QVBoxLayout(intro)
        intro_layout.setContentsMargins(16, 12, 16, 12)
        title_row = QHBoxLayout()
        title = QLabel("ВСЕ ФАЙЛЫ И ИХ СОСТОЯНИЕ", objectName="sectionTitle")
        self.files_summary_label = QLabel("ФАЙЛОВ: 0", objectName="fileStatus")
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.files_summary_label)
        intro_layout.addLayout(title_row)
        note = QLabel(
            "Общая очередь загрузки и выгрузки: источник сверху, назначение снизу, "
            "статус, прогресс и эффективная скорость передачи для каждого файла."
        )
        note.setObjectName("settingDescription")
        note.setWordWrap(True)
        intro_layout.addWidget(note)
        layout.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.files_overview_content = QWidget()
        self.files_overview_layout = QVBoxLayout(self.files_overview_content)
        self.files_overview_layout.setContentsMargins(0, 0, 4, 0)
        self.files_overview_layout.setSpacing(8)
        scroll.setWidget(self.files_overview_content)
        layout.addWidget(scroll, 1)
        self.files_overview_rows: dict[tuple[str, str], FilesOverviewRow] = {}
        return page

    def create_restart_banner(self) -> QFrame:
        banner = QFrame(objectName="restartBanner")
        restart_layout = QHBoxLayout(banner)
        restart_layout.setContentsMargins(14, 7, 9, 7)
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
        box.setContentsMargins(16, 13, 16, 13)
        box.setSpacing(7)
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

        speed_card, speed_box = self.settings_section("ДВИЖОК И ПРОИЗВОДИТЕЛЬНОСТЬ")
        speed_box.addWidget(QLabel("Движок копирования"))
        self.copy_engine_combo = QComboBox()
        if not is_macos():
            self.copy_engine_combo.addItem("Robocopy · встроен в Windows", "robocopy")
        self.copy_engine_combo.addItem("Rclone · чанки и несколько потоков", "rclone")
        if not is_macos():
            self.copy_engine_combo.addItem("Совместный · Rclone для файлов, Robocopy для папок", "hybrid")
        speed_box.addWidget(self.copy_engine_combo)
        self.engine_status = QLabel()
        self.engine_status.setObjectName("engineStatus")
        self.engine_status.setWordWrap(True)
        speed_box.addWidget(self.engine_status)
        rclone_path_row = QHBoxLayout()
        self.rclone_path_edit = QLineEdit()
        self.rclone_path_edit.setPlaceholderText("Встроенный Rclone выбирается автоматически")
        browse_rclone_button = QPushButton("Выбрать Rclone…")
        browse_rclone_button.clicked.connect(self.browse_rclone_executable)
        self.download_rclone_button = QPushButton("Переустановить Rclone")
        self.download_rclone_button.setObjectName("primarySmall")
        self.download_rclone_button.clicked.connect(self.start_rclone_install)
        rclone_path_row.addWidget(self.rclone_path_edit, 1)
        rclone_path_row.addWidget(browse_rclone_button)
        rclone_path_row.addWidget(self.download_rclone_button)
        speed_box.addLayout(rclone_path_row)
        self.rclone_install_progress = QProgressBar()
        self.rclone_install_progress.setRange(0, 100)
        self.rclone_install_progress.setFormat("Подготовка загрузки Rclone…")
        self.rclone_install_progress.setVisible(False)
        speed_box.addWidget(self.rclone_install_progress)
        google_card = QFrame(objectName="oauthCard")
        google_box = QVBoxLayout(google_card)
        google_box.setContentsMargins(13, 11, 13, 11)
        google_box.setSpacing(7)
        google_title_row = QHBoxLayout()
        google_title_row.addWidget(
            QLabel("GOOGLE DRIVE · ПРЯМОЕ ПОДКЛЮЧЕНИЕ", objectName="sectionTitle")
        )
        google_title_row.addStretch()
        self.google_drive_status = QLabel("Не подключён", objectName="engineStatus")
        google_title_row.addWidget(self.google_drive_status)
        google_box.addLayout(google_title_row)
        google_note = QLabel(
            "OAuth2 открывает страницу согласия Google. После подтверждения Neon выгружает "
            "через Rclone прямо в облако — приложение Google Drive для компьютера не участвует."
        )
        google_note.setObjectName("settingDescription")
        google_note.setWordWrap(True)
        google_box.addWidget(google_note)
        google_account_row = QHBoxLayout()
        self.google_account_combo = QComboBox()
        self.google_account_combo.setMinimumWidth(220)
        self.google_account_combo.setPlaceholderText("Нет подключённых аккаунтов")
        self.google_account_combo.currentIndexChanged.connect(
            self.set_active_google_account
        )
        self.google_account_kind_combo = QComboBox()
        self.google_account_kind_combo.addItem("Личный", "personal")
        self.google_account_kind_combo.addItem("Рабочий / Workspace", "workspace")
        self.google_account_kind_combo.addItem("Общий / команда", "team")
        google_account_row.addWidget(QLabel("Аккаунт"))
        google_account_row.addWidget(self.google_account_combo, 1)
        google_account_row.addWidget(self.google_account_kind_combo)
        google_box.addLayout(google_account_row)
        google_actions = QHBoxLayout()
        self.google_drive_add_button = QPushButton("Добавить аккаунт")
        self.google_drive_add_button.setObjectName("primarySmall")
        self.google_drive_add_button.clicked.connect(
            lambda _checked=False: self.start_google_drive_oauth(add_new=True)
        )
        self.google_drive_connect_button = QPushButton("Переподключить")
        self.google_drive_connect_button.setObjectName("primarySmall")
        self.google_drive_connect_button.clicked.connect(self.start_google_drive_oauth)
        self.google_drive_disconnect_button = QPushButton("Отключить")
        self.google_drive_disconnect_button.setObjectName("danger")
        self.google_drive_disconnect_button.clicked.connect(self.disconnect_google_drive_account)
        google_actions.addWidget(self.google_drive_add_button)
        google_actions.addWidget(self.google_drive_connect_button)
        google_actions.addWidget(self.google_drive_disconnect_button)
        google_actions.addStretch()
        google_box.addLayout(google_actions)
        speed_box.addWidget(google_card)
        engine_note = QLabel(
            "Совместный режим безопасно распределяет элементы очереди между двумя движками: "
            "Rclone получает отдельные файлы, Robocopy — папки. Один файл никогда не копируется "
            "двумя программами одновременно."
        )
        engine_note.setObjectName("settingDescription")
        engine_note.setWordWrap(True)
        speed_box.addWidget(engine_note)
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

        speed_box.addWidget(QLabel("Профиль производительности"))
        self.copy_profile_combo = QComboBox()
        self.copy_profile_combo.addItem("Надёжный · /Z и полная докачка", "stable")
        self.copy_profile_combo.addItem("Ускоренный · /Z + многопоточность", "optimized")
        self.copy_profile_combo.addItem("Максимальная скорость · без /Z", "maximum")
        self.copy_profile_combo.addItem(
            "Турбо · большой файл несколькими сегментами", "turbo"
        )
        speed_box.addWidget(self.copy_profile_combo)
        self.directory_threads_controls = QWidget()
        directory_threads_box = QVBoxLayout(self.directory_threads_controls)
        directory_threads_box.setContentsMargins(0, 2, 0, 2)
        directory_threads_box.setSpacing(6)
        self.directory_threads_label = QLabel("Потоков внутри одной папки: 8")
        directory_threads_box.addWidget(self.directory_threads_label)
        self.directory_threads_slider = QSlider(Qt.Orientation.Horizontal)
        self.directory_threads_slider.setRange(2, MAX_DIRECTORY_THREADS)
        self.directory_threads_slider.setValue(8)
        self.directory_threads_slider.valueChanged.connect(
            lambda value: self.directory_threads_label.setText(
                f"Потоков внутри одной папки: {value}"
            )
        )
        directory_threads_box.addWidget(self.directory_threads_slider)
        speed_box.addWidget(self.directory_threads_controls)
        self.turbo_threads_controls = QWidget()
        turbo_threads_box = QVBoxLayout(self.turbo_threads_controls)
        turbo_threads_box.setContentsMargins(0, 2, 0, 2)
        turbo_threads_box.setSpacing(6)
        self.turbo_threads_label = QLabel("Турбо-потоков для одного файла: 8")
        turbo_threads_box.addWidget(self.turbo_threads_label)
        self.turbo_threads_slider = QSlider(Qt.Orientation.Horizontal)
        self.turbo_threads_slider.setRange(2, MAX_TURBO_THREADS)
        self.turbo_threads_slider.setValue(8)
        self.turbo_threads_slider.valueChanged.connect(
            lambda value: self.turbo_threads_label.setText(
                f"Турбо-потоков для одного файла: {value}"
            )
        )
        turbo_threads_box.addWidget(self.turbo_threads_slider)
        speed_box.addWidget(self.turbo_threads_controls)
        self.performance_note = QLabel()
        self.performance_note.setObjectName("settingDescription")
        self.performance_note.setWordWrap(True)
        speed_box.addWidget(self.performance_note)
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
        self.keep_open_after_finish_check = self.add_setting_toggle(
            behavior_box, "После завершения передачи оставлять Neon открытым в tray"
        )
        self.windows_startup_check = self.add_setting_toggle(
            behavior_box,
            "Запускать Neon Drive при входе в macOS"
            if is_macos()
            else "Запускать Neon Drive при входе в Windows",
        )
        self.notifications_check = self.add_setting_toggle(
            behavior_box,
            "Системное уведомление после завершения"
            if is_macos()
            else "Windows-уведомление после завершения",
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
        monitor_note = QLabel("Монитор Rclone открывается только кнопкой «Rclone ↗».")
        monitor_note.setWordWrap(True)
        logs_box.addWidget(monitor_note)
        # Retain the old settings object for migration, never auto-open a window.
        self.auto_rclone_monitor_check = QCheckBox()
        self.download_buffer_check = self.add_setting_toggle(
            behavior_box, "Буфер загрузки на диске · отдельные файлы"
        )
        buffer_note = QLabel(
            "Файл сначала сохраняется в скрытый буфер рядом с папкой назначения. "
            "После успешной передачи переносится без повторного копирования, буфер удаляется. "
            "Папки и выгрузка работают напрямую. При отмене временные данные удаляются."
        )
        buffer_note.setWordWrap(True)
        behavior_box.addWidget(buffer_note)
        self.auto_direction_check = self.add_setting_toggle(
            behavior_box, "Автоматически определять загрузку / выгрузку"
        )
        behavior_box.addWidget(QLabel("Папка Google Drive в Проводнике / Finder"))
        self.google_route_combo = QComboBox()
        self.google_route_combo.addItem("Спрашивать: Neon Rclone или клиент Google", "ask")
        self.google_route_combo.addItem("Путь из Проводника → Neon Rclone", "direct")
        self.google_route_combo.addItem("Обычное копирование через клиент Google", "filesystem")
        behavior_box.addWidget(self.google_route_combo)
        behavior_box.addWidget(QLabel("Чанк прямой выгрузки Google Drive"))
        self.drive_chunk_combo = QComboBox()
        for chunk in (8, 16, 32, 64, 128, 256, 512, 1024):
            self.drive_chunk_combo.addItem(f"{chunk} МиБ" + (" · 1 ГиБ" if chunk == 1024 else ""), chunk)
        behavior_box.addWidget(self.drive_chunk_combo)
        chunk_note = QLabel("Экстрим: до 1 ГиБ RAM на файл. При чанке 1 ГиБ выгружается один файл за раз. Большой чанк не гарантирует большую скорость.")
        chunk_note.setWordWrap(True)
        behavior_box.addWidget(chunk_note)
        grid.addWidget(logs_card, 1, 1)

        rclone_card, rclone_box = self.settings_section("RCLONE · СКОРОСТЬ И НАДЁЖНОСТЬ")
        rclone_note = QLabel(
            "Один процесс Rclone может нагружать канал несколькими потоками. Выберите готовый "
            "профиль или настройте параметры вручную; целостность и безопасная запись сохраняются."
        )
        rclone_note.setObjectName("settingDescription")
        rclone_note.setWordWrap(True)
        rclone_box.addWidget(rclone_note)
        rclone_profile_row = QHBoxLayout()
        rclone_profile_row.addWidget(QLabel("Профиль использования канала"))
        self.rclone_performance_combo = QComboBox()
        self.rclone_performance_combo.addItem("Сбалансированный · 4 потока", "balanced")
        self.rclone_performance_combo.addItem("Быстрый · 8 потоков", "fast")
        self.rclone_performance_combo.addItem("Максимальный · 16 потоков", "maximum")
        self.rclone_performance_combo.addItem("Экстремальный · 32 потока", "extreme")
        self.rclone_performance_combo.addItem("Ручная настройка", "manual")
        rclone_profile_row.addWidget(self.rclone_performance_combo, 1)
        rclone_box.addLayout(rclone_profile_row)
        self.rclone_profile_note = QLabel()
        self.rclone_profile_note.setObjectName("performanceNotice")
        self.rclone_profile_note.setWordWrap(True)
        rclone_box.addWidget(self.rclone_profile_note)
        self.rclone_controls = QWidget()
        rclone_grid = QGridLayout(self.rclone_controls)
        rclone_grid.setContentsMargins(0, 2, 0, 0)
        rclone_grid.setHorizontalSpacing(12)
        rclone_grid.setVerticalSpacing(7)

        def number_combo(values: tuple[int, ...], suffix: str = "") -> QComboBox:
            combo = QComboBox()
            for value in values:
                combo.addItem(f"{value}{suffix}", value)
            return combo

        self.rclone_chunk_combo = number_combo(
            (16, 32, 64, 128, 256, 512, 1024, 2048), " МиБ"
        )
        self.rclone_cutoff_combo = number_combo((64, 128, 256, 512, 1024), " МиБ")
        self.rclone_streams_combo = number_combo((1, 2, 4, 8, 12, 16, 24, 32))
        self.rclone_transfers_combo = number_combo((1, 2, 4, 8, 12, 16, 24, 32))
        self.rclone_checkers_combo = number_combo((2, 4, 8, 16, 32, 64))
        self.rclone_buffer_combo = number_combo((0, 8, 16, 32, 64, 128), " МиБ")
        self.rclone_write_buffer_combo = number_combo((1, 2, 4, 8), " МиБ")
        self.rclone_retries_combo = number_combo((1, 3, 5, 10))
        self.rclone_low_retries_combo = number_combo((1, 5, 10, 20))
        rclone_fields = (
            ("Размер чанка", self.rclone_chunk_combo, "Порог многопоточности", self.rclone_cutoff_combo),
            ("Потоков на файл", self.rclone_streams_combo, "Передач внутри папки", self.rclone_transfers_combo),
            ("Параллельных проверок", self.rclone_checkers_combo, "Буфер на передачу", self.rclone_buffer_combo),
            ("Буфер записи на поток", self.rclone_write_buffer_combo, "Повторов операции", self.rclone_retries_combo),
            ("Низкоуровневых повторов", self.rclone_low_retries_combo, "", None),
        )
        for row, (left_label, left_widget, right_label, right_widget) in enumerate(rclone_fields):
            rclone_grid.addWidget(QLabel(left_label), row, 0)
            rclone_grid.addWidget(left_widget, row, 1)
            if right_label:
                rclone_grid.addWidget(QLabel(right_label), row, 2)
                rclone_grid.addWidget(right_widget, row, 3)
        rclone_grid.setColumnStretch(1, 1)
        rclone_grid.setColumnStretch(3, 1)
        rclone_box.addWidget(self.rclone_controls)
        self.rclone_checksum_check = self.add_setting_toggle(
            rclone_box, "Сверять контрольную сумму, если её поддерживает источник"
        )
        self.rclone_no_sparse_check = self.add_setting_toggle(
            rclone_box, "Не создавать sparse-файлы на подключённом диске Windows"
        )
        grid.addWidget(rclone_card, 2, 0, 1, 2)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addWidget(self.settings_scroll(grid), 1)
        return page

    def build_settings_hub(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 2)
        layout.setSpacing(14)

        sections = self.card()
        sections.setObjectName("settingsSectionsCard")
        sections.setMinimumWidth(190)
        sections.setMaximumWidth(230)
        sections_layout = QVBoxLayout(sections)
        sections_layout.setContentsMargins(16, 16, 16, 16)
        sections_layout.setSpacing(7)
        sections_layout.addWidget(QLabel("Разделы", objectName="sectionTitle"))
        self.settings_section_buttons: dict[str, QPushButton] = {}
        for key, title in (
            ("general", "Общие"),
            ("interface", "Интерфейс"),
            ("transfers", "Передачи"),
            ("rclone", "Rclone"),
            ("updates", "Обновления"),
            ("diagnostics", "Диагностика"),
        ):
            button = QPushButton(title)
            button.setObjectName("settingsSectionButton")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, selected=key: self.show_settings_section(selected)
            )
            self.settings_section_buttons[key] = button
            sections_layout.addWidget(button)
        sections_layout.addStretch()
        layout.addWidget(sections)

        self.settings_content_stack = QStackedWidget()
        self.settings_content_stack.addWidget(self.interface_settings_page)
        self.settings_content_stack.addWidget(self.advanced_page)
        layout.addWidget(self.settings_content_stack, 1)
        self.show_settings_section("rclone")
        return page

    def show_settings_section(self, section: str) -> None:
        if section == "updates" and hasattr(self, "updates_page"):
            self.tabs.setCurrentWidget(self.updates_page)
            return
        advanced_sections = {"transfers", "rclone"}
        target = self.advanced_page if section in advanced_sections else self.interface_settings_page
        self.settings_content_stack.setCurrentWidget(target)
        for key, button in self.settings_section_buttons.items():
            button.setChecked(key == section)
        self.settings.setValue("settings_section", section)

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

        health_card, health_box = self.settings_section("ДИАГНОСТИКА И АВТОМАТИЧЕСКОЕ ИСПРАВЛЕНИЕ")
        health_header = QHBoxLayout()
        self.system_health_status = QLabel("●  СИСТЕМА ЕЩЁ НЕ ПРОВЕРЕНА")
        self.system_health_status.setObjectName("systemHealthStatus")
        self.system_health_status.setProperty("state", "idle")
        health_header.addWidget(self.system_health_status)
        health_header.addStretch()
        self.system_health_button = QPushButton("ПРОВЕРИТЬ И ИСПРАВИТЬ")
        self.system_health_button.setObjectName("primarySmall")
        self.system_health_button.clicked.connect(self.start_system_health_check)
        health_header.addWidget(self.system_health_button)
        health_box.addLayout(health_header)
        health_note = QLabel(
            "Проверяет Robocopy, Rclone, интернет, свободное место, папки приложения, "
            "Google Drive и выбранные пути. Недостающие служебные папки и Rclone "
            "восстанавливаются автоматически с проверкой SHA-256."
        )
        health_note.setObjectName("settingDescription")
        health_note.setWordWrap(True)
        health_box.addWidget(health_note)
        self.auto_system_health_check = self.add_setting_toggle(
            health_box, "Автоматически проверять и исправлять систему при запуске Neon"
        )
        self.system_health_progress = QProgressBar()
        self.system_health_progress.setRange(0, 100)
        self.system_health_progress.setValue(0)
        self.system_health_progress.setFormat("Ожидание запуска…")
        self.system_health_progress.setVisible(False)
        health_box.addWidget(self.system_health_progress)
        self.system_health_summary = QLabel(
            "Безопасные исправления выполняются автоматически; системные ограничения "
            "будут показаны отдельным отчётом."
        )
        self.system_health_summary.setObjectName("settingDescription")
        self.system_health_summary.setWordWrap(True)
        health_box.addWidget(self.system_health_summary)
        grid.addWidget(health_card, 0, 0, 1, 2)

        stats_card, stats_box = self.settings_section("СТАТИСТИКА ПЕРЕДАЧ")
        self.transfer_stats_summary = QLabel("Передано 0.0 Б · за 1 день")
        self.transfer_stats_summary.setObjectName("transferStatsValue")
        self.transfer_stats_summary.setWordWrap(True)
        stats_box.addWidget(self.transfer_stats_summary)
        self.transfer_stats_details = QLabel(
            "Скачивание: 0.0 Б · Выгрузка: 0.0 Б"
        )
        self.transfer_stats_details.setObjectName("settingDescription")
        self.transfer_stats_details.setWordWrap(True)
        stats_box.addWidget(self.transfer_stats_details)
        self.monthly_stats_reset_check = self.add_setting_toggle(
            stats_box, "Автоматически сбрасывать счётчик при наступлении нового месяца"
        )
        reset_stats_row = QHBoxLayout()
        reset_stats_row.addStretch()
        self.reset_transfer_stats_button = QPushButton("СБРОСИТЬ СЧЁТЧИК")
        self.reset_transfer_stats_button.setObjectName("danger")
        self.reset_transfer_stats_button.clicked.connect(self.reset_transfer_statistics)
        reset_stats_row.addWidget(self.reset_transfer_stats_button)
        stats_box.addLayout(reset_stats_row)
        stats_note = QLabel(
            "Учитываются только успешно завершённые файлы. Ручной сброс начинает новый период сразу."
        )
        stats_note.setObjectName("settingDescription")
        stats_note.setWordWrap(True)
        stats_box.addWidget(stats_note)
        grid.addWidget(stats_card, 1, 0, 1, 2)

        mode_card, mode_box = self.settings_section("ПРОСТОЙ И РАСШИРЕННЫЙ РЕЖИМ")
        mode_note = QLabel(
            "В простом режиме скрыты терминал и технические параметры. Включите Advanced mode, "
            "чтобы выбрать движок, чанки, потоки, повторы и подробное поведение очереди."
        )
        mode_note.setObjectName("settingDescription")
        mode_note.setWordWrap(True)
        mode_box.addWidget(mode_note)
        self.advanced_mode_check = self.add_setting_toggle(
            mode_box, "Показывать вкладку Advanced mode и технический терминал"
        )
        self.files_tab_check = self.add_setting_toggle(
            mode_box, "Показывать вкладку «Файлы» с общей очередью и скоростями"
        )
        mode_box.addWidget(QLabel("Расположение вкладок"))
        self.navigation_mode_combo = QComboBox()
        self.navigation_mode_combo.addItem("Слева · открытая панель", "side")
        self.navigation_mode_combo.addItem("Слева · начинать свёрнутой", "side_compact")
        mode_box.addWidget(self.navigation_mode_combo)
        navigation_note = QLabel(
            "Кнопка ≡ в заголовке плавно скрывает и возвращает левую панель, "
            "не меняя открытую страницу."
        )
        navigation_note.setObjectName("settingDescription")
        navigation_note.setWordWrap(True)
        mode_box.addWidget(navigation_note)
        mode_box.addWidget(QLabel("Размер окна"))
        self.window_size_combo = QComboBox()
        self.window_size_combo.addItem("Автоматически под рабочую область", "auto")
        self.window_size_combo.addItem("Малое окно · 900 × 640", "small")
        self.window_size_combo.addItem("Стандартное окно · 1180 × 760", "standard")
        self.window_size_combo.addItem("Большое окно · 1380 × 880", "large")
        self.window_size_combo.addItem("Запоминать размер, изменённый вручную", "remember")
        mode_box.addWidget(self.window_size_combo)
        window_size_note = QLabel(
            "Пресет применяется сразу. В режиме запоминания можно растянуть окно мышью — "
            "его размер восстановится при следующем запуске."
        )
        window_size_note.setObjectName("settingDescription")
        window_size_note.setWordWrap(True)
        mode_box.addWidget(window_size_note)
        memory_note = QLabel(
            "Все параметры, выбранная вкладка, положение окна и пути сохраняются автоматически "
            "и восстанавливаются при следующем запуске."
        )
        memory_note.setObjectName("settingDescription")
        memory_note.setWordWrap(True)
        mode_box.addWidget(memory_note)
        grid.addWidget(mode_card, 2, 0, 1, 2)

        theme_card, theme_box = self.settings_section("ТЕМА ПРИЛОЖЕНИЯ")
        theme_box.addWidget(QLabel("Основная тема"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Автоматически · светлая днём, тёмная ночью", "automatic")
        self.theme_combo.addItem("Светлая тема", "light")
        self.theme_combo.addItem("Google Drive · приглушённая", "google_drive")
        self.theme_combo.addItem("Google Drive · тёмная, цветные кнопки", "google_drive_dark")
        self.theme_combo.addItem("Тёмная тема", "dark")
        self.theme_combo.addItem("Чёрный OLED", "oled")
        theme_box.addWidget(self.theme_combo)
        self.automatic_theme_note = QLabel()
        self.automatic_theme_note.setObjectName("settingDescription")
        theme_box.addWidget(self.automatic_theme_note)
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
        self.contextual_buttons_check = self.add_setting_toggle(
            theme_box, "Разноцветные кнопки по назначению действия"
        )
        grid.addWidget(theme_card, 3, 0)

        design_card, design_box = self.settings_section("РЕЖИМ ДИЗАЙНА")
        design_box.addWidget(QLabel("Плотность и форма интерфейса"))
        self.design_mode_combo = QComboBox()
        self.design_mode_combo.addItem("Малый экран · максимум места", "small")
        self.design_mode_combo.addItem("Компактный · больше информации", "compact")
        self.design_mode_combo.addItem("Комфортный · крупнее элементы", "comfortable")
        self.design_mode_combo.addItem("Минималистичный · строгие формы", "minimal")
        design_box.addWidget(self.design_mode_combo)
        self.design_mode_note = QLabel(
            "Компактный режим уменьшает кнопки, вкладки и отступы, сохраняя удобные зоны нажатия."
        )
        self.design_mode_note.setObjectName("settingDescription")
        self.design_mode_note.setWordWrap(True)
        design_box.addWidget(self.design_mode_note)
        grid.addWidget(design_card, 3, 1)

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
        grid.addWidget(motion_card, 4, 0, 1, 2)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(4, 1)
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
        self.last_download_status = QLabel()
        self.last_download_status.setObjectName("cacheStatus")
        self.last_download_status.setWordWrap(True)
        update_box.addWidget(self.last_download_status)
        self.refresh_last_download_ui()
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
        installer_button = QPushButton("ОТКРЫТЬ NEON DRIVE INSTALLER")
        installer_button.setObjectName("primarySmall")
        installer_button.clicked.connect(self.open_version_manager)
        update_row.addWidget(installer_button)
        update_row.addStretch()
        update_box.addLayout(update_row)
        layout.addWidget(update_card)

        if self.beta_build:
            addon_card, addon_box = self.settings_section("BETA-ДОПОЛНЕНИЕ · ВЫГРУЗКА")
            self.addon_card = addon_card
            addon_header = QHBoxLayout()
            self.addon_status_badge = QLabel()
            self.addon_status_badge.setObjectName("addonBadge")
            addon_header.addWidget(self.addon_status_badge)
            addon_header.addStretch()
            beta_badge = QLabel("BETA ONLY")
            beta_badge.setObjectName("betaBadge")
            addon_header.addWidget(beta_badge)
            addon_box.addLayout(addon_header)
            addon_note = QLabel(
                "Добавляет отдельную вкладку для выгрузки локальных файлов и папок в "
                "Google Drive, подключённый к Проводнику Windows."
            )
            addon_note.setObjectName("settingDescription")
            addon_note.setWordWrap(True)
            addon_box.addWidget(addon_note)
            addon_actions = QHBoxLayout()
            addon_actions.setSpacing(7)
            self.addon_install_button = QPushButton("СКАЧАТЬ И УСТАНОВИТЬ")
            self.addon_install_button.setObjectName("primarySmall")
            self.addon_install_button.clicked.connect(self.start_upload_addon_install)
            self.addon_remove_button = QPushButton("УДАЛИТЬ")
            self.addon_remove_button.setObjectName("danger")
            self.addon_remove_button.clicked.connect(self.remove_upload_addon_clicked)
            self.addon_github_button = QPushButton("ОТКРЫТЬ НА GITHUB")
            self.addon_github_button.clicked.connect(self.open_upload_addon_github)
            addon_actions.addWidget(self.addon_install_button)
            addon_actions.addWidget(self.addon_remove_button)
            addon_actions.addWidget(self.addon_github_button)
            addon_actions.addStretch()
            addon_box.addLayout(addon_actions)
            layout.addWidget(addon_card)
            self.refresh_upload_addon_ui()

        history_card, history_box = self.settings_section("ДОСТУПНЫЕ ВЕРСИИ")
        self.manual_update_card = history_card
        history_note = QLabel(
            "Выберите стабильную или BETA-версию, затем скачайте и установите её вручную."
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

    def refresh_last_download_ui(self) -> None:
        cached = last_downloaded_release()
        if cached:
            downloaded_date = str(cached.get("downloaded_at") or "")[:10]
            date_suffix = f" · {downloaded_date}" if downloaded_date else ""
            self.last_download_status.setText(
                f"Последняя скачанная версия: {cached.get('version') or '—'}{date_suffix}"
            )
            self.last_download_status.setToolTip(str(cached.get("path") or ""))
            self.last_download_status.setProperty("cached", True)
        else:
            self.last_download_status.setText("Последняя скачанная версия: ещё не скачивалась")
            self.last_download_status.setToolTip("")
            self.last_download_status.setProperty("cached", False)
        self.last_download_status.style().unpolish(self.last_download_status)
        self.last_download_status.style().polish(self.last_download_status)

    def open_version_manager(self) -> None:
        candidates = [
            Path(sys.executable).resolve().parent / "NeonDriveInstaller.exe",
            Path(__file__).resolve().parents[1] / "dist" / "NeonDriveInstaller.exe",
        ]
        executable = next((path for path in candidates if path.is_file()), None)
        if executable is None:
            QDesktopServices.openUrl(
                QUrl(f"https://github.com/{REPOSITORY}/releases/latest")
            )
            QMessageBox.information(
                self,
                APP_NAME,
                "Neon Drive Installer ещё не установлен рядом с приложением. "
                "Открыта страница последнего релиза для его скачивания.",
            )
            return
        subprocess.Popen([str(executable)], close_fds=True)

    def refresh_upload_addon_ui(self) -> None:
        if not self.beta_build or not hasattr(self, "addon_status_badge"):
            return
        addon = read_upload_addon(__version__) if self.upload_addon_enabled else None
        installed = addon is not None
        addon_version = str(addon.get("version") or "") if addon else ""
        self.addon_status_badge.setText(
            f"●  УСТАНОВЛЕНО · {addon_version} · ВКЛАДКА ДОСТУПНА"
            if installed
            else "○  НЕ УСТАНОВЛЕНО"
        )
        self.addon_status_badge.setProperty("installed", installed)
        self.addon_status_badge.style().unpolish(self.addon_status_badge)
        self.addon_status_badge.style().polish(self.addon_status_badge)
        busy = bool(self.addon_install_thread and self.addon_install_thread.isRunning())
        self.addon_install_button.setText(
            "ПЕРЕУСТАНОВИТЬ" if installed else "СКАЧАТЬ И УСТАНОВИТЬ"
        )
        self.addon_install_button.setEnabled(not busy)
        self.addon_remove_button.setEnabled(installed and not busy)
        self.addon_github_button.setEnabled(not busy)

    def set_upload_addon_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled and self.beta_build)
        self.upload_addon_enabled = enabled
        if hasattr(self, "upload_direction_button"):
            self.upload_direction_button.setEnabled(enabled)
            self.upload_direction_button.setToolTip(
                "Выгрузка через Проводник"
                if enabled
                else "Установите дополнение «Выгрузка» в разделе обновлений"
            )
        download_panel = self.transfer_panels.get("download")
        if download_panel is not None and hasattr(download_panel, "direction_toggle_button"):
            download_panel.direction_toggle_button.setEnabled(enabled)
        if not enabled and hasattr(self, "home_transfer_stack"):
            self.show_transfer_direction("download", switch_to_home=False)
        self.refresh_tab_indexes()
        if not enabled:
            self.bind_transfer_panel("download")
            self.update_start_button()
        self.refresh_files_overview()
        self.refresh_upload_addon_ui()

    def toggle_settings_page(self) -> None:
        """Open settings from the compact bottom gear without exposing a tab."""
        if self.tabs.currentWidget() is self.settings_page:
            page = getattr(self, "_page_before_settings", self.home_page)
            if self.tabs.indexOf(page) < 0 or page is self.settings_page:
                page = self.home_page
            self.tabs.setCurrentWidget(page)
            self.settings_gear_button.setToolTip("Настройки Neon Drive")
            return
        self._page_before_settings = self.tabs.currentWidget()
        self.tabs.setCurrentWidget(self.settings_page)
        self.settings_gear_button.setToolTip("Вернуться к передаче")

    def apply_transfer_preset(
        self,
        preset: str,
        persist: bool = True,
        configure: bool = True,
    ) -> None:
        """Apply the same simple speed preset to Robocopy and Rclone."""
        preset = preset if preset in ("slow", "optimal", "maximum", "extreme") else "optimal"
        mapping = {
            "slow": ("stable", "balanced", "sequential", 2, 2),
            "optimal": ("optimized", "fast", "sequential", 8, 8),
            "maximum": ("maximum", "extreme", "all", 16, 16),
            "extreme": ("maximum", "extreme", "sequential", 16, 16),
        }
        copy_profile, rclone_profile, queue_mode, directory_threads, turbo_threads = mapping[preset]
        for panel in self.transfer_panels.values():
            index = panel.preset_combo.findData(preset)
            if index >= 0 and panel.preset_combo.currentIndex() != index:
                panel.preset_combo.blockSignals(True)
                panel.preset_combo.setCurrentIndex(index)
                panel.preset_combo.blockSignals(False)
        for key, button in getattr(self, "profile_buttons", {}).items():
            selected = key == preset
            button.setText("АКТИВНЫЙ ПРОФИЛЬ" if selected else "ВЫБРАТЬ")
            button.setProperty("selected", selected)
            button.style().unpolish(button)
            button.style().polish(button)

        if not hasattr(self, "copy_profile_combo"):
            return
        if not configure:
            return

        def select(combo: QComboBox, value) -> None:
            index = combo.findData(value)
            if index >= 0:
                combo.setCurrentIndex(index)

        select(self.copy_profile_combo, copy_profile)
        select(self.rclone_performance_combo, rclone_profile)
        engine = str(self.copy_engine_combo.currentData() or "robocopy")
        select(self.download_mode_combo, "sequential" if engine in ("rclone", "hybrid") else queue_mode)
        self.directory_threads_slider.setValue(directory_threads)
        self.turbo_threads_slider.setValue(turbo_threads)
        if hasattr(self, "drive_chunk_combo"):
            select(self.drive_chunk_combo, 1024 if preset == "extreme" else 64)
        if persist:
            self.settings.setValue("transfer_preset", preset)
            self.settings.sync()

    def handle_agent_request(self, request: dict) -> dict:
        """Handle the hidden local JSON interface used by NeonDriveCLI."""
        command = str(request.get("command") or "").casefold()
        if command == "activate":
            self.show_from_tray()
            return {"ok": True, "state": "visible", "version": __version__}
        if command == "status":
            return {
                "ok": True,
                "name": APP_NAME,
                "version": __version__,
                "running": self.running,
                "paused": self.paused,
                "direction": self.active_transfer,
                "queued": len(self.queue),
                "active": len(self.workers) + len(self.turbo_workers),
                "completed": self.completed_items,
                "failed": self.failed_items,
                "speed_bytes_per_second": int(self.speed_bps),
                "rclone": self.resolved_rclone_executable() or "",
            }
        if command in ("pause", "resume"):
            if not self.running:
                return {"ok": False, "error": "Нет активной передачи"}
            if command == "pause" and not self.paused:
                self.toggle_pause()
            elif command == "resume" and self.paused:
                self.toggle_pause()
            return {"ok": True, "paused": self.paused}
        if command == "stop":
            if self.running:
                QTimer.singleShot(0, self.stop_now)
            return {"ok": True, "stopping": self.running}
        if command == "shutdown":
            if self.running:
                self.stop_now()
            self.force_exit = True
            QTimer.singleShot(0, self.close)
            return {"ok": True, "state": "closing"}
        if command == "add":
            if self.running:
                return {"ok": False, "error": "Дождитесь завершения текущей очереди"}
            direction = str(request.get("direction") or "download")
            if direction not in self.transfer_panels:
                return {"ok": False, "error": "Неизвестное направление передачи"}
            if direction == "upload" and not self.upload_addon_enabled:
                return {"ok": False, "error": "Дополнение «Выгрузка» не установлено"}
            raw_sources = request.get("sources")
            sources = [str(value).strip() for value in raw_sources or [] if str(value).strip()]
            destination = str(request.get("destination") or "").strip()
            if not sources or not destination:
                return {"ok": False, "error": "Нужны source и destination"}
            panel = self.transfer_panels[direction]
            panel.sources.setPlainText("\n".join(sources))
            panel.destination.setText(destination)
            preset = str(request.get("profile") or "optimal")
            self.apply_transfer_preset(preset)
            self.show_transfer_direction(direction)
            if bool(request.get("start")):
                QTimer.singleShot(0, lambda: self.start_transfers(direction))
            return {
                "ok": True,
                "direction": direction,
                "sources": len(sources),
                "destination": destination,
                "profile": preset,
                "started": bool(request.get("start")),
            }
        return {"ok": False, "error": f"Неизвестная команда: {command}"}

    def start_upload_addon_install(self) -> None:
        if not self.beta_build:
            return
        if self.addon_install_thread and self.addon_install_thread.isRunning():
            return
        self.addon_status_badge.setText("◌  СКАЧИВАНИЕ ПАКЕТА С GITHUB…")
        self.addon_install_button.setEnabled(False)
        self.addon_remove_button.setEnabled(False)
        self.addon_github_button.setEnabled(False)
        thread = AddonInstallThread(self)
        self.addon_install_thread = thread
        thread.succeeded.connect(self.upload_addon_install_succeeded)
        thread.failed.connect(self.upload_addon_install_failed)
        thread.finished.connect(self.upload_addon_install_finished)
        thread.start()

    def upload_addon_install_succeeded(self, path: str) -> None:
        self.set_upload_addon_enabled(True)
        self.append_log(f"Дополнение «Выгрузка» установлено: {path}\n")
        QMessageBox.information(
            self,
            APP_NAME,
            "Дополнение «Выгрузка» установлено. Вкладка уже доступна без перезапуска.",
        )

    def upload_addon_install_failed(self, message: str) -> None:
        self.addon_status_badge.setText("!  ОШИБКА УСТАНОВКИ")
        self.append_log(f"Дополнение «Выгрузка»: {message}\n")
        QMessageBox.critical(
            self,
            APP_NAME,
            f"Не удалось установить дополнение «Выгрузка»:\n{message}",
        )

    def upload_addon_install_finished(self) -> None:
        self.addon_install_thread = None
        self.refresh_upload_addon_ui()

    def remove_upload_addon_clicked(self) -> None:
        if self.running and self.active_transfer == "upload":
            QMessageBox.warning(
                self,
                APP_NAME,
                "Сначала завершите или остановите текущую выгрузку.",
            )
            return
        answer = QMessageBox.question(
            self,
            APP_NAME,
            "Удалить дополнение «Выгрузка»? Файлы в Google Drive останутся на месте.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        remove_upload_addon()
        self.set_upload_addon_enabled(False)
        self.append_log("Дополнение «Выгрузка» удалено.\n")

    def open_upload_addon_github(self) -> None:
        QDesktopServices.openUrl(QUrl(upload_addon_github_url(__version__)))

    def sync_file_display_radios(self, index: int) -> None:
        if 0 <= index < len(self.file_display_radios):
            self.file_display_radios[index].setChecked(True)

    @staticmethod
    def day_count_text(days: int) -> str:
        value = max(1, int(days))
        if value % 10 == 1 and value % 100 != 11:
            word = "день"
        elif value % 10 in (2, 3, 4) and value % 100 not in (12, 13, 14):
            word = "дня"
        else:
            word = "дней"
        return f"{value} {word}"

    def refresh_transfer_stats_ui(self) -> None:
        snapshot = self.transfer_stats.snapshot()
        period = self.day_count_text(snapshot.period_days)
        summary = f"Передано {human_size(snapshot.total_bytes)} · за {period}"
        details = (
            f"Скачивание: {human_size(snapshot.download_bytes)} · "
            f"Выгрузка: {human_size(snapshot.upload_bytes)}"
        )
        if hasattr(self, "sidebar_transfer_stats"):
            self.sidebar_transfer_stats.setText(summary)
            self.sidebar_transfer_stats.setToolTip(details)
        if hasattr(self, "transfer_stats_summary"):
            self.transfer_stats_summary.setText(summary)
        if hasattr(self, "transfer_stats_details"):
            self.transfer_stats_details.setText(details)
        for panel in self.transfer_panels.values():
            label = getattr(panel, "transfer_stats_label", None)
            if label is not None:
                label.setText(summary)
                label.setToolTip(details)

    def record_transfer_statistics(self, byte_count: int, direction: str) -> None:
        self.transfer_stats.record(byte_count, direction)
        self.refresh_transfer_stats_ui()

    def reset_transfer_statistics(self) -> None:
        answer = QMessageBox.question(
            self,
            APP_NAME,
            "Сбросить объём скачиваний и выгрузок и начать новый период?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.transfer_stats.reset()
        self.refresh_transfer_stats_ui()

    def set_monthly_transfer_stats_reset(self, enabled: bool) -> None:
        self.transfer_stats.set_auto_monthly_reset(enabled)
        self.refresh_transfer_stats_ui()

    @staticmethod
    def automatic_theme_for_hour(hour: int) -> str:
        return "light" if 7 <= int(hour) < 19 else "dark"

    def refresh_automatic_theme(self) -> None:
        if hasattr(self, "theme_combo") and self.theme_combo.currentData() == "automatic":
            if self.automatic_theme_for_hour(datetime.now().hour) != getattr(self, "_applied_theme", None):
                self.apply_theme()

    def apply_theme(self) -> None:
        selected_theme = (
            self.theme_combo.currentData() if hasattr(self, "theme_combo") else "light"
        )
        theme = (
            self.automatic_theme_for_hour(datetime.now().hour)
            if selected_theme == "automatic"
            else selected_theme
        )
        self._applied_theme = theme
        if hasattr(self, "automatic_theme_note"):
            if selected_theme == "automatic":
                label = "светлая" if theme == "light" else "тёмная"
                self.automatic_theme_note.setText(
                    f"Сейчас активна {label} тема · переключение в 07:00 и 19:00."
                )
            else:
                self.automatic_theme_note.setText(
                    "Автоматический режим использует светлую тему с 07:00 до 19:00."
                )
        design = (
            self.design_mode_combo.currentData()
            if hasattr(self, "design_mode_combo")
            else "compact"
        )
        design_modes = {
            "small": {
                "outer": (10, 8, 10, 8), "spacing": 6, "start_height": 38,
                "button_v": 4, "button_h": 8, "input_v": 4, "input_h": 7,
                "tab_v": 5, "tab_h": 10, "tab_gap": 3,
                "radius": 6, "card_radius": 8, "title": 21,
                "metric": 17, "section": 12,
            },
            "compact": {
                "outer": (18, 14, 18, 12), "spacing": 9, "start_height": 42,
                "button_v": 6, "button_h": 10, "input_v": 6, "input_h": 9,
                "tab_v": 7, "tab_h": 13, "tab_gap": 4,
                "radius": 7, "card_radius": 10, "title": 25,
                "metric": 19, "section": 13,
            },
            "comfortable": {
                "outer": (26, 20, 26, 18), "spacing": 13, "start_height": 52,
                "button_v": 9, "button_h": 14, "input_v": 9, "input_h": 11,
                "tab_v": 10, "tab_h": 20, "tab_gap": 5,
                "radius": 10, "card_radius": 15, "title": 29,
                "metric": 23, "section": 15,
            },
            "minimal": {
                "outer": (16, 12, 16, 11), "spacing": 8, "start_height": 40,
                "button_v": 6, "button_h": 9, "input_v": 6, "input_h": 8,
                "tab_v": 7, "tab_h": 12, "tab_gap": 2,
                "radius": 4, "card_radius": 5, "title": 24,
                "metric": 18, "section": 13,
            },
        }
        metrics = design_modes.get(str(design), design_modes["compact"])
        themes = {
            "oled": {
                "background": "#000000", "card": "#080b0d", "input": "#020405",
                "text": "#f1fcff", "muted": "#a6b9be", "disabled": "#526469",
                "border": "#17282d",
                "button": "#10171a", "track": "#132024", "terminal": "#020405",
            },
            "dark": {
                "background": "#071321", "card": "#101e2d", "input": "#0b1724",
                "text": "#edf4fb", "muted": "#8da1b5", "disabled": "#60758a",
                "border": "#263a4e",
                "button": "#17273a", "track": "#26394e", "terminal": "#06111d",
            },
            "light": {
                "background": "#eef2f5", "card": "#ffffff", "input": "#f8fafb",
                "text": "#11191e", "muted": "#52626a", "disabled": "#9aa6ac",
                "border": "#cdd8de",
                "button": "#e6edf1", "track": "#d9e3e8", "terminal": "#101820",
            },
            "google_drive": {
                "background": "#eef1f4", "card": "#f8fafd", "input": "#f1f4f8",
                "text": "#202124", "muted": "#5f6368", "disabled": "#9aa0a6",
                "border": "#d2d8e0",
                "button": "#e8f0fe", "track": "#dde3ea", "terminal": "#202124",
            },
            "google_drive_dark": {
                "background": "#1b1b1f", "card": "#25262a", "input": "#202124",
                "text": "#e3e3e3", "muted": "#b5b8bf", "disabled": "#71757b",
                "border": "#44474e", "button": "#30343b", "track": "#44474e",
                "terminal": "#17181b",
            },
        }
        colors = themes.get(str(theme), themes["light"])
        drive_theme = str(theme) in ("google_drive", "google_drive_dark")
        light_theme = str(theme) in ("light", "google_drive")
        root_background = (
            f"qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            f"stop:0 {colors['background']}, stop:1 {colors['input']})"
        )
        if hasattr(self, "outer_layout"):
            self.outer_layout.setContentsMargins(*metrics["outer"])
            self.outer_layout.setSpacing(metrics["spacing"])
        for panel in self.transfer_panels.values():
            panel.start_button.setMinimumHeight(metrics["start_height"])
        if hasattr(self, "design_mode_note"):
            notes = {
                "small": "Режим малого экрана уменьшает шапку, кнопки, поля и отступы, чтобы панель запуска всегда оставалась видимой.",
                "compact": "Компактный режим уменьшает кнопки, вкладки и отступы, сохраняя удобные зоны нажатия.",
                "comfortable": "Комфортный режим увеличивает элементы и расстояния для большого экрана или сенсорного ввода.",
                "minimal": "Минималистичный режим использует плотную сетку, строгие формы и меньше визуального шума.",
            }
            self.design_mode_note.setText(notes.get(str(design), notes["compact"]))
        selected_accent = self.accent_combo.currentData() if hasattr(self, "accent_combo") else "#00e8f5"
        accent = getattr(self, "accent_color", None) or str(selected_accent or "#00e8f5")
        if drive_theme:
            accent = "#1a73e8"
        accent_color = QColor(accent)
        if not accent_color.isValid():
            accent_color = QColor("#00e8f5")
        accent = accent_color.name()
        accent_hover = accent_color.lighter(122).name()
        accent_text = "#081012" if accent_color.lightness() > 145 else "#ffffff"
        green = "#42d56b" if not light_theme else "#188038"
        terminal_text = accent_color.lighter(135).name()
        all_buttons = bool(
            hasattr(self, "accent_all_buttons_check") and self.accent_all_buttons_check.isChecked()
        ) and not drive_theme
        contextual_buttons = bool(
            hasattr(self, "contextual_buttons_check")
            and self.contextual_buttons_check.isChecked()
        )
        sidebar_background = colors["card"] if light_theme or drive_theme else "#08131f"
        selected_surface = (
            ("#d2e3fc" if light_theme else "#004a77") if drive_theme else
            accent_color.lighter(185).name() if light_theme else
            accent_color.darker(310).name()
        )
        selected_text = "#174ea6" if drive_theme and light_theme else colors["text"]
        general_button = (
            f"background: {accent}; color: {accent_text}; border-color: {accent_hover};"
            if all_buttons else
            f"background: {colors['button']}; color: {colors['text']}; border-color: {colors['border']};"
        )
        stylesheet = f"""
            * {{ font-family: 'Segoe UI Variable', 'Segoe UI'; color: {colors['text']}; }}
            #root {{ background: {root_background}; }}
            #dashboardSidebar {{ background: {sidebar_background}; border: 1px solid {colors['border']}; border-radius: 22px; }}
            #sidebarLogo {{ background: {accent}; color: {accent_text}; border-radius: 20px; font-size: 19px; font-weight: 900; }}
            #sidebarBrand {{ color: {colors['text']}; font-size: 19px; font-weight: 850; }}
            #sidebarVersion {{ color: {colors['muted']}; font-size: 10px; }}
            #dashboardTitle {{ color: {colors['text']}; font-size: {metrics['title']}px; font-weight: 850; }}
            #dashboardSubtitle {{ color: {colors['muted']}; font-size: 12px; }}
            #headerReadyBadge {{ color: {accent}; border: 1px solid {accent}; border-radius: 16px; padding: 7px 18px; font-weight: 700; }}
            #headerVersionBadge {{ color: {colors['muted']}; background: {colors['button']}; border: 1px solid {colors['border']}; border-radius: 16px; padding: 7px 18px; }}
            #newTransferButton {{ background: {accent}; color: {accent_text}; border-color: {accent}; text-align: center; font-weight: 800; }}
            #sidebarNavButton, #settingsGear {{ background: transparent; color: {colors['muted']}; border-color: transparent; text-align: left; padding-left: 13px; }}
            #sidebarNavButton:hover, #settingsGear:hover {{ background: {colors['button']}; color: {colors['text']}; border-color: {colors['border']}; }}
            #sidebarNavButton:checked, #settingsGear:checked {{ background: {selected_surface}; color: {selected_text}; border-color: {accent}; }}
            #sidebarStatusCard {{ background: {colors['card']}; border: 1px solid {colors['border']}; border-radius: 15px; }}
            #settingsSectionsCard {{ background: {colors['card']}; border: 1px solid {colors['border']}; border-radius: {metrics['card_radius']}px; }}
            #settingsSectionButton {{ background: transparent; color: {colors['muted']}; border-color: transparent; text-align: left; padding: 10px 12px; }}
            #settingsSectionButton:checked {{ background: {selected_surface}; color: {selected_text}; border-color: {accent}; }}
            QDialog, QMessageBox {{ background-color: {colors['background']}; }}
            QMessageBox QLabel {{ color: {colors['text']}; font-size: 13px; min-width: 270px; }}
            QMessageBox QPushButton {{ min-width: 78px; }}
            QMenu {{ background: {colors['card']}; color: {colors['text']}; border: 1px solid {colors['border']}; }}
            QMenu::item:selected {{ background: {accent}; color: {accent_text}; }}
            QToolTip {{ background: {colors['card']}; color: {colors['text']}; border: 1px solid {accent}; padding: 5px; }}
            #title, #brandAccent {{ font-size: {metrics['title']}px; font-weight: 800; letter-spacing: 2px; }}
            #brandAccent {{ color: {accent}; }}
            #versionBadge {{ color: {colors['muted']}; font-size: 15px; font-weight: 700; padding-top: 7px; }}
            #subtitle, #caption {{ color: {colors['muted']}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
            #transferTitle {{ color: {colors['text']}; font-size: {metrics['section'] + 3}px; font-weight: 760; letter-spacing: 0.3px; }}
            #transferSubtitle {{ color: {colors['muted']}; font-size: 11px; padding-top: 1px; }}
            #state {{ color: {accent}; background: {colors['card']}; border: 1px solid {accent}; border-radius: {metrics['radius'] + 4}px; padding: {metrics['button_v']}px {metrics['button_h'] + 1}px; }}
            #footerInfo {{ color: {colors['muted']}; }}
            #transferStats {{ color: {colors['muted']}; font-size: 10px; font-weight: 650; }}
            #transferStatsValue {{ color: {accent}; font-size: {metrics['section'] + 3}px; font-weight: 800; }}
            #systemState {{ color: {green}; font-size: 11px; font-weight: 750; }}
            #card, #fileRow, #filesCard, #terminalCard, #statusCard, #profileCard, #directionSwitch, #recentCard {{ background: {colors['card']}; border: 1px solid {colors['border']}; border-radius: {metrics['card_radius']}px; }}
            #heroCard {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {colors['card']}, stop:1 {colors['input']}); border: 1px solid {accent_color.darker(170).name()}; border-radius: {metrics['card_radius'] + 2}px; }}
            #statusCard {{ border-color: {accent_color.darker(190).name()}; }}
            #fileRow:hover {{ border-color: {accent}; }}
            #profileCard:hover {{ border-color: {accent}; background: {colors['input']}; }}
            #profileCard[profileKey="slow"]:hover {{ border: 2px solid #34a853; background: {colors['input']}; }}
            #profileCard[profileKey="optimal"]:hover {{ border: 2px solid #f9ab00; background: {colors['input']}; }}
            #profileCard[profileKey="maximum"]:hover {{ border: 2px solid #ea4335; background: {colors['input']}; }}
            #profileCard[profileKey="slow"] #profileValue {{ color: #34a853; }}
            #profileCard[profileKey="optimal"] #profileValue {{ color: #f9ab00; }}
            #profileCard[profileKey="maximum"] #profileValue {{ color: #ea4335; }}
            #profileTitle {{ color: {colors['text']}; font-size: {metrics['title']}px; font-weight: 800; }}
            #profileValue {{ color: {accent}; font-weight: 800; }}
            #transferArrow {{ color: {accent}; font-size: 34px; font-weight: 400; }}
            #profileCard QPushButton[selected="true"] {{ background: {accent}; color: {accent_text}; border-color: {accent_hover}; }}
            #restartBanner {{ background: {colors['card']}; border: 1px solid {accent}; border-radius: {metrics['radius']}px; }}
            QPlainTextEdit, QLineEdit, QComboBox {{ background: {colors['input']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: {metrics['radius']}px; padding: {metrics['input_v']}px {metrics['input_h']}px; selection-background-color: {accent}; }}
            QComboBox QAbstractItemView {{ background: {colors['card']}; color: {colors['text']}; border: 1px solid {colors['border']}; selection-background-color: {accent}; selection-color: {accent_text}; }}
            QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus {{ border-color: {accent}; }}
            #terminal {{ color: {terminal_text}; font-family: 'Cascadia Mono', Consolas; font-size: 11px; background: {colors['terminal']}; }}
            QPushButton {{ {general_button} border-width: 1px; border-style: solid; border-radius: {metrics['radius']}px; padding: {metrics['button_v']}px {metrics['button_h']}px; font-weight: 650; }}
            QPushButton:hover {{ border-color: {accent_hover}; color: {accent if not all_buttons else accent_text}; }}
            QPushButton:pressed {{ background: {accent_color.darker(135).name()}; color: {accent_text}; }}
            QPushButton:disabled {{ color: {colors['disabled']}; border-color: {colors['border']}; background: {colors['track']}; }}
            QLabel:disabled, QCheckBox:disabled, QRadioButton:disabled {{ color: {colors['disabled']}; }}
            QPlainTextEdit:disabled, QLineEdit:disabled, QComboBox:disabled {{ color: {colors['disabled']}; background: {colors['track']}; border-color: {colors['border']}; }}
            #pathButton, #folderLink {{ text-align: left; color: {accent}; background: transparent; border: 0; padding: 2px; font-weight: 600; }}
            #pathButton:hover, #folderLink:hover {{ color: {accent_hover}; text-decoration: underline; }}
            #tilePathButton {{ text-align: left; color: {accent}; background: {colors['input']}; border-color: {colors['border']}; }}
            #fileStatus {{ color: {green}; font-weight: 800; }}
            #fileInfo {{ color: {colors['muted']}; font-family: 'Cascadia Mono', Consolas; font-size: 11px; }}
            #danger:hover {{ border-color: #ff426d; color: #ff426d; }}
            #primary, #primarySmall {{ background: {accent}; color: {accent_text}; border: 1px solid {accent_hover}; border-radius: {metrics['radius'] + 2}px; letter-spacing: 0.7px; }}
            #primary {{ font-size: {metrics['section']}px; }}
            #primary:hover, #primarySmall:hover {{ background: {accent_hover}; color: {accent_text}; }}
            #primary:disabled {{ background: {colors['track']}; color: {colors['disabled']}; border-color: {colors['border']}; }}
            #directionButton {{ min-width: 150px; background: transparent; color: {colors['muted']}; border-color: transparent; }}
            #directionButton:checked {{ background: {accent}; color: {accent_text}; border-color: {accent_hover}; }}
            #directionToggleButton {{ background: {colors['button']}; color: {accent}; border: 1px solid {colors['border']}; border-radius: 10px; font-size: 18px; font-weight: 800; padding: 0; }}
            #directionToggleButton:hover {{ background: {selected_surface}; border-color: {accent}; }}
            QProgressBar {{ background: {colors['track']}; border: 0; border-radius: 4px; height: 8px; }}
            QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}
            #progressText {{ font-size: 12px; font-weight: 700; }}
            #eta {{ color: {accent}; font-size: {metrics['metric']}px; font-weight: 700; }}
            #speed {{ color: {green}; font-size: {metrics['metric']}px; font-weight: 700; min-width: 130px; }}
            #navTabs::pane {{ border: 0; }}
            QTabBar {{ background: transparent; }}
            QTabBar::tab {{ background: transparent; color: {colors['muted']}; border: 1px solid transparent; padding: {metrics['tab_v']}px {metrics['tab_h']}px; margin: 0 {metrics['tab_gap']}px {metrics['tab_gap']}px 0; border-radius: {metrics['radius']}px; font-weight: 650; }}
            QTabBar::tab:selected {{ color: {accent}; background: {colors['input']}; border-color: {colors['border']}; }}
            QTabBar::tab:hover {{ color: {accent_hover}; border-color: {accent}; }}
            #navToggle {{ background: {colors['card']}; color: {accent}; border: 1px solid {colors['border']}; border-radius: {metrics['radius']}px; padding: 0; font-size: 21px; font-weight: 700; }}
            #navToggle:hover {{ background: {colors['input']}; border-color: {accent}; color: {accent_hover}; }}
            #settingsGear {{ border-radius: {metrics['radius']}px; }}
            #settingCheck, #settingToggle {{ font-size: 12px; spacing: 8px; padding: 3px 0; }}
            #sectionTitle {{ font-size: {metrics['section']}px; font-weight: 750; padding-bottom: 5px; }}
            #settingDescription {{ color: {colors['muted']}; padding: 3px 0; }}
            #performanceNotice {{ color: {green}; background: {colors['input']}; border: 1px solid {colors['border']}; border-radius: {metrics['radius']}px; padding: 7px 9px; font-size: 11px; font-weight: 650; }}
            #performanceNotice[warning="true"] {{ color: #ffb84d; border-color: #8f5c18; }}
            #systemHealthStatus {{ color: {colors['muted']}; background: {colors['input']}; border: 1px solid {colors['border']}; border-radius: {metrics['radius']}px; padding: 6px 9px; font-size: 11px; font-weight: 750; }}
            #systemHealthStatus[state="running"] {{ color: {accent}; border-color: {accent}; }}
            #systemHealthStatus[state="ok"] {{ color: {green}; border-color: {green}; }}
            #systemHealthStatus[state="warning"] {{ color: #ffb84d; border-color: #8f5c18; }}
            #systemHealthStatus[state="error"] {{ color: #ff647f; border-color: #ff647f; }}
            #separator {{ color: {colors['border']}; margin: 10px 0; }}
            #updateButton {{ color: {green}; border-color: {green}; }}
            #betaBadge {{ color: {accent}; background: {colors['input']}; border: 1px solid {accent}; border-radius: {metrics['radius']}px; padding: 3px 8px; font-size: 10px; font-weight: 800; letter-spacing: 1px; }}
            #directionBadge {{ color: {accent}; background: {colors['input']}; border: 1px solid {colors['border']}; border-radius: {metrics['radius']}px; padding: 3px 8px; font-size: 10px; font-weight: 800; letter-spacing: 1px; }}
            #addonBadge {{ color: {colors['muted']}; font-size: 12px; font-weight: 750; }}
            #addonBadge[installed="true"] {{ color: {green}; }}
            #engineStatus {{ color: #ff647f; background: {colors['input']}; border: 1px solid #ff647f; border-radius: {metrics['radius']}px; padding: 6px 9px; font-size: 11px; font-weight: 700; }}
            #engineStatus[ready="true"] {{ color: {green}; border-color: {green}; }}
            #cacheStatus {{ color: {colors['muted']}; background: {colors['input']}; border: 1px solid {colors['border']}; border-radius: {metrics['radius']}px; padding: 6px 9px; font-size: 11px; }}
            #cacheStatus[cached="true"] {{ color: {green}; border-color: {green}; }}
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
            QSlider::sub-page:horizontal:disabled, QSlider::handle:horizontal:disabled {{ background: {colors['disabled']}; }}
            QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
            QScrollBar:vertical {{ background: transparent; width: 8px; }}
            QScrollBar::handle:vertical {{ background: {colors['border']}; border-radius: 4px; min-height: 30px; }}
        """
        if drive_theme:
            stylesheet += """
                #newTransferButton, QPushButton#primary {
                    background: #1a73e8; color: #ffffff; border-color: #1a73e8;
                }
                #newTransferButton:hover, QPushButton#primary:hover {
                    background: #185abc; color: #ffffff; border-color: #185abc;
                }
                QPushButton#primarySmall, QPushButton#updateButton {
                    background: #34a853; color: #ffffff; border-color: #2d9148;
                }
                QPushButton#primarySmall:hover, QPushButton#updateButton:hover {
                    background: #2d9148; color: #ffffff; border-color: #188038;
                }
                QPushButton#danger { background: #fce8e6; color: #c5221f; border-color: #ea4335; }
                QPushButton#danger:hover { background: #ea4335; color: #ffffff; border-color: #c5221f; }
                #settingsGear:checked { background: #fef7e0; color: #b06000; border-color: #f9ab00; }
                #headerReadyBadge { color: #188038; border-color: #34a853; }
                #systemState, #fileStatus { color: #188038; }
                #profileCard QPushButton[selected="true"] {
                    background: #1a73e8; color: #ffffff; border-color: #185abc;
                }
            """
        if str(theme) == "google_drive_dark":
            stylesheet += """
                #headerReadyBadge, #systemState, #fileStatus { color: #81c995; }
                #settingsGear:checked { background: #494117; color: #fdd663; }
            """
        if contextual_buttons or drive_theme:
            stylesheet += """
                QPushButton[colorRole="download"], QPushButton#primary[colorRole="download"] {
                    background: #1a73e8; color: #ffffff; border-color: #185abc;
                }
                QPushButton[colorRole="upload"], QPushButton#primary[colorRole="upload"] {
                    background: #34a853; color: #ffffff; border-color: #188038;
                }
                QPushButton[colorRole="folder"] {
                    background: #f9ab00; color: #202124; border-color: #e69500;
                }
                QPushButton[colorRole="danger"] {
                    background: #ea4335; color: #ffffff; border-color: #c5221f;
                }
                QPushButton[colorRole="monitor"] {
                    background: #9c27b0; color: #ffffff; border-color: #7b1fa2;
                }
                QPushButton[colorRole]:hover { border-color: #ffffff; color: #ffffff; }
            """
        application = QApplication.instance()
        if application is not None:
            if application.styleSheet() != stylesheet:
                application.setStyleSheet(stylesheet)
        else:
            if self.styleSheet() != stylesheet:
                self.setStyleSheet(stylesheet)
        animations = not hasattr(self, "animations_check") or self.animations_check.isChecked()
        for panel in self.transfer_panels.values():
            panel.ring.set_colors(colors["track"], accent, colors["text"])
            if hasattr(panel, "speed_graph"):
                panel.speed_graph.set_colors(accent)
            panel.progress.animations_enabled = animations
            for row in panel.file_rows.values():
                row.progress.animations_enabled = animations
        for row in getattr(self, "files_overview_rows", {}).values():
            row.progress.animations_enabled = animations
        if self.rclone_monitor is not None:
            self.rclone_monitor.graph.set_colors(accent)

    def restore_settings(self) -> None:
        def select(combo: QComboBox, value) -> None:
            index = combo.findData(value)
            combo.setCurrentIndex(index if index >= 0 else 0)

        dashboard_migrated = self.settings.value(
            "dashboard_reference_migrated", False, type=bool
        )
        saved_geometry = self.settings.value("window_geometry")
        if saved_geometry and dashboard_migrated:
            self.restoreGeometry(saved_geometry)
        self.should_restore_maximized = (
            self.settings.value("window_maximized", False, type=bool)
            if dashboard_migrated
            else False
        )
        old_parallel = self.settings.value("parallel_downloads", False, type=bool)
        self.advanced_mode_check.setChecked(
            self.settings.value("advanced_mode_visible", False, type=bool)
        )
        self.files_tab_check.setChecked(
            self.settings.value("files_tab_visible", True, type=bool)
            if dashboard_migrated
            else True
        )
        stored_navigation_mode = self.settings.value("navigation_mode", "side")
        if not self.settings.value("interface_v55_migrated", False, type=bool):
            if stored_navigation_mode == "top":
                stored_navigation_mode = "side"
            self.settings.setValue("interface_v55_migrated", True)
        select(self.navigation_mode_combo, stored_navigation_mode)
        if not self.settings.contains("sidebar_expanded"):
            self.sidebar_expanded = stored_navigation_mode != "side_compact"
        select(
            self.window_size_combo,
            self.settings.value("window_size_mode", "auto")
            if dashboard_migrated
            else "auto",
        )
        select(
            self.copy_engine_combo,
            self.settings.value("copy_engine", "rclone" if is_macos() else "robocopy"),
        )
        self.rclone_path_edit.setText(str(self.settings.value("rclone_path", "")))
        select(self.download_mode_combo, self.settings.value(
            "download_mode", "all" if old_parallel else "sequential"
        ))
        if self.copy_engine_combo.currentData() in ("rclone", "hybrid"):
            select(self.download_mode_combo, "sequential")
        self.concurrency_spin.setValue(self.settings.value("concurrency", 3, type=int))
        select(self.copy_profile_combo, self.settings.value("copy_profile", "optimized"))
        self.directory_threads_slider.setValue(
            self.settings.value("directory_threads", 8, type=int)
        )
        self.turbo_threads_slider.setValue(
            self.settings.value("turbo_threads", 8, type=int)
        )
        select(
            self.rclone_performance_combo,
            self.settings.value("rclone_performance_profile", "balanced"),
        )
        select(self.rclone_chunk_combo, self.settings.value("rclone_chunk_mib", 64, type=int))
        select(self.rclone_cutoff_combo, self.settings.value("rclone_cutoff_mib", 256, type=int))
        select(self.rclone_streams_combo, self.settings.value("rclone_streams", 4, type=int))
        select(self.rclone_transfers_combo, self.settings.value("rclone_transfers", 4, type=int))
        select(self.rclone_checkers_combo, self.settings.value("rclone_checkers", 8, type=int))
        select(self.rclone_buffer_combo, self.settings.value("rclone_buffer_mib", 16, type=int))
        select(
            self.rclone_write_buffer_combo,
            self.settings.value("rclone_write_buffer_mib", 1, type=int),
        )
        select(self.rclone_retries_combo, self.settings.value("rclone_retries", 3, type=int))
        select(self.rclone_low_retries_combo, self.settings.value("rclone_low_retries", 10, type=int))
        self.rclone_checksum_check.setChecked(
            self.settings.value("rclone_checksum", False, type=bool)
        )
        self.rclone_no_sparse_check.setChecked(
            self.settings.value("rclone_no_sparse", True, type=bool)
        )
        select(self.file_display_combo, self.settings.value("file_display", "list"))
        self.compact_check.setChecked(self.settings.value("compact_rows", False, type=bool))
        self.show_source_links_check.setChecked(
            self.settings.value("show_source_links", True, type=bool)
        )
        self.show_destination_links_check.setChecked(
            self.settings.value("show_destination_links", True, type=bool)
        )
        select(
            self.theme_combo,
            self.settings.value("theme", "dark") if dashboard_migrated else "dark",
        )
        select(self.design_mode_combo, self.settings.value("design_mode", "compact"))
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
        self.contextual_buttons_check.setChecked(
            self.settings.value("contextual_buttons", False, type=bool)
        )
        self.animations_check.setChecked(self.settings.value("animations", True, type=bool))
        self.monthly_stats_reset_check.setChecked(
            self.transfer_stats.auto_monthly_reset_enabled()
        )
        select(self.update_mode_combo, self.settings.value(
            "update_mode",
            "automatic" if self.settings.value("auto_updates", True, type=bool) else "manual",
        ))
        self.tray_check.setChecked(self.settings.value("tray_enabled", True, type=bool))
        self.continue_in_tray_check.setChecked(
            self.settings.value("continue_in_tray", True, type=bool)
        )
        self.keep_open_after_finish_check.setChecked(
            self.settings.value("keep_open_after_finish", False, type=bool)
        )
        self.windows_startup_check.setChecked(
            self.settings.value("windows_startup", startup_enabled(), type=bool)
        )
        self.auto_system_health_check.setChecked(
            self.settings.value("auto_system_health", False, type=bool)
        )
        self.notifications_check.setChecked(
            self.settings.value("notifications", True, type=bool)
        )
        self.auto_start_check.setChecked(self.settings.value("auto_start", False, type=bool))
        self.smart_terminal_check.setChecked(
            self.settings.value("smart_terminal", True, type=bool)
        )
        self.auto_rclone_monitor_check.setChecked(False)
        self.download_buffer_check.setChecked(self.settings.value("download_buffer", False, type=bool))
        self.auto_direction_check.setChecked(self.settings.value("auto_direction", True, type=bool))
        select(self.google_route_combo, self.settings.value("google_drive_route", "ask"))
        select(self.drive_chunk_combo, self.settings.value("drive_chunk_mib", 64, type=int))
        self.cleanup_logs_check.setChecked(
            self.settings.value("cleanup_logs", True, type=bool)
        )
        select(self.log_retention_combo, self.settings.value("log_retention_days", 30, type=int))
        self.destination.setText(self.settings.value("destination", str(Path.home() / "Downloads")))
        self.sources.setPlainText(self.settings.value("sources", ""))
        self.upload_destination.setText(self.settings.value("upload_destination", ""))
        self.upload_sources.setPlainText(self.settings.value("upload_sources", ""))
        self.apply_transfer_preset(
            str(self.settings.value("transfer_preset", "optimal")),
            persist=False,
            configure=False,
        )

        for signal in (
            self.advanced_mode_check.stateChanged,
            self.files_tab_check.stateChanged,
            self.navigation_mode_combo.currentIndexChanged,
            self.window_size_combo.currentIndexChanged,
            self.copy_engine_combo.currentIndexChanged,
            self.download_mode_combo.currentIndexChanged,
            self.concurrency_spin.valueChanged,
            self.copy_profile_combo.currentIndexChanged,
            self.directory_threads_slider.valueChanged,
            self.turbo_threads_slider.valueChanged,
            self.rclone_performance_combo.currentIndexChanged,
            self.rclone_chunk_combo.currentIndexChanged,
            self.rclone_cutoff_combo.currentIndexChanged,
            self.rclone_streams_combo.currentIndexChanged,
            self.rclone_transfers_combo.currentIndexChanged,
            self.rclone_checkers_combo.currentIndexChanged,
            self.rclone_buffer_combo.currentIndexChanged,
            self.rclone_write_buffer_combo.currentIndexChanged,
            self.rclone_retries_combo.currentIndexChanged,
            self.rclone_low_retries_combo.currentIndexChanged,
            self.rclone_checksum_check.stateChanged,
            self.rclone_no_sparse_check.stateChanged,
            self.file_display_combo.currentIndexChanged,
            self.compact_check.stateChanged,
            self.show_source_links_check.stateChanged,
            self.show_destination_links_check.stateChanged,
            self.theme_combo.currentIndexChanged,
            self.design_mode_combo.currentIndexChanged,
            self.accent_combo.currentIndexChanged,
            self.accent_all_buttons_check.stateChanged,
            self.contextual_buttons_check.stateChanged,
            self.animations_check.stateChanged,
            self.monthly_stats_reset_check.stateChanged,
            self.update_mode_combo.currentIndexChanged,
            self.tray_check.stateChanged,
            self.continue_in_tray_check.stateChanged,
            self.keep_open_after_finish_check.stateChanged,
            self.windows_startup_check.stateChanged,
            self.auto_system_health_check.stateChanged,
            self.notifications_check.stateChanged,
            self.auto_start_check.stateChanged,
            self.smart_terminal_check.stateChanged,
            self.auto_rclone_monitor_check.stateChanged,
            self.download_buffer_check.stateChanged,
            self.auto_direction_check.stateChanged,
            self.google_route_combo.currentIndexChanged,
            self.drive_chunk_combo.currentIndexChanged,
            self.cleanup_logs_check.stateChanged,
            self.log_retention_combo.currentIndexChanged,
        ):
            signal.connect(self.settings_changed)
        self.rclone_path_edit.editingFinished.connect(self.settings_changed)
        self.sources.textChanged.connect(lambda: self.refresh_file_rows("download"))
        self.destination.textChanged.connect(lambda _text: self.refresh_file_rows("download"))
        self.upload_sources.textChanged.connect(lambda: self.refresh_file_rows("upload"))
        self.upload_destination.textChanged.connect(
            lambda _text: self.refresh_file_rows("upload")
        )
        self.update_settings_visibility()
        self.apply_theme()
        self.refresh_transfer_stats_ui()
        self.apply_window_size_mode()
        self.refresh_file_rows("download")
        self.refresh_file_rows("upload")
        self.refresh_google_drive_status()
        self.show_settings_section(str(self.settings.value("settings_section", "rclone")))
        self.restore_active_tab()
        self.settings.setValue("dashboard_reference_migrated", True)

    def persist_settings(self) -> None:
        self.settings.setValue("download_buffer", self.download_buffer_check.isChecked())
        self.settings.setValue("auto_direction", self.auto_direction_check.isChecked())
        self.settings.setValue("google_drive_route", self.google_route_combo.currentData())
        self.settings.setValue("drive_chunk_mib", self.drive_chunk_combo.currentData())
        self.settings.setValue("window_geometry", self.saveGeometry())
        self.settings.setValue("window_maximized", self.isMaximized())
        self.settings.setValue("active_tab", self.tab_key(self.tabs.currentWidget()))
        self.settings.setValue("advanced_mode_visible", self.advanced_mode_check.isChecked())
        self.settings.setValue("files_tab_visible", self.files_tab_check.isChecked())
        self.settings.setValue("navigation_mode", self.navigation_mode_combo.currentData())
        self.settings.setValue("interface_v55_migrated", True)
        self.settings.setValue("dashboard_reference_migrated", True)
        self.settings.setValue("sidebar_expanded", self.sidebar_expanded)
        self.settings.setValue("window_size_mode", self.window_size_combo.currentData())
        if self.window_size_combo.currentData() == "remember" and not self.isMaximized():
            self.settings.setValue("window_width", self.width())
            self.settings.setValue("window_height", self.height())
        self.settings.setValue("copy_engine", self.copy_engine_combo.currentData())
        self.settings.setValue("rclone_path", self.rclone_path_edit.text().strip())
        self.settings.setValue("download_mode", self.download_mode_combo.currentData())
        self.settings.setValue("concurrency", self.concurrency_spin.value())
        self.settings.setValue("copy_profile", self.copy_profile_combo.currentData())
        self.settings.setValue("directory_threads", self.directory_threads_slider.value())
        self.settings.setValue("turbo_threads", self.turbo_threads_slider.value())
        self.settings.setValue(
            "rclone_performance_profile", self.rclone_performance_combo.currentData()
        )
        self.settings.setValue("rclone_chunk_mib", self.rclone_chunk_combo.currentData())
        self.settings.setValue("rclone_cutoff_mib", self.rclone_cutoff_combo.currentData())
        self.settings.setValue("rclone_streams", self.rclone_streams_combo.currentData())
        self.settings.setValue("rclone_transfers", self.rclone_transfers_combo.currentData())
        self.settings.setValue("rclone_checkers", self.rclone_checkers_combo.currentData())
        self.settings.setValue("rclone_buffer_mib", self.rclone_buffer_combo.currentData())
        self.settings.setValue(
            "rclone_write_buffer_mib", self.rclone_write_buffer_combo.currentData()
        )
        self.settings.setValue("rclone_retries", self.rclone_retries_combo.currentData())
        self.settings.setValue("rclone_low_retries", self.rclone_low_retries_combo.currentData())
        self.settings.setValue("rclone_checksum", self.rclone_checksum_check.isChecked())
        self.settings.setValue("rclone_no_sparse", self.rclone_no_sparse_check.isChecked())
        self.settings.setValue("file_display", self.file_display_combo.currentData())
        self.settings.setValue("compact_rows", self.compact_check.isChecked())
        self.settings.setValue("show_source_links", self.show_source_links_check.isChecked())
        self.settings.setValue(
            "show_destination_links", self.show_destination_links_check.isChecked()
        )
        self.settings.setValue("theme", self.theme_combo.currentData())
        self.settings.setValue("design_mode", self.design_mode_combo.currentData())
        self.settings.setValue("accent_color", self.accent_color)
        self.settings.setValue("accent_all_buttons", self.accent_all_buttons_check.isChecked())
        self.settings.setValue("contextual_buttons", self.contextual_buttons_check.isChecked())
        self.settings.setValue("animations", self.animations_check.isChecked())
        self.settings.setValue("update_mode", self.update_mode_combo.currentData())
        self.settings.setValue("tray_enabled", self.tray_check.isChecked())
        self.settings.setValue("continue_in_tray", self.continue_in_tray_check.isChecked())
        self.settings.setValue(
            "keep_open_after_finish", self.keep_open_after_finish_check.isChecked()
        )
        self.settings.setValue("windows_startup", self.windows_startup_check.isChecked())
        self.settings.setValue(
            "auto_system_health", self.auto_system_health_check.isChecked()
        )
        self.settings.setValue("notifications", self.notifications_check.isChecked())
        self.settings.setValue("auto_start", self.auto_start_check.isChecked())
        self.settings.setValue("smart_terminal", self.smart_terminal_check.isChecked())
        self.settings.setValue(
            "auto_rclone_monitor", self.auto_rclone_monitor_check.isChecked()
        )
        self.settings.setValue("cleanup_logs", self.cleanup_logs_check.isChecked())
        self.settings.setValue("log_retention_days", self.log_retention_combo.currentData())
        self.settings.setValue("destination", self.destination.text())
        self.settings.setValue("sources", self.sources.toPlainText())
        self.settings.setValue("upload_destination", self.upload_destination.text())
        self.settings.setValue("upload_sources", self.upload_sources.toPlainText())
        active_panel = self.current_transfer_panel()
        self.settings.setValue(
            "transfer_preset", active_panel.preset_combo.currentData() or "optimal"
        )
        self.settings.sync()

    def settings_changed(self, *_args) -> None:
        sender = self.sender()
        rclone_tuning_controls = (
            self.rclone_chunk_combo,
            self.rclone_cutoff_combo,
            self.rclone_streams_combo,
            self.rclone_transfers_combo,
            self.rclone_checkers_combo,
            self.rclone_buffer_combo,
            self.rclone_write_buffer_combo,
        )
        if sender is self.rclone_performance_combo:
            self.apply_rclone_performance_profile()
        elif sender in rclone_tuning_controls and not self._applying_rclone_profile:
            manual_index = self.rclone_performance_combo.findData("manual")
            if manual_index >= 0 and self.rclone_performance_combo.currentIndex() != manual_index:
                self.rclone_performance_combo.blockSignals(True)
                self.rclone_performance_combo.setCurrentIndex(manual_index)
                self.rclone_performance_combo.blockSignals(False)
        self.update_rclone_performance_note()
        if sender is self.copy_engine_combo:
            engine = str(
                self.copy_engine_combo.currentData()
                or ("rclone" if is_macos() else "robocopy")
            )
            if engine in ("rclone", "hybrid"):
                sequential = self.download_mode_combo.findData("sequential")
                if sequential >= 0:
                    self.download_mode_combo.setCurrentIndex(sequential)
        if sender is self.accent_combo:
            self.accent_color = str(self.accent_combo.currentData())
        if sender is self.contextual_buttons_check and self.contextual_buttons_check.isChecked():
            self.accent_all_buttons_check.blockSignals(True)
            self.accent_all_buttons_check.setChecked(False)
            self.accent_all_buttons_check.blockSignals(False)
        elif sender is self.accent_all_buttons_check and self.accent_all_buttons_check.isChecked():
            self.contextual_buttons_check.blockSignals(True)
            self.contextual_buttons_check.setChecked(False)
            self.contextual_buttons_check.blockSignals(False)
        if sender is self.monthly_stats_reset_check:
            self.set_monthly_transfer_stats_reset(
                self.monthly_stats_reset_check.isChecked()
            )
        if sender is self.windows_startup_check:
            self.apply_windows_startup_setting()
        if sender is self.navigation_mode_combo:
            mode = str(self.navigation_mode_combo.currentData() or "side")
            self.sidebar_expanded = mode != "side_compact"
        if sender is self.window_size_combo:
            self.apply_window_size_mode()
        self.persist_settings()
        self.settings_dirty = True
        self.update_settings_visibility()
        self.apply_theme()
        self.refresh_file_rows("download")
        self.refresh_file_rows("upload")
        if sender in (self.tray_check, self.notifications_check):
            self.setup_tray()

    def windows_startup_command(self) -> str:
        if getattr(sys, "frozen", False):
            arguments = [str(Path(sys.executable).resolve()), "--startup"]
        else:
            arguments = [sys.executable, str(resource_path("main.py")), "--startup"]
        return shlex.join(arguments) if is_macos() else subprocess.list2cmdline(arguments)

    def apply_windows_startup_setting(self) -> None:
        enabled = self.windows_startup_check.isChecked()
        try:
            set_startup_enabled(enabled, self.windows_startup_command())
        except OSError as exc:
            self.windows_startup_check.blockSignals(True)
            self.windows_startup_check.setChecked(not enabled)
            self.windows_startup_check.blockSignals(False)
            QMessageBox.warning(
                self,
                APP_NAME,
                f"Не удалось изменить автозапуск системы:\n{exc}",
            )

    @staticmethod
    def set_combo_data(combo: QComboBox, value: int) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def apply_rclone_performance_profile(self) -> None:
        profile_name = str(self.rclone_performance_combo.currentData() or "manual")
        profile = RCLONE_PERFORMANCE_PROFILES.get(profile_name)
        if profile is None:
            return
        controls = (
            (self.rclone_chunk_combo, profile["chunk"]),
            (self.rclone_cutoff_combo, profile["cutoff"]),
            (self.rclone_streams_combo, profile["streams"]),
            (self.rclone_transfers_combo, profile["transfers"]),
            (self.rclone_checkers_combo, profile["checkers"]),
            (self.rclone_buffer_combo, profile["buffer"]),
            (self.rclone_write_buffer_combo, profile["write_buffer"]),
        )
        self._applying_rclone_profile = True
        try:
            for combo, value in controls:
                combo.blockSignals(True)
                self.set_combo_data(combo, value)
                combo.blockSignals(False)
        finally:
            self._applying_rclone_profile = False

    def update_rclone_performance_note(self) -> None:
        if not hasattr(self, "rclone_profile_note"):
            return
        profile = str(self.rclone_performance_combo.currentData() or "manual")
        notes = {
            "balanced": "Для обычной работы: умеренная нагрузка на память, диск и Google Drive.",
            "fast": "Быстрый профиль: до 8 потоков на файл и 8 файлов внутри одной папки.",
            "maximum": "Максимальный профиль: до 16 потоков и усиленные буферы. Рекомендуется для канала 1 Гбит/с.",
            "extreme": "Высокая нагрузка: до 32 потоков, большой расход RAM и нагрузка на Google Drive. Используйте только на быстром ПК.",
            "manual": "Ручной профиль: значения ниже изменены отдельно и будут сохранены.",
        }
        self.rclone_profile_note.setText(notes.get(profile, notes["manual"]))
        self.rclone_profile_note.setProperty("warning", profile == "extreme")
        self.rclone_profile_note.style().unpolish(self.rclone_profile_note)
        self.rclone_profile_note.style().polish(self.rclone_profile_note)

    def apply_window_size_mode(self) -> None:
        if not hasattr(self, "window_size_combo"):
            return
        mode = str(self.window_size_combo.currentData() or "standard")
        if mode == "auto":
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                self.resize(
                    min(1280, max(self.minimumWidth(), int(available.width() * 0.88))),
                    min(850, max(self.minimumHeight(), int(available.height() * 0.88))),
                )
            return
        if mode in WINDOW_SIZE_PRESETS:
            width, height = WINDOW_SIZE_PRESETS[mode]
            if self.isMaximized():
                self.showNormal()
            self.resize(width, height)
            return
        width = self.settings.value("window_width", self.width(), type=int)
        height = self.settings.value("window_height", self.height(), type=int)
        self.resize(max(self.minimumWidth(), width), max(self.minimumHeight(), height))

    def update_settings_visibility(self) -> None:
        self.set_advanced_mode_visible(self.advanced_mode_check.isChecked())
        self.set_files_tab_visible(self.files_tab_check.isChecked())
        self.apply_navigation_layout(animate=False)
        engine = str(self.copy_engine_combo.currentData() or "robocopy")
        single_rclone_process = engine in ("rclone", "hybrid")
        self.download_mode_combo.setEnabled(not single_rclone_process and not self.running)
        self.download_mode_combo.setToolTip(
            "Для Rclone используется только один процесс: файлы идут строго по очереди."
            if single_rclone_process
            else ""
        )
        limited = (
            self.download_mode_combo.currentData() == "limited"
            and not self.running
            and not single_rclone_process
        )
        self.concurrency_controls.setEnabled(limited)
        self.concurrency_controls.setToolTip(
            ""
            if limited
            else (
                "Для Rclone используется только один процесс."
                if single_rclone_process
                else "Число файлов задаётся только в ограниченном режиме."
            )
        )
        profile = str(self.copy_profile_combo.currentData() or "optimized")
        profile_notes = {
            "stable": (
                "Robocopy использует /Z: оборванный файл продолжается с сохранённого места. "
                "Внутри одной папки файлы копируются последовательно."
            ),
            "optimized": (
                "Рекомендуемый режим: /Z сохраняет докачку, а /MT реально копирует несколько "
                "файлов выбранной папки параллельно. Для одного большого файла остаётся /J."
            ),
            "maximum": (
                "Максимальная скорость: /MT для папок и короткие повторы. /Z отключён, поэтому "
                "после обрыва текущий незавершённый файл может начаться заново."
            ),
            "turbo": (
                "Для одного большого файла приложение читает несколько независимых участков "
                "одновременно. Это может сильнее загрузить канал Google Drive. Завершённые "
                "сегменты сохраняются для докачки; для папок используется быстрый Robocopy."
            ),
        }
        self.performance_note.setText(profile_notes.get(profile, profile_notes["optimized"]))
        threaded_profile = profile in ("optimized", "maximum", "turbo") and not self.running
        self.directory_threads_controls.setEnabled(threaded_profile)
        turbo_profile = profile == "turbo" and not self.running
        self.turbo_threads_controls.setEnabled(turbo_profile)
        self.turbo_threads_controls.setToolTip(
            "" if turbo_profile else "Число сегментов задаётся только для профиля «Турбо»."
        )
        self.directory_threads_controls.setToolTip(
            "" if threaded_profile else "Число внутренних потоков используется в ускоренных профилях."
        )
        rclone_selected = engine in ("rclone", "hybrid") and not self.running
        self.rclone_performance_combo.setEnabled(rclone_selected)
        self.rclone_controls.setEnabled(rclone_selected)
        self.rclone_controls.setToolTip(
            "" if rclone_selected else "Параметры используются в режимах Rclone и «Совместный»."
        )
        self.set_toggle_available(
            self.rclone_checksum_check,
            rclone_selected,
            "Сначала выберите Rclone или совместный режим.",
        )
        self.set_toggle_available(
            self.rclone_no_sparse_check,
            rclone_selected,
            "Сначала выберите Rclone или совместный режим.",
        )
        self.update_rclone_performance_note()
        self.refresh_engine_status()
        self.manual_update_card.setEnabled(True)
        self.manual_update_card.setToolTip("")
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

    def apply_navigation_layout(self, animate: bool = False) -> None:
        if not hasattr(self, "sidebar") or not hasattr(self, "tabs"):
            return
        mode = str(self.navigation_mode_combo.currentData() or "side")
        self.tabs.tabBar().hide()
        self.navigation_toggle_button.show()
        expanded = self.sidebar_expanded and mode != "side_compact"
        self.set_navigation_panel_expanded(expanded, animate=animate)

    def set_navigation_panel_expanded(self, expanded: bool, animate: bool = True) -> None:
        if not hasattr(self, "sidebar"):
            return
        self.sidebar_expanded = bool(expanded)
        self.navigation_toggle_button.setText("‹" if expanded else "≡")
        self.navigation_toggle_button.setToolTip(
            "Свернуть боковую панель" if expanded else "Показать боковую панель"
        )
        animations_enabled = (
            animate
            and hasattr(self, "animations_check")
            and self.animations_check.isChecked()
        )
        if self.sidebar_animation is not None:
            self.sidebar_animation.stop()
        start_width = self.sidebar.width()
        end_width = 220 if expanded else 66
        self.sidebar.setMinimumWidth(66)
        self.sidebar_brand.setVisible(expanded)
        self.sidebar_version.setVisible(expanded)
        self.global_rclone_status.setVisible(expanded)
        self.sidebar_transfer_stats.setVisible(expanded)
        self.new_transfer_button.setText("＋ Новая передача" if expanded else "＋")
        self.settings_gear_button.setText("⚙  Настройки" if expanded else "⚙")
        for _page, icon, label in self.sidebar_button_specs:
            button = self.sidebar_page_buttons[_page]
            button.setText(f"{icon}   {label}" if expanded else icon)
        if not animations_enabled:
            self.sidebar.setMinimumWidth(end_width)
            self.sidebar.setMaximumWidth(end_width)
            return
        animation = QPropertyAnimation(self.sidebar, b"maximumWidth", self)
        animation.setDuration(300)
        animation.setStartValue(start_width)
        animation.setEndValue(end_width)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        animation.finished.connect(lambda: self.sidebar.setMinimumWidth(end_width))
        self.sidebar_animation = animation
        animation.start()

    def toggle_navigation_panel(self) -> None:
        self.set_navigation_panel_expanded(not self.sidebar_expanded, animate=True)
        self.settings.setValue("sidebar_expanded", self.sidebar_expanded)
        self.settings.sync()

    def set_advanced_mode_visible(self, visible: bool) -> None:
        if not hasattr(self, "advanced_page"):
            return
        self.advanced_mode_visible = bool(visible)
        self.advanced_tab_index = -1
        self.refresh_tab_indexes()
        for panel in self.transfer_panels.values():
            panel.terminal_card.setVisible(bool(visible))

    def refresh_tab_indexes(self) -> None:
        for attribute, page_attribute in (
            ("download_tab_index", "home_page"),
            ("files_tab_index", "files_page"),
            ("profiles_tab_index", "profiles_page"),
            ("settings_tab_index", "settings_page"),
            ("advanced_tab_index", "advanced_page"),
            ("updates_tab_index", "updates_page"),
        ):
            page = getattr(self, page_attribute, None)
            setattr(self, attribute, self.tabs.indexOf(page) if page is not None else -1)
        self.upload_tab_index = -1

    def tab_key(self, page: QWidget | None) -> str:
        for key, candidate in (
            ("home", self.home_page),
            ("files", self.files_page),
            ("profiles", self.profiles_page),
            ("settings", self.settings_page),
            ("advanced", self.advanced_page),
            ("updates", self.updates_page),
        ):
            if page is candidate:
                return key
        return "home"

    @Slot(int)
    def remember_active_tab(self, index: int) -> None:
        if not hasattr(self, "settings") or index < 0:
            return
        self.settings.setValue("active_tab", self.tab_key(self.tabs.widget(index)))

    def restore_active_tab(self) -> None:
        key = str(self.settings.value("active_tab", "home") or "home")
        pages = {
            "home": self.home_page,
            "download": self.home_page,
            "upload": self.home_page,
            "files": self.files_page,
            "profiles": self.profiles_page,
            "settings": self.settings_page,
            "advanced": self.advanced_page,
            "updates": self.updates_page,
        }
        page = pages.get(key, self.home_page)
        if self.tabs.indexOf(page) < 0:
            page = self.settings_page if key in ("files", "advanced") else self.home_page
        self.tabs.setCurrentWidget(page)
        if page is self.home_page:
            direction = str(
                self.settings.value(
                    "home_transfer_direction",
                    "upload" if key == "upload" else "download",
                )
            )
            self.show_transfer_direction(direction, switch_to_home=False)
        self.settings.setValue("active_tab", self.tab_key(page))
        if hasattr(self, "settings_gear_button") and page is self.settings_page:
            self.settings_gear_button.setToolTip("Вернуться к передаче")

    def set_files_tab_visible(self, visible: bool) -> None:
        if not hasattr(self, "files_page"):
            return
        current_index = self.tabs.indexOf(self.files_page)
        if visible and current_index < 0:
            insert_at = self.tabs.indexOf(self.home_page) + 1
            self.tabs.insertTab(insert_at, self.files_page, "Файлы")
        elif not visible and current_index >= 0:
            if self.tabs.currentWidget() is self.files_page:
                self.tabs.setCurrentWidget(self.settings_page)
            self.tabs.removeTab(current_index)
        self.files_tab_visible = bool(visible)
        if hasattr(self, "sidebar_files_button"):
            self.sidebar_files_button.setVisible(bool(visible))
        self.refresh_tab_indexes()
        if visible:
            self.refresh_files_overview()

    def browse_rclone_executable(self) -> None:
        file_filter = (
            "Rclone (rclone);;Исполняемые файлы (*)"
            if is_macos()
            else "rclone.exe (rclone.exe);;Программы (*.exe);;Все файлы (*)"
        )
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите Rclone",
            self.rclone_path_edit.text().strip(),
            file_filter,
        )
        if selected:
            self.rclone_path_edit.setText(selected)
            self.settings_changed()

    def start_rclone_install(self) -> None:
        if self.rclone_install_thread is not None and self.rclone_install_thread.isRunning():
            return
        if any(isinstance(worker, RcloneDownloader) for worker in self.workers.values()):
            QMessageBox.warning(
                self,
                APP_NAME,
                "Rclone сейчас используется. Остановите загрузку или выгрузку, "
                "дождитесь завершения процесса и повторите обновление.",
            )
            return
        self._stop_orphaned_rclone_processes()
        self.download_rclone_button.setEnabled(False)
        self.download_rclone_button.setText("Переустановка…")
        self.rclone_install_progress.setValue(0)
        self.rclone_install_progress.setFormat("Подготовка загрузки Rclone…")
        self.rclone_install_progress.show()
        thread = RcloneInstallThread(self)
        self.rclone_install_thread = thread
        thread.progress.connect(self.rclone_install_progressed)
        thread.succeeded.connect(self.rclone_install_succeeded)
        thread.failed.connect(self.rclone_install_failed)
        thread.finished.connect(self.rclone_install_finished)
        thread.start()

    @Slot(int, str)
    def rclone_install_progressed(self, percent: int, message: str) -> None:
        self.rclone_install_progress.setValue(max(0, min(100, percent)))
        self.rclone_install_progress.setFormat(f"{message}  ·  {percent}%")

    @Slot(str, str)
    def rclone_install_succeeded(self, path: str, version: str) -> None:
        self.rclone_path_edit.setText(path)
        self.settings.setValue("rclone_path", path)
        self.settings.sync()
        self.rclone_install_progress.setValue(100)
        self.rclone_install_progress.setFormat(f"Rclone {version} скачан и подключён")
        self.refresh_engine_status()
        QMessageBox.information(
            self,
            APP_NAME,
            f"Rclone {version} скачан из официального источника, проверен по SHA-256 "
            f"и подключён к приложению.\n\n{path}",
        )

    @Slot(str)
    def rclone_install_failed(self, message: str) -> None:
        self.rclone_install_progress.setFormat("Не удалось подключить Rclone")
        QMessageBox.critical(self, APP_NAME, f"Не удалось скачать Rclone:\n{message}")

    @Slot()
    def rclone_install_finished(self) -> None:
        self.download_rclone_button.setEnabled(True)
        self.download_rclone_button.setText("Переустановить Rclone")
        self.rclone_install_thread = None
        self.refresh_engine_status()

    def active_google_remote(self) -> str:
        if hasattr(self, "google_account_combo"):
            selected = str(self.google_account_combo.currentData() or "")
            if selected:
                return selected
        saved = str(self.settings.value("active_google_drive_remote", "") or "")
        remotes = {account.remote_name for account in google_drive_accounts()}
        if saved in remotes:
            return saved
        if remotes:
            return next(
                (account.remote_name for account in google_drive_accounts() if account.remote_name == GOOGLE_DRIVE_REMOTE),
                next(iter(remotes)),
            )
        return GOOGLE_DRIVE_REMOTE

    def google_drive_is_connected(self) -> bool:
        return google_drive_connected(remote_name=self.active_google_remote())

    @Slot(int)
    def set_active_google_account(self, _index: int = -1) -> None:
        if self._refreshing_google_accounts or not hasattr(self, "google_account_combo"):
            return
        remote_name = str(self.google_account_combo.currentData() or "")
        if remote_name:
            self.settings.setValue("active_google_drive_remote", remote_name)
            self.settings.sync()
        self.refresh_google_drive_status()

    def refresh_google_drive_status(self) -> None:
        if not hasattr(self, "google_drive_status"):
            return
        accounts = google_drive_accounts()
        saved = str(self.settings.value("active_google_drive_remote", "") or "")
        selected = saved if any(account.remote_name == saved for account in accounts) else ""
        if not selected and accounts:
            selected = next(
                (account.remote_name for account in accounts if account.remote_name == GOOGLE_DRIVE_REMOTE),
                accounts[0].remote_name,
            )
        self._refreshing_google_accounts = True
        self.google_account_combo.blockSignals(True)
        self.google_account_combo.clear()
        kind_names = {"personal": "Личный", "workspace": "Workspace", "team": "Команда"}
        for account in accounts:
            detail = account.email if account.email and account.email != account.label else kind_names.get(account.kind, "Google")
            self.google_account_combo.addItem(f"{account.label} · {detail}", account.remote_name)
        index = self.google_account_combo.findData(selected)
        if index >= 0:
            self.google_account_combo.setCurrentIndex(index)
            self.settings.setValue("active_google_drive_remote", selected)
        self.google_account_combo.blockSignals(False)
        self._refreshing_google_accounts = False
        connected = bool(selected and google_drive_connected(remote_name=selected))
        active = next((account for account in accounts if account.remote_name == selected), None)
        self.google_drive_status.setText(
            f"● {active.label}" if connected and active else "Не подключён"
        )
        self.google_drive_status.setProperty("ready", connected)
        self.google_drive_status.style().unpolish(self.google_drive_status)
        self.google_drive_status.style().polish(self.google_drive_status)
        busy = self.google_drive_oauth_thread is not None
        self.google_drive_add_button.setEnabled(not busy)
        self.google_account_combo.setEnabled(bool(accounts) and not busy)
        self.google_account_kind_combo.setEnabled(not busy)
        self.google_drive_connect_button.setEnabled(connected and not busy)
        self.google_drive_connect_button.setText(
            "Переподключить выбранный" if connected else "Сначала добавьте аккаунт"
        )
        self.google_drive_disconnect_button.setEnabled(connected and not busy)
        upload_button = getattr(self.transfer_panels.get("upload"), "google_drive_button", None)
        if upload_button is not None:
            upload_button.setText("Google Drive ✓" if connected else "Google Drive")

    def use_or_connect_google_drive(self) -> None:
        if self.running:
            return
        current = self.upload_destination.text().strip()
        if is_rclone_remote_path(current):
            current = str(self.settings.value("google_explorer_destination/upload", ""))
        folder = QFileDialog.getExistingDirectory(
            self,
            "Сначала выберите конечную папку Google Drive",
            current,
        )
        if folder:
            self.accept_destination_folder("upload", folder, force_cloud=True)

    def choose_cloud_destination(self, direction: str, original: str) -> bool:
        """Compatibility entry point: selection now always happens in Explorer."""
        if self.running:
            return False
        if virtual_drive_parts(original):
            return self.accept_destination_folder(direction, original, force_cloud=True)
        panel = self.transfer_panels[direction]
        start = str(self.settings.value(f"google_explorer_destination/{direction}", ""))
        folder = QFileDialog.getExistingDirectory(
            self, "Сначала выберите конечную папку Google Drive", start
        )
        return bool(folder and self.accept_destination_folder(direction, folder, force_cloud=True))

    def start_google_drive_oauth(self, add_new: bool = False) -> None:
        if self.google_drive_oauth_thread is not None:
            return
        if self.running:
            QMessageBox.warning(self, APP_NAME, "Дождитесь завершения текущей передачи.")
            return
        executable = self.resolved_rclone_executable()
        if not executable:
            QMessageBox.critical(
                self,
                APP_NAME,
                "Встроенный Rclone не найден. Сначала нажмите «Переустановить Rclone».",
            )
            return
        accounts = {account.remote_name: account for account in google_drive_accounts()}
        if add_new or not accounts:
            remote_name = (
                GOOGLE_DRIVE_REMOTE if not accounts else new_google_drive_remote_name()
            )
            label = ""
            kind = str(self.google_account_kind_combo.currentData() or "personal")
        else:
            remote_name = self.active_google_remote()
            account = accounts.get(remote_name)
            label = account.label if account else ""
            kind = account.kind if account else "personal"
        thread = GoogleDriveOAuthThread(
            executable,
            remote_name=remote_name,
            label=label,
            kind=kind,
            parent=self,
        )
        self.google_drive_oauth_thread = thread
        thread.progress.connect(self.google_drive_oauth_progressed)
        thread.succeeded.connect(self.google_drive_oauth_succeeded)
        thread.failed.connect(self.google_drive_oauth_failed)
        thread.finished.connect(self.google_drive_oauth_finished)
        self.google_drive_status.setText("Ожидание Google…")
        self.google_drive_add_button.setEnabled(False)
        self.google_drive_connect_button.setEnabled(False)
        thread.start()

    @Slot(str)
    def google_drive_oauth_progressed(self, message: str) -> None:
        self.google_drive_status.setText("Ожидание подтверждения…")
        self.append_log(f"Google Drive OAuth2: {message}\n")

    @Slot(str, str)
    def google_drive_oauth_succeeded(
        self, _config_path: str, remote_name: str = GOOGLE_DRIVE_REMOTE
    ) -> None:
        self.settings.setValue("active_google_drive_remote", remote_name)
        self.settings.sync()
        self._shared_drive_ids_cache.pop(remote_name, None)
        self.settings.remove("google_shared_drive_id")
        self.append_log("Google Drive OAuth2: аккаунт подключён напрямую к Neon.\n")
        QMessageBox.information(
            self,
            APP_NAME,
            "Google Drive подключён к Neon Rclone. Выбранный в Проводнике путь сохранён; "
            "облачный поиск папок не запускается.",
        )

    @Slot(str)
    def google_drive_oauth_failed(self, message: str) -> None:
        self._cloud_picker_request = None
        self._start_after_google_oauth = None
        self.append_log(f"Google Drive OAuth2: авторизация не завершена — {message}\n")
        QMessageBox.critical(self, APP_NAME, f"Не удалось подключить Google Drive:\n{message}")

    @Slot()
    def google_drive_oauth_finished(self) -> None:
        self.google_drive_oauth_thread = None
        self.refresh_google_drive_status()
        request = self._cloud_picker_request
        self._cloud_picker_request = None
        start_direction = self._start_after_google_oauth
        self._start_after_google_oauth = None
        if request and self.google_drive_is_connected():
            direction, path = request
            self.transfer_panels[direction].destination.setText(path)
            self.append_log("Google Drive: конечный путь из Проводника сохранён без облачного поиска.\n")
        if start_direction and self.google_drive_is_connected():
            QTimer.singleShot(0, lambda: self.start_transfers(start_direction))

    def disconnect_google_drive_account(self) -> None:
        if self.running:
            QMessageBox.warning(self, APP_NAME, "Дождитесь завершения текущей передачи.")
            return
        answer = QMessageBox.question(
            self,
            APP_NAME,
            "Отключить Google Drive от Neon? Файлы в облаке останутся на месте.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        remote_name = self.active_google_remote()
        account = next(
            (item for item in google_drive_accounts() if item.remote_name == remote_name),
            None,
        )
        disconnect_google_drive(remote_name=remote_name)
        self._shared_drive_ids_cache.pop(remote_name, None)
        self.settings.remove("active_google_drive_remote")
        if is_rclone_remote_path(self.upload_destination.text()):
            self.upload_destination.clear()
            self.settings.setValue("upload_destination", "")
        self.refresh_google_drive_status()
        self.append_log(
            f"Google Drive OAuth2: подключение {account.label if account else remote_name} удалено.\n"
        )

    def _stop_orphaned_rclone_processes(self) -> int:
        """Stop only stale Rclone processes that belong to this Neon installation."""
        candidates = {
            os.path.normcase(str(path.resolve()))
            for path in (installed_rclone_path(), bundled_rclone_path())
            if path is not None
        }
        stopped = 0
        for process in psutil.process_iter(("pid", "exe", "name")):
            try:
                executable = process.info.get("exe") or ""
                if not executable or os.path.normcase(str(Path(executable).resolve())) not in candidates:
                    continue
                process.terminate()
                process.wait(timeout=3)
                stopped += 1
            except (psutil.Error, OSError):
                continue
        return stopped

    def set_system_health_state(self, state: str, text: str) -> None:
        self.system_health_status.setText(text)
        self.system_health_status.setProperty("state", state)
        self.system_health_status.style().unpolish(self.system_health_status)
        self.system_health_status.style().polish(self.system_health_status)

    def maybe_auto_system_health_check(self) -> None:
        if self.auto_system_health_check.isChecked() and not self.running and self.cloud_browser is None:
            self.start_system_health_check(silent=True)

    def start_system_health_check(self, silent: bool = False) -> None:
        if self.cloud_browser is not None or self.google_drive_oauth_thread is not None:
            return
        if self.system_health_thread is not None and self.system_health_thread.isRunning():
            return
        if self.running or self.workers or self.turbo_workers:
            QMessageBox.warning(
                self,
                APP_NAME,
                "Дождитесь завершения загрузки или выгрузки. Диагностика может "
                "переустановить Rclone, поэтому она запускается только без активных задач.",
            )
            return
        if self.rclone_install_thread is not None and self.rclone_install_thread.isRunning():
            QMessageBox.information(
                self,
                APP_NAME,
                "Установка Rclone уже выполняется. Дождитесь её завершения и повторите проверку.",
            )
            return

        self.system_health_silent = bool(silent)
        self.persist_settings()
        self.system_health_button.setEnabled(False)
        self.system_health_button.setText("ПРОВЕРКА…")
        self.system_health_progress.setValue(0)
        self.system_health_progress.setFormat("Подготовка диагностики…")
        self.system_health_progress.show()
        self.system_health_summary.setText(
            "Не закрывайте приложение: идёт проверка и безопасное восстановление компонентов."
        )
        self.set_system_health_state("running", "◌  ИДЁТ ПОЛНАЯ ПРОВЕРКА")
        sources = [
            line.strip()
            for line in (
                self.sources.toPlainText().splitlines()
                + self.upload_sources.toPlainText().splitlines()
            )
            if line.strip()
        ]
        thread = SystemHealthThread(
            app_root=app_data_dir(),
            rclone_candidate=self.resolved_rclone_executable(),
            download_destination=self.destination.text().strip(),
            upload_destination=self.upload_destination.text().strip(),
            sources=sources,
            parent=self,
        )
        self.system_health_thread = thread
        thread.progress.connect(self.system_health_progressed)
        thread.succeeded.connect(self.system_health_succeeded)
        thread.failed.connect(self.system_health_failed)
        thread.finished.connect(self.system_health_finished)
        thread.start()

    @Slot(int, str)
    def system_health_progressed(self, percent: int, message: str) -> None:
        self.system_health_progress.setValue(max(0, min(100, percent)))
        self.system_health_progress.setFormat(f"{message}  ·  {percent}%")

    @Slot(object)
    def system_health_succeeded(self, report: SystemHealthReport) -> None:
        if report.rclone_path:
            self.rclone_path_edit.setText(report.rclone_path)
            self.settings.setValue("rclone_path", report.rclone_path)
            self.settings.sync()
        self.refresh_engine_status()
        if report.error_count:
            state = "error"
            title = "!  ТРЕБУЕТСЯ ВНИМАНИЕ"
        elif report.warning_count:
            state = "warning"
            title = "●  ПРОВЕРЕНО · ЕСТЬ РЕКОМЕНДАЦИИ"
        else:
            state = "ok"
            title = "✓  СИСТЕМА ИСПРАВНА"
        self.set_system_health_state(state, title)
        self.system_health_summary.setText(
            f"Проверок: {len(report.items)} · исправлено: {report.fixed_count} · "
            f"предупреждений: {report.warning_count} · ошибок: {report.error_count}."
        )
        icons = {"ok": "✓", "fixed": "↻", "warning": "!", "error": "✕"}
        lines = [
            f"{icons.get(item.status, '•')} {item.name}: {item.details}"
            for item in report.items
        ]
        self.append_log("\nСИСТЕМНАЯ ДИАГНОСТИКА\n" + "\n".join(lines) + "\n")
        message = self.system_health_summary.text() + "\n\n" + "\n".join(lines)
        if not self.system_health_silent:
            if report.error_count:
                QMessageBox.critical(self, "Диагностика Neon Drive", message)
            elif report.warning_count:
                QMessageBox.warning(self, "Диагностика Neon Drive", message)
            else:
                QMessageBox.information(self, "Диагностика Neon Drive", message)

    @Slot(str)
    def system_health_failed(self, message: str) -> None:
        self.set_system_health_state("error", "✕  ПРОВЕРКА ПРЕРВАНА")
        self.system_health_summary.setText(message)
        self.append_log(f"Системная диагностика: {message}\n")
        if not self.system_health_silent:
            QMessageBox.critical(
                self,
                "Диагностика Neon Drive",
                f"Не удалось завершить диагностику:\n{message}",
            )

    @Slot()
    def system_health_finished(self) -> None:
        self.system_health_progress.setValue(100)
        self.system_health_button.setEnabled(True)
        self.system_health_button.setText("ПРОВЕРИТЬ ЕЩЁ РАЗ")
        self.system_health_thread = None
        self.system_health_silent = False

    def resolved_rclone_executable(self) -> str | None:
        custom = self.rclone_path_edit.text().strip() if hasattr(self, "rclone_path_edit") else ""
        if custom:
            path = Path(custom).expanduser()
            if path.is_file():
                return str(path)
        managed = installed_rclone_path()
        if managed:
            return str(managed)
        bundled = bundled_rclone_path()
        if bundled:
            return str(bundled)
        return (
            shutil.which("rclone")
            if is_macos()
            else shutil.which("rclone.exe") or shutil.which("rclone")
        )

    def refresh_engine_status(self) -> None:
        if not hasattr(self, "engine_status"):
            return
        robocopy_ready = False if is_macos() else bool(shutil.which("robocopy.exe"))
        rclone_path = self.resolved_rclone_executable()
        engine = str(
            self.copy_engine_combo.currentData()
            or ("rclone" if is_macos() else "robocopy")
        )
        required_ready = robocopy_ready if engine == "robocopy" else bool(rclone_path)
        if engine == "hybrid":
            required_ready = robocopy_ready and bool(rclone_path)
        robo_text = "готов" if robocopy_ready else "не найден"
        managed_version = installed_rclone_version() or bundled_rclone_version()
        managed_paths = {path for path in (installed_rclone_path(), bundled_rclone_path()) if path}
        rclone_text = (
            f"{managed_version} · подключён"
            if rclone_path and managed_version and Path(rclone_path) in managed_paths
            else Path(rclone_path).name if rclone_path else "не найден"
        )
        self.engine_status.setText(
            f"macOS · Rclone: {rclone_text}"
            if is_macos()
            else f"Robocopy: {robo_text}   ·   Rclone: {rclone_text}"
        )
        if hasattr(self, "download_rclone_button") and self.rclone_install_thread is None:
            self.download_rclone_button.setText("Переустановить Rclone")
        if hasattr(self, "global_rclone_status"):
            self.global_rclone_status.setText(
                "Rclone · встроен и готов" if rclone_path else "Rclone · требуется восстановление"
            )
        self.engine_status.setProperty("ready", required_ready)
        self.engine_status.style().unpolish(self.engine_status)
        self.engine_status.style().polish(self.engine_status)

    def selected_rclone_options(self) -> RcloneOptions:
        managed_config = managed_rclone_config_path()
        return RcloneOptions(
            drive_chunk_size_mib=int(self.drive_chunk_combo.currentData() or 64),
            chunk_size_mib=int(self.rclone_chunk_combo.currentData() or 64),
            multi_thread_cutoff_mib=int(self.rclone_cutoff_combo.currentData() or 256),
            multi_thread_streams=int(self.rclone_streams_combo.currentData() or 4),
            transfers=int(self.rclone_transfers_combo.currentData() or 4),
            checkers=int(self.rclone_checkers_combo.currentData() or 8),
            buffer_size_mib=int(self.rclone_buffer_combo.currentData() or 0),
            multi_thread_write_buffer_size_mib=int(
                self.rclone_write_buffer_combo.currentData() or 1
            ),
            retries=int(self.rclone_retries_combo.currentData() or 3),
            low_level_retries=int(self.rclone_low_retries_combo.currentData() or 10),
            checksum=self.rclone_checksum_check.isChecked(),
            local_no_sparse=self.rclone_no_sparse_check.isChecked(),
            config_path=str(managed_config) if managed_config.is_file() else None,
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
        engine = str(self.copy_engine_combo.currentData() or "robocopy")
        if engine in ("rclone", "hybrid"):
            return 1
        mode = self.download_mode_combo.currentData()
        if mode == "sequential":
            return 1
        if mode == "limited":
            return min(MAX_CONCURRENT_DOWNLOADS, max(1, self.concurrency_spin.value()))
        return min(MAX_CONCURRENT_DOWNLOADS, max(1, self.total_items))

    def effective_directory_threads(self) -> int:
        requested = self.directory_threads_slider.value()
        # Keep total Robocopy worker pressure bounded when several folders run at once.
        per_process_budget = max(2, 64 // max(1, self.max_concurrent_downloads()))
        return max(2, min(MAX_DIRECTORY_THREADS, requested, per_process_budget))

    def effective_turbo_threads(self) -> int:
        requested = self.turbo_threads_slider.value()
        # Independent cloud reads are useful, but dozens per active file can trigger
        # provider throttling. Keep the total pressure bounded across the queue.
        per_file_budget = max(2, 32 // max(1, self.max_concurrent_downloads()))
        return max(2, min(MAX_TURBO_THREADS, requested, per_file_budget))

    def effective_copy_profile(self) -> str:
        profile = str(self.copy_profile_combo.currentData() or "optimized")
        if self.active_transfer == "upload" and profile == "turbo":
            return "maximum"
        return profile

    def current_transfer_panel(self) -> TransferPanel:
        return self.transfer_panels[self.active_transfer]

    def bind_transfer_panel(self, direction: str) -> TransferPanel:
        panel = self.transfer_panels[direction]
        self.active_transfer = direction
        self.file_rows = panel.file_rows
        # Compatibility aliases keep transfer logic focused on the active page while
        # each page owns and displays its own status controls.
        self.start_button = panel.start_button
        self.state_label = panel.state_label
        self.footer_info = panel.footer_info
        self.ring = panel.ring
        self.progress_text = panel.progress_text
        self.progress = panel.progress
        self.eta = panel.eta
        self.speed = panel.speed
        return panel

    @Slot(int)
    def transfer_tab_changed(self, index: int) -> None:
        if self.running:
            return
        page = self.tabs.widget(index)
        if page is self.home_page:
            direction = (
                "upload"
                if self.home_transfer_stack.currentWidget() is self.upload_page
                and self.upload_addon_enabled
                else "download"
            )
            self.bind_transfer_panel(direction)
        else:
            return
        self.update_start_button()

    def update_start_button(self) -> None:
        self.transfer_panels["download"].start_button.setText("Начать передачу")
        self.transfer_panels["upload"].start_button.setText("Начать передачу")

    def start_current_transfer(self) -> None:
        self.start_transfers(self.active_transfer)

    @Slot(int)
    def animate_tab(self, index: int) -> None:
        previous_index = self._last_tab_index
        self._last_tab_index = index
        if not hasattr(self, "animations_check") or not self.animations_check.isChecked():
            return
        page = self.tabs.widget(index)
        self.animate_appearance(page, duration=320, start_opacity=0.55)
        mode = str(self.navigation_mode_combo.currentData() or "side")
        if mode != "top":
            direction = 1 if index >= previous_index else -1
            QTimer.singleShot(
                0,
                page,
                lambda selected=page, slide_direction=direction: self.animate_side_tab_slide(
                    selected, slide_direction
                ),
            )

    def animate_side_tab_slide(self, page: QWidget, direction: int = 1) -> None:
        if self.tab_slide_animation is not None:
            self.tab_slide_animation.stop()
        end_position = page.pos()
        offset = -22 if direction >= 0 else 22
        page.move(end_position + QPoint(offset, 0))
        animation = QPropertyAnimation(page, b"pos", self)
        animation.setDuration(280)
        animation.setStartValue(page.pos())
        animation.setEndValue(end_position)
        animation.setEasingCurve(QEasingCurve.Type.OutQuart)
        self.tab_slide_animation = animation
        self._animations.append(animation)

        def cleanup() -> None:
            try:
                page.move(end_position)
            except RuntimeError:
                pass
            if animation in self._animations:
                self._animations.remove(animation)
            if self.tab_slide_animation is animation:
                self.tab_slide_animation = None

        animation.finished.connect(cleanup)
        animation.start()

    def animate_appearance(
        self,
        widget: QWidget,
        duration: int = 240,
        start_opacity: float = 0.5,
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
        animation.setEasingCurve(QEasingCurve.Type.OutQuart)
        self._animations.append(animation)

        def discard() -> None:
            if animation in self._animations:
                self._animations.remove(animation)

        def cleanup() -> None:
            try:
                widget.setGraphicsEffect(None)
            except RuntimeError:
                pass
            discard()

        def widget_destroyed() -> None:
            try:
                animation.stop()
            except RuntimeError:
                pass
            discard()

        animation.finished.connect(cleanup)
        widget.destroyed.connect(widget_destroyed)
        if delay:
            # Qt 6.5 (macOS) lacks the context+callable singleShot overload.
            # Parenting the timer keeps the same lifetime/cancellation safety.
            timer = QTimer(widget)
            timer.setSingleShot(True)
            timer.timeout.connect(animation.start)
            timer.timeout.connect(timer.deleteLater)
            timer.start(delay)
        else:
            animation.start()

    def choose_destination_for(self, direction: str) -> bool:
        panel = self.transfer_panels[direction]
        title = "Выберите папку на Google Drive" if direction == "upload" else "Выберите папку"
        start = panel.destination.text()
        if is_rclone_remote_path(start):
            start = str(self.settings.value(f"google_explorer_destination/{direction}", ""))
        folder = QFileDialog.getExistingDirectory(self, title, start)
        if folder:
            return self.accept_destination_folder(direction, folder)
        return False

    def accept_destination_folder(
        self, direction: str, folder: str, force_cloud: bool = False
    ) -> bool:
        panel = self.transfer_panels[direction]
        mode = str(self.google_route_combo.currentData() or "ask")
        drive_path = virtual_drive_parts(folder)
        use_cloud = force_cloud or mode == "direct"
        if drive_path and mode == "ask" and not force_cloud:
            prompt = QMessageBox(self)
            prompt.setWindowTitle("Как передавать в Google Drive?")
            prompt.setText("Выбрана папка Google Drive:\n" + folder)
            prompt.setInformativeText(
                "Через Neon — выбранный путь передаётся Rclone без облачного поиска папок. "
                "Обычное копирование — файл затем синхронизирует клиент Google."
            )
            direct = prompt.addButton("Через Neon", QMessageBox.ButtonRole.AcceptRole)
            local = prompt.addButton("Обычное копирование", QMessageBox.ButtonRole.NoRole)
            prompt.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
            prompt.exec()
            if prompt.clickedButton() not in (direct, local):
                return False
            use_cloud = prompt.clickedButton() == direct
        panel.destination.setText(folder)
        key = "upload_destination" if direction == "upload" else "destination"
        self.settings.setValue(key, folder)
        direct_key = f"google_explorer_destination/{direction}"
        if drive_path and use_cloud:
            # Keep the human-readable Explorer path on screen. It is converted
            # to an Rclone destination only after Start is pressed.
            self.settings.setValue(direct_key, folder)
            panel.destination.setToolTip("Через Neon Rclone · без облачного поиска папок")
            rclone_index = self.copy_engine_combo.findData("rclone")
            if rclone_index >= 0:
                self.copy_engine_combo.setCurrentIndex(rclone_index)
            if not self.google_drive_is_connected():
                self._cloud_picker_request = (direction, folder)
                self.start_google_drive_oauth()
        else:
            self.settings.remove(direct_key)
            panel.destination.setToolTip("")
        self.persist_settings()
        self.refresh_file_rows(direction)
        return True

    def direct_google_destination(self, direction: str, value: str) -> bool:
        if not virtual_drive_parts(value):
            return False
        if str(self.google_route_combo.currentData() or "ask") == "direct":
            return True
        saved = str(self.settings.value(f"google_explorer_destination/{direction}", ""))
        return os.path.normcase(os.path.normpath(saved)) == os.path.normcase(os.path.normpath(value))

    def resolve_explorer_google_destination(self, value: str) -> str:
        parsed = virtual_drive_parts(value)
        if not parsed:
            raise ValueError("Сначала выберите конечную папку Google Drive в Проводнике.")
        kind, drive_name, _names = parsed
        remote_name = self.active_google_remote()
        shared_ids: dict[str, str] = {}
        if kind == "shared":
            target = explorer_shared_drive_target(value)
            if remote_name not in self._shared_drive_ids_cache:
                executable = self.resolved_rclone_executable()
                if not executable:
                    raise ValueError("Встроенный Rclone не найден. Переустановите его в настройках.")
                self._shared_drive_ids_cache[remote_name] = DriveClient(
                    executable, remote_name
                ).shared_drive_ids()
            shared_ids = self._shared_drive_ids_cache[remote_name]
            available_ids = set(shared_ids.values())
            if target is not None:
                if target.drive_id not in available_ids:
                    drive = Path(value).drive or value[:2]
                    raise SharedDriveAccessError(drive_name, drive)
                return remote_from_explorer_path(
                    value,
                    shared_ids,
                    shared_target=target,
                    remote_name=remote_name,
                )
            if drive_name.casefold() not in {
                name.casefold() for name in shared_ids
            }:
                drive = Path(value).drive or value[:2]
                raise SharedDriveAccessError(drive_name, drive)
        return remote_from_explorer_path(
            value, shared_ids, remote_name=remote_name
        )

    def choose_destination(self) -> bool:
        return self.choose_destination_for("download")

    def open_destination_folder_for(self, direction: str) -> None:
        panel = self.transfer_panels[direction]
        text = panel.destination.text().strip()
        if not text:
            if not self.choose_destination_for(direction):
                return
            text = panel.destination.text().strip()
        if is_rclone_remote_path(text):
            match = re.search(r",root_folder_id=([A-Za-z0-9_-]+):", text)
            address = ("https://drive.google.com/drive/folders/" + match.group(1)
                       if match and match.group(1) != "root" else "https://drive.google.com/drive/my-drive")
            QDesktopServices.openUrl(QUrl(address))
            return
        folder = Path(text).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, f"Не удалось открыть папку:\n{exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def open_destination_folder(self) -> None:
        self.open_destination_folder_for("download")

    def _append_sources_for(self, direction: str, paths: list[str]) -> None:
        panel = self.transfer_panels[direction]
        existing = [line.strip() for line in panel.sources.toPlainText().splitlines() if line.strip()]
        seen = {os.path.normcase(os.path.normpath(item)) for item in existing}
        for path in paths:
            normalized = os.path.normcase(os.path.normpath(path))
            if normalized not in seen:
                existing.append(path)
                seen.add(normalized)
        panel.sources.setPlainText("\n".join(existing))

    def _append_sources(self, paths: list[str]) -> None:
        self._append_sources_for("download", paths)

    def choose_files_for(self, direction: str) -> None:
        key = "last_upload_source_dir" if direction == "upload" else "last_source_dir"
        start = self.settings.value(key, "")
        files, _ = QFileDialog.getOpenFileNames(self, "Выберите файлы", start, "Все файлы (*)")
        if files:
            self.settings.setValue(key, str(Path(files[0]).parent))
            self._append_sources_for(direction, files)
            if direction == "download":
                self.maybe_auto_start()

    def choose_single_file_for(self, direction: str) -> None:
        if self.running:
            return
        key = "last_upload_source_dir" if direction == "upload" else "last_source_dir"
        selected, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл · заменить список", str(self.settings.value(key, "")), "Все файлы (*)"
        )
        if selected:
            self.settings.setValue(key, str(Path(selected).parent))
            self.transfer_panels[direction].sources.setPlainText(selected)
            if direction == "download":
                self.maybe_auto_start()

    def choose_files(self) -> None:
        self.choose_files_for("download")

    def choose_source_folder_for(self, direction: str) -> None:
        key = "last_upload_source_dir" if direction == "upload" else "last_source_dir"
        start = self.settings.value(key, "")
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку или диск", start)
        if folder:
            self.settings.setValue(key, folder)
            self._append_sources_for(direction, [folder])
            if direction == "download":
                self.maybe_auto_start()

    def choose_source_folder(self) -> None:
        self.choose_source_folder_for("download")

    def maybe_auto_start(self) -> None:
        if self.running or not self.auto_start_check.isChecked():
            return
        if not self.destination.text().strip() and not self.choose_destination():
            return
        QTimer.singleShot(150, self.start_downloads)

    def clear_file_rows(self, direction: str | None = None) -> None:
        panel = self.transfer_panels[direction or self.active_transfer]
        while panel.file_list_layout.count():
            item = panel.file_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        panel.file_rows.clear()
        if panel.direction == self.active_transfer:
            self.file_rows = panel.file_rows

    def refresh_file_rows(self, direction: str = "download") -> None:
        if self.running:
            return
        panel = self.transfer_panels[direction]
        items = [line.strip() for line in panel.sources.toPlainText().splitlines() if line.strip()]
        fallback = Path.home() / "Downloads" if direction == "download" else Path.home()
        destination_text = panel.destination.text().strip()
        destination: str | Path = (
            destination_text
            if is_rclone_remote_path(destination_text)
            else Path(destination_text or fallback)
        )
        mode = str(self.file_display_combo.currentData() or "list")
        mode_names = {
            "list": "ПОДРОБНЫЙ СПИСОК",
            "shortcut": "ЯРЛЫКИ",
            "paths": "ПУТИ КАК В ТЕРМИНАЛЕ",
        }
        panel.file_mode_label.setText(mode_names.get(mode, mode_names["list"]))
        self.clear_file_rows(direction)
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
                size = Path(source).stat().st_size if Path(source).is_file() else 0
            except OSError:
                size = 0
            row.update_data(size, 0, 0, 0, "ОЖИДАНИЕ")
            panel.file_rows[source] = row
            self.place_file_row(row, index, mode, direction)
        if not items:
            empty = QLabel("Выбранные файлы появятся здесь")
            empty.setObjectName("settingDescription")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            panel.file_list_layout.addWidget(empty, 0, 0, 1, 3)
        if self.files_tab_visible:
            self.refresh_files_overview()

    def refresh_files_overview(self) -> None:
        if not hasattr(self, "files_overview_layout"):
            return
        while self.files_overview_layout.count():
            item = self.files_overview_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.files_overview_rows.clear()

        counts = {"download": 0, "upload": 0}
        directions = ["download"]
        if self.upload_addon_enabled:
            directions.append("upload")
        for direction in directions:
            panel = self.transfer_panels[direction]
            sources = [
                line.strip()
                for line in panel.sources.toPlainText().splitlines()
                if line.strip()
            ]
            fallback = Path.home() / "Downloads" if direction == "download" else Path.home()
            destination_text = panel.destination.text().strip()
            destination: str | Path = (
                destination_text
                if is_rclone_remote_path(destination_text)
                else Path(destination_text or fallback)
            )
            for source in sources:
                task = (
                    self.tasks.get(source)
                    if self.running and direction == self.active_transfer
                    else None
                )
                existing_row = panel.file_rows.get(source)
                if task is not None:
                    size = task.size
                    downloaded = task.downloaded
                    speed = task.speed
                    state = task.status
                else:
                    size = existing_row.size if existing_row is not None else 0
                    downloaded = 0
                    speed = 0.0
                    state = "ОЖИДАНИЕ"
                row = FilesOverviewRow(direction, source, destination)
                row.progress.animations_enabled = self.animations_check.isChecked()
                row.update_data(size, downloaded, speed, state)
                self.files_overview_rows[(direction, source)] = row
                self.files_overview_layout.addWidget(row)
                self.animate_appearance(
                    row,
                    duration=260,
                    start_opacity=0.2,
                    delay=min(len(self.files_overview_rows) * 30, 240),
                )
                counts[direction] += 1

        total = counts["download"] + counts["upload"]
        self.files_summary_label.setText(
            f"ФАЙЛОВ: {total} · ЗАГРУЗКА: {counts['download']} · ВЫГРУЗКА: {counts['upload']}"
        )
        if not total:
            empty = QLabel(
                "Добавьте загрузку или выгрузку на странице «Главная» — файлы появятся здесь."
            )
            empty.setObjectName("settingDescription")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.files_overview_layout.addWidget(empty)
        self.files_overview_layout.addStretch()

    def sync_files_overview_row(self, direction: str, source: str) -> None:
        if not self.files_tab_visible:
            return
        row = self.files_overview_rows.get((direction, source))
        task = self.tasks.get(source) if direction == self.active_transfer else None
        if row is None or task is None:
            return
        row.update_data(task.size, task.downloaded, task.speed, task.status)

    def place_file_row(self, row: FileRow, index: int, mode: str, direction: str | None = None) -> None:
        panel = self.transfer_panels[direction or self.active_transfer]
        if mode == "shortcut":
            panel.file_list_layout.addWidget(row, index // 3, index % 3)
        else:
            panel.file_list_layout.addWidget(row, index, 0, 1, 3)
        self.animate_appearance(
            row,
            duration=240,
            start_opacity=0.15,
            delay=min(index * 35, 280),
        )

    def set_inputs_enabled(self, enabled: bool) -> None:
        panel_widgets = []
        for panel in self.transfer_panels.values():
            panel_widgets.extend(
                [
                    panel.sources,
                    panel.destination,
                    panel.choose_files_button,
                    panel.choose_file_button,
                    panel.choose_folder_button,
                    panel.clear_button,
                    panel.browse_button,
                    getattr(panel, "google_drive_button", panel.browse_button),
                    getattr(panel, "direction_toggle_button", panel.browse_button),
                ]
            )
        for widget in (
            *panel_widgets,
            self.download_mode_combo,
            self.copy_profile_combo,
            self.copy_engine_combo,
            self.rclone_path_edit,
            self.drive_chunk_combo,
            self.google_route_combo,
            *(panel.preset_combo for panel in self.transfer_panels.values()),
        ):
            widget.setEnabled(enabled)
        self.update_settings_visibility()

    def set_transfer_controls_enabled(self, enabled: bool) -> None:
        for direction, panel in self.transfer_panels.items():
            active = enabled and direction == self.active_transfer
            for button in (panel.pause_button, panel.after_button, panel.stop_button, panel.visible_stop_button):
                button.setEnabled(active)

    def set_download_controls_enabled(self, enabled: bool) -> None:
        self.set_transfer_controls_enabled(enabled)

    def start_downloads(self) -> None:
        self.start_transfers("download")

    def start_uploads(self) -> None:
        self.start_transfers("upload")

    def show_rclone_monitor(self) -> None:
        if self.rclone_monitor is None:
            self.rclone_monitor = RcloneMonitorWindow(self)
            self.apply_theme()
        self.rclone_monitor.show()
        self.rclone_monitor.raise_()
        self.rclone_monitor.activateWindow()

    def start_transfers(self, direction: str) -> None:
        if self.running or self.workers or self.cloud_browser is not None or self.google_drive_oauth_thread is not None:
            return
        if direction == "upload" and not self.upload_addon_enabled:
            QMessageBox.warning(
                self,
                APP_NAME,
                "Сначала установите BETA-дополнение «Выгрузка» во вкладке обновлений.",
            )
            return
        panel = self.bind_transfer_panel(direction)
        self.update_start_button()
        raw_items = [line.strip() for line in panel.sources.toPlainText().splitlines() if line.strip()]
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
        if not panel.destination.text().strip():
            if not self.choose_destination_for(direction):
                return
        explorer_destination_text = panel.destination.text().strip()
        destination_text = explorer_destination_text
        direct_google = self.direct_google_destination(direction, explorer_destination_text)
        detected, route_description = detect_direction(items, explorer_destination_text)
        if self.auto_direction_check.isChecked() and detected and detected != direction:
            if detected != "upload" or self.upload_addon_enabled:
                previous_sources = panel.sources.toPlainText()
                direction = detected
                panel = self.bind_transfer_panel(direction)
                panel.sources.setPlainText(previous_sources)
                panel.destination.setText(explorer_destination_text)
                self.show_transfer_direction(direction)
        if direct_google:
            if not self.google_drive_is_connected():
                self._cloud_picker_request = (direction, explorer_destination_text)
                self._start_after_google_oauth = direction
                self.start_google_drive_oauth()
                return
            try:
                destination_text = self.resolve_explorer_google_destination(
                    explorer_destination_text
                )
            except SharedDriveAccessError as exc:
                prompt = QMessageBox(self)
                prompt.setIcon(QMessageBox.Icon.Warning)
                prompt.setWindowTitle(APP_NAME)
                prompt.setText(str(exc))
                prompt.setInformativeText(
                    "Выбранный путь сохранён. Переподключите Neon и в окне Google "
                    "выберите тот же аккаунт, который подключён к этому диску в Проводнике."
                )
                reconnect = prompt.addButton(
                    "Переподключить Google Drive", QMessageBox.ButtonRole.AcceptRole
                )
                prompt.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
                prompt.exec()
                if prompt.clickedButton() == reconnect:
                    self._cloud_picker_request = (direction, explorer_destination_text)
                    self._start_after_google_oauth = direction
                    self.start_google_drive_oauth()
                return
            except (RuntimeError, ValueError) as exc:
                QMessageBox.critical(
                    self,
                    APP_NAME,
                    "Не удалось подготовить выбранный путь для Neon Rclone:\n"
                    f"{exc}\n\nПуть в Проводнике сохранён и не изменён.",
                )
                return
            route_description = (
                "Через Neon Rclone · путь сначала выбран в Проводнике · "
                "облачный поиск папок отключён"
            )
        remote_destination = is_rclone_remote_path(destination_text)
        if remote_destination:
            if not is_managed_drive_path(destination_text):
                QMessageBox.critical(
                    self,
                    APP_NAME,
                    "Этот облачный путь не принадлежит управляемому подключению Neon. "
                    "Нажмите кнопку «Google Drive» рядом с полем назначения.",
                )
                return
            if not self.google_drive_is_connected():
                QMessageBox.warning(
                    self,
                    APP_NAME,
                    "Google Drive ещё не подключён. Нажмите кнопку «Google Drive» и "
                    "подтвердите доступ в браузере.",
                )
                return
            destination: str | Path = destination_text
            storage_summary = "Облачное хранилище · свободное место определяет Google Drive"
            rclone_index = self.copy_engine_combo.findData("rclone")
            if rclone_index >= 0:
                self.copy_engine_combo.setCurrentIndex(rclone_index)
        else:
            destination = Path(destination_text).expanduser()
        if direction == "upload" and not remote_destination:
            requirement = upload_destination_requirement(Path(destination))
            if requirement:
                QMessageBox.critical(self, APP_NAME, requirement)
                return
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
                "Операция остановлена, чтобы не повредить файлы:\n\n"
                + "\n\n".join(details),
            )
            return
        if not remote_destination:
            try:
                Path(destination).mkdir(parents=True, exist_ok=True)
                usage = shutil.disk_usage(destination)
            except OSError as exc:
                QMessageBox.critical(self, APP_NAME, f"Не удалось подготовить папку назначения:\n{exc}")
                return
            write_problem = destination_write_problem(Path(destination))
            if write_problem:
                QMessageBox.critical(self, APP_NAME, write_problem)
                return
            storage_summary = f"Свободно: {human_size(usage.free)} из {human_size(usage.total)}"
        engine_mode = str(self.copy_engine_combo.currentData() or "robocopy")
        if direction == "download" and self.download_buffer_check.isChecked():
            engine_mode = "rclone"
            self.copy_engine_combo.setCurrentIndex(self.copy_engine_combo.findData("rclone"))
        required_engines = {copy_engine_for_source(engine_mode, item) for item in items}
        self.active_engines = required_engines
        robocopy = shutil.which("robocopy.exe") if "robocopy" in required_engines else None
        rclone = self.resolved_rclone_executable() if "rclone" in required_engines else None
        missing_engines: list[str] = []
        if "robocopy" in required_engines and not robocopy:
            missing_engines.append("системный robocopy.exe")
        if "rclone" in required_engines and not rclone:
            missing_engines.append(
                "Rclone не установлен — включите Advanced mode в «Настройках», затем в "
                "«Advanced mode → Движок и производительность» нажмите «Скачать и подключить», "
                "либо выберите Robocopy"
            )
        if missing_engines:
            QMessageBox.critical(
                self,
                APP_NAME,
                "Не найдены необходимые движки:\n• " + "\n• ".join(missing_engines),
            )
            return
        self.active_destination = destination
        if robocopy:
            self.robocopy_executable = robocopy
        if rclone:
            self.rclone_executable = rclone

        destination_key = "upload_destination" if direction == "upload" else "destination"
        sources_key = "upload_sources" if direction == "upload" else "sources"
        self.settings.setValue(destination_key, str(destination))
        self.settings.setValue(sources_key, panel.sources.toPlainText())
        self.persist_settings()
        self.set_state("●  АНАЛИЗ ФАЙЛОВ")
        QApplication.processEvents()
        self.queue = deque(items)
        self.snapshot_results.clear()
        self.tasks = {}
        self.total_bytes = 0
        for source in items:
            try:
                # Folder enumeration is performed by the asynchronous source gate.
                size = Path(source).stat().st_size if Path(source).is_file() else 0
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
        panel.terminal.clear()
        if "rclone" in required_engines:
            if self.rclone_monitor is None:
                self.rclone_monitor = RcloneMonitorWindow(self)
                self.apply_theme()
            self.rclone_monitor.reset_monitor()
        self.log_path = self.log_dir / f"session-{datetime.now():%Y%m%d-%H%M%S}.log"
        self.set_inputs_enabled(False)
        self.set_transfer_controls_enabled(True)
        for transfer_panel in self.transfer_panels.values():
            transfer_panel.start_button.setEnabled(False)
        operation = "ВЫГРУЗКА" if direction == "upload" else "ЗАГРУЗКА"
        self.set_state(f"●  {operation}")
        mode_names = {
            "sequential": "Последовательно",
            "limited": f"До {self.concurrency_spin.value()} одновременно",
            "all": f"Все доступные · до {MAX_CONCURRENT_DOWNLOADS} одновременно",
        }
        selected_mode = str(self.download_mode_combo.currentData())
        selected_profile = self.effective_copy_profile()
        profile_name = COPY_PROFILE_NAMES.get(selected_profile, COPY_PROFILE_NAMES["optimized"])
        effective_threads = self.effective_directory_threads()
        mt_status = (
            str(effective_threads)
            if selected_profile in ("optimized", "maximum", "turbo")
            else "выключен"
        )
        turbo_status = (
            str(self.effective_turbo_threads())
            if selected_profile == "turbo" and direction == "download"
            else "выключен"
        )
        self.footer_info.setText(mode_names.get(selected_mode, "Последовательно"))
        self.speed.setText("ИЗМЕРЕНИЕ…")
        self.eta.setText("ИЗМЕРЕНИЕ…")
        self.metrics_timer.start()
        self.rebuild_task_rows(destination)
        self.refresh_files_overview()
        mode = mode_names.get(selected_mode, "Последовательно").lower()
        rclone_options = self.selected_rclone_options()
        self.append_log(
            f"{APP_NAME}\nСеанс: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"Движок: {COPY_ENGINE_NAMES.get(engine_mode, COPY_ENGINE_NAMES['robocopy'])}\n"
            f"Robocopy: {robocopy or 'не используется'}\nRclone: {rclone or 'не используется'}\n"
            f"Операция: {operation.lower()}\n"
            f"Маршрут: {route_description}\n"
            f"Режим: {mode}\nЛимит процессов: {self.max_concurrent_downloads()}\n"
            f"Проверка источника: обязательная · стабильность {SOURCE_STABLE_SECONDS:.0f} сек.\n"
            f"Профиль: {profile_name}\nПотоков /MT на папку: {mt_status}\n"
            f"Турбо-сегментов на большой файл: {turbo_status}\n"
            f"Rclone: чанк {rclone_options.chunk_size_mib} МиБ · "
            f"потоков {rclone_options.multi_thread_streams} · "
            f"передач {rclone_options.transfers}\n"
            f"Очередь: {len(items)}\nОбщий объём: {human_size(self.total_bytes)}\n"
            f"Назначение: {destination}\n{storage_summary}\n"
            f"Лог: {self.log_path}\n"
        )
        self.fill_worker_slots()

    def rebuild_task_rows(self, destination: str | Path) -> None:
        panel = self.current_transfer_panel()
        self.clear_file_rows(self.active_transfer)
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
            panel.file_rows[source] = row
            self.place_file_row(row, index, mode, self.active_transfer)
        self.file_rows = panel.file_rows

    def start_next(self) -> None:
        self.fill_worker_slots()

    def source_ready_for_transfer(self, task: TaskInfo, snapshot: tuple | None = None) -> bool:
        now = time.monotonic()
        signature, problem = snapshot if snapshot is not None else source_snapshot(Path(task.source))
        if problem or signature is None:
            task.source_signature = None
            task.source_stable_since = None
            message = problem or "Источник ещё не готов."
            self.set_source_waiting(task, message)
            return False
        if task.source_signature != signature:
            task.source_signature = signature
            task.source_stable_since = now
            self.set_source_waiting(
                task,
                f"Проверяем стабильность размера и даты изменения: {human_size(signature[1])}.",
            )
            return False
        stable_since = task.source_stable_since or now
        remaining = SOURCE_STABLE_SECONDS - (now - stable_since)
        if remaining > 0:
            self.set_source_waiting(
                task,
                f"Источник не меняется; контроль ещё {max(1, int(remaining + 0.99))} сек.",
            )
            return False

        if task.size != signature[1]:
            task.size = signature[1]
            self.total_bytes = sum(item.size for item in self.tasks.values())
            if task.row:
                task.row.update_data(task.size, 0, 0, task.elapsed(), task.status)
            self.update_overall_progress()
        task.source_wait_message = ""
        return True

    def set_source_waiting(self, task: TaskInfo, message: str) -> None:
        task.status = "ОЖИДАНИЕ ИСХОДНИКА"
        if task.source_wait_message != message:
            task.source_wait_message = message
            self.append_log(f"⏳ {Path(task.source).name}: {message}\n")
        if task.row:
            task.row.update_data(task.size, 0, 0, task.elapsed(), task.status)
        self.sync_files_overview_row(self.active_transfer, task.source)
        self.set_state("●  ОЖИДАНИЕ ГОТОВНОСТИ ФАЙЛА")

    def fill_worker_slots(self) -> None:
        if not self.running or self.stopping or self.paused:
            return
        limit = self.max_concurrent_downloads()
        waiting = False
        candidates = len(self.queue)
        for _ in range(candidates):
            if len(self.workers) >= limit or self.stop_after_file:
                break
            source = self.queue.popleft()
            task = self.tasks.get(source)
            snapshot = self.snapshot_results.pop(source, None)
            if task is not None and snapshot is None:
                if source not in self.snapshot_threads and len(self.snapshot_threads) < 2:
                    self.set_source_waiting(task, "Проверка размера, доступности и готовности исходника…")
                    thread = SourceSnapshotThread(source, self)
                    self.snapshot_threads[source] = thread
                    def snapshot_finished(current=thread, selected=task) -> None:
                        self.snapshot_threads.pop(current.source, None)
                        if self.running and self.tasks.get(current.source) is selected and not self.stopping:
                            self.snapshot_results[current.source] = current.result
                            QTimer.singleShot(0, self.fill_worker_slots)
                        current.deleteLater()
                    thread.finished.connect(snapshot_finished)
                    thread.start()
                self.queue.append(source)
                waiting = True
                continue
            if task is not None and not self.source_ready_for_transfer(task, snapshot):
                self.queue.append(source)
                waiting = True
                continue
            self.start_task(source)
        if waiting and self.queue and not self.stopping:
            self.source_check_timer.start()
        if not self.workers and (not self.queue or self.stop_after_file):
            self.finish_queue(stopped=self.stop_after_file)

    def start_task(self, source: str) -> None:
        task = self.tasks[source]
        task.status = "ВЫГРУЗКА" if self.active_transfer == "upload" else "ЗАГРУЗКА"
        self.set_state(f"●  {task.status}")
        task.started_at = task.started_at or time.monotonic()
        if task.row:
            task.row.update_data(task.size, task.downloaded, task.speed, task.elapsed(), task.status)
        self.sync_files_overview_row(self.active_transfer, source)
        selected_profile = self.effective_copy_profile()
        engine_mode = str(self.copy_engine_combo.currentData() or "robocopy")
        task_engine = copy_engine_for_source(engine_mode, source)
        turbo_file = (
            task_engine == "robocopy"
            and self.active_transfer == "download"
            and selected_profile == "turbo"
            and Path(source).is_file()
        )
        worker: Downloader | RcloneDownloader | TurboFileDownloader
        if task_engine == "rclone":
            worker = RcloneDownloader(self)
        else:
            worker = TurboFileDownloader(self) if turbo_file else Downloader(self)
        if isinstance(worker, TurboFileDownloader):
            self.turbo_workers.add(worker)
            worker.finished.connect(lambda current=worker: self.turbo_worker_finished(current))
        worker.log.connect(self.append_log)
        worker.progress.connect(self.on_progress)
        worker.item_done.connect(self.on_item_done)
        self.workers[source] = worker
        if isinstance(worker, RcloneDownloader):
            destination_text = self.active_destination or self.current_transfer_panel().destination.text()
            if self.active_transfer == "download" and self.download_buffer_check.isChecked():
                if Path(source).is_file() and not is_rclone_remote_path(destination_text):
                    try:
                        worker.disk_buffer = TransferBuffer(Path(destination_text), task.size)
                        destination_text = str(worker.disk_buffer.root)
                        self.append_log(f"Буфер файла: {destination_text}\n")
                    except OSError as exc:
                        worker.failure_reason = str(exc)
                        self.on_item_done(False, source)
                        return
                else:
                    self.append_log("Папка передаётся напрямую; файловый буфер не используется.\n")
            self.append_log("Запуск Rclone: подключение, проверка исходника и контрольной суммы…\n")
            worker.start_item(
                self.rclone_executable,
                source,
                destination_text
                if is_rclone_remote_path(destination_text)
                else Path(destination_text),
                self.selected_rclone_options(),
                task.size,
            )
        elif turbo_file:
            worker.start_item(
                source,
                Path(self.active_destination or self.current_transfer_panel().destination.text()),
                self.effective_turbo_threads(),
            )
        else:
            worker.start_item(
                source,
                Path(self.active_destination or self.current_transfer_panel().destination.text()),
                selected_profile,
                self.effective_directory_threads(),
            )

    @Slot(str, float, float)
    def on_progress(self, source: str, percent: float, item_bytes: float) -> None:
        task = self.tasks.get(source)
        if task is None:
            return
        measured = min(int(item_bytes), task.size) if task.size else int(item_bytes)
        task.downloaded = max(task.downloaded, measured)
        if isinstance(self.workers.get(source), RcloneDownloader):
            if measured:
                task.status = "ВЫГРУЗКА" if self.active_transfer == "upload" else "ЗАГРУЗКА"
            else:
                task.status = "ПОДГОТОВКА / ПРОВЕРКА"
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
        self.sync_files_overview_row(self.active_transfer, source)

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
            self.sync_files_overview_row(self.active_transfer, task.source)
        if not self.metrics_started:
            seconds = int(now - self.started_at) if self.started_at else 0
            self.speed.setText(f"ПОДГОТОВКА · {seconds} с")
            self.eta.setText("ПОДКЛЮЧЕНИЕ / ПРОВЕРКА")
            return
        self.measured_done_bytes = sum(item.downloaded for item in self.tasks.values())
        self.speed_samples.append((now, self.measured_done_bytes))
        while len(self.speed_samples) > 2 and now - self.speed_samples[0][0] > 15:
            self.speed_samples.popleft()
        first_time, first_bytes = self.speed_samples[0]
        elapsed = now - first_time
        delta = self.measured_done_bytes - first_bytes
        self.speed_bps = max(0.0, delta / elapsed) if elapsed >= 1 else 0.0
        panel = self.current_transfer_panel()
        if hasattr(panel, "speed_graph"):
            panel.speed_graph.setValue(self.speed_bps / (1024 * 1024))
        if self.rclone_monitor is not None and "rclone" in self.active_engines:
            self.rclone_monitor.set_speed(self.speed_bps / (1024 * 1024))
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
        failure_reason = str(getattr(worker, "failure_reason", "") or "").strip()
        if worker:
            if not isinstance(worker, TurboFileDownloader):
                QTimer.singleShot(0, worker.deleteLater)
        if ok and task and task.source_signature is not None and not self.stopping:
            final_signature, final_problem = source_snapshot(Path(source))
            if final_problem or final_signature != task.source_signature:
                task.downloaded = 0
                task.fraction = 0.0
                task.speed = 0.0
                task.finished_at = None
                task.samples.clear()
                task.source_signature = None
                task.source_stable_since = None
                self.queue.append(source)
                self.set_source_waiting(
                    task,
                    final_problem
                    or "Источник изменился во время передачи; ждём стабильную версию и повторим.",
                )
                self.append_log(
                    f"↻ Результат {source} не принят: источник изменился во время копирования. "
                    "Файл возвращён в очередь.\n"
                )
                disk_buffer = getattr(worker, "disk_buffer", None)
                if disk_buffer:
                    try:
                        disk_buffer.discard()
                    except OSError as exc:
                        self.append_log(f"Не удалось очистить буфер {disk_buffer.root}: {exc}\n")
                self.measured_done_bytes = sum(item.downloaded for item in self.tasks.values())
                self.update_overall_progress()
                self.fill_worker_slots()
                return
        disk_buffer = getattr(worker, "disk_buffer", None)
        if disk_buffer:
            try:
                if ok and task:
                    disk_buffer.commit(Path(source).name, task.size)
                    self.append_log("Файл сохранён в назначение; буфер очищен.\n")
                else:
                    disk_buffer.discard()
            except OSError as exc:
                ok = False
                failure_reason = f"Буфер {disk_buffer.root}: {exc}"
                self.append_log(failure_reason + "\n")
        if task:
            task.finished_at = time.monotonic()
            if ok:
                task.downloaded = task.size
                task.fraction = 1.0
                task.status = "ГОТОВО"
                self.completed_items += 1
                self.record_transfer_statistics(task.size, self.active_transfer)
                self.append_log(f"✓ Завершено: {source}\n")
            else:
                task.status = "ОШИБКА" if not self.stopping else "ОСТАНОВЛЕНО"
                task.error_message = failure_reason
                if not self.stopping:
                    self.failed_items += 1
                self.append_log(f"✕ Не завершено: {source}\n")
            if task.row:
                task.row.update_data(task.size, task.downloaded, task.speed, task.elapsed(), task.status)
            self.sync_files_overview_row(self.active_transfer, source)
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

    def turbo_worker_finished(self, worker: TurboFileDownloader) -> None:
        self.turbo_workers.discard(worker)
        worker.deleteLater()
        self.maybe_close_when_idle()

    def toggle_pause(self) -> None:
        if not self.workers:
            return
        panel = self.current_transfer_panel()
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
                panel.pause_button.setText("ПАУЗА")
                panel.visible_stop_button.setText("Остановить")
                panel.visible_stop_button.setProperty("colorRole", "danger")
                operation = "ВЫГРУЗКА" if self.active_transfer == "upload" else "ЗАГРУЗКА"
                self.set_state(f"●  {operation}")
                for source, task in self.tasks.items():
                    if source in self.workers and task.finished_at is None:
                        task.status = "РАБОТАЕТ"
                        self.sync_files_overview_row(self.active_transfer, source)
                self.append_log(
                    "▶ Передачи продолжены в тех же процессах и с тех же активных сессий.\n"
                )
            else:
                for worker in self.workers.values():
                    worker.suspend()
                self.paused = True
                panel.pause_button.setText("ПРОДОЛЖИТЬ")
                panel.visible_stop_button.setText("Продолжить")
                panel.visible_stop_button.setProperty("colorRole", "upload")
                self.set_state("●  ПАУЗА")
                for source, task in self.tasks.items():
                    if source in self.workers and task.finished_at is None:
                        task.status = "ОСТАНОВЛЕНО · МОЖНО ПРОДОЛЖИТЬ"
                        self.sync_files_overview_row(self.active_transfer, source)
                self.append_log(
                    "Ⅱ Передачи остановлены без закрытия процессов. Нажмите «Продолжить» — "
                    "Rclone сохранит текущую resumable-сессию, Robocopy /Z и Turbo сохранят своё место.\n"
                )
        except psutil.Error as exc:
            self.append_log(f"Не удалось изменить состояние процесса: {exc}\n")

    def toggle_resumable_stop(self) -> None:
        """Pause active sessions; cancel only a queue that has not started yet."""
        if self.workers:
            self.toggle_pause()
        elif self.queue:
            self.stop_now()

    def toggle_stop_after(self) -> None:
        if not self.workers:
            return
        panel = self.current_transfer_panel()
        if self.stop_after_file:
            self.stop_after_file = False
            self.stop_after_source = None
            panel.after_button.setText("ПОСЛЕ ФАЙЛА")
            panel.after_button.setToolTip("Остановить очередь после завершения текущего файла")
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
        panel.after_button.setText("ОТМЕНИТЬ")
        panel.after_button.setToolTip(f"Остановка после: {current_name}")
        self.append_log(
            f"■ Очередь остановится сразу после текущего файла: {self.stop_after_source}\n"
            "Новые файлы не запускаются. После него остальные активные процессы будут остановлены.\n"
        )

    def stop_now(self) -> None:
        if not self.workers and not self.queue:
            return
        self.stopping = True
        self.stop_after_file = True
        self.stop_after_source = None
        self.source_check_timer.stop()
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
        self.append_log("■ Получена команда немедленной остановки всех передач и копирований.\n")
        if not self.workers:
            self.finish_queue(stopped=True)

    def finish_queue(self, stopped: bool = False) -> None:
        if not self.running:
            return
        self.running = False
        self.active_destination = None
        self.metrics_timer.stop()
        self.source_check_timer.stop()
        for transfer_panel in self.transfer_panels.values():
            transfer_panel.start_button.setEnabled(True)
        self.set_inputs_enabled(True)
        self.set_transfer_controls_enabled(False)
        panel = self.current_transfer_panel()
        panel.pause_button.setText("ПАУЗА")
        panel.visible_stop_button.setText("Остановить")
        panel.visible_stop_button.setProperty("colorRole", "danger")
        panel.after_button.setText("ПОСЛЕ ФАЙЛА")
        panel.after_button.setToolTip("Остановить очередь после завершения текущего файла")
        self.update_start_button()
        self.stop_after_source = None
        operation = "Выгрузка" if self.active_transfer == "upload" else "Загрузка"
        if stopped:
            for task in self.tasks.values():
                if task.status in ("ОЖИДАНИЕ", "ОЖИДАНИЕ ИСХОДНИКА"):
                    task.status = "ОСТАНОВЛЕНО"
                    self.sync_files_overview_row(self.active_transfer, task.source)
            self.set_state("●  ОСТАНОВЛЕНО")
            buffered = self.active_transfer == "download" and self.download_buffer_check.isChecked()
            detail = "Временный файловый буфер очищен." if buffered else "Частичные файлы оставлены для продолжения."
            self.append_log(f"\nОчередь остановлена. {detail}\n")
            notification = f"{operation} остановлена. {detail}"
        elif self.failed_items:
            self.set_state("●  ЗАВЕРШЕНО С ОШИБКАМИ")
            self.append_log(f"\nЗавершено с ошибками: {self.failed_items}.\n")
            notification = f"Очередь завершена. Ошибок: {self.failed_items}."
            details = []
            for task in self.tasks.values():
                if task.error_message:
                    details.append(f"{Path(task.source).name}:\n{task.error_message}")
                if len(details) == 3:
                    break
            message = f"Не удалось обработать файлов: {self.failed_items}."
            if details:
                message += "\n\n" + "\n\n".join(details)
            if self.log_path:
                message += f"\n\nПолный журнал:\n{self.log_path}"
            QMessageBox.warning(self, APP_NAME, message)
        else:
            self.set_state("●  ГОТОВО")
            self.progress.set_progress(1000)
            self.ring.setValue(100)
            self.eta.setText("00:00")
            self.append_log(f"\n✓ {operation}: вся очередь успешно завершена.\n")
            notification = f"{operation} завершена. Файлов: {self.completed_items}."
        self.footer_info.setText(
            f"Готово: {self.completed_items}/{self.total_items} · Ошибок: {self.failed_items}"
        )
        self.notify(APP_NAME, notification)

        self.maybe_close_when_idle()

    def maybe_close_when_idle(self) -> None:
        if self.close_when_idle and not self.workers and not self.turbo_workers:
            if self.keep_open_after_finish_check.isChecked():
                self.close_when_idle = False
                self.setup_tray()
                return
            self.force_exit = True
            QTimer.singleShot(0, self.close)

    def set_state(self, text: str) -> None:
        self.state_label.setText(text)
        if hasattr(self, "global_system_status"):
            self.global_system_status.setText(text.replace("ГОТОВО", "СИСТЕМА ГОТОВА"))
        if hasattr(self, "header_ready_badge"):
            self.header_ready_badge.setText(text.replace("СИСТЕМА ", "").title())
        if self.rclone_monitor is not None:
            self.rclone_monitor.state_label.setText(text)
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
        terminal = self.current_transfer_panel().terminal
        scroll = terminal.verticalScrollBar()
        old_position = scroll.value()
        was_at_bottom = old_position >= scroll.maximum() - 3
        cursor = terminal.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        terminal.setTextCursor(cursor)
        smart_scroll = not hasattr(self, "smart_terminal_check") or self.smart_terminal_check.isChecked()
        if was_at_bottom or not smart_scroll:
            scroll.setValue(scroll.maximum())
        else:
            scroll.setValue(old_position)
        if self.rclone_monitor is not None and "rclone" in self.active_engines:
            self.rclone_monitor.append_text(text)

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
            if release.get("migration"):
                self.update_status.setText(
                    "Доступна стабильная установка без временной распаковки _MEI"
                )
                self.install_update_button.setText("УСТАНОВИТЬ СТАБИЛЬНУЮ ВЕРСИЮ")
                message = (
                    "Можно перейти с однофайловой версии на обычную установку. "
                    "Это уберёт предупреждения об удалении _MEI."
                )
            else:
                self.update_status.setText(
                    f"Доступна версия {release['version']} · установлена {release['current_version']}"
                )
                self.install_update_button.setText("СКАЧАТЬ И УСТАНОВИТЬ")
                message = (
                    f"Доступно обновление {release['version']}.\n"
                    "Нажмите «Скачать и установить» во вкладке обновлений."
                )
            self.install_update_button.setVisible(True)
            if not silent:
                QMessageBox.information(self, APP_NAME, message)
        else:
            self.install_update_button.setVisible(False)
            self.install_update_button.setText("СКАЧАТЬ И УСТАНОВИТЬ")
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
            channel = " · BETA" if release.get("prerelease") else ""
            marker = (
                " · установлена"
                if version_tuple(str(release.get("version") or ""))
                == version_tuple(__version__)
                else ""
            )
            self.release_combo.addItem(
                f"{release.get('tag', release.get('version'))}{channel} · {published}{marker}",
                index,
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
        action = (
            "установить стабильную версию без временной распаковки"
            if release.get("asset_name") in SETUP_ASSET_NAMES
            else "скачать и заменить текущий EXE"
        )
        answer = QMessageBox.question(
            self,
            APP_NAME,
            f"Версия {release['version']}: {action} и перезапустить приложение?",
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
        self.refresh_last_download_ui()
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
        if self.cloud_browser is not None:
            self.cloud_browser.reject()
            event.ignore()
            return
        if any(thread.isRunning() for thread in self.snapshot_threads.values()):
            self.set_state("●  ЗАВЕРШЕНИЕ ПРОВЕРКИ ИСХОДНИКА")
            event.ignore()
            return
        if self.google_drive_oauth_thread is not None and self.google_drive_oauth_thread.isRunning():
            QMessageBox.information(
                self,
                APP_NAME,
                "Завершите или отмените подтверждение Google Drive в браузере.",
            )
            event.ignore()
            return
        if self.system_health_thread is not None and self.system_health_thread.isRunning():
            QMessageBox.information(
                self,
                APP_NAME,
                "Дождитесь завершения диагностики и автоматического исправления системы.",
            )
            event.ignore()
            return
        if self.rclone_install_thread is not None and self.rclone_install_thread.isRunning():
            QMessageBox.information(
                self,
                APP_NAME,
                "Дождитесь завершения установки Rclone перед закрытием приложения.",
            )
            event.ignore()
            return
        if self.force_exit:
            self.auto_health_timer.stop()
            self.theme_timer.stop()
            event.accept()
            return
        if (
            (self.workers or self.turbo_workers)
            and
            self.tray_check.isChecked()
            and self.continue_in_tray_check.isChecked()
            and QSystemTrayIcon.isSystemTrayAvailable()
        ):
            self.close_when_idle = True
            self.hide()
            ending = (
                "После завершения Neon останется в tray."
                if self.keep_open_after_finish_check.isChecked()
                else "После завершения Neon Drive полностью закроется."
            )
            self.notify(
                APP_NAME,
                f"Передача продолжится в фоне. {ending}",
            )
            event.ignore()
            return
        if self.workers or self.turbo_workers:
            if self.close_when_idle:
                event.ignore()
                return
            answer = QMessageBox.question(self, APP_NAME, "Остановить загрузки и закрыть приложение?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.close_when_idle = True
            if self.workers:
                self.stop_now()
            self.set_state("●  ЗАВЕРШЕНИЕ РАБОТЫ")
            event.ignore()
            self.maybe_close_when_idle()
            return
        self.auto_health_timer.stop()
        self.theme_timer.stop()
        event.accept()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not hasattr(self, "sidebar") or getattr(self, "_responsive_resize", False):
            return
        width = event.size().width()
        self._responsive_resize = True
        try:
            for panel in self.transfer_panels.values():
                if hasattr(panel, "speed_graph"):
                    panel.speed_graph.setVisible(event.size().height() >= 880)
                    panel.ring.setVisible(event.size().height() >= 760)
                    panel.recent_card.setVisible(event.size().height() >= 820)
            if width < 1020 and self.sidebar_expanded:
                self._responsive_sidebar_collapsed = True
                self.set_navigation_panel_expanded(False, animate=False)
            elif width >= 1120 and getattr(self, "_responsive_sidebar_collapsed", False):
                self._responsive_sidebar_collapsed = False
                self.set_navigation_panel_expanded(True, animate=False)
        finally:
            self._responsive_resize = False

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
    icon_path = resource_path("assets/neon-drive-v2.png")
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    if not macos_version_supported(12):
        QMessageBox.critical(
            None,
            APP_NAME,
            "Neon Drive со встроенным Rclone требует macOS 12 Monterey или новее.",
        )
        return 2

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
    window_holder: dict[str, MainWindow] = {}

    def dispatch(request: dict) -> dict:
        window = window_holder.get("window")
        if window is None:
            return {"ok": False, "error": "Neon Drive ещё запускается"}
        return window.handle_agent_request(request)

    instance_server = InstanceServer(dispatch, app)
    if "--smoke-test" not in sys.argv and not instance_server.listen():
        send_request({"command": "activate"}, timeout_ms=1800)
        return 0

    window = MainWindow()
    window_holder["window"] = window
    window._instance_server = instance_server
    if "--smoke-test" in sys.argv:
        window.show()
        QTimer.singleShot(900, app.quit)
    elif window.should_restore_maximized:
        window.showMaximized()
    else:
        window.show()
    return app.exec()
