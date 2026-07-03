import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from rednote2tg.config import HomefeedSourceConfig, KeywordSourceConfig, SourcesConfig, XhsConfig
from rednote2tg.xhs_source import XhsSource


class FakeXhsClient:
    def __init__(self):
        self.calls = []
        self.fail_query = None

    def search_notes(self, query, limit=20, sort_type_choice=0, note_type=0, note_time=0, with_detail=False):
        self.calls.append(("search", query, limit, sort_type_choice, note_type, note_time, with_detail))
        if query == self.fail_query:
            raise RuntimeError("bad search")
        return [
            {
                "note_id": f"{query}-1",
                "note_url": f"https://xhs/{query}-1",
                "title": "Title",
                "image_list": ["https://img/1.jpg"],
            }
        ]

    def homefeed_notes(self, category, limit=20, with_detail=False):
        self.calls.append(("homefeed", category, limit, with_detail))
        return [{"note_id": f"{category}-1", "url": f"https://xhs/{category}-1", "video_addr": "https://v/1.mp4"}]


def write_rules(directory: str) -> str:
    path = Path(directory) / "keyword_rules.yaml"
    path.write_text(
        """
joiner: " "
length_weights:
  3: 1.0
required_pools:
  - ["a"]
  - ["b"]
optional_groups:
  only:
    weight: 1.0
    pools:
      - "c"
time_weights:
  one_week: 1.0
""".strip(),
        encoding="utf-8",
    )
    return str(path)


def source_config(rules_path: str):
    return SourcesConfig(
        keywords=KeywordSourceConfig(True, rules_path, 5, 1, 2),
        homefeed=HomefeedSourceConfig(True, ("home",), 3),
    )


class XhsSourceTest(unittest.TestCase):
    def test_collects_keyword_and_homefeed_notes(self):
        with TemporaryDirectory() as tmp:
            client = FakeXhsClient()
            source = XhsSource(XhsConfig("cookie"), source_config(write_rules(tmp)), client=client)

            notes, errors = source.collect()

        self.assertEqual(errors, [])
        self.assertEqual([note.source.source_type for note in notes], ["keyword", "homefeed"])
        self.assertEqual(client.calls[0], ("search", "a b c", 5, 1, 2, 2, True))
        self.assertEqual(client.calls[-1], ("homefeed", "home", 3, True))

    def test_source_failure_does_not_abort_other_sources(self):
        with TemporaryDirectory() as tmp:
            client = FakeXhsClient()
            client.fail_query = "a b c"
            source = XhsSource(XhsConfig("cookie"), source_config(write_rules(tmp)), client=client)

            with self.assertLogs("rednote2tg.xhs_source", level="ERROR") as logs:
                notes, errors = source.collect()

        self.assertEqual(len(errors), 1)
        self.assertEqual(len(notes), 1)
        self.assertIn("keyword source failed: a b c", logs.output[0])

    def test_invalid_keyword_rules_skips_keyword_and_keeps_homefeed(self):
        with TemporaryDirectory() as tmp:
            bad_rules = Path(tmp) / "keyword_rules.yaml"
            bad_rules.write_text("length_weights: []", encoding="utf-8")
            client = FakeXhsClient()
            source = XhsSource(XhsConfig("cookie"), source_config(str(bad_rules)), client=client)

            notes, errors = source.collect()

        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].source_type, "keyword")
        self.assertEqual(errors[0].source_key, "generated")
        self.assertEqual([call[0] for call in client.calls], ["homefeed"])
        self.assertEqual([note.source.source_type for note in notes], ["homefeed"])


if __name__ == "__main__":
    unittest.main()
