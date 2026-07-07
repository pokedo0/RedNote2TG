# RedNote2TG

RedNote2TG forwards Xiaohongshu notes to a Telegram channel. It uses `Spider_XHS` for Xiaohongshu access and an `aiogram` bot for Telegram publishing.

## Install

Create a Python 3.10+ environment, then install this project and the local Xiaohongshu dependency:

```powershell
pip install -e D:\Program\java_project\Spider_XHS
pip install -e .
```

## Configure

Copy the example config:

```powershell
Copy-Item config/config.example.yaml config/config.yaml
```

Edit `config/config.yaml`:

- `telegram.bot_token`: token from BotFather.
- `telegram.channel_id`: channel username such as `@your_channel`.
- `telegram.admin_user_ids`: Telegram user IDs allowed to use `/status` and `/run_once`; empty means all users are allowed.
- `xhs.cookies`: Xiaohongshu browser cookies.
- `sources.keywords`: keyword search configuration. Remote `rules_path` is supported; if `config/keyword_rules.yaml` exists, it overrides the remote rules for local testing.
- `sources.homefeed`: homefeed recommendation configuration.
- `publishing.notes_per_run`: total notes to publish per scheduled run.
- `logging`: console and persistent file logging. By default logs are written to `logs/rednote2tg.log`, rotated at 5 MB, compressed after rotation, and cleaned after 14 days or 20 rotated files.
- `dedup.ttl_days`: short-term dedup window, 7 to 14 days.
- `schedule.interval_minutes`: publish interval in `schedule.timezone`.

`config/config.yaml` and `config/keyword_rules.yaml` are ignored by git because they contain local runtime data.

## Telegram channel permissions

Add the bot as an administrator in the target channel. It needs permission to post messages. Media posting uses the normal Bot API methods for photos, videos, media groups, and text fallback.

## Run

```powershell
rednote2tg
```

Useful bot commands:

- `/status`: show scheduler and recent publish status.
- `/run_once`: trigger one publish job manually.
- `/reload`: reload supported crawl config and keyword rules without restarting.

## Behavior

- Keyword search and homefeed sources can be enabled independently.
- Each run removes expired dedup records, collects candidates, skips active note IDs, and publishes up to `notes_per_run` notes.
- Deduplication only covers the configured 7-14 day window. After expiry, the same note may be sent again.
- All note media is attempted. Media groups are split into chunks of 10, and only the first group has the full caption.
- Media download or upload failures are retried. If media still fails, the note is marked `failed`; no text-only fallback is sent for media failures.
