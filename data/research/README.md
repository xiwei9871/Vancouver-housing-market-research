# Research input conventions

Phase 1 reads local case records only. Each case must identify its quality, source type, source URL or local source path, retrieval timestamp, and the expected validation behavior.

Use TRACEABLE_RESEARCH only for a real property or transaction that can be traced to an existing local research artifact. Use BOUNDARY_FIXTURE for synthetic rule tests. Use COVERAGE_GAP when a requested market category has no traceable local case.

Unknown fields must be omitted or set to null. Do not infer PID, approved use, zoning entitlement, sold price, school catchment, or redevelopment rights from listing copy.

Directory roles:

- `raw/`: preserved local snapshots or source payloads;
- `normalized/`: reviewed normalized inputs;
- `history/`: versioned hand-edits or observation exports;
- `cases/`: the small Phase 1 validation manifest and payloads.

Every manifest case needs a source URL or local source reference, source type,
observation timestamp, expected behavior, and verification notes. A plausible
sample record is a `BOUNDARY_FIXTURE`, not a real research case.
# Research source conventions

The Phase 2–5 research pipeline is intentionally bounded and read-only by default.

`config/research.yaml` defines the deterministic fixture baseline. The operational
`config/research-mls-first.yaml` profile orders an explicit `MLS_PRIMARY` slot,
then bounded `REALTOR_CA_PUBLIC` and `ZOLO_PUBLIC` public HTTP sources. The latter
two are never relabeled as direct MLS access. The public HTTP adapter uses
robots checks, per-run request caps, and hard stops for access challenges; it
does not use browser automation, stealth, proxies, or CAPTCHA bypasses.

`config/research-playwright.yaml` is a separate Phase 1 rendered-page trial. It
uses `PLAYWRIGHT_PUBLIC` with standard Playwright Chromium, one bounded page per
configured source, the same allowlist/robots/challenge policy, and optional PNG
screenshots. Its rendered HTML is saved under `raw/browser-feeds/` and its
snapshot metadata is appended to SQLite. It is not complete market coverage.

`config/research-full-market.yaml` is the later Scrapy + `scrapy-playwright`
profile. It writes each source/run to
`crawl_jobs/<source>/<run_id>/scrapy-jobdir/` and
`crawl_jobs/<source>/<run_id>/artifacts/`, including
`artifacts/manifest.jsonl`, `artifacts/summary.json`, raw search/detail bodies,
and optional screenshots. The crawl manifest is consumed explicitly with
`research-acquire --from-crawl`; it does not create a second property database.
Full-market coverage is true only after all configured entrypoints naturally
complete without policy, pagination, cap, or parse gaps. A public 403/challenge
is preserved as a blocked source result.

Local JSON/CSV rows must carry `source_url` and `source_type`. Rows in a
timestamped snapshot profile may inherit `snapshot_observed_at` and
`source_version` from the source definition; otherwise the adapter supplies an
observation-time default. Each important value is
retained as a `SourceRecord` with raw and normalized values. `mls_number` is the
listing cross-check key; PID/address remains the property identity.

Remote or API-backed sources are disabled unless a source definition explicitly sets
`enabled: true`, `authorization_status: AUTHORIZED`, and the process injects an
authorized transport. The repository does not bypass robots, login, paywall, MLS/VOW,
or terms-of-use controls. A disabled/unauthorized/failed source leaves fields as
`UNKNOWN` and is reported as a partial or skipped run.

The `phase2_*` files are validation fixtures. The `web_*_YYYY-MM-DD.json` files are
bounded, manually captured public snapshots for that observation date, not a
complete market feed. A snapshot may be accepted provisionally when the profile
enables `allow_single_source_provisional`; it remains in the manual verification
queue until title, official use, and other critical fields are checked. The live
MLS-first profile writes received target responses
under `raw/public-feeds/` and appends their URL, status, timestamp, byte count,
SHA-256, and source version to the `source_snapshots` table. Replace or append
fixtures on the next run while preserving the source URL, capture version, and
retrieval timestamp. Public listing legal-looking values remain raw, unverified
claims and cannot establish title,
approved use, assessment, zoning entitlement, schools, roads, or redevelopment
rights without the corresponding official/authorized source.

Every observed MLS alias is additionally retained in the append-only
`identity_alias_observations` table. A conflicting MLS/address/PID combination
is kept as separate property identities and sent to the conflict queue.

The SQLite research store is append-only for observations, provenance, source runs,
comparables, redevelopment scenarios, score snapshots, conflicts, and watchlist
events. Current property data is only a projection and may be rebuilt from history.

Phase 5 operations use `research-run-weekly` under the configured local lock.
`--dry-run` performs the pipeline without persisting research rows, and
`--observed-at <ISO-8601>` makes replayed captures deterministic. Complete crawl
manifests are allowed to generate `REMOVED_OR_UNKNOWN` observations for listings
absent from the next capture; partial, blocked, challenge, or otherwise
incomplete manifests are never treated as proof of removal. `research-conflicts`
and `research-verifications` expose the current open queues without changing
their append-only history.
