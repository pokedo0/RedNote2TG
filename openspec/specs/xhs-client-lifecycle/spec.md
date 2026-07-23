## Purpose

Define ownership, replacement, and error-preservation behavior for the long-lived Spider_XHS client used by RedNote2TG.

## Requirements

### Requirement: Application-owned XHS client lifecycle
The application SHALL retain one owned XHS client for the `XhsSource` lifetime and close it during application shutdown.

#### Scenario: Normal application shutdown
- **WHEN** polling stops and application cleanup begins
- **THEN** the owned XHS client is closed before the note store is closed

#### Scenario: Injected test client
- **WHEN** `XhsSource` receives an externally supplied client
- **THEN** closing the source does not close the external client

### Requirement: Transactional Cookie client replacement
The application SHALL preserve the current runtime client unless both replacement construction and Cookie configuration persistence succeed.

#### Scenario: Successful Cookie update
- **WHEN** an authorized administrator supplies a new Cookie and configuration persistence succeeds
- **THEN** the source adopts the new client and closes the previously owned client

#### Scenario: Configuration persistence fails
- **WHEN** a replacement client was created but the configuration file cannot be updated
- **THEN** the replacement is closed and the previous runtime client remains active

### Requirement: Upstream error preservation
The application SHALL retain actionable Spider_XHS operation, code, and message details in source failures without exposing Cookie or signature values.

#### Scenario: Xiaohongshu response omits msg
- **WHEN** Spider_XHS reports a failure using an alternative message field or only a business code
- **THEN** RedNote2TG records a meaningful `SourceError` rather than `KeyError("msg")`
