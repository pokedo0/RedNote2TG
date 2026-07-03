## ADDED Requirements

### Requirement: Configure Xiaohongshu sources
The system SHALL allow keyword search and homefeed recommendation sources to be enabled or disabled independently through configuration.

#### Scenario: Keyword source enabled
- **WHEN** keyword search is enabled and configured with one or more query terms
- **THEN** the publish job fetches candidate notes for each configured query term

#### Scenario: Homefeed source disabled
- **WHEN** homefeed recommendation is disabled in configuration
- **THEN** the publish job does not request homefeed candidates

### Requirement: Import Spider_XHS facade
The system SHALL use the Spider_XHS public facade as the Xiaohongshu integration boundary.

#### Scenario: Source adapter starts
- **WHEN** the source adapter is initialized with configured Xiaohongshu cookies
- **THEN** it creates an `XhsPcClient` instance from the `spider_xhs` package

### Requirement: Fetch detailed notes
The system SHALL fetch detailed note data for candidates before publishing.

#### Scenario: Keyword search candidate found
- **WHEN** Spider_XHS returns keyword search candidates
- **THEN** the system requests or receives detailed note data before passing notes to the publisher

#### Scenario: Homefeed candidate found
- **WHEN** Spider_XHS returns homefeed candidates
- **THEN** the system requests or receives detailed note data before passing notes to the publisher

### Requirement: Normalize notes
The system SHALL convert Spider_XHS note dictionaries into internal note models before deduplication or publishing.

#### Scenario: Note contains images
- **WHEN** a detailed note includes an image list
- **THEN** the normalized note contains media items for those images

#### Scenario: Note contains video
- **WHEN** a detailed note includes a video URL
- **THEN** the normalized note contains a video media item

### Requirement: Report source failures
The system SHALL treat Xiaohongshu API, cookie, and parsing failures as source errors without stopping the whole scheduled service.

#### Scenario: One source fails
- **WHEN** one configured source raises an error during a publish job
- **THEN** the error is logged and other configured sources may still produce candidates
