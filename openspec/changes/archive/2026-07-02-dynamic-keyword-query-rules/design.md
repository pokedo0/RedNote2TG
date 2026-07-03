## Context

`XhsSource.collect()` currently iterates over static `sources.keywords.queries` and calls `XhsPcClient.search_notes()` for each query. `Spider_XHS` already supports search time filtering through `note_time`, but RedNote2TG does not expose or pass that parameter.

The desired behavior is one generated keyword query per run. The rule data changes more often than code, so it belongs in a manually edited external YAML file that is reloaded on each collection run.

```
config.yaml
    |
    | rules_path
    v
keyword_rules.yaml
    |
    v
KeywordRuleLoader -> KeywordQueryGenerator -> XhsSource.search_notes(query, note_time)
```

## Goals / Non-Goals

**Goals:**

- Replace static keyword query lists with a generated query.
- Keep keyword rules editable without code changes.
- Read the rule file once per collection run so manual edits apply on the next run.
- Generate one query containing 3-6 unique terms joined by spaces.
- Pass generated search time filtering to `spider_xhs` as `note_time`.
- Treat invalid rule configuration as a recoverable keyword-source failure.

**Non-Goals:**

- No UI, remote rule sync, or automatic rule editing.
- No support for generating multiple queries per run.
- No fallback to legacy static `queries`.
- No local post-search filtering by note publish time.

## Decisions

### Use an external YAML rule file referenced by main config

`config.yaml` keeps operational source settings and points to `keyword_rules.yaml` via `sources.keywords.rules_path`.

Alternative considered: put all rules in `config.yaml`. Rejected because the rule file contains frequently edited word pools and would make the main config noisy and easier to break.

Alternative considered: encode rules in Python. Rejected because the user needs manual word list updates without code edits.

### Use a compact convention-based YAML shape

The rule file uses:

```yaml
joiner: " "

length_weights:
  3: 0.12
  4: 0.40
  5: 0.38
  6: 0.10

required_pools:
  - ["高跟", "凉鞋", "凉拖"]
  - ["蕾丝", "水晶", "透明", "裸色", "水钻"]

optional_groups:
  attributes:
    weight: 0.50
    pools:
      - ["紫色", "白色", "黑色", "银色"]
      - ["粗跟", "一字带"]
      - "露脚趾"
      - ["足控", "腿"]

time_weights:
  unlimited: 0.70
  half_year: 0.20
  one_week: 0.10
```

Strings represent single-term pools. Arrays represent mutually exclusive pools that can be used at most once per generated query.

### Validate before generation

The loader validates the full rule file before generating. Weight maps must sum to `1.0` within a small floating-point tolerance. Required pools, optional groups, and pools must be non-empty.

Invalid configuration returns a keyword source error and prevents `search_notes()` from being called for that run.

### Generate with availability-aware weight renormalization

Each remaining optional position is filled by:

1. Removing optional groups with no usable pools.
2. Renormalizing weights across remaining groups.
3. Selecting a group.
4. Selecting an unused pool within that group.
5. Selecting one unused term from the pool.

This preserves configured preferences while avoiding dead groups. For example, if the brand group is exhausted, attributes and long-tail are selected using `0.50 / (0.50 + 0.40)` and `0.40 / (0.50 + 0.40)`.

### Pass search time through `note_time`

Generated `time_weights` map to Spider_XHS values:

- `unlimited` -> `0`
- `one_week` -> `2`
- `half_year` -> `3`

The XHS client protocol and call site will include `note_time`.

## Risks / Trade-offs

- Rule syntax is compact but convention-heavy -> document it in `config.example.yaml` and tests.
- Random generation can make bugs hard to reproduce -> keep generator injectable with a seeded random object in tests.
- Rule files can become unable to fill the selected target length -> validate and return a clear `SourceError`.
- Removing `queries` is breaking -> update examples and tests in the same change.

## Migration Plan

1. Replace `sources.keywords.queries` with `sources.keywords.rules_path`.
2. Add `keyword_rules.yaml` example using the agreed rule structure.
3. Update config parsing and tests.
4. Update keyword collection to generate one query and pass `note_time`.
5. Remove assumptions that keyword source always has a static query list.

Rollback is straightforward: revert this change and restore `queries` in `config.yaml`.

## Open Questions

None for the current scope.
