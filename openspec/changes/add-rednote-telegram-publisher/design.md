## Context

RedNote2TG is starting from an almost empty project shell and needs its first application design. The project will be a Python service that imports the local Spider_XHS package from `D:\Program\java_project\Spider_XHS` and forwards Xiaohongshu notes into a Telegram channel.

Spider_XHS already exposes a public facade:

```python
from spider_xhs import XhsPcClient
```

The facade supports keyword search, homefeed recommendations, note detail fetching, and no-watermark media helpers. The Telegram side will use a bot that has administrator rights in the target channel.

## Goals / Non-Goals

**Goals:**

- Build a single-process Python service that can run scheduled Xiaohongshu-to-Telegram forwarding jobs.
- Use `aiogram` for Telegram bot integration.
- Use Spider_XHS as the Xiaohongshu dependency instead of reimplementing Xiaohongshu API access.
- Support keyword search and homefeed recommendation sources behind configuration toggles.
- Publish all available note media, splitting large media sets across multiple Telegram media groups.
- Keep captions useful but compact, with the Xiaohongshu note URL rendered as an HTML hyperlink.
- Store short-term deduplication records in SQLite for 7-14 days, then allow old notes to appear again.
- Retry media transfer failures and degrade to text-only publishing when media cannot be sent.

**Non-Goals:**

- No multi-instance locking or distributed worker model in the first version.
- No web admin panel.
- No automatic Xiaohongshu login flow; the first version uses configured cookies.
- No permanent archive of every note ID ever seen.
- No user-account Telegram automation through MTProto clients such as Telethon.

## Decisions

### Use `aiogram` for Telegram integration

`aiogram` fits an asynchronous Python bot service and keeps the door open for bot commands such as `/status` and `/run_once`. It will call Bot API methods for channel publishing, including single media sends and media groups.

Alternatives considered:

- `python-telegram-bot`: simpler for small bots, but the user selected `aiogram`.
- `Telethon`: useful for MTProto/user-account workflows, but this service only needs a bot posting to a channel.

### Use a single-process scheduler

The first version will run `aiogram` and `APScheduler` in one Python process. The scheduler triggers publish jobs at multiple configured times each day.

This is simpler than a queue or split worker design and is enough for a low-volume channel publisher. The trade-off is that running multiple service instances can duplicate work; first deployment should run one instance.

### Wrap Spider_XHS behind an internal source adapter

Application code will not pass Spider_XHS raw dictionaries throughout the system. A source adapter will call `XhsPcClient.search_notes(..., with_detail=True)` and `XhsPcClient.homefeed_notes(..., with_detail=True)`, then normalize results into internal `Note` and `MediaItem` models.

This keeps Telegram formatting, deduplication, and scheduling independent from Spider_XHS response shapes.

### Keep configuration in `config.yaml`

The first version will keep Telegram credentials, Xiaohongshu cookies, source toggles, schedule times, dedup settings, and storage paths in `config.yaml`. A checked-in `config.example.yaml` should show the shape while real `config.yaml` should be ignored by git.

The user selected `config.yaml` for Xiaohongshu cookies instead of `.env` or `cookies.txt`.

### Use SQLite with TTL-based deduplication

SQLite will store sent and failed note records. Deduplication is based on `note_id` within a configured retention window of 7-14 days. Once the record expires, the same note may be sent again if Xiaohongshu surfaces it again.

The table should keep enough metadata for debugging: source type, source key, note URL, title, timestamps, status, Telegram message IDs, and error text.

### Send all media and split media groups

The publisher will attempt to send all note images and videos. Telegram media groups have a per-group limit, so the service will chunk media into groups of up to 10. The first group gets the full caption; later groups have no caption.

If a note has one media item, the publisher uses the corresponding single-media method. If a note has no media or media sending fails after retries, it sends text only.

### Retry and degrade on media failures

Media download and Telegram upload should retry twice. If media still fails, the service sends a text-only message with the formatted caption and original Xiaohongshu hyperlink, then records `sent_degraded`.

Statuses:

- `sent`: media and text published successfully.
- `sent_degraded`: text published after media failed.
- `failed`: even the text fallback failed.
- `skipped`: the note was skipped by validation or configuration.

## Risks / Trade-offs

- Xiaohongshu cookies expire or become invalid -> Surface source errors in logs and `/status`; require the operator to refresh cookies in `config.yaml`.
- Xiaohongshu media URLs expire or block downloads -> Download media shortly before publishing and fall back to text-only when retries fail.
- Telegram media size or format limits reject uploads -> Detect local file size when possible, retry, then degrade to text-only.
- Multiple service instances can duplicate sends -> First version documents single-instance operation; later versions can add DB or file locks.
- `config.yaml` contains sensitive cookies and bot token -> Add `.gitignore` entry and provide only `config.example.yaml`.
- Deduplication expires after 7-14 days -> Old notes may be reposted after expiry; this is accepted to keep storage bounded.
