"""Optional same-volume staging: completed files become visible atomically."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from uuid import uuid4


class TransferBuffer:
    def __init__(self, destination: Path, size: int) -> None:
        self.destination = destination.resolve()
        self.destination.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.destination).free < max(0, size) + 64 * 1024 * 1024:
            raise OSError("Недостаточно места для буфера и запаса 64 МиБ.")
        self.root = self.destination / (".neon-buffer-" + uuid4().hex)
        self.root.mkdir()

    def commit(self, name: str, size: int) -> None:
        if Path(name).name != name:
            raise ValueError("Недопустимое имя файла буфера.")
        staged = self.root / name
        if not staged.is_file() or staged.stat().st_size != size:
            raise OSError("Размер файла в буфере не совпадает с исходником.")
        # Same volume, no second 25 GB copy and no partially replaced final file.
        os.replace(staged, self.destination / name)
        self.discard()

    def discard(self) -> None:
        # Only this instance's freshly allocated directory may be removed.
        resolved = self.root.resolve()
        if resolved.parent != self.destination or not resolved.name.startswith(".neon-buffer-"):
            raise OSError("Небезопасный путь буфера.")
        if self.root.exists():
            shutil.rmtree(self.root)
