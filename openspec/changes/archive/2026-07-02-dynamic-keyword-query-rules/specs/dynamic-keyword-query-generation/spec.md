## ADDED Requirements

### Requirement: Rule file based keyword generation
The system SHALL generate keyword search input from an external YAML rule file referenced by `sources.keywords.rules_path`.

#### Scenario: Rule file is loaded for a collection run
- **WHEN** keyword collection starts and `sources.keywords.enabled` is true
- **THEN** the system SHALL read the configured rule file before generating the keyword query

#### Scenario: Manual rule changes apply on next run
- **WHEN** the rule file is changed between collection runs
- **THEN** the next collection run SHALL use the updated rule file contents

### Requirement: Single generated search query
The system SHALL generate exactly one keyword search query per collection run.

#### Scenario: Keyword query is generated
- **WHEN** keyword collection runs with a valid rule file
- **THEN** the system SHALL call Xiaohongshu search once with the generated query

#### Scenario: Query terms are joined
- **WHEN** the generator selects query terms
- **THEN** the system SHALL join them using the configured `joiner`

### Requirement: Weighted keyword length selection
The system SHALL select the final keyword count using `length_weights`.

#### Scenario: Length weights are valid
- **WHEN** `length_weights` contains decimal weights summing to `1.0`
- **THEN** the system SHALL use those weights to select a target length

#### Scenario: Generated query respects target length
- **WHEN** the target length is selected
- **THEN** the generated query SHALL contain that number of unique terms

### Requirement: Required pools
The system SHALL select one term from each configured required pool before selecting optional terms.

#### Scenario: Required pools are selected
- **WHEN** keyword generation starts
- **THEN** the system SHALL select exactly one term from each entry in `required_pools`

### Requirement: Optional group weighted selection
The system SHALL fill remaining query positions from `optional_groups` using group weights.

#### Scenario: Optional group is selected by weight
- **WHEN** optional terms are needed
- **THEN** the system SHALL select among available optional groups according to their configured decimal weights

#### Scenario: Exhausted groups are excluded
- **WHEN** an optional group has no available pools left
- **THEN** the system SHALL exclude that group and renormalize remaining group weights before selection

### Requirement: Mutually exclusive pools
The system SHALL treat array pools as mutually exclusive within one generated query.

#### Scenario: Array pool is used once
- **WHEN** a term has already been selected from an array pool
- **THEN** the system SHALL NOT select another term from that same pool in the same generated query

#### Scenario: Terms do not repeat
- **WHEN** a term has already been selected
- **THEN** the system SHALL NOT select that term again in the same generated query

### Requirement: Weighted Xiaohongshu time filter
The system SHALL select a Xiaohongshu search time filter using `time_weights`.

#### Scenario: Time weights map to note_time
- **WHEN** a time filter is selected
- **THEN** the system SHALL pass `unlimited` as `note_time=0`, `one_week` as `note_time=2`, and `half_year` as `note_time=3`

### Requirement: Invalid rules skip keyword search
The system SHALL skip keyword search for the current run when keyword rules are invalid.

#### Scenario: Invalid rule file is encountered
- **WHEN** the rule file is missing, malformed, has invalid weights, empty pools, or cannot fill the selected target length
- **THEN** the system SHALL record a keyword `SourceError` and SHALL NOT call Xiaohongshu keyword search

#### Scenario: Other sources continue
- **WHEN** keyword generation fails but other sources are enabled
- **THEN** the system SHALL continue collecting from the other enabled sources
