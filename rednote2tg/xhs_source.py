from __future__ import annotations

import logging
import random
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Protocol
from urllib.parse import urlencode

from rednote2tg.config import KeywordRuleSourceConfig, SourcesConfig, XhsConfig
from rednote2tg.keyword_rules import KeywordRuleError, generate_keyword_query, load_keyword_rules
from rednote2tg.models import MediaItem, MediaType, Note, SourceError, SourceRef

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _DetailCandidate:
    note_id: str
    item: dict[str, Any]
    source: SourceRef
    default_xsec_source: str


@dataclass(frozen=True)
class SearchPage:
    items: tuple[dict[str, Any], ...]
    page: int
    search_id: str
    has_more: bool


@dataclass(frozen=True)
class CollectionBatch:
    notes: tuple[Note, ...]
    errors: tuple[SourceError, ...]
    exhausted: bool


class XhsClientProtocol(Protocol):
    def search_notes(
        self,
        query: str,
        limit: int = 20,
        sort_type_choice: int = 0,
        note_type: int = 0,
        note_time: int = 0,
        with_detail: bool = False,
        page: int = 1,
        search_id: str | None = None,
        return_meta: bool = False,
    ):
        ...

    def homefeed_notes(self, category: str, limit: int = 20, with_detail: bool = False):
        ...

    def fetch_note(self, note_url: str):
        ...

    def unread_message(self):
        ...

    def merged_cookie_header(self) -> str:
        ...

    def close(self) -> None:
        ...


class XhsSource:
    def __init__(
        self,
        xhs_config: XhsConfig,
        sources_config: SourcesConfig,
        client: XhsClientProtocol | None = None,
        rng: random.Random | None = None,
    ):
        self.sources_config = sources_config
        self._owns_client = client is None
        self.client = client if client is not None else self._create_client(xhs_config)
        self.rng = rng or random.Random()
        self.last_keyword_query = None
        self.last_keyword_rule_name = ""
        self.last_pre_detail_dedup_skipped = 0

    def start_collection(self, active_note_ids: set[str] | None = None) -> "XhsCollectionSession":
        return XhsCollectionSession(self, active_note_ids or set())

    @staticmethod
    def _create_client(xhs_config: XhsConfig) -> XhsClientProtocol | None:
        from spider_xhs import XhsPcAuthClient
        from xhs_utils.xhs_pc import XHSPcAuth

        logger.info("creating XHS auth client from configured cookie")
        auth = XHSPcAuth.from_cookie(
            xhs_config.cookies,
            proxies=xhs_config.proxies,
        )
        try:
            return XhsPcAuthClient(auth)
        except RuntimeError:
            logger.exception("XHS client bootstrap failed (cookie may be expired)")
            return None

    @staticmethod
    def _close_client(client: XhsClientProtocol) -> None:
        try:
            client.close()
        except Exception:
            logger.exception("failed to close owned XHS client")

    def replace_client(self, client: XhsClientProtocol, *, owned: bool = True) -> None:
        old_client = self.client
        old_owned = self._owns_client
        if client is old_client:
            self._owns_client = owned
            return

        self.client = client
        self._owns_client = owned
        logger.info("XHS client replaced: old_owned=%s new_owned=%s", old_owned, owned)
        if old_owned:
            self._close_client(old_client)

    def close(self) -> None:
        if not self._owns_client:
            return
        self._owns_client = False
        logger.info("closing application-owned XHS client")
        self._close_client(self.client)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def merged_cookie_header(self) -> str | None:
        if self.client is None:
            logger.debug("cannot export XHS cookies: client unavailable")
            return None
        try:
            return str(self.client.merged_cookie_header() or "") or None
        except Exception:
            logger.exception("failed to export merged XHS cookies")
            return None

    def collect(
        self,
        active_note_ids: set[str] | None = None,
        detail_limit: int | None = None,
        on_note: Callable[[Note], None] | None = None,
    ) -> tuple[list[Note], list[SourceError]]:
        if self.client is None:
            logger.warning("XHS client not available, skipping collect")
            return [], [SourceError("client", "unavailable", "XHS client not initialized (cookie expired?)")]
        if detail_limit is not None and detail_limit < 0:
            raise ValueError("detail_limit must be non-negative")
        session = self.start_collection(active_note_ids)
        batch = session.collect_next(detail_limit=detail_limit, on_note=on_note)
        return list(batch.notes), list(batch.errors)

    def _sleep_between_detail_fetches(self) -> None:
        detail_config = self.sources_config.detail_fetch
        fixed_seconds = detail_config.fixed_delay_seconds
        random_seconds = detail_config.random_delay_seconds
        if fixed_seconds <= 0 and random_seconds <= 0:
            return

        random_component = self.rng.uniform(0, random_seconds) if random_seconds > 0 else 0.0
        sleep_seconds = fixed_seconds + random_component
        logger.info(
            "note detail fetch interval: fixed_seconds=%s random_limit=%s random_seconds=%s sleep_seconds=%s",
            fixed_seconds,
            random_seconds,
            random_component,
            sleep_seconds,
        )
        time.sleep(sleep_seconds)

    def _select_keyword_rule_source(self) -> KeywordRuleSourceConfig | None:
        rules = self.sources_config.keywords.rules
        if not rules:
            return None
        threshold = self.rng.random()
        cumulative = 0.0
        for rule in rules:
            cumulative += rule.weight
            if threshold <= cumulative:
                return rule
        return rules[-1]

    def _normalize_many(self, items: list[dict[str, Any]], source: SourceRef) -> list[Note]:
        normalized = []
        for item in items or []:
            note = normalize_note(item, source)
            if note is not None:
                normalized.append(note)
        return normalized

    def _filter_detail_candidates(
        self,
        items: list[dict[str, Any]],
        source: SourceRef,
        active_note_ids: set[str],
        default_xsec_source: str,
    ) -> list[_DetailCandidate]:
        candidates = []
        for item in items or []:
            if not isinstance(item, dict):
                logger.warning(
                    "note skipped before detail: source=%s key=%s reason=invalid_list_item",
                    source.source_type,
                    source.source_key,
                )
                continue
            note_id = _first(item, "note_id", "id", "source_note_id")
            if not note_id:
                logger.warning(
                    "note skipped before detail: source=%s key=%s reason=missing_note_id",
                    source.source_type,
                    source.source_key,
                )
                continue
            note_id = str(note_id)
            if note_id in active_note_ids:
                self.last_pre_detail_dedup_skipped += 1
                logger.info("note skipped before detail: note_id=%s reason=active_dedup", note_id)
                continue
            candidates.append(_DetailCandidate(note_id, item, source, default_xsec_source))
        return candidates

    def fetch_note_url(self, note_url: str) -> Note | None:
        if self.client is None:
            logger.warning("XHS client not available, cannot fetch note")
            return None
        raw = self.client.fetch_note(note_url)
        return normalize_note(raw, SourceRef("manual", note_url))


class XhsCollectionSession:
    """Collect one keyword search across pages without changing its query context."""

    def __init__(self, source: XhsSource, active_note_ids: set[str]):
        self.source = source
        self.active_note_ids = set(active_note_ids)
        self.page = 1
        self.search_id: str | None = None
        self.exhausted = False
        self._keyword_initialized = False
        self._keyword_has_more = True
        self._homefeed_loaded = False
        self._seen_note_ids: set[str] = set()
        self.source.last_keyword_query = None
        self.source.last_keyword_rule_name = ""
        self.source.last_pre_detail_dedup_skipped = 0

    def collect_next(
        self,
        active_note_ids: set[str] | None = None,
        detail_limit: int | None = None,
        on_note: Callable[[Note], None] | None = None,
    ) -> CollectionBatch:
        if detail_limit is not None and detail_limit < 0:
            raise ValueError("detail_limit must be non-negative")
        if self.exhausted:
            return CollectionBatch((), (), True)

        self.source.last_pre_detail_dedup_skipped = 0
        if active_note_ids is not None:
            self.active_note_ids = set(active_note_ids)
        if self.source.client is None:
            self.exhausted = True
            logger.warning("XHS client not available, ending collection session")
            return CollectionBatch(
                (),
                (SourceError("client", "unavailable", "XHS client not initialized (cookie expired?)"),),
                True,
            )

        candidates: Deque[_DetailCandidate] = deque()
        errors: list[SourceError] = []
        page_has_more = False
        keyword_items: list[dict[str, Any]] = []

        if self.source.sources_config.keywords.enabled:
            keyword_page, keyword_errors = self._fetch_keyword_page()
            keyword_items = list(keyword_page.items)
            keyword_has_more = keyword_page.has_more
            errors.extend(keyword_errors)
            page_has_more = keyword_has_more
            if self.source.last_keyword_query is not None:
                candidates.extend(
                    self._eligible_candidates(
                        keyword_items,
                        SourceRef("keyword", self.source.last_keyword_query.query),
                        "pc_search",
                    )
                )
        elif not self._keyword_initialized:
            self._keyword_initialized = True

        if not self._homefeed_loaded and self.source.sources_config.homefeed.enabled:
            self._homefeed_loaded = True
            for category in self.source.sources_config.homefeed.categories:
                try:
                    items = self.source.client.homefeed_notes(
                        category,
                        limit=self.source.sources_config.homefeed.limit_per_category,
                        with_detail=False,
                    )
                    candidates.extend(
                        self._eligible_candidates(items, SourceRef("homefeed", category), "pc_feed")
                    )
                except Exception as exc:  # pragma: no cover - exact XHS exceptions vary.
                    logger.exception("homefeed source failed: %s", category)
                    errors.append(SourceError("homefeed", category, str(exc)))

        selected_candidates = list(candidates)
        if detail_limit is not None:
            selected_candidates = selected_candidates[:detail_limit]
            logger.info(
                "legacy detail fetch limit: limit=%d eligible_candidates=%d selected_candidates=%d",
                detail_limit,
                len(candidates),
                len(selected_candidates),
            )

        notes: list[Note] = []
        for index, candidate in enumerate(selected_candidates):
            if index:
                self.source._sleep_between_detail_fetches()

            note_url = _note_url_from_list_item(
                candidate.item,
                candidate.note_id,
                candidate.default_xsec_source,
            )
            logger.info(
                "note detail fetch started: note_id=%s source=%s page=%d",
                candidate.note_id,
                candidate.source.source_type,
                self.page,
            )
            try:
                note = normalize_note(self.source.client.fetch_note(note_url), candidate.source)
            except Exception as exc:  # pragma: no cover - exact XHS exceptions vary.
                logger.exception("note detail fetch failed: note_id=%s", candidate.note_id)
                errors.append(SourceError("detail", candidate.note_id, str(exc)))
                continue
            if note is None:
                logger.warning("note skipped after detail: note_id=%s reason=missing_note_id", candidate.note_id)
                continue
            notes.append(note)
            if on_note is not None:
                on_note(note)

        if not self.source.sources_config.keywords.enabled:
            page_has_more = False
        if self.source.sources_config.keywords.enabled and not selected_candidates and not errors and not page_has_more:
            logger.info("keyword search page yielded no eligible details: page=%d", self.page)
        if page_has_more and not keyword_items:
            logger.warning(
                "keyword pagination stopped: page=%d reported has_more=true but returned no items",
                self.page,
            )
            page_has_more = False

        self.exhausted = not page_has_more
        if not self.exhausted:
            self.page += 1
        logger.info(
            "note detail page finished: page=%d notes=%d errors=%d candidates=%d has_more=%s",
            self.page if self.exhausted else self.page - 1,
            len(notes),
            len(errors),
            len(candidates),
            page_has_more,
        )
        return CollectionBatch(tuple(notes), tuple(errors), self.exhausted)

    def _fetch_keyword_page(
        self,
    ) -> tuple[SearchPage, list[SourceError]]:
        errors: list[SourceError] = []
        if not self._keyword_initialized:
            self._keyword_initialized = True
            try:
                rule_source = self.source._select_keyword_rule_source()
                rules_path = (
                    rule_source.rules_path
                    if rule_source is not None
                    else self.source.sources_config.keywords.rules_path
                )
                self.source.last_keyword_query = generate_keyword_query(
                    load_keyword_rules(rules_path, allow_local_override=rule_source is None),
                    self.source.rng,
                )
                self.source.last_keyword_rule_name = rule_source.name if rule_source is not None else ""
            except KeywordRuleError as exc:
                logger.error("keyword rules failed: %s", exc)
                errors.append(SourceError("keyword", "generated", str(exc)))
                self._keyword_has_more = False

        keyword_query = self.source.last_keyword_query
        if keyword_query is None or not self._keyword_has_more:
            return SearchPage((), self.page, self.search_id or "", False), errors

        query = keyword_query.query
        try:
            result = self.source.client.search_notes(
                query,
                limit=self.source.sources_config.keywords.search_limit_per_query,
                sort_type_choice=self.source.sources_config.keywords.sort_type,
                note_type=self.source.sources_config.keywords.note_type,
                note_time=keyword_query.note_time,
                with_detail=False,
                page=self.page,
                search_id=self.search_id,
                return_meta=True,
            )
            if isinstance(result, dict):
                items = result.get("items") or []
                self.search_id = str(result.get("search_id") or self.search_id or "") or None
                returned_page = int(result.get("page", self.page))
                has_more = bool(result.get("has_more"))
            else:
                # Keep compatibility with clients that predate the paged facade.
                items = result or []
                returned_page = self.page
                has_more = False
            logger.info(
                "keyword rule=%s query=%s page=%d search_id=%s has_more=%s items=%d",
                self.source.last_keyword_rule_name or "-",
                query,
                returned_page,
                self.search_id or "-",
                has_more,
                len(items),
            )
            self._keyword_has_more = has_more
            return SearchPage(tuple(items), returned_page, self.search_id or "", has_more), errors
        except TypeError as exc:
            # Test doubles and older installed facades may not accept pagination keywords.
            if "unexpected keyword" not in str(exc) and "positional argument" not in str(exc):
                raise
            logger.warning("keyword pagination unsupported by client; using one legacy search page")
            try:
                items = self.source.client.search_notes(
                    query,
                    limit=self.source.sources_config.keywords.search_limit_per_query,
                    sort_type_choice=self.source.sources_config.keywords.sort_type,
                    note_type=self.source.sources_config.keywords.note_type,
                    note_time=keyword_query.note_time,
                    with_detail=False,
                )
                self._keyword_has_more = False
                logger.info(
                    "keyword rule=%s query=%s page=%d search_id=%s has_more=false items=%d",
                    self.source.last_keyword_rule_name or "-",
                    query,
                    self.page,
                    self.search_id or "-",
                    len(items or []),
                )
                return SearchPage(tuple(items or []), self.page, self.search_id or "", False), errors
            except Exception as legacy_exc:  # pragma: no cover - exact XHS exceptions vary.
                logger.exception("keyword source failed: %s", query)
                errors.append(SourceError("keyword", query, str(legacy_exc)))
                self._keyword_has_more = False
                return SearchPage((), self.page, self.search_id or "", False), errors
        except Exception as exc:  # pragma: no cover - exact XHS exceptions vary.
            logger.exception("keyword source failed: %s page=%d", query, self.page)
            errors.append(SourceError("keyword", query, str(exc)))
            self._keyword_has_more = False
            return SearchPage((), self.page, self.search_id or "", False), errors

    def _eligible_candidates(
        self,
        items: list[dict[str, Any]],
        source: SourceRef,
        default_xsec_source: str,
    ) -> list[_DetailCandidate]:
        candidates = []
        for candidate in self.source._filter_detail_candidates(
            items,
            source,
            self.active_note_ids,
            default_xsec_source,
        ):
            if candidate.note_id in self._seen_note_ids:
                logger.info("note skipped before detail: note_id=%s reason=run_dedup", candidate.note_id)
                continue
            self._seen_note_ids.add(candidate.note_id)
            candidates.append(candidate)
        return candidates


def normalize_note(raw: dict[str, Any], source: SourceRef) -> Note | None:
    note_id = _first(raw, "note_id", "id", "source_note_id")
    if not note_id:
        return None
    url = _first(raw, "note_url", "url") or f"https://www.xiaohongshu.com/explore/{note_id}"
    media: list[MediaItem] = []
    live_video_list = raw.get("live_video_list") or ()
    if not isinstance(live_video_list, (list, tuple)):
        live_video_list = ()
    for idx, image_url in enumerate(raw.get("image_list") or []):
        if image_url:
            live_video_url = live_video_list[idx] if idx < len(live_video_list) else None
            media_type = MediaType.LIVE_PHOTO if live_video_url else MediaType.IMAGE
            media.append(
                MediaItem(
                    str(image_url),
                    media_type,
                    f"{note_id}_image_{idx}",
                    str(live_video_url) if live_video_url else None,
                )
            )
    video_url = _first(raw, "video_addr", "video_url")
    if video_url:
        media.append(MediaItem(str(video_url), MediaType.VIDEO, f"{note_id}_video"))
    return Note(
        note_id=str(note_id),
        url=str(url),
        title=str(raw.get("title") or ""),
        description=str(raw.get("desc") or raw.get("description") or ""),
        author=str(raw.get("nickname") or raw.get("author") or ""),
        liked_count=raw.get("liked_count"),
        collected_count=raw.get("collected_count"),
        comment_count=raw.get("comment_count"),
        share_count=raw.get("share_count"),
        upload_time=raw.get("upload_time"),
        ip_location=raw.get("ip_location"),
        source=source,
        media=tuple(media),
        raw=dict(raw),
    )


def _first(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None


def _note_url_from_list_item(item: dict[str, Any], note_id: str, default_xsec_source: str) -> str:
    params = {}
    xsec_token = item.get("xsec_token")
    if xsec_token:
        params["xsec_token"] = str(xsec_token)
    xsec_source = item.get("xsec_source") or default_xsec_source
    if xsec_source:
        params["xsec_source"] = str(xsec_source)
    url = f"https://www.xiaohongshu.com/explore/{note_id}"
    return f"{url}?{urlencode(params)}" if params else url
