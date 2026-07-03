## Why

Keyword search is currently driven by static `sources.keywords.queries`, which makes keyword rotation and search freshness manual and brittle. The project needs a configurable rule file so each run can generate one weighted keyword query and search with a matching Xiaohongshu time filter.

## What Changes

- **BREAKING**: Replace static `sources.keywords.queries` with dynamic keyword query generation.
- Add a `keyword_rules.yaml` file that is manually maintained and reloaded on each collection run.
- Generate exactly one keyword search query per run.
- Generate query length using decimal weights for 3-6 keywords.
- Always include one term from each required pool.
- Fill remaining terms from optional groups using weighted selection and automatic weight renormalization when groups become unavailable.
- Treat array pools as mutually exclusive within a generated query.
- Join selected terms with spaces.
- Generate Xiaohongshu search time filter from decimal weights and pass it to `spider_xhs` as `note_time`.
- On invalid rule configuration, record a keyword `SourceError`, skip keyword search for that run, and continue other sources.

## Capabilities

### New Capabilities

- `dynamic-keyword-query-generation`: Generates one weighted keyword search query and Xiaohongshu time filter from an external YAML rule file.

### Modified Capabilities

- None.

## Impact

- `config.yaml` / `config.example.yaml`: replace `queries` with `rules_path`.
- `rednote2tg.config`: parse the new keyword source shape.
- `rednote2tg.xhs_source`: generate query parameters before keyword search and pass `note_time`.
- New keyword rule loading/generation module.
- Tests for rule parsing, validation, generation, search call parameters, and error handling.
