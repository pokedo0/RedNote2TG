import asyncio
import unittest
from unittest.mock import Mock, patch

from rednote2tg.main import _shutdown_runtime, async_main


class MainLifecycleTest(unittest.IsolatedAsyncioTestCase):
    def test_shutdown_closes_source_before_store(self):
        calls = []
        scheduler = Mock()
        source = Mock()
        store = Mock()
        scheduler.shutdown.side_effect = lambda **kwargs: calls.append(("scheduler", kwargs))
        source.close.side_effect = lambda: calls.append(("source", None))
        store.close.side_effect = lambda: calls.append(("store", None))

        _shutdown_runtime(scheduler, source, store)

        self.assertEqual(
            calls,
            [
                ("scheduler", {"wait": False}),
                ("source", None),
                ("store", None),
            ],
        )

    def test_shutdown_handles_nones_and_exceptions(self):
        scheduler = Mock()
        scheduler.shutdown.side_effect = RuntimeError("shutdown error")
        source = Mock()
        source.close.side_effect = RuntimeError("close error")
        store = Mock()
        store.close.side_effect = RuntimeError("store close error")

        # Must not raise
        _shutdown_runtime(None, None, None)
        _shutdown_runtime(scheduler, source, store)

    async def test_async_main_logs_fatal_startup_error(self):
        with patch("rednote2tg.main.load_config", side_effect=ValueError("invalid config")), \
             patch("rednote2tg.main.logger.exception") as mock_log_exc:
            with self.assertRaises(ValueError):
                await async_main("invalid_path.yaml")
            mock_log_exc.assert_called_once_with("Fatal startup error")


if __name__ == "__main__":
    unittest.main()
