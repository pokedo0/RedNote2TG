## Why

RedNote2TG relies on a long-lived Spider_XHS client but does not currently close it during shutdown or safely dispose of it when `/update_cookie` replaces the client. Upstream risk-control responses are also reduced to opaque strings, including the observed `search_notes failed: 'msg'`, which prevents useful diagnosis.

## What Changes

- Adopt the stateful Spider_XHS PC client for the full application lifetime.
- Close owned XHS clients on application shutdown and after successful cookie replacement.
- Make `/update_cookie` replacement transactional so failures preserve the working client.
- Preserve structured upstream error details in source logs and user-visible failures.
- Keep request frequency, collection volume, scheduler behavior, and automatic Cookie persistence unchanged.

## Capabilities

### New Capabilities
- `xhs-client-lifecycle`: Defines ownership, replacement, shutdown, and upstream-error propagation for the application XHS client.

### Modified Capabilities

None.

## Impact

Affected areas include XHS source ownership, application shutdown, the `/update_cookie` handler, client test doubles, and integration tests. No YAML configuration changes are required.
