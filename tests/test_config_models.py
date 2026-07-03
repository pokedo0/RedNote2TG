import tempfile
import unittest
from pathlib import Path

import yaml

from rednote2tg.config import ConfigError, load_config, parse_config
from rednote2tg.models import MediaType, SourceRef
from rednote2tg.xhs_source import normalize_note


def base_config():
    return {
        "telegram": {
            "bot_token": "123:abc",
            "channel_id": "@channel",
            "admin_user_ids": [1, 2],
        },
        "xhs": {"cookies": "a1=test"},
        "sources": {
            "keywords": {
                "enabled": True,
                "rules_path": "keyword_rules.yaml",
                "search_limit_per_query": 20,
            },
            "homefeed": {"enabled": False, "categories": ["homefeed_recommend"]},
        },
        "publishing": {"notes_per_run": 3},
        "dedup": {"ttl_days": 14},
        "schedule": {"timezone": "Asia/Shanghai", "times": ["09:00", "21:30"]},
        "storage": {"sqlite_path": "data/test.db", "media_temp_dir": "data/tmp"},
    }


class ConfigModelsTest(unittest.TestCase):
    def test_parse_config_accepts_expected_shape(self):
        config = parse_config(base_config())

        self.assertEqual(config.telegram.channel_id, "@channel")
        self.assertEqual(config.telegram.admin_user_ids, (1, 2))
        self.assertEqual(config.sources.keywords.rules_path, "keyword_rules.yaml")
        self.assertEqual(config.publishing.notes_per_run, 3)
        self.assertEqual(config.publishing.telegram_retry_after_padding_seconds, 1.0)
        self.assertTrue(config.publishing.upload_live_photo)
        self.assertFalse(config.debug.enabled)

    def test_parse_config_accepts_retry_after_padding(self):
        data = base_config()
        data["publishing"]["telegram_retry_after_padding_seconds"] = 2.5

        config = parse_config(data)

        self.assertEqual(config.publishing.telegram_retry_after_padding_seconds, 2.5)

    def test_parse_config_accepts_upload_live_photo_false(self):
        data = base_config()
        data["publishing"]["upload_live_photo"] = False

        config = parse_config(data)

        self.assertFalse(config.publishing.upload_live_photo)

    def test_parse_config_accepts_debug_enabled(self):
        data = base_config()
        data["debug"] = {"enabled": True}

        config = parse_config(data)

        self.assertTrue(config.debug.enabled)

    def test_load_config_reads_yaml_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(yaml.safe_dump(base_config(), allow_unicode=True), encoding="utf-8")

            config = load_config(path)

        self.assertEqual(config.dedup.ttl_days, 14)

    def test_load_config_resolves_rules_path_relative_to_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(yaml.safe_dump(base_config(), allow_unicode=True), encoding="utf-8")

            config = load_config(path)

        self.assertEqual(config.sources.keywords.rules_path, str(Path(tmp) / "keyword_rules.yaml"))

    def test_rejects_missing_keyword_rules_path_when_enabled(self):
        data = base_config()
        del data["sources"]["keywords"]["rules_path"]

        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_rejects_bad_notes_per_run(self):
        data = base_config()
        data["publishing"]["notes_per_run"] = 11

        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_rejects_bad_ttl(self):
        data = base_config()
        data["dedup"]["ttl_days"] = 6

        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_rejects_negative_retry_after_padding(self):
        data = base_config()
        data["publishing"]["telegram_retry_after_padding_seconds"] = -1

        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_rejects_non_boolean_upload_live_photo(self):
        data = base_config()
        data["publishing"]["upload_live_photo"] = "false"

        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_rejects_non_boolean_debug_enabled(self):
        data = base_config()
        data["debug"] = {"enabled": "false"}

        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_rejects_bad_schedule_time(self):
        data = base_config()
        data["schedule"]["times"] = ["24:00"]

        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_normalize_note_extracts_media(self):
        note = normalize_note(
            {
                "note_id": "n1",
                "note_url": "https://xhs/n1",
                "title": "Title",
                "desc": "Desc",
                "nickname": "Author",
                "image_list": ["https://img/1.jpg"],
                "video_addr": "https://video/1.mp4",
            },
            SourceRef("keyword", "榴莲"),
        )

        self.assertEqual(note.note_id, "n1")
        self.assertEqual(note.media[0].media_type, MediaType.IMAGE)
        self.assertEqual(note.media[1].media_type, MediaType.VIDEO)

    def test_normalize_note_extracts_live_photo_media(self):
        note = normalize_note(
            {
                "note_id": "n1",
                "note_url": "https://xhs/n1",
                "image_list": ["https://img/1.jpg", "https://img/2.jpg", "https://img/3.jpg"],
                "live_video_list": ["https://video/1.mp4", None, "https://video/3.mp4"],
            },
            SourceRef("keyword", "榴莲"),
        )

        self.assertEqual(
            [item.media_type for item in note.media],
            [MediaType.LIVE_PHOTO, MediaType.IMAGE, MediaType.LIVE_PHOTO],
        )
        self.assertEqual(note.media[0].live_video_url, "https://video/1.mp4")
        self.assertIsNone(note.media[1].live_video_url)
        self.assertEqual(note.media[2].live_video_url, "https://video/3.mp4")


if __name__ == "__main__":
    unittest.main()
