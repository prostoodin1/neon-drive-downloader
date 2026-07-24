from __future__ import annotations

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
    retries: int = 3
    low_level_retries: int = 10
    checksum: bool = False
    local_no_sparse: bool = True


def copy_engine_for_source(mode: str, source: str | Path) -> str:
    """Resolve the actual engine without ever writing to one item from two tools."""
    if mode == "rclone":
        return "rclone"
    if mode == "hybrid":
        return "robocopy" if Path(source).is_dir() else "rclone"
    return "robocopy"


def rclone_arguments(
    source: str,
    destination: Path,
    options: RcloneOptions | None = None,
) -> tuple[list[str], Path]:
    """Build an rclone command for an Explorer path or configured remote path."""
    selected = options or RcloneOptions()
    source_path = Path(source)
    target = destination / (source_path.name or source_path.drive.rstrip(":\\/") or "drive")
    command = "copy" if source_path.is_dir() else "copyto"
    args = [
        command,
        str(source_path),
        str(target),
        "--progress",
        "--stats=1s",
        "--stats-one-line",
        "--color=NEVER",
        f"--multi-thread-chunk-size={max(1, int(selected.chunk_size_mib))}Mi",
        f"--multi-thread-cutoff={max(1, int(selected.multi_thread_cutoff_mib))}Mi",
        f"--multi-thread-streams={max(1, min(32, int(selected.multi_thread_streams)))}",
        f"--transfers={max(1, min(32, int(selected.transfers)))}",
        f"--checkers={max(1, min(64, int(selected.checkers)))}",
        f"--buffer-size={max(0, int(selected.buffer_size_mib))}Mi",
        f"--retries={max(1, min(20, int(selected.retries)))}",
        f"--low-level-retries={max(1, min(50, int(selected.low_level_retries)))}",
        "--partial-suffix=.neon-partial",
    ]
    if source_path.is_dir():
        args.append("--create-empty-src-dirs")
    if selected.checksum:
        args.append("--checksum")
    if selected.local_no_sparse:
        args.append("--local-no-sparse")
    return args, target
