import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from rednote2tg.config import (
    DetailFetchConfig,
    HomefeedSourceConfig,
    KeywordRuleSourceConfig,
    KeywordSourceConfig,
    SourcesConfig,
    XhsConfig,
)
from rednote2tg.xhs_source import XhsSource


class FakeXhsClient:
    def __init__(self):
        self.calls = []
        self.fail_query = None
        self.close_calls = 0

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

    def fetch_note(self, note_url):
        self.calls.append(("fetch_note", note_url))
        return {
            "note_id": "manual-1",
            "note_url": note_url,
            "title": "Manual",
            "desc": "Text",
            "nickname": "Author",
        }

    def merged_cookie_header(self):
        return "a1=test;web_session=fake"

    def close(self):
        self.close_calls += 1


class DetailFilteringClient(FakeXhsClient):
    def __init__(self):
        super().__init__()
        self.detail_urls = []

    def search_notes(self, query, limit=20, sort_type_choice=0, note_type=0, note_time=0, with_detail=False):
        self.calls.append(("search", query, limit, sort_type_choice, note_type, note_time, with_detail))
        return [
            {"note_id": "published-1", "xsec_token": "old-token"},
            {"note_id": "new-1", "xsec_token": "new-token"},
        ]

    def fetch_note(self, note_url):
        self.detail_urls.append(note_url)
        return {
            "note_id": "new-1",
            "note_url": note_url,
            "title": "New note",
            "image_list": ["https://img/new.jpg"],
        }


class DetailLimitClient(FakeXhsClient):
    def __init__(self):
        super().__init__()
        self.detail_urls = []

    def search_notes(self, query, limit=20, sort_type_choice=0, note_type=0, note_time=0, with_detail=False):
        self.calls.append(("search", query, limit, sort_type_choice, note_type, note_time, with_detail))
        return [
            {"note_id": "published-1"},
            {"note_id": "keyword-1"},
            {"note_id": "keyword-2"},
        ]

    def homefeed_notes(self, category, limit=20, with_detail=False):
        self.calls.append(("homefeed", category, limit, with_detail))
        return [
            {"note_id": "home-1"},
            {"note_id": "home-2"},
        ]

    def fetch_note(self, note_url):
        self.detail_urls.append(note_url)
        note_id = note_url.split("/explore/", 1)[1].split("?", 1)[0]
        return {
            "note_id": note_id,
            "note_url": note_url,
            "title": note_id,
        }


class PagedClient(FakeXhsClient):
    def __init__(self):
        super().__init__()
        self.search_page_calls = []

    def search_notes(
        self,
        query,
        limit=20,
        sort_type_choice=0,
        note_type=0,
        note_time=0,
        with_detail=False,
        page=1,
        search_id=None,
        return_meta=False,
    ):
        self.search_page_calls.append((query, page, search_id, return_meta))
        items = [{"note_id": f"page-{page}", "xsec_token": f"token-{page}"}]
        if return_meta:
            return {
                "items": items,
                "page": page,
                "search_id": search_id or "search-1",
                "has_more": page == 1,
            }
        return items

    def fetch_note(self, note_url):
        note_id = note_url.split("/explore/", 1)[1].split("?", 1)[0]
        return {
            "note_id": note_id,
            "note_url": note_url,
            "title": note_id,
        }


class DeterministicRandom:
    def __init__(self, values):
        self.values = iter(values)

    def random(self):
        return next(self.values)

    def choice(self, values):
        return values[0]

    def uniform(self, low: float, high: float) -> float:
        return (low + high) / 2


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


def write_named_rules(directory: str, filename: str, required_prefix: str, time_key: str) -> str:
    path = Path(directory) / filename
    path.write_text(
        f"""
joiner: " "
length_weights:
  3: 1.0
required_pools:
  - ["{required_prefix}1"]
  - ["{required_prefix}2"]
optional_groups:
  only:
    weight: 1.0
    pools:
      - "{required_prefix}3"
time_weights:
  {time_key}: 1.0
""".strip(),
        encoding="utf-8",
    )
    return str(path)


def source_config(rules_path: str, detail_fetch: DetailFetchConfig | None = None):
    return SourcesConfig(
        keywords=KeywordSourceConfig(True, rules_path, 5, 1, 2),
        homefeed=HomefeedSourceConfig(True, ("home",), 3),
        detail_fetch=detail_fetch or DetailFetchConfig(),
    )


def weighted_source_config(rules_a_path: str, rules_b_path: str):
    return SourcesConfig(
        keywords=KeywordSourceConfig(
            enabled=True,
            search_limit_per_query=5,
            sort_type=1,
            note_type=2,
            rules=(
                KeywordRuleSourceConfig("A", 0.7, rules_a_path),
                KeywordRuleSourceConfig("B", 0.3, rules_b_path),
            ),
        ),
        homefeed=HomefeedSourceConfig(False, (), 3),
    )


class XhsSourceTest(unittest.TestCase):
    def test_owned_client_is_closed_once(self):
        with TemporaryDirectory() as tmp:
            client = FakeXhsClient()
            with patch.object(XhsSource, "_create_client", return_value=client):
                source = XhsSource(XhsConfig("cookie"), source_config(write_rules(tmp)))

            source.close()
            source.close()

        self.assertEqual(client.close_calls, 1)

    def test_injected_client_is_not_closed(self):
        with TemporaryDirectory() as tmp:
            client = FakeXhsClient()
            source = XhsSource(XhsConfig("cookie"), source_config(write_rules(tmp)), client=client)

            source.close()

        self.assertEqual(client.close_calls, 0)

    def test_replace_client_closes_previous_owned_client(self):
        with TemporaryDirectory() as tmp:
            old_client = FakeXhsClient()
            new_client = FakeXhsClient()
            with patch.object(XhsSource, "_create_client", return_value=old_client):
                source = XhsSource(XhsConfig("cookie"), source_config(write_rules(tmp)))

            source.replace_client(new_client, owned=True)
            source.close()

        self.assertIs(source.client, new_client)
        self.assertEqual(old_client.close_calls, 1)
        self.assertEqual(new_client.close_calls, 1)

    def test_create_client_uses_auth_backed_facade(self):
        auth = object()
        client = object()
        proxies = {"http": "http://proxy"}
        config = XhsConfig("a1=browser; web_session=session", proxies)

        with patch("xhs_utils.xhs_pc.XHSPcAuth.from_cookie", return_value=auth) as from_cookie:
            with patch("spider_xhs.XhsPcAuthClient", return_value=client) as facade:
                result = XhsSource._create_client(config)

        self.assertIs(result, client)
        from_cookie.assert_called_once_with(config.cookies, proxies=proxies)
        facade.assert_called_once_with(auth)

    def test_structured_upstream_error_text_is_preserved(self):
        from spider_xhs import XhsApiError

        class FailingClient(FakeXhsClient):
            def search_notes(self, *args, **kwargs):
                raise XhsApiError("search_notes", "request blocked")

        with TemporaryDirectory() as tmp:
            source = XhsSource(
                XhsConfig("cookie"),
                source_config(write_rules(tmp)),
                client=FailingClient(),
            )
            _, errors = source.collect()

        self.assertEqual(len(errors), 1)
        self.assertIn("search_notes", errors[0].message)
        self.assertIn("request blocked", errors[0].message)

    def test_collects_keyword_and_homefeed_notes(self):
        with TemporaryDirectory() as tmp:
            client = FakeXhsClient()
            source = XhsSource(XhsConfig("cookie"), source_config(write_rules(tmp)), client=client)

            notes, errors = source.collect()

        self.assertEqual(errors, [])
        self.assertEqual([note.source.source_type for note in notes], ["keyword", "homefeed"])
        self.assertEqual(client.calls[0], ("search", "a b c", 5, 1, 2, 2, False))
        self.assertEqual(client.calls[1], ("homefeed", "home", 3, False))
        self.assertEqual(client.calls[2][0], "fetch_note")
        self.assertEqual(client.calls[3][0], "fetch_note")

    def test_collection_session_reuses_keyword_and_search_id_across_pages(self):
        with TemporaryDirectory() as tmp:
            client = PagedClient()
            config = SourcesConfig(
                keywords=KeywordSourceConfig(True, write_rules(tmp), 5, 1, 2),
                homefeed=HomefeedSourceConfig(False, (), 3),
            )
            source = XhsSource(XhsConfig("cookie"), config, client=client)
            session = source.start_collection()

            first = session.collect_next()
            second = session.collect_next()

        self.assertFalse(first.exhausted)
        self.assertTrue(second.exhausted)
        self.assertEqual([note.note_id for note in first.notes], ["page-1"])
        self.assertEqual([note.note_id for note in second.notes], ["page-2"])
        self.assertEqual(
            client.search_page_calls,
            [("a b c", 1, None, True), ("a b c", 2, "search-1", True)],
        )
        self.assertEqual(source.last_keyword_query.query, "a b c")

    def test_collect_filters_active_ids_before_fetching_details(self):
        with TemporaryDirectory() as tmp:
            client = DetailFilteringClient()
            config = SourcesConfig(
                keywords=KeywordSourceConfig(True, write_rules(tmp), 5, 1, 2),
                homefeed=HomefeedSourceConfig(False, (), 3),
            )
            source = XhsSource(XhsConfig("cookie"), config, client=client)

            notes, errors = source.collect(active_note_ids={"published-1"})

        self.assertEqual(errors, [])
        self.assertEqual([note.note_id for note in notes], ["new-1"])
        self.assertEqual(client.calls, [("search", "a b c", 5, 1, 2, 2, False)])
        self.assertEqual(
            client.detail_urls,
            ["https://www.xiaohongshu.com/explore/new-1?xsec_token=new-token&xsec_source=pc_search"],
        )

    def test_collect_limits_global_detail_fetches_after_active_dedup(self):
        with TemporaryDirectory() as tmp:
            client = DetailLimitClient()
            source = XhsSource(XhsConfig("cookie"), source_config(write_rules(tmp)), client=client)

            with self.assertLogs("rednote2tg.xhs_source", level="INFO") as logs:
                notes, errors = source.collect(active_note_ids={"published-1"}, detail_limit=3)

        self.assertEqual(errors, [])
        self.assertEqual([note.note_id for note in notes], ["keyword-1", "keyword-2", "home-1"])
        self.assertEqual(
            client.detail_urls,
            [
                "https://www.xiaohongshu.com/explore/keyword-1?xsec_source=pc_search",
                "https://www.xiaohongshu.com/explore/keyword-2?xsec_source=pc_search",
                "https://www.xiaohongshu.com/explore/home-1?xsec_source=pc_feed",
            ],
        )
        self.assertTrue(
            any(
                "note detail page finished: page=1 notes=3 errors=0 candidates=4 has_more=False" in output
                for output in logs.output
            )
        )
        self.assertTrue(
            any(
                "legacy detail fetch limit: limit=3 eligible_candidates=4 selected_candidates=3" in output
                for output in logs.output
            )
        )

    def test_collect_sleeps_only_between_detail_fetches(self):
        with TemporaryDirectory() as tmp:
            client = FakeXhsClient()
            config = source_config(
                write_rules(tmp),
                DetailFetchConfig(fixed_delay_seconds=1.5, random_delay_seconds=0.8),
            )
            source = XhsSource(
                XhsConfig("cookie"),
                config,
                client=client,
                rng=DeterministicRandom([0.0] * 10),
            )

            with patch("rednote2tg.xhs_source.time.sleep") as sleep:
                notes, errors = source.collect()

        self.assertEqual(errors, [])
        self.assertEqual(len(notes), 2)
        sleep.assert_called_once_with(1.9)
        self.assertEqual(client.calls[2][0], "fetch_note")
        self.assertEqual(client.calls[3][0], "fetch_note")

    def test_collect_does_not_sleep_for_zero_or_single_fetch_delay(self):
        with TemporaryDirectory() as tmp:
            single_client = DetailFilteringClient()
            single_config = SourcesConfig(
                keywords=KeywordSourceConfig(True, write_rules(tmp), 5, 1, 2),
                homefeed=HomefeedSourceConfig(False, (), 3),
                detail_fetch=DetailFetchConfig(fixed_delay_seconds=2),
            )
            single_source = XhsSource(
                XhsConfig("cookie"),
                single_config,
                client=single_client,
            )
            with patch("rednote2tg.xhs_source.time.sleep") as single_sleep:
                single_source.collect(active_note_ids={"published-1"})

            zero_client = FakeXhsClient()
            zero_source = XhsSource(
                XhsConfig("cookie"),
                source_config(write_rules(tmp), DetailFetchConfig()),
                client=zero_client,
            )
            with patch("rednote2tg.xhs_source.time.sleep") as zero_sleep:
                zero_source.collect()

        single_sleep.assert_not_called()
        zero_sleep.assert_not_called()

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
        self.assertEqual([call[0] for call in client.calls], ["homefeed", "fetch_note"])
        self.assertEqual([note.source.source_type for note in notes], ["homefeed"])

    def test_weighted_rules_select_b_and_search_once(self):
        with TemporaryDirectory() as tmp:
            rules_a = write_named_rules(tmp, "keyword_rules_A.yaml", "a", "one_week")
            rules_b = write_named_rules(tmp, "keyword_rules_B.yaml", "b", "half_year")
            client = FakeXhsClient()
            source = XhsSource(
                XhsConfig("cookie"),
                weighted_source_config(rules_a, rules_b),
                client=client,
                rng=DeterministicRandom([0.8, 0.0, 0.0, 0.0]),
            )

            notes, errors = source.collect()

        self.assertEqual(errors, [])
        self.assertEqual([call[0] for call in client.calls], ["search", "fetch_note"])
        self.assertEqual(client.calls[0], ("search", "b1 b2 b3", 5, 1, 2, 3, False))
        self.assertEqual(source.last_keyword_rule_name, "B")
        self.assertEqual(source.last_keyword_query.query, "b1 b2 b3")
        self.assertEqual([note.source.source_type for note in notes], ["keyword"])

    def test_fetch_note_url_normalizes_single_manual_note(self):
        client = FakeXhsClient()
        source = XhsSource(XhsConfig("cookie"), source_config("unused.yaml"), client=client)

        note = source.fetch_note_url("https://www.xiaohongshu.com/explore/manual-1?xsec_token=abc")

        self.assertIsNotNone(note)
        self.assertEqual(note.note_id, "manual-1")
        self.assertEqual(note.title, "Manual")
        self.assertEqual(note.description, "Text")
        self.assertEqual(note.author, "Author")
        self.assertEqual(note.source.source_type, "manual")
        self.assertEqual(client.calls, [("fetch_note", "https://www.xiaohongshu.com/explore/manual-1?xsec_token=abc")])


if __name__ == "__main__":
    unittest.main()
