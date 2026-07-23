## 1. XHS Client Ownership

- [x] 1.1 Extend the XHS client protocol and source with explicit owned-client cleanup
- [x] 1.2 Close the XHS source during normal application shutdown

## 2. Cookie Replacement

- [x] 2.1 Implement transactional XHS client replacement for `/update_cookie`
- [x] 2.2 Preserve the active client on replacement or configuration persistence failures

## 3. Error Integration

- [x] 3.1 Preserve structured Spider_XHS error details in source and Telegram-facing failures

## 4. Verification

- [x] 4.1 Add deterministic source, scheduler, and shutdown lifecycle tests
- [x] 4.2 Run focused and full tests against the local editable Spider_XHS package
- [x] 4.3 Validate the OpenSpec change
