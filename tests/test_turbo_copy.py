from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from neon_drive.turbo_copy import (
    TurboCopyStopped,
    build_segments,
    parallel_copy_file,
    partial_paths,
)


class TurboCopyTests(unittest.TestCase):
    def test_segments_cover_file_exactly_once(self) -> None:
        segments = build_segments(10, segment_size=4)

        self.assertEqual([(item.start, item.end) for item in segments], [(0, 4), (4, 8), (8, 10)])
        self.assertEqual(sum(item.size for item in segments), 10)

    def test_parallel_copy_preserves_contents_and_reports_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.bin"
            target = root / "output" / "target.bin"
            payload = bytes(range(256)) * (1024 * 17)
            source.write_bytes(payload)
            samples: list[int] = []

            result = parallel_copy_file(
                source,
                target,
                workers=4,
                segment_size=512 * 1024,
                block_size=64 * 1024,
                progress=lambda copied, _total: samples.append(copied),
            )

            self.assertEqual(result, target)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(samples[-1], len(payload))
            self.assertEqual(samples, sorted(samples))
            part, checkpoint = partial_paths(target)
            self.assertFalse(part.exists())
            self.assertFalse(checkpoint.exists())

    def test_stopped_copy_keeps_checkpoint_and_can_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "large.bin"
            target = root / "downloaded.bin"
            payload = bytes(range(251)) * (1024 * 32)
            source.write_bytes(payload)
            stopped = threading.Event()

            def cancel_after_one_segment(copied: int, _total: int) -> None:
                if copied >= 512 * 1024:
                    stopped.set()

            with self.assertRaises(TurboCopyStopped):
                parallel_copy_file(
                    source,
                    target,
                    workers=2,
                    segment_size=512 * 1024,
                    block_size=64 * 1024,
                    stop_event=stopped,
                    progress=cancel_after_one_segment,
                )

            part, checkpoint = partial_paths(target)
            self.assertTrue(part.exists())
            self.assertTrue(checkpoint.exists())
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertIn("completed", saved)
            self.assertFalse(target.exists())

            stopped.clear()
            parallel_copy_file(
                source,
                target,
                workers=4,
                segment_size=512 * 1024,
                block_size=64 * 1024,
                stop_event=stopped,
            )
            self.assertEqual(target.read_bytes(), payload)
            self.assertFalse(part.exists())
            self.assertFalse(checkpoint.exists())


if __name__ == "__main__":
    unittest.main()
