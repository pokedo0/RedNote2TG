## 1. Configuration Shape

- [x] 1.1 Replace `KeywordSourceConfig.queries` with `rules_path` while keeping existing keyword search options.
- [x] 1.2 Update `parse_config` to require `sources.keywords.rules_path` when keyword source is enabled.
- [x] 1.3 Update `config.example.yaml` to reference `keyword_rules.yaml`.

## 2. Rule File and Generator

- [x] 2.1 Add the example `keyword_rules.yaml` using decimal weights and compact pool syntax.
- [x] 2.2 Implement rule file loading and validation for weights, pools, and supported time keys.
- [x] 2.3 Implement weighted length selection and required pool selection.
- [x] 2.4 Implement optional group selection with exhausted-group removal and weight renormalization.
- [x] 2.5 Enforce no duplicate terms and one hit per array pool per generated query.
- [x] 2.6 Implement `time_weights` selection and mapping to `note_time`.

## 3. Keyword Source Integration

- [x] 3.1 Extend `XhsClientProtocol.search_notes` to include `note_time`.
- [x] 3.2 Update `XhsSource.collect()` to generate one query and pass `note_time`.
- [x] 3.3 On rule load/generation failure, record a keyword `SourceError` and skip keyword search.
- [x] 3.4 Ensure homefeed collection continues when keyword generation fails.

## 4. Tests

- [x] 4.1 Update config model tests for the new keyword source shape.
- [x] 4.2 Add generator tests for deterministic length, required pools, optional selection, and joiner behavior.
- [x] 4.3 Add tests for array-pool mutual exclusion and duplicate-term prevention.
- [x] 4.4 Add tests for exhausted-group weight renormalization.
- [x] 4.5 Add tests for `time_weights` to `note_time` mapping.
- [x] 4.6 Add source integration tests for successful generated search and invalid-rule skip behavior.

## 5. Validation

- [x] 5.1 Run the project test suite.
- [x] 5.2 Validate the OpenSpec change.
