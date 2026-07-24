from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from neon_drive.copy_engines import (
    RcloneOptions,
    copy_engine_for_source,
    rclone_arguments,
)


class CopyEngineTests(unittest.TestCase):
    def test_rclone_file_uses_copyto_and_selected_chunk_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "large.bin"
            source.write_bytes(b"neon")
            destination = root / "target"
            options = RcloneOptions(
                chunk_size_mib=128,
                multi_thread_cutoff_mib=512,
                multi_thread_streams=8,
                transfers=4,
                checkers=16,
                buffer_size_mib=32,
                retries=5,
                low_level_retries=20,
                checksum=True,
            )

            args, target = rclone_arguments(str(source), destination, options)

            self.assertEqual(args[0], "copyto")
            self.assertEqual(target, destination / source.name)
            self.assertIn("--multi-thread-chunk-size=128Mi", args)
            self.assertIn("--multi-thread-cutoff=512Mi", args)
            self.assertIn("--multi-thread-streams=8", args)
            self.assertIn("--checksum", args)

    def test_rclone_directory_keeps_the_source_folder_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "folder"
            source.mkdir()
            destination = root / "target"

            args, target = rclone_arguments(str(source), destination)

            self.assertEqual(args[0], "copy")
            self.assertEqual(target, destination / "folder")
            self.assertIn("--create-empty-src-dirs", args)

    def test_hybrid_never_assigns_two_engines_to_one_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_source = root / "movie.mkv"
            file_source.write_bytes(b"neon")
            folder_source = root / "collection"
            folder_source.mkdir()

            self.assertEqual(copy_engine_for_source("hybrid", file_source), "rclone")
            self.assertEqual(copy_engine_for_source("hybrid", folder_source), "robocopy")
            self.assertEqual(copy_engine_for_source("robocopy", file_source), "robocopy")


if __name__ == "__main__":
    unittest.main()
