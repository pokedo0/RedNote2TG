## 1. Project Setup

- [x] 1.1 Create Python package structure for RedNote2TG modules.
- [x] 1.2 Add runtime dependencies for `aiogram`, `APScheduler`, YAML config loading, HTTP media downloads, and SQLite access.
- [x] 1.3 Add editable local dependency instructions for `D:\Program\java_project\Spider_XHS`.
- [x] 1.4 Add `.gitignore` rules for real `config.yaml`, SQLite files, downloaded media, logs, and Python cache files.
- [x] 1.5 Add `config.example.yaml` with Telegram, Xiaohongshu, source, publishing, dedup, schedule, and storage settings.

## 2. Configuration and Models

- [x] 2.1 Implement config loading and validation from `config.yaml`.
- [x] 2.2 Validate schedule times, timezone, `notes_per_run`, and dedup TTL range.
- [x] 2.3 Define internal `Note`, `MediaItem`, source, and publish result models.
- [x] 2.4 Add unit tests for config validation and model normalization edge cases.

## 3. SQLite Persistence

- [x] 3.1 Implement SQLite schema for note deduplication and publish status tracking.
- [x] 3.2 Implement cleanup for expired deduplication records.
- [x] 3.3 Implement active note ID lookup for the deduplication window.
- [x] 3.4 Implement status writes for `sent`, `sent_degraded`, `failed`, and `skipped`.
- [x] 3.5 Add tests for TTL cleanup, active dedup lookup, and expired note repost eligibility.

## 4. Xiaohongshu Source Adapter

- [x] 4.1 Implement `XhsPcClient` initialization from configured cookies and optional proxies.
- [x] 4.2 Implement keyword search source using `search_notes(..., with_detail=True)`.
- [x] 4.3 Implement homefeed source using `homefeed_notes(..., with_detail=True)`.
- [x] 4.4 Normalize Spider_XHS note dictionaries into internal note models.
- [x] 4.5 Handle one source failure without aborting all configured sources.
- [x] 4.6 Add tests with fake Spider_XHS clients for keyword, homefeed, normalization, and source failure behavior.

## 5. Media Handling

- [x] 5.1 Implement media download to configured temporary directory.
- [x] 5.2 Detect media type, file extension, and file size before publishing.
- [x] 5.3 Implement retry behavior for media download failures.
- [x] 5.4 Clean temporary media files after publish attempts.
- [x] 5.5 Add tests for download success, retry failure, and cleanup.

## 6. Telegram Publishing

- [x] 6.1 Implement HTML caption rendering with escaping and Xiaohongshu URL hyperlink.
- [x] 6.2 Implement single-photo, single-video, media-group, and text-only publishing paths with `aiogram`.
- [x] 6.3 Split media groups into chunks of at most 10 items.
- [x] 6.4 Apply full caption only to the first media group and no caption to later groups.
- [x] 6.5 Retry Telegram media upload failures twice.
- [x] 6.6 Degrade to text-only publishing after media retries fail.
- [x] 6.7 Add tests using fake bot objects for caption formatting, chunking, retry, and fallback behavior.

## 7. Scheduler and Bot Commands

- [x] 7.1 Implement publish job orchestration: cleanup, collect candidates, deduplicate, publish up to `notes_per_run`, record status.
- [x] 7.2 Register one scheduled job per configured daily time with the configured timezone.
- [x] 7.3 Implement administrator-only `/run_once` command.
- [x] 7.4 Implement administrator-only `/status` command.
- [x] 7.5 Add tests for publish job selection limits, dedup skip behavior, and command authorization.

## 8. Integration and Documentation

- [x] 8.1 Wire application startup for config, database, source adapter, publisher, scheduler, and bot polling.
- [x] 8.2 Add README usage for installing Spider_XHS in editable mode, configuring bot/channel/cookies, and running the service.
- [x] 8.3 Document Telegram channel administrator permission requirements.
- [x] 8.4 Run unit tests and OpenSpec validation for the change.
- [x] 8.5 Perform a dry-run or fake-bot integration test that exercises keyword source to publish-job flow without sending real Telegram messages.
