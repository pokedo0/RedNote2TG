## Context

`XhsSource` creates one `XhsPcClient` that is reused by scheduled and manual operations, but ownership is implicit. Application shutdown closes the scheduler and database only, and `/update_cookie` replaces the client without disposing of the previous transport. Upstream failures are currently stored as unstructured strings.

## Goals / Non-Goals

**Goals:**
- Make XHS client ownership and cleanup explicit.
- Replace the client transactionally when an administrator updates Cookie credentials.
- Preserve structured upstream error text for logs and Telegram responses.
- Adopt upstream in-memory session Cookie updates without changing configuration.

**Non-Goals:**
- No new request interval, jitter, retry, or collection-limit configuration.
- No change to scheduler frequency or consecutive-error pause behavior.
- No automatic write-back of response Cookies to `config.yaml`.

## Decisions

- `XhsSource` tracks whether it created the client. `close()` only closes owned clients.
- `replace_client()` swaps in a new owned client and closes the previous owned client after the swap.
- `/update_cookie` constructs the replacement first, writes configuration second, and swaps last. Failed writes close the unused new client and preserve the current runtime client.
- Application shutdown calls `source.close()` before closing persistent storage.
- RedNote2TG consumes enriched `XhsApiError` text but does not introduce a second risk classifier or retry policy.

## Risks / Trade-offs

- [Configuration can succeed while a Cookie is invalid] → Preserve existing behavior; `/ping` remains the explicit validity check.
- [Test doubles may not implement close] → Use the protocol in production and defensive callable checks at ownership boundaries.
- [Updated response Cookies are lost on abnormal process exit] → This is intentional; administrators persist credentials through `/update_cookie`.
