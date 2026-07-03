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


if __name__ == "__main__":
    unittest.main()
