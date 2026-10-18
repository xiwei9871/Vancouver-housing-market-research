# Weekly Auto-Acquisition Design

## Goal

Restore the intended weekly radar flow: search current public Realtor.ca and Zolo.ca listings, write timestamped snapshots, re-score the purchase and rental candidates, compare them with the last successful run, and deliver the fresh result to Feishu.

## Context and boundary

The existing Saturday shell cron only runs the purchase and rental report commands. Those commands read local HTML snapshots; they do not acquire remote data. The June snapshots were produced by a public-web research pass, which is why the later cron runs repeated the same five purchase and four rental listings.

The restored acquisition stage will use the existing OpenClaw `agent_jarvis` web-search capability. It will use public search/fetch only, will not log in or bypass CAPTCHA/anti-bot challenges, and will leave unavailable fields unknown rather than inventing them.

## Architecture

The single Saturday wrapper becomes an ordered pipeline:

```text
Saturday cron
  -> OpenClaw Jarvis acquisition turn
       -> web_search/web_fetch Realtor.ca + Zolo.ca
       -> timestamped HTML snapshots + acquisition manifest
  -> deterministic manifest/snapshot validation
  -> existing parse/enrich/score/history pipeline
  -> Feishu fresh report
```

The acquisition turn is isolated with a per-run session key and is explicitly instructed not to send Feishu messages or run the reporting stage. The shell wrapper waits for it to finish, so scoring cannot race ahead of search.

## Snapshot contract

Each acquisition run writes four append-only HTML files:

- `data/realtor_snapshots/auto_realtor_<stamp>.html`
- `data/zolo_snapshots/auto_zolo_<stamp>.html`
- `data/rentals/snapshots/auto_realtor_rentals_<stamp>.html`
- `data/rentals/snapshots/auto_zolo_rentals_<stamp>.html`

Each file contains a source/status metadata marker and zero or more existing-parser-compatible listing cards. A card may contain only fields visible in the source result or fetched page. The source URL must remain on `realtor.ca` or `zolo.ca`.

The run also writes `outputs/acquisition/latest.json`. The manifest records the observed time, overall status, each search domain's status, query list, snapshot path, and result count. It is the hand-off contract between the agent acquisition stage and the deterministic report stage.

## Validation and delivery policy

The wrapper validates that:

1. the manifest is successful and recent;
2. all four required source entries are successful;
3. every referenced snapshot exists, is recent, is inside its configured directory, and has the expected HTML/source markers;
4. every parsed card URL belongs to the allowed source domain.

If acquisition or validation fails, the wrapper skips normal delivery and sends an acquisition/error alert. If acquisition succeeds but the listing set is empty, it is still a valid fresh run and the normal report may state that no candidates were found. Existing seven-day freshness protection remains as a second safety gate.

## Testing

Unit tests cover successful manifest validation, missing/old/partial manifests, path/domain restrictions, and valid empty search results. Shell tests cover that acquisition is invoked before the two report commands and that acquisition failure changes delivery mode to error. The final verification runs the full pytest suite, shell syntax/compile checks, a live one-shot acquisition, and a live fresh report delivery.

