## ADDED Requirements

### Requirement: Run configured daily schedules
The system SHALL run publish jobs at multiple configured local times each day.

#### Scenario: Schedule contains three times
- **WHEN** configuration contains `09:00`, `15:00`, and `21:00`
- **THEN** the scheduler registers one publish job for each configured time in the configured timezone

### Requirement: Limit notes per run
The system SHALL limit each scheduled run to the configured number of published notes.

#### Scenario: More candidates than limit
- **WHEN** a publish job finds more eligible notes than `notes_per_run`
- **THEN** it publishes no more than `notes_per_run` notes

### Requirement: Deduplicate recent notes
The system SHALL prevent reposting the same note ID while its deduplication record is active.

#### Scenario: Note was recently sent
- **WHEN** a candidate note ID exists in the active deduplication window
- **THEN** the publish job skips that note

### Requirement: Expire deduplication records
The system SHALL keep deduplication records only for the configured retention window of 7 to 14 days.

#### Scenario: Record expires
- **WHEN** a deduplication record has an `expire_at` timestamp older than the current time
- **THEN** the cleanup step removes that record

#### Scenario: Expired note appears again
- **WHEN** a note ID was sent before but its deduplication record has expired
- **THEN** the publish job may publish that note again

### Requirement: Track publish status
The system SHALL record publish status and useful debugging metadata for each attempted note.

#### Scenario: Note sends successfully
- **WHEN** a note is published with media
- **THEN** the system records `sent` status, source metadata, timestamps, and Telegram message IDs

#### Scenario: Note fails completely
- **WHEN** both media publishing and text fallback fail
- **THEN** the system records `failed` status with an error message

### Requirement: Support manual run command
The system SHALL expose an administrator-only bot command to trigger one publish run manually.

#### Scenario: Admin triggers run once
- **WHEN** an authorized administrator sends `/run_once`
- **THEN** the system starts one publish job using the same rules as scheduled jobs

### Requirement: Support status command
The system SHALL expose an administrator-only bot command to inspect basic service status.

#### Scenario: Admin requests status
- **WHEN** an authorized administrator sends `/status`
- **THEN** the system replies with scheduler, source, and recent publish status summary
