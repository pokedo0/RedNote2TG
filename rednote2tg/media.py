from __future__ import annotations

import asyncio
import mimetypes
import shutil
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Awaitable, Callable

from rednote2tg.models import DownloadedMedia, MediaItem, MediaType

FetchResult = tuple[str | None, int]
Fetcher = Callable[[str, Path], FetchResult | Awaitable[FetchResult]]


class MediaDownloadError(RuntimeError):
    pass


class MediaDownloader:
    def __init__(self, temp_dir: str | Path, retries: int = 2, fetcher: Fetcher | None = None):
        self.temp_dir = Path(temp_dir)
        self.retries = retries
        self.fetcher = fetcher or self._default_fetcher

    async def download_all(
        self,
        note_id: str,
        items: tuple[MediaItem, ...],
        upload_live_photo: bool = True,
    ) -> list[DownloadedMedia]:
        downloads = []
        for index, item in enumerate(items):
            downloads.append(await self.download(note_id, item, index, upload_live_photo=upload_live_photo))
        return downloads

    async def download(
        self,
        note_id: str,
        item: MediaItem,
        index: int,
        upload_live_photo: bool = True,
    ) -> DownloadedMedia:
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        path = self.temp_dir / self._filename(note_id, item, index)
        live_path = self.temp_dir / self._live_filename(note_id, item, index) if _should_download_live_video(item, upload_live_photo) else None
        last_error: Exception | None = None
        for _ in range(self.retries + 1):
            try:
                content_type, size = await self._fetch(item.url, path)
                if size <= 0:
                    raise MediaDownloadError(f"downloaded empty media: {item.url}")
                if live_path is None or item.live_video_url is None:
                    return DownloadedMedia(item, path, size, content_type)
                live_content_type, live_size = await self._fetch(item.live_video_url, live_path)
                if live_size <= 0:
                    raise MediaDownloadError(f"downloaded empty media: {item.live_video_url}")
                return DownloadedMedia(item, path, size, content_type, live_path, live_size, live_content_type)
            except Exception as exc:
                last_error = exc
                if path.exists():
                    path.unlink()
                if live_path is not None and live_path.exists():
                    live_path.unlink()
        raise MediaDownloadError(str(last_error) if last_error else f"failed to download {item.url}")

    def cleanup(self) -> None:
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def _filename(self, note_id: str, item: MediaItem, index: int) -> str:
        suffix = _suffix_from_url(item.url)
        if not suffix:
            suffix = ".mp4" if item.media_type is MediaType.VIDEO else ".jpg"
        stem = item.filename_hint or f"{note_id}_{index}"
        return f"{_safe_name(stem)}{suffix}"

    def _live_filename(self, note_id: str, item: MediaItem, index: int) -> str:
        suffix = _suffix_from_url(item.live_video_url or "")
        if not suffix:
            suffix = ".mp4"
        stem = item.filename_hint or f"{note_id}_{index}"
        return f"{_safe_name(stem)}_live{suffix}"

    async def _fetch(self, url: str, path: Path) -> FetchResult:
        result = self.fetcher(url, path)
        if asyncio.iscoroutine(result):
            return await result
        return result

    @staticmethod
    def _default_fetcher(url: str, path: Path) -> FetchResult:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            content_type = response.headers.get("Content-Type")
            with path.open("wb") as fh:
                shutil.copyfileobj(response, fh)
        return content_type, path.stat().st_size


def detect_content_type(path: str | Path) -> str | None:
    return mimetypes.guess_type(str(path))[0]


def _suffix_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    suffix = Path(parsed.path).suffix
    if len(suffix) > 10:
        return ""
    return suffix


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)[:80]


def _should_download_live_video(item: MediaItem, upload_live_photo: bool) -> bool:
    return upload_live_photo and item.media_type is MediaType.LIVE_PHOTO and bool(item.live_video_url)
