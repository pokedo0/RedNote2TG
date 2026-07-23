import unittest
from unittest.mock import Mock

from rednote2tg.main import _shutdown_runtime


class MainLifecycleTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
