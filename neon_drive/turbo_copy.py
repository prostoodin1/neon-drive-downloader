from __future__ import annotations

import json
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


DEFAULT_SEGMENT_SIZE = 64 * 1024 * 1024
DEFAULT_BLOCK_SIZE = 4 * 1024 * 1024
CHECKPOINT_VERSION = 1


class TurboCopyStopped(Exception):
    """Raised when a segmented copy is cancelled by the user."""


@dataclass(frozen=True)
class CopySegment:
    index: int
    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start


def build_segments(file_size: int, segment_size: int = DEFAULT_SEGMENT_SIZE) -> list[CopySegment]:
    if file_size < 0:
        raise ValueError("file_size must not be negative")
    if segment_size <= 0:
        raise ValueError("segment_size must be positive")
    return [
        CopySegment(index, start, min(file_size, start + segment_size))
        for index, start in enumerate(range(0, file_size, segment_size))
    ]


def partial_paths(target: Path) -> tuple[Path, Path]:
    part = target.with_name(f"{target.name}.neon-part")
    checkpoint = target.with_name(f"{target.name}.neon-part.json")
    return part, checkpoint


def _checkpoint_payload(
    source: Path,
    source_size: int,
    source_mtime_ns: int,
    segment_size: int,
    completed: set[int],
) -> dict:
    return {
        "version": CHECKPOINT_VERSION,
        "source": str(source.resolve()),
        "source_size": source_size,
        "source_mtime_ns": source_mtime_ns,
        "segment_size": segment_size,
        "completed": sorted(completed),
    }


def _write_checkpoint(path: Path, payload: dict) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _load_completed_segments(
    checkpoint: Path,
    part: Path,
    source: Path,
    source_size: int,
    source_mtime_ns: int,
    segment_size: int,
    segment_count: int,
) -> set[int]:
    if not checkpoint.exists() or not part.exists() or part.stat().st_size != source_size:
        return set()
    try:
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set()
    expected = {
        "version": CHECKPOINT_VERSION,
        "source": str(source.resolve()),
        "source_size": source_size,
        "source_mtime_ns": source_mtime_ns,
        "segment_size": segment_size,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return set()
    completed = payload.get("completed", [])
    if not isinstance(completed, list):
        return set()
    return {
        index
        for index in completed
        if isinstance(index, int) and 0 <= index < segment_count
    }


def parallel_copy_file(
    source: str | Path,
    target: str | Path,
    workers: int = 8,
    *,
    segment_size: int = DEFAULT_SEGMENT_SIZE,
    block_size: int = DEFAULT_BLOCK_SIZE,
    stop_event: threading.Event | None = None,
    run_event: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Copy one file in independent ranges and resume fully completed ranges.

    The source is opened separately by every worker. This is intentional for cloud-backed
    mounted drives: independent random reads give the provider a chance to fetch multiple
    remote ranges concurrently instead of serving one sequential Robocopy stream.
    """

    source_path = Path(source)
    target_path = Path(target)
    if not source_path.is_file():
        raise FileNotFoundError(f"Source file not found: {source_path}")
    if source_path.resolve() == target_path.resolve():
        raise ValueError("Source and target must be different files")
    if workers <= 0:
        raise ValueError("workers must be positive")
    if block_size <= 0:
        raise ValueError("block_size must be positive")

    workers = max(1, min(32, int(workers)))
    source_stat = source_path.stat()
    source_size = source_stat.st_size
    source_mtime_ns = source_stat.st_mtime_ns
    segments = build_segments(source_size, segment_size)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    part_path, checkpoint_path = partial_paths(target_path)
    stopped = stop_event or threading.Event()
    runnable = run_event or threading.Event()
    if run_event is None:
        runnable.set()

    completed = _load_completed_segments(
        checkpoint_path,
        part_path,
        source_path,
        source_size,
        source_mtime_ns,
        segment_size,
        len(segments),
    )
    if not completed:
        with part_path.open("wb") as part_file:
            part_file.truncate(source_size)
        _write_checkpoint(
            checkpoint_path,
            _checkpoint_payload(
                source_path,
                source_size,
                source_mtime_ns,
                segment_size,
                completed,
            ),
        )

    copied = sum(segments[index].size for index in completed)
    copied_lock = threading.Lock()
    checkpoint_lock = threading.Lock()
    if progress is not None:
        progress(copied, source_size)

    def copy_segment(segment: CopySegment) -> None:
        nonlocal copied
        if stopped.is_set():
            raise TurboCopyStopped()
        remaining = segment.size
        offset = segment.start
        with source_path.open("rb", buffering=0) as source_file, part_path.open(
            "r+b", buffering=0
        ) as part_file:
            source_file.seek(offset)
            part_file.seek(offset)
            while remaining:
                if stopped.is_set():
                    raise TurboCopyStopped()
                runnable.wait()
                if stopped.is_set():
                    raise TurboCopyStopped()
                data = source_file.read(min(block_size, remaining))
                if not data:
                    raise OSError(
                        f"Unexpected end of source at byte {offset} of {source_size}"
                    )
                written = part_file.write(data)
                if written != len(data):
                    raise OSError(f"Short write at byte {offset}: {written} of {len(data)}")
                offset += written
                remaining -= written
                with copied_lock:
                    copied += written
                    if progress is not None:
                        progress(copied, source_size)

        with checkpoint_lock:
            completed.add(segment.index)
            _write_checkpoint(
                checkpoint_path,
                _checkpoint_payload(
                    source_path,
                    source_size,
                    source_mtime_ns,
                    segment_size,
                    completed,
                ),
            )

    pending = [segment for segment in segments if segment.index not in completed]
    if pending:
        executor = ThreadPoolExecutor(
            max_workers=min(workers, len(pending)),
            thread_name_prefix="neon-turbo",
        )
        futures = [executor.submit(copy_segment, segment) for segment in pending]
        try:
            for future in as_completed(futures):
                future.result()
        except BaseException:
            stopped.set()
            for future in futures:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)

    if stopped.is_set():
        raise TurboCopyStopped()
    os.replace(part_path, target_path)
    checkpoint_path.unlink(missing_ok=True)
    try:
        shutil.copystat(source_path, target_path)
    except OSError:
        pass
    if progress is not None:
        progress(source_size, source_size)
    return target_path
