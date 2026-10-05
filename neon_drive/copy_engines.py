from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


COPY_ENGINE_NAMES = {
    "robocopy": "Robocopy",
    "rclone": "Rclone",
    "hybrid": "Совместный",
}


@dataclass(frozen=True)
class RcloneOptions:
    chunk_size_mib: int = 64
    multi_thread_cutoff_mib: int = 256
    multi_thread_streams: int = 4
    transfers: int = 4
    checkers: int = 8
    buffer_size_mib: int = 16
    multi_thread_write_buffer_size_mib: int = 1
    retries: int = 3
    low_level_retries: int = 10
    checksum: bool = False
    local_no_sparse: bool = True
    local_no_preallocate: bool = True
    config_path: str | None = None
    drive_chunk_size_mib: int = 64
    drive_tps_limit: int = 0


def is_rclone_remote_path(value: str | Path) -> bool:
    text = str(value).strip()
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return False
    return bool(re.match(
        r"^[A-Za-z0-9_.-]+(?:,(?:[A-Za-z_]+=[A-Za-z0-9_-]*|[A-Za-z_]+))*:",
        text,
    ))


def rclone_target_path(source: str | Path, destination: str | Path) -> str | Path:
    source_text = str(source).strip().rstrip("/\\")
    source_path = Path(source_text)
    if is_rclone_remote_path(source_text):
        remote_tail = source_text.split(":", 1)[1].rstrip("/\\")
        name = remote_tail.replace("\\", "/").rsplit("/", 1)[-1] or "drive"
    else:
        name = source_path.name or source_path.drive.rstrip(":\\/") or "drive"
    if is_rclone_remote_path(destination):
        base = str(destination).strip().rstrip("/\\")
        separator = "" if base.endswith(":") else "/"
        return f"{base}{separator}{name}"
    return Path(destination) / name


def copy_engine_for_source(mode: str, source: str | Path) -> str:
    """Resolve the actual engine without ever writing to one item from two tools."""
    if is_rclone_remote_path(source):
        return "rclone"
    if mode == "rclone":
        return "rclone"
    if mode == "hybrid":
        return "robocopy" if Path(source).is_dir() else "rclone"
    return "robocopy"


def rclone_arguments(
    source: str,
    destination: str | Path,
    options: RcloneOptions | None = None,
    source_is_dir: bool | None = None,
) -> tuple[list[str], str | Path]:
    """Build an rclone command for an Explorer path or configured remote path."""
    selected = options or RcloneOptions()
    source_path = Path(source)
    remote_source = is_rclone_remote_path(source)
    is_directory = source_path.is_dir() if source_is_dir is None else bool(source_is_dir)
    target = rclone_target_path(source, destination)
    command = "copy" if is_directory else "copyto"
    args = [
        command,
        source if remote_source else str(source_path),
        str(target),
        "--stats=1s",
        "--stats-log-level=NOTICE",
        "--use-json-log",
        "--log-level=INFO",
        "--color=NEVER",
        "--contimeout=30s",
        "--timeout=10m",
        "--max-buffer-memory=512Mi",
        f"--multi-thread-chunk-size={max(1, int(selected.chunk_size_mib))}Mi",
        f"--multi-thread-cutoff={max(1, int(selected.multi_thread_cutoff_mib))}Mi",
        f"--multi-thread-streams={max(1, min(32, int(selected.multi_thread_streams)))}",
        f"--transfers={max(1, min(32, int(selected.transfers)))}",
        f"--checkers={max(1, min(64, int(selected.checkers)))}",
        f"--buffer-size={max(0, int(selected.buffer_size_mib))}Mi",
        f"--multi-thread-write-buffer-size={max(1, int(selected.multi_thread_write_buffer_size_mib))}Mi",
        f"--retries={max(1, min(20, int(selected.retries)))}",
        f"--low-level-retries={max(1, min(50, int(selected.low_level_retries)))}",
        "--retries-sleep=3s",
        "--partial-suffix=.neon-partial",
    ]
    if is_directory:
        args.append("--create-empty-src-dirs")
    if selected.checksum:
        args.append("--checksum")
    if selected.local_no_sparse:
        args.append("--local-no-sparse")
    if selected.local_no_preallocate:
        args.append("--local-no-preallocate")
    if is_rclone_remote_path(destination):
        # Drive buffers one chunk per transfer; do not reuse the 2 GiB local
        # multithread chunk setting (which could allocate tens of GiB of RAM).
        drive_chunk = int(selected.drive_chunk_size_mib)
        if drive_chunk not in (8, 16, 32, 64, 128, 256, 512, 1024):
            raise ValueError("Чанк Google Drive должен быть степенью двойки от 8 до 1024 МиБ.")
        args.append(f"--drive-chunk-size={drive_chunk}Mi")
        # The Drive backend holds one full chunk per transfer. Large chunks
        # must not multiply the RAM requirement by 16 or 32 simultaneous files.
        if drive_chunk > 64:
            args = [arg for arg in args if not arg.startswith("--transfers=")]
            args.append(f"--transfers={min(max(1, int(selected.transfers)), max(1, 1024 // drive_chunk))}")
    if remote_source or is_rclone_remote_path(destination):
        # Let the Drive pacer smooth API bursts instead of repeatedly hitting
        # quota backoff. This does not cap transfer bandwidth.
        args.extend(("--drive-pacer-min-sleep=10ms", "--drive-pacer-burst=20"))
        if int(selected.drive_tps_limit) > 0:
            tps = max(1, min(50, int(selected.drive_tps_limit)))
            args.extend((f"--tpslimit={tps}", f"--tpslimit-burst={tps * 2}"))
    if selected.config_path:
        args.append(f"--config={selected.config_path}")
    return args, target
