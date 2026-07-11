# Roadmap

## Pre-Detail Deduplication

Status: Done

Decision:
- Fetch keyword and homefeed results without details first.
- Filter active published note IDs before any per-note detail request.
- Preserve existing source order and publish quota behavior after detail normalization.

Implementation:
- `PublishJobRunner` passes a snapshot of active dedup IDs into `XhsSource.collect`.
- `XhsSource` builds detail URLs from list-item IDs and XHS security parameters, then fetches only eligible notes.
- Logs record pre-detail dedup skips, malformed list items, and per-note detail fetch starts.

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
