# Roadmap

## Keyword A/B Weighted Rule Sources

Status: Done

Decision:
- Use separate external rule URLs for A/B keyword generation.
- Configure A with weight 0.7 and B with weight 0.3.
- Keep each rule file complete; duplicate `joiner`, `length_weights`, and `time_weights` in every file instead of sharing them.

Implementation:
- `sources.keywords.rules` selects one rule source per collection run by weight.
- Legacy `sources.keywords.rules_path` remains supported for single-rule mode.
- Multi-rule mode does not use the global local override; point each rule at a local file for local testing.
