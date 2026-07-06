import gzip
import logging
import os
import tempfile
import time
import unittest
from pathlib import Path

from rednote2tg.config import LoggingConfig
from rednote2tg.logging import configure_logging


class LoggingTest(unittest.TestCase):
    def setUp(self):
        self.root = logging.getLogger()
        self.original_handlers = list(self.root.handlers)
        self.original_level = self.root.level
        self.spider_logger = logging.getLogger("spider_xhs")
        self.original_spider_level = self.spider_logger.level
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self):
        for handler in self.root.handlers:
            handler.close()
        self.root.handlers = self.original_handlers
        self.root.setLevel(self.original_level)
        self.spider_logger.setLevel(self.original_spider_level)
        self.temp_dir.cleanup()

    def test_configure_logging_replaces_existing_handlers_and_enables_spider_xhs(self):
        existing_handler = logging.NullHandler()
        self.root.handlers = [existing_handler]

        configure_logging(logging.INFO)

        self.assertNotIn(existing_handler, self.root.handlers)
        self.assertEqual(self.root.level, logging.INFO)
        self.assertEqual(self.spider_logger.level, logging.INFO)

    def test_configure_logging_writes_to_file(self):
        path = self.temp_path / "rednote2tg.log"
        configure_logging(
            LoggingConfig(
                console_enabled=False,
                file_path=str(path),
                max_bytes=1024,
            ),
        )

        logging.getLogger("rednote2tg.test").info("file log works")
        for handler in self.root.handlers:
            handler.flush()

        self.assertIn("file log works", path.read_text(encoding="utf-8"))

    def test_configure_logging_keeps_console_handler_when_enabled(self):
        configure_logging(
            LoggingConfig(
                console_enabled=True,
                file_enabled=True,
                file_path=str(self.temp_path / "rednote2tg.log"),
            ),
        )

        self.assertTrue(any(isinstance(handler, logging.StreamHandler) for handler in self.root.handlers))
        self.assertGreaterEqual(len(self.root.handlers), 2)

    def test_rotated_logs_are_compressed(self):
        path = self.temp_path / "rednote2tg.log"
        configure_logging(
            LoggingConfig(
                console_enabled=False,
                file_path=str(path),
                max_bytes=128,
                max_files=3,
                compress_rotated=True,
            ),
        )

        logger = logging.getLogger("rednote2tg.test")
        for index in range(10):
            logger.info("compressed rotation %s %s", index, "x" * 200)
        for handler in self.root.handlers:
            handler.flush()

        rotated = sorted(path.parent.glob("rednote2tg.log.*.gz"))
        self.assertTrue(rotated)
        with gzip.open(rotated[0], "rt", encoding="utf-8") as fh:
            self.assertIn("compressed rotation", fh.read())

    def test_old_logs_are_removed_by_age(self):
        path = self.temp_path / "rednote2tg.log"
        old_log = self.temp_path / "rednote2tg.log.1.gz"
        old_log.write_text("old", encoding="utf-8")
        old_mtime = time.time() - 3 * 24 * 60 * 60
        old_log.touch()
        os.utime(old_log, (old_mtime, old_mtime))

        configure_logging(
            LoggingConfig(
                console_enabled=False,
                file_path=str(path),
                retention_days=1,
                max_files=20,
            ),
        )

        self.assertFalse(old_log.exists())

    def test_old_logs_are_removed_by_file_count(self):
        path = self.temp_path / "rednote2tg.log"
        newest = self.temp_path / "rednote2tg.log.1.gz"
        older = self.temp_path / "rednote2tg.log.2.gz"
        oldest = self.temp_path / "rednote2tg.log.3.gz"
        for index, candidate in enumerate((newest, older, oldest)):
            candidate.write_text(candidate.name, encoding="utf-8")
            mtime = time.time() - index
            os.utime(candidate, (mtime, mtime))

        configure_logging(
            LoggingConfig(
                console_enabled=False,
                file_path=str(path),
                retention_days=14,
                max_files=1,
            ),
        )

        self.assertTrue(newest.exists())
        self.assertFalse(older.exists())
        self.assertFalse(oldest.exists())


if __name__ == "__main__":
    unittest.main()
