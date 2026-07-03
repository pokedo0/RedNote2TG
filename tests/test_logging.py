import logging
import unittest

from rednote2tg.logging import configure_logging


class LoggingTest(unittest.TestCase):
    def test_configure_logging_replaces_existing_handlers_and_enables_spider_xhs(self):
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        original_level = root.level
        spider_logger = logging.getLogger("spider_xhs")
        original_spider_level = spider_logger.level
        existing_handler = logging.NullHandler()

        try:
            root.handlers = [existing_handler]

            configure_logging(logging.INFO)

            self.assertNotIn(existing_handler, root.handlers)
            self.assertEqual(root.level, logging.INFO)
            self.assertEqual(spider_logger.level, logging.INFO)
        finally:
            for handler in root.handlers:
                handler.close()
            root.handlers = original_handlers
            root.setLevel(original_level)
            spider_logger.setLevel(original_spider_level)


if __name__ == "__main__":
    unittest.main()
