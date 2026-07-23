from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Any, Protocol
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


class XhsClientProtocol(Protocol):
    def search_notes(
        self,
        query: str,
        limit: int = 20,
        sort_type_choice: int = 0,
        note_type: int = 0,
        note_time: int = 0,
        with_detail: bool = False,
    ):
        ...

    def homefeed_notes(self, category: str, limit: int = 20, with_detail: bool = False):
        ...

    def fetch_note(self, note_url: str):
        ...

    def unread_message(self):
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

    @staticmethod
    def _create_client(xhs_config: XhsConfig) -> XhsClientProtocol:
        from spider_xhs import XhsPcClient

        return XhsPcClient(xhs_config.cookies, proxies=xhs_config.proxies)

    @staticmethod
    def _close_client(client: XhsClientProtocol) -> None:
        close = getattr(client, "close", None)
        if not callable(close):
            return
        try:
            close()
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

    def collect(
        self,
        active_note_ids: set[str] | None = None,
        detail_limit: int | None = None,
    ) -> tuple[list[Note], list[SourceError]]:
        if detail_limit is not None and detail_limit < 0:
            raise ValueError("detail_limit must be non-negative")

        notes: list[Note] = []
        errors: list[SourceError] = []
        candidate_batches: list[tuple[list[dict[str, Any]], SourceRef, str]] = []
        active_note_ids = active_note_ids or set()
        self.last_keyword_query = None
        self.last_keyword_rule_name = ""
        self.last_pre_detail_dedup_skipped = 0

        if self.sources_config.keywords.enabled:
            try:
                rule_source = self._select_keyword_rule_source()
                rules_path = rule_source.rules_path if rule_source is not None else self.sources_config.keywords.rules_path
                keyword_query = generate_keyword_query(
                    load_keyword_rules(rules_path, allow_local_override=rule_source is None),
                    self.rng,
                )
            except KeywordRuleError as exc:
                logger.error("keyword rules failed: %s", exc)
                errors.append(SourceError("keyword", "generated", str(exc)))
            else:
                self.last_keyword_query = keyword_query
                self.last_keyword_rule_name = rule_source.name if rule_source is not None else ""
                try:
                    items = self.client.search_notes(
                        keyword_query.query,
                        limit=self.sources_config.keywords.search_limit_per_query,
                        sort_type_choice=self.sources_config.keywords.sort_type,
                        note_type=self.sources_config.keywords.note_type,
                        note_time=keyword_query.note_time,
                        with_detail=False,
                    )
                    candidate_batches.append(
                        (
                            items,
                            SourceRef("keyword", keyword_query.query),
                            "pc_search",
                        )
                    )
                except Exception as exc:  # pragma: no cover - exact XHS exceptions vary.
                    logger.exception("keyword source failed: %s", keyword_query.query)
                    errors.append(SourceError("keyword", keyword_query.query, str(exc)))

        if self.sources_config.homefeed.enabled:
            for category in self.sources_config.homefeed.categories:
                try:
                    items = self.client.homefeed_notes(
                        category,
                        limit=self.sources_config.homefeed.limit_per_category,
                        with_detail=False,
                    )
                    candidate_batches.append(
                        (
                            items,
                            SourceRef("homefeed", category),
                            "pc_feed",
                        )
                    )
                except Exception as exc:  # pragma: no cover - exact XHS exceptions vary.
                    logger.exception("homefeed source failed: %s", category)
                    errors.append(SourceError("homefeed", category, str(exc)))

        candidates: list[_DetailCandidate] = []
        for items, source, default_xsec_source in candidate_batches:
            candidates.extend(
                self._filter_detail_candidates(
                    items,
                    source,
                    active_note_ids,
                    default_xsec_source,
                )
            )

        selected_candidates = candidates if detail_limit is None else candidates[:detail_limit]
        if detail_limit is not None:
            logger.info(
                "note detail fetch limit: limit=%d eligible_candidates=%d selected_candidates=%d",
                detail_limit,
                len(candidates),
                len(selected_candidates),
            )
            skipped_candidates = len(candidates) - len(selected_candidates)
            if skipped_candidates:
                logger.info(
                    "note detail fetch limit reached: limit=%d skipped_candidates=%d",
                    detail_limit,
                    skipped_candidates,
                )

        for candidate in selected_candidates:
            note_url = _note_url_from_list_item(
                candidate.item,
                candidate.note_id,
                candidate.default_xsec_source,
            )
            logger.info(
                "note detail fetch started: note_id=%s source=%s",
                candidate.note_id,
                candidate.source.source_type,
            )
            note = normalize_note(self.client.fetch_note(note_url), candidate.source)
            if note is not None:
                notes.append(note)
            else:
                logger.warning("note skipped after detail: note_id=%s reason=missing_note_id", candidate.note_id)

        return notes, errors

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
        raw = self.client.fetch_note(note_url)
        return normalize_note(raw, SourceRef("manual", note_url))


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
