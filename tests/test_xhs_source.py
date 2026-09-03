import random
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

    def get_homefeed_recommend(self, category, cursor_score, refresh_type, note_index, *, num=20, need_num=10):
        self.calls.append(("get_homefeed_recommend", category, cursor_score, refresh_type, note_index, num, need_num))
        return True, "success", {
            "data": {
                "items": [
                    {
                        "note_id": f"{category}-1",
                        "url": f"https://xhs/{category}-1",
                        "video_addr": "https://v/1.mp4",
                        "note_card": {
                            "interact_info": {
                                "liked_count": "100",
                                "collected_count": "50",
                                "comment_count": "20",
                                "shared_count": "10",
                            }
                        },
                    }
                ],
                "cursor_score": f"cursor-{category}",
            }
        }

    def fetch_note(self, note_url):
        self.calls.append(("fetch_note", note_url))
        note_id = "manual-1"
        if "/" in note_url:
            candidate_id = note_url.split("?")[0].rstrip("/").split("/")[-1]
            if candidate_id:
                note_id = candidate_id
        return {
            "note_id": note_id,
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
            {"note_id": "keyword-3"},
        ]

    def fetch_note(self, note_url):
        self.detail_urls.append(note_url)
        note_id = note_url.split("/explore/", 1)[1].split("?", 1)[0]
        return {
            "note_id": note_id,
            "note_url": note_url,
            "title": note_id,
        }


class InteractionFilteringClient(FakeXhsClient):
    def __init__(self):
        super().__init__()
        self.detail_urls = []

    def search_notes(self, query, limit=20, sort_type_choice=0, note_type=0, note_time=0, with_detail=False):
        return [
            self._card("filtered-all-zero", "0", "0", "0", "0"),
            self._card("filtered-two-zero", "0", "1", "0", "2"),
            self._card("kept-one-zero", "0", "1", "2", "3"),
            self._card("kept-base-hot", "1", "1", "0", "0"),
            self._card("filtered-collection-zero", "2", "0", "0", "0"),
            {"id": "incomplete", "note_card": {"display_title": "Incomplete", "interact_info": {"liked_count": "0"}}},
        ]

    @staticmethod
    def _card(note_id, liked, collected, comment, shared):
        return {
            "id": note_id,
            "note_card": {
                "display_title": note_id,
                "interact_info": {
                    "liked_count": liked,
                    "collected_count": collected,
                    "comment_count": comment,
                    "shared_count": shared,
                },
            },
        }

    def fetch_note(self, note_url):
        self.detail_urls.append(note_url)
        note_id = note_url.split("/explore/", 1)[1].split("?", 1)[0]
        return {"note_id": note_id, "note_url": note_url, "title": note_id}


class InteractionPaginationClient(InteractionFilteringClient):
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
        self.search_page_calls.append(page)
        if page == 1:
            items = [self._card("filtered-page-1", "0", "0", "0", "0")]
            has_more = True
        else:
            items = [self._card("kept-page-2", "1", "1", "0", "0")]
            has_more = False
        return {
            "items": items,
            "page": page,
            "search_id": search_id or "search-1",
            "has_more": has_more,
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


def source_config(rules_path: str, detail_fetch: DetailFetchConfig | None = None, homefeed_enabled: bool = False):
    return SourcesConfig(
        keywords=KeywordSourceConfig(rules_path=rules_path, search_limit_per_query=5, sort_type=1, note_type=2, weight=1.0),
        homefeed=HomefeedSourceConfig(weight=1.0 if homefeed_enabled else 0.0, limit_per_page=3),
        detail_fetch=detail_fetch or DetailFetchConfig(),
    )


def weighted_source_config(rules_a_path: str, rules_b_path: str):
    return SourcesConfig(
        keywords=KeywordSourceConfig(
            search_limit_per_query=5,
            sort_type=1,
            note_type=2,
            rules=(
                KeywordRuleSourceConfig("A", 0.7, rules_a_path),
                KeywordRuleSourceConfig("B", 0.3, rules_b_path),
            ),
            weight=1.0,
        ),
        homefeed=HomefeedSourceConfig(weight=0.0, limit_per_page=3),
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
        config = XhsConfig("a1=browser; web_session=session")

        with patch("xhs_utils.xhs_pc.XHSPcAuth.from_cookie", return_value=auth) as from_cookie:
            with patch("spider_xhs.XhsPcAuthClient", return_value=client) as facade:
                result = XhsSource._create_client(config)

        self.assertIs(result, client)
        from_cookie.assert_called_once_with(config.cookies)
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

    def test_collects_homefeed_notes(self):
        with TemporaryDirectory() as tmp:
            client = FakeXhsClient()
            config = SourcesConfig(
                keywords=KeywordSourceConfig(rules_path=write_rules(tmp), weight=0.0),
                homefeed=HomefeedSourceConfig(weight=1.0, limit_per_page=3),
            )
            source = XhsSource(XhsConfig("cookie"), config, client=client)

            notes, errors = source.collect()

        self.assertEqual(errors, [])
        self.assertEqual([note.source.source_type for note in notes], ["homefeed"])
        self.assertEqual(client.calls[0][0], "get_homefeed_recommend")
        self.assertEqual(client.calls[1][0], "fetch_note")

    def test_collection_session_reuses_keyword_and_search_id_across_pages(self):
        with TemporaryDirectory() as tmp:
            client = PagedClient()
            config = SourcesConfig(
                keywords=KeywordSourceConfig(rules_path=write_rules(tmp), search_limit_per_query=5, sort_type=1, note_type=2, weight=1.0),
                homefeed=HomefeedSourceConfig(weight=0.0),
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
                keywords=KeywordSourceConfig(rules_path=write_rules(tmp), search_limit_per_query=5, sort_type=1, note_type=2, weight=1.0),
                homefeed=HomefeedSourceConfig(weight=0.0),
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
                notes, errors = source.collect(active_note_ids={"published-1"}, detail_limit=2)

        self.assertEqual(errors, [])
        self.assertEqual([note.note_id for note in notes], ["keyword-1", "keyword-2"])
        self.assertEqual(
            client.detail_urls,
            [
                "https://www.xiaohongshu.com/explore/keyword-1?xsec_source=pc_search",
                "https://www.xiaohongshu.com/explore/keyword-2?xsec_source=pc_search",
            ],
        )
        self.assertTrue(
            any(
                "note detail batch finished: source=keyword page=1 notes=2 errors=0 remaining_candidates=1 has_more=False exhausted=False" in output
                for output in logs.output
            )
        )

    def test_keyword_interaction_filter_skips_details_and_keeps_incomplete_data(self):
        with TemporaryDirectory() as tmp:
            client = InteractionFilteringClient()
            config = SourcesConfig(
                keywords=KeywordSourceConfig(rules_path=write_rules(tmp), search_limit_per_query=5, sort_type=1, note_type=2, weight=1.0),
                homefeed=HomefeedSourceConfig(weight=0.0),
            )
            source = XhsSource(XhsConfig("cookie"), config, client=client)

            batch = source.start_collection().collect_next()

        self.assertEqual(
            [note.note_id for note in batch.notes],
            ["kept-one-zero", "kept-base-hot", "incomplete"],
        )
        self.assertEqual(
            [note.note_id for note in batch.filtered_notes],
            ["filtered-all-zero", "filtered-two-zero", "filtered-collection-zero"],
        )
        self.assertEqual(
            client.detail_urls,
            [
                "https://www.xiaohongshu.com/explore/kept-one-zero?xsec_source=pc_search",
                "https://www.xiaohongshu.com/explore/kept-base-hot?xsec_source=pc_search",
                "https://www.xiaohongshu.com/explore/incomplete?xsec_source=pc_search",
            ],
        )
        self.assertEqual(
            batch.filtered_notes[1].reason,
            "low_interaction: liked=0, collected=1, comment=0, shared=2",
        )

    def test_homefeed_is_subject_to_interaction_filter(self):
        with TemporaryDirectory() as tmp:
            client = FakeXhsClient()
            calls = 0
            def mock_feed(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls > 1:
                    return True, "ok", {"data": {"items": [], "cursor_score": ""}}
                return True, "ok", {
                    "data": {
                        "items": [
                            InteractionFilteringClient._card("home-low", "0", "0", "0", "0")
                        ],
                        "cursor_score": "c1",
                    }
                }
            client.get_homefeed_recommend = mock_feed
            config = SourcesConfig(
                keywords=KeywordSourceConfig(rules_path=write_rules(tmp), weight=0.0),
                homefeed=HomefeedSourceConfig(weight=1.0, limit_per_page=3),
            )
            source = XhsSource(XhsConfig("cookie"), config, client=client)

            batch = source.start_collection().collect_next()

        self.assertEqual(len(batch.notes), 0)
        self.assertIn("home-low", [note.note_id for note in batch.filtered_notes])

    def test_interaction_filters_are_returned_when_next_page_has_eligible_note(self):
        with TemporaryDirectory() as tmp:
            client = InteractionPaginationClient()
            config = SourcesConfig(
                keywords=KeywordSourceConfig(rules_path=write_rules(tmp), search_limit_per_query=5, sort_type=1, note_type=2, weight=1.0),
                homefeed=HomefeedSourceConfig(weight=0.0),
            )
            source = XhsSource(XhsConfig("cookie"), config, client=client)

            batch = source.start_collection().collect_next()

        self.assertEqual(client.search_page_calls, [1, 2])
        self.assertEqual([item.note_id for item in batch.filtered_notes], ["filtered-page-1"])
        self.assertEqual([item.note_id for item in batch.notes], ["kept-page-2"])
        self.assertEqual(
            client.detail_urls,
            ["https://www.xiaohongshu.com/explore/kept-page-2?xsec_source=pc_search"],
        )

    def test_collect_sleeps_only_between_detail_fetches(self):
        with TemporaryDirectory() as tmp:
            client = FakeXhsClient()
            def fake_search(query, *args, **kwargs):
                client.calls.append(("search", query))
                return [
                    {"note_id": f"{query}-1", "note_url": f"https://xhs/{query}-1", "title": "Title 1"},
                    {"note_id": f"{query}-2", "note_url": f"https://xhs/{query}-2", "title": "Title 2"},
                ]
            client.search_notes = fake_search
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
        self.assertEqual(client.calls[1][0], "fetch_note")
        self.assertEqual(client.calls[2][0], "fetch_note")

    def test_collect_does_not_sleep_for_zero_or_single_fetch_delay(self):
        with TemporaryDirectory() as tmp:
            single_client = DetailFilteringClient()
            single_config = SourcesConfig(
                keywords=KeywordSourceConfig(rules_path=write_rules(tmp), search_limit_per_query=5, sort_type=1, note_type=2, weight=1.0),
                homefeed=HomefeedSourceConfig(weight=0.0),
                detail_fetch=DetailFetchConfig(fixed_delay_seconds=2),
            )
            single_source = XhsSource(
                XhsConfig("cookie"),
                single_config,
                client=single_client,
                rng=DeterministicRandom([0.0] * 10),
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
        self.assertEqual(len(notes), 0)
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
        self.assertEqual(notes, [])

    def test_homefeed_pagination_refresh_type_and_note_index(self):
        with TemporaryDirectory() as tmp:
            client = FakeXhsClient()
            calls = []
            def fake_get_homefeed(category, cursor_score, refresh_type, note_index, **kwargs):
                calls.append((refresh_type, cursor_score, note_index, kwargs.get("num"), kwargs.get("need_num")))
                page = len(calls)
                return True, "ok", {
                    "data": {
                        "items": [
                            {
                                "note_id": f"page-{page}-1",
                                "url": f"https://xhs/page-{page}-1",
                                "note_card": {
                                    "interact_info": {"liked_count": "10", "collected_count": "5"}
                                }
                            }
                        ],
                        "cursor_score": f"cursor-{page}",
                    }
                }
            client.get_homefeed_recommend = fake_get_homefeed
            config = SourcesConfig(
                keywords=KeywordSourceConfig(rules_path=write_rules(tmp), weight=0.0),
                homefeed=HomefeedSourceConfig(weight=1.0, limit_per_page=20),
            )
            source = XhsSource(XhsConfig("cookie"), config, client=client)
            session = source.start_collection()

            first_batch = session.collect_next()
            second_batch = session.collect_next()

            self.assertEqual(calls[0], (1, "", 0, 20, 10))
            self.assertEqual(calls[1], (3, "cursor-1", 1, 20, 10))
            self.assertEqual([n.note_id for n in first_batch.notes], ["page-1-1"])
            self.assertEqual([n.note_id for n in second_batch.notes], ["page-2-1"])

    def test_source_weight_selection(self):
        with TemporaryDirectory() as tmp:
            client = FakeXhsClient()
            config_kw = SourcesConfig(
                keywords=KeywordSourceConfig(rules_path=write_rules(tmp), weight=1.0),
                homefeed=HomefeedSourceConfig(weight=0.0, limit_per_page=3),
            )
            rng = random.Random(42)
            source_kw = XhsSource(XhsConfig("cookie"), config_kw, client=client, rng=rng)
            session_kw = source_kw.start_collection()
            self.assertEqual(session_kw.source_type, "keyword")

            config_hf = SourcesConfig(
                keywords=KeywordSourceConfig(rules_path=write_rules(tmp), weight=0.0),
                homefeed=HomefeedSourceConfig(weight=1.0, limit_per_page=3),
            )
            source_hf = XhsSource(XhsConfig("cookie"), config_hf, client=client, rng=rng)
            session_hf = source_hf.start_collection()
            self.assertEqual(session_hf.source_type, "homefeed")

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
