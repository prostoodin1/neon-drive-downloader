import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from PySide6.QtCore import QCoreApplication
from neon_drive import single_instance


class SingleInstanceTests(unittest.TestCase):
    def test_macos_lock_prevents_duplicate_and_allows_restart(self) -> None:
        app = QCoreApplication.instance() or QCoreApplication([])
        self.assertIsNotNone(app)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(single_instance.sys, "platform", "darwin"),
            patch.object(single_instance, "app_data_directory", return_value=Path(directory)),
            patch.object(single_instance, "SERVER_NAME", "neon-test-" + uuid4().hex),
        ):
            first = single_instance.InstanceServer(lambda request: {"ok": True})
            second = single_instance.InstanceServer(lambda request: {"ok": True})
            try:
                self.assertTrue(first.listen())
                self.assertFalse(second.listen())
                first.server.close()
                first._instance_lock.unlock()
                self.assertTrue(second.listen())
            finally:
                first.server.close()
                second.server.close()
                if first._instance_lock:
                    first._instance_lock.unlock()
                if second._instance_lock:
                    second._instance_lock.unlock()


if __name__ == "__main__":
    unittest.main()
