## Why

RedNote2TG needs a first working Python service that can collect Xiaohongshu notes and publish them to a Telegram channel without manual copying. The project should reuse the local Spider_XHS package for Xiaohongshu access and use a bot-based Telegram stack that fits channel publishing.

## What Changes

- Add a Python Telegram publishing service based on `aiogram`.
- Import `D:\Program\java_project\Spider_XHS` as the Xiaohongshu dependency package.
- Support two Xiaohongshu sources: keyword search and homefeed recommendation, each configurable.
- Publish note text, images, and videos to a Telegram channel where the bot is an administrator.
- Format note captions with title, description, author, engagement counts, upload metadata, and an HTML hyperlink to the Xiaohongshu note.
- Send all available media, splitting media groups when a note has more than Telegram's per-group media limit.
- Add SQLite-backed note ID deduplication with a configurable 7-14 day retention window.
- Add scheduled publishing at multiple configured times per day, with configurable notes per run.
- Retry media download/upload failures and degrade to text-only publishing when media cannot be sent.

## Capabilities

### New Capabilities

- `xhs-note-ingestion`: Collect Xiaohongshu notes from keyword search and homefeed recommendation through Spider_XHS.
- `telegram-channel-publishing`: Publish normalized Xiaohongshu notes and media to a Telegram channel through an `aiogram` bot.
- `scheduled-note-forwarding`: Run periodic publish jobs, apply short-term deduplication, and track publish status.

### Modified Capabilities

None.

## Impact

- New application modules for configuration, data models, Spider_XHS source access, media handling, Telegram publishing, SQLite persistence, and scheduling.
- New runtime dependencies: `aiogram`, `APScheduler`, and a SQLite access layer such as `SQLAlchemy` or direct `sqlite3`.
- New local package dependency on `Spider_XHS`, expected to be installed in editable mode from `D:\Program\java_project\Spider_XHS`.
- New configuration file shape for Telegram bot credentials, Xiaohongshu cookies, source toggles, schedule times, dedup retention, and storage paths.
- Operational requirement: the Telegram bot must be an administrator in the target channel with permission to post messages.
