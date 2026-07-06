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
        "schedule": {
            "timezone": "Asia/Shanghai",
            "interval_minutes": 60,
            "jitter_minutes": 10,
            "quiet_window": {"start": "03:00", "end": "09:00"},
        },
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
        self.assertEqual(config.schedule.interval_minutes, 60)
        self.assertEqual(config.schedule.jitter_minutes, 10)
        self.assertEqual(config.schedule.quiet_window.start, "03:00")
        self.assertEqual(config.schedule.quiet_window.end, "09:00")
        self.assertFalse(config.debug.enabled)
        self.assertEqual(config.logging.level, "INFO")
        self.assertTrue(config.logging.console_enabled)
        self.assertTrue(config.logging.file_enabled)
        self.assertEqual(config.logging.file_path, "logs/rednote2tg.log")
        self.assertEqual(config.logging.max_bytes, 5 * 1024 * 1024)
        self.assertEqual(config.logging.retention_days, 14)
        self.assertEqual(config.logging.max_files, 20)
        self.assertTrue(config.logging.compress_rotated)

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

    def test_parse_config_accepts_logging_config(self):
        data = base_config()
        data["logging"] = {
            "level": "warning",
            "console_enabled": False,
            "file_enabled": True,
            "file_path": "logs/custom.log",
            "max_bytes": 1024,
            "retention_days": 7,
            "max_files": 3,
            "compress_rotated": False,
        }

        config = parse_config(data)

        self.assertEqual(config.logging.level, "WARNING")
        self.assertFalse(config.logging.console_enabled)
        self.assertTrue(config.logging.file_enabled)
        self.assertEqual(config.logging.file_path, "logs/custom.log")
        self.assertEqual(config.logging.max_bytes, 1024)
        self.assertEqual(config.logging.retention_days, 7)
        self.assertEqual(config.logging.max_files, 3)
        self.assertFalse(config.logging.compress_rotated)

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

    def test_config_example_is_valid(self):
        config = load_config(Path(__file__).resolve().parents[1] / "config.example.yaml")

        self.assertEqual(config.logging.file_path, "logs/rednote2tg.log")

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

    def test_rejects_bad_logging_config(self):
        invalid_configs = [
            ("level", "TRACE"),
            ("console_enabled", "true"),
            ("file_enabled", "true"),
            ("file_path", ""),
            ("max_bytes", 0),
            ("retention_days", 0),
            ("max_files", 0),
            ("compress_rotated", "true"),
        ]
        for key, value in invalid_configs:
            data = base_config()
            data["logging"] = {key: value}
            with self.subTest(key=key):
                with self.assertRaises(ConfigError):
                    parse_config(data)

    def test_rejects_bad_schedule_time(self):
        data = base_config()
        data["schedule"]["quiet_window"]["start"] = "24:00"

        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_rejects_old_schedule_times(self):
        data = base_config()
        data["schedule"]["times"] = ["09:00"]

        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_rejects_bad_interval_schedule_values(self):
        invalid_configs = [
            ("interval_minutes", 0),
            ("jitter_minutes", -1),
        ]
        for key, value in invalid_configs:
            data = base_config()
            data["schedule"][key] = value
            with self.subTest(key=key):
                with self.assertRaises(ConfigError):
                    parse_config(data)

    def test_rejects_equal_quiet_window_bounds(self):
        data = base_config()
        data["schedule"]["quiet_window"] = {"start": "03:00", "end": "03:00"}

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
