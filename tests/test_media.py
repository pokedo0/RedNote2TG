import tempfile
import unittest
from pathlib import Path

from rednote2tg.media import MediaDownloadError, MediaDownloader, detect_content_type
from rednote2tg.models import MediaItem, MediaType


class MediaDownloaderTest(unittest.IsolatedAsyncioTestCase):
    async def test_download_success_and_cleanup(self):
        def fetcher(url, path):
            path.write_bytes(b"image")
            return "image/jpeg", path.stat().st_size

        with tempfile.TemporaryDirectory() as tmp:
            downloader = MediaDownloader(tmp, fetcher=fetcher)

            downloaded = await downloader.download("n1", MediaItem("https://x/1.jpg", MediaType.IMAGE), 0)

            self.assertTrue(downloaded.path.exists())
            self.assertEqual(downloaded.size_bytes, 5)
            self.assertEqual(detect_content_type(downloaded.path), "image/jpeg")

            downloader.cleanup()
            self.assertFalse(Path(tmp).exists())

    async def test_download_retries_then_fails(self):
        calls = 0

        def fetcher(url, path):
            nonlocal calls
            calls += 1
            raise RuntimeError("network")

        with tempfile.TemporaryDirectory() as tmp:
            downloader = MediaDownloader(tmp, retries=2, fetcher=fetcher)

            with self.assertRaises(MediaDownloadError):
                await downloader.download("n1", MediaItem("https://x/1.jpg", MediaType.IMAGE), 0)

        self.assertEqual(calls, 3)

    async def test_download_live_photo_downloads_image_and_video(self):
        calls = []

        def fetcher(url, path):
            calls.append(url)
            path.write_bytes(b"video" if url.endswith(".mp4") else b"image")
            return ("video/mp4" if url.endswith(".mp4") else "image/jpeg"), path.stat().st_size

        with tempfile.TemporaryDirectory() as tmp:
            downloader = MediaDownloader(tmp, fetcher=fetcher)
            item = MediaItem(
                "https://x/1.jpg",
                MediaType.LIVE_PHOTO,
                "n1_image_0",
                "https://x/1.mp4",
            )

            downloaded = await downloader.download("n1", item, 0)

            self.assertEqual(calls, ["https://x/1.jpg", "https://x/1.mp4"])
            self.assertTrue(downloaded.path.exists())
            self.assertTrue(downloaded.live_video_path.exists())
            self.assertEqual(downloaded.live_video_size_bytes, 5)
            self.assertEqual(downloaded.live_video_content_type, "video/mp4")

    async def test_download_live_photo_disabled_skips_video(self):
        calls = []

        def fetcher(url, path):
            calls.append(url)
            path.write_bytes(b"image")
            return "image/jpeg", path.stat().st_size

        with tempfile.TemporaryDirectory() as tmp:
            downloader = MediaDownloader(tmp, fetcher=fetcher)
            item = MediaItem(
                "https://x/1.jpg",
                MediaType.LIVE_PHOTO,
                "n1_image_0",
                "https://x/1.mp4",
            )

            downloaded = await downloader.download("n1", item, 0, upload_live_photo=False)

            self.assertEqual(calls, ["https://x/1.jpg"])
            self.assertIsNone(downloaded.live_video_path)


if __name__ == "__main__":
    unittest.main()
