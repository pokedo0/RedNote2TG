# AGENTS.md

AI working guide for this repository. Keep changes small, verify facts from code, and ask before large refactors.

## First Steps

- Use CodeGraph first for code navigation when available.
- Prefer `rtk`-prefixed shell commands when possible, for example `rtk git status`, `rtk git diff`, `rtk grep`, `rtk find`, and `rtk read`.
- Avoid unnecessary full-file reads. Read the specific symbol, section, or nearby lines needed for the task.
- Do not automatically commit changes.
- Before editing, check worktree status and preserve user changes.

## Project Shape

- `rednote2tg/main.py`: application entrypoint. Builds config, logging, bot, dispatcher, store, XHS source, downloader, publisher, scheduler, and registers bot handlers.
- `rednote2tg/config.py`: YAML config dataclasses and parsing/validation.
- `rednote2tg/scheduler.py`: scheduled publish job runner and Telegram bot command handlers.
- `rednote2tg/xhs_source.py`: Xiaohongshu source collection and note normalization.
- `rednote2tg/telegram_publisher.py`: Telegram publishing logic.
- `rednote2tg/media.py`: media download/temp file handling.
- `rednote2tg/db.py`: SQLite note store and dedup state.
- `rednote2tg/models.py`: shared domain models.
- `rednote2tg/keyword_rules.py`: keyword rule loading and time/rule helpers.
- `rednote2tg/logging.py`: logging setup and log rotation cleanup.
- `config/config.example.yaml`: documented config shape and defaults.
- `config/keyword_rules.yaml`: optional local keyword search rules override, ignored by git.
- `tests/`: unit and integration tests.
- `tests/debug/`: personal/manual debug scripts. Do not treat these as normal unit tests.

## Config Structure

Runtime config is loaded from `config/config.yaml` by `rednote2tg.config.load_config`; `config/config.yaml` is ignored because it contains secrets. Update `config/config.example.yaml` and `tests/test_config_models.py` when adding or changing config fields.

Top-level config keys:

- `telegram`: `bot_token`, `channel_id`, `admin_user_ids`.
- `xhs`: `cookies`, optional `proxies`.
- `sources.keywords`: `enabled`, `rules_path`, `search_limit_per_query`, `sort_type`, `note_type`.
- `sources.homefeed`: `enabled`, `categories`, `limit_per_category`.
- `publishing`: `notes_per_run`, `media_strategy`, `caption_parse_mode`, `telegram_retry_after_padding_seconds`, `upload_live_photo`.
- `debug`: `enabled`.
- `logging`: `level`, `console_enabled`, `file_enabled`, `file_path`, `max_bytes`, `retention_days`, `max_files`, `compress_rotated`.
- `dedup`: `ttl_days`.
- `schedule`: `timezone`, `interval_minutes`, `jitter_minutes`, `quiet_window.start`, `quiet_window.end`.
- `storage`: `sqlite_path`, `media_temp_dir`.

## Where To Change Common Things

- Add or change YAML fields: edit `rednote2tg/config.py`, `config/config.example.yaml`, and `tests/test_config_models.py`.
- Change scheduled publishing behavior: start in `rednote2tg/scheduler.py`, especially `PublishJobRunner`, `run_once`, and scheduler helpers. Verify with `tests/test_scheduler.py` and `tests/test_integration_dry_run.py`.
- Change XHS collection or normalization: edit `rednote2tg/xhs_source.py`; verify with `tests/test_xhs_source.py` and relevant config tests.
- Change Telegram output: edit `rednote2tg/telegram_publisher.py`; verify with `tests/test_telegram_publisher.py`.
- Change media handling: edit `rednote2tg/media.py`; verify with `tests/test_media.py`.
- Change storage/dedup logic: edit `rednote2tg/db.py`; verify with `tests/test_db.py`.
- Change keyword rule behavior: edit `rednote2tg/keyword_rules.py`; use ignored `config/keyword_rules.yaml` for local rule testing; verify with `tests/test_keyword_rules.py`.
- Change logging: edit `rednote2tg/logging.py`; verify with `tests/test_logging.py`.

## Bot Commands

Bot command handlers are registered in `rednote2tg/scheduler.py` inside `register_handlers`.

When adding a new bot command:

- Add the handler function in `rednote2tg/scheduler.py`.
- Register it in `register_handlers` with `Command("name")`.
- Add the visible Telegram command menu entry in `rednote2tg/main.py` with `aiogram.types.BotCommand`.
- Add or update tests in `tests/test_scheduler.py` when behavior is testable.

Current commands:

- `/run_once`: run one collect-and-publish pass.
- `/status`: show scheduler and recent publish status.
- `/start_tasks`: start scheduled tasks.
- `/stop_tasks`: stop scheduled tasks.
- `/note`: fetch a Xiaohongshu note URL in private chat.
- `/ping`: check whether the XHS cookie still works.
- `/update_cookie`: update XHS cookie in the config file and rebuild the client.
- `/reload`: reload supported crawl config and keyword rules without restarting.

## Tests

- Use `tests/` for unit and integration tests intended for normal automation.
- Use `tests/debug/` for personal/manual/live debug files only.
- Personal debug files that hit real XHS or Telegram must live under `tests/debug/`, not in normal test modules.
- Run focused tests for changed areas, for example `rtk pytest tests/test_config_models.py -q`.
- Run `rtk pytest -q` for broader verification when the change touches shared behavior.

## Safety Notes

- `config/config.yaml`, `config/keyword_rules.yaml`, logs, DB files, and downloaded media may contain secrets or local runtime data. Do not commit them.
- Live debug scripts may upload to Telegram or call real XHS APIs. Keep explicit confirmation flags for live actions.
- If unsure whether a change belongs in production tests or personal debug code, put exploratory code under `tests/debug/` and keep production tests deterministic.
