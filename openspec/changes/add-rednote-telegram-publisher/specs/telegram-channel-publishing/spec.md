## ADDED Requirements

### Requirement: Publish to configured Telegram channel
The system SHALL publish notes through an `aiogram` bot to the configured Telegram channel.

#### Scenario: Bot publishes to channel
- **WHEN** a note is selected for publishing
- **THEN** the system sends it to the configured channel ID or channel username

### Requirement: Format note caption
The system SHALL format each note caption with title, description, author, engagement counts, upload metadata when available, and an HTML hyperlink to the original Xiaohongshu note.

#### Scenario: Caption is generated
- **WHEN** a note has title, description, author, counts, metadata, and URL
- **THEN** the generated caption contains those fields and renders the URL as an HTML link

#### Scenario: Caption contains special characters
- **WHEN** note text contains HTML-sensitive characters
- **THEN** the system escapes those characters before sending with HTML parse mode

### Requirement: Send all note media
The system SHALL attempt to send all images and videos associated with a note.

#### Scenario: Note has one image
- **WHEN** a note has exactly one image media item
- **THEN** the system sends the note with a single-photo Telegram method

#### Scenario: Note has one video
- **WHEN** a note has exactly one video media item
- **THEN** the system sends the note with a single-video Telegram method

#### Scenario: Note has multiple media items
- **WHEN** a note has multiple media items
- **THEN** the system sends them using Telegram media groups

### Requirement: Split media groups
The system SHALL split notes with more than 10 media items into multiple Telegram media groups.

#### Scenario: Note has more than 10 media items
- **WHEN** a note has 11 or more media items
- **THEN** the system sends the media in chunks of at most 10 items per group

#### Scenario: Later media group is sent
- **WHEN** the system sends the second or later media group for a note
- **THEN** the group is sent without a caption

### Requirement: Degrade to text-only on media failure
The system SHALL retry media download or upload failures twice and then publish a text-only fallback when possible.

#### Scenario: Media fails after retries
- **WHEN** note media cannot be downloaded or uploaded after retry attempts
- **THEN** the system sends the note caption as a text-only Telegram message

#### Scenario: Text fallback succeeds
- **WHEN** media publishing fails but text fallback succeeds
- **THEN** the note publish status is recorded as `sent_degraded`
