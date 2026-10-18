# northshore_property_watchdog

Python MVP for monitoring low-risk detached freehold listings in West Vancouver and North Vancouver.

The system is designed to work from authorized feeds, bounded public listing
requests, saved-search emails, local search-result snapshots, manual links, and
future third-party APIs. It does not bypass CAPTCHA, login restrictions,
anti-bot controls, paywalls, or API limits. The MLS-first research profile uses
ordinary public HTTP only, respects robots.txt, limits requests, preserves raw
responses, and stops on 401/403/429 or challenge pages.

## Install

```bash
cd /Users/xiwei/northshore_property_watchdog
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

For the bounded rendered-page trial, install the optional browser dependency
and Chromium separately:

```bash
.venv/bin/pip install -e '.[dev,browser]'
.venv/bin/python -m playwright install chromium
```

## Run The MVP

```bash
.venv/bin/northshore-watchdog run-weekly --config config/default.yaml
.venv/bin/northshore-watchdog report --format markdown --output outputs/weekly_report.md
```

Generated files:

- `outputs/weekly_report.md`
- `outputs/feishu_payload_sample.json`
- `outputs/openclaw_payload_sample.json`
- `outputs/watchdog.sqlite`

Run tests:

```bash
.venv/bin/pytest -q
```

## Detached Research V1 (Read-Only Phase 1)

The purchase-research core is separate from the legacy purchase and rental
watchdogs. It reads only the local case manifest, keeps field provenance and
history in `outputs/research_watchdog.sqlite`, and writes the validation
report to `reports/phase1/` plus `reports/latest.md`.

```bash
.venv/bin/northshore-watchdog research-validate-phase1 --config config/research.yaml
```

This command does not acquire listings or call remote collectors. Unknown
title, zoning, assessment, school, comparable, and redevelopment fields stay
explicitly unknown until separately verified. Phase 2 acquisition is not part
of this command.

## Detached Research V1 (Phases 2–5)

The bounded operational pipeline adds ordered acquisition, conservative official
enrichment, sold comparables, redevelopment scenarios, append-only weekly deltas,
and a lock-protected scheduler. The operational profile is MLS-first:

1. `MLS_PRIMARY`: an explicitly supplied MLS export/feed or injected adapter;
2. `REALTOR_CA_PUBLIC`: public REALTOR.ca snapshot for discovery and, when
   configured, provisional single-source observations;
3. `ZOLO_PUBLIC`: public Zolo snapshot for a second observation or cross-check.

The bundled MLS primary slot is intentionally unavailable until a real feed or
export is supplied. The run reports that state and marks public rows as fallback;
it never calls public pages a direct MLS feed. The profile makes a bounded,
explicit regional request list to public REALTOR.ca and Zolo surfaces; it is not
a complete MLS universe.

For real-data smoke testing, `allow_single_source_provisional` can be enabled.
This accepts a traceable public listing observation before cross-checking, keeps
the raw source provenance, and creates a manual verification item. It does not
promote public listing copy to official title, zoning, assessment, or approved
use; provisional properties remain `NEED_VERIFICATION` and cannot become an A
candidate or a site-visit recommendation until the manual gate is cleared.

```bash
.venv/bin/northshore-watchdog research-acquire --config config/research-mls-first.yaml --dry-run
.venv/bin/northshore-watchdog research-acquire --config config/research-mls-first.yaml
.venv/bin/northshore-watchdog research-enrich --config config/research-mls-first.yaml
.venv/bin/northshore-watchdog research-comps --config config/research-mls-first.yaml
.venv/bin/northshore-watchdog research-redevelop --config config/research-mls-first.yaml
.venv/bin/northshore-watchdog research-run-weekly --config config/research-mls-first.yaml --dry-run
.venv/bin/northshore-watchdog research-run-weekly --config config/research-mls-first.yaml --observed-at 2026-09-02T09:00:00+00:00
.venv/bin/northshore-watchdog research-conflicts --config config/research-mls-first.yaml
.venv/bin/northshore-watchdog research-verifications --config config/research-mls-first.yaml
```

The dry run checks access and policy without persistence. The non-dry run saves
source-run metadata and immutable raw HTTP snapshots under
`data/research/raw/public-feeds/`, while the SQLite history remains append-only.
Direct MLS/API-backed execution still requires an explicitly supplied authorized
adapter/export. See `docs/superpowers/specs/2026-08-30-mls-first-pipeline-design.md`
and `data/research/README.md` for source provenance and fixture rules.

### Phase 1 direct Playwright trial

`config/research-playwright.yaml` is a separate, intentionally bounded browser
profile. It uses standard Playwright Chromium to render one configured
Realtor.ca page and one configured Zolo page, with a one-request/one-page cap,
robots checks, explicit domain allowlists, optional screenshots, and
`coverage_complete: false`. It reuses the existing parser, SQLite history, and
provenance contracts. It does not use login, stealth, proxy rotation, CAPTCHA
solving, or a hidden HTTP fallback.

```bash
.venv/bin/northshore-watchdog research-acquire --config config/research-playwright.yaml
```

Rendered HTML and screenshots are stored under
`data/research/raw/browser-feeds/`. The trial is not a full-market scan. See
`docs/superpowers/specs/2026-09-02-playwright-public-acquisition-design.md`.

### Full-market acquisition (Scrapy + scrapy-playwright)

`config/research-full-market.yaml` is the explicit full-market acquisition
profile. It starts the configured Vancouver, North Vancouver, West Vancouver,
and Burnaby routes for detached-house discovery, uses Scrapy JOBDIR for isolated
resume state, and uses standard Playwright rendering only at the browser crawl
boundary. It preserves raw responses and an append-only manifest under
`data/research/crawl_jobs/<source>/<run_id>/artifacts/`.

```bash
.venv/bin/pip install -e '.[dev,browser,crawler]'
.venv/bin/python -m playwright install chromium
.venv/bin/northshore-watchdog research-crawl \
  --config config/research-full-market.yaml \
  --sources zolo-full-market,realtor-full-market \
  --run-id 20260902-full-market
.venv/bin/northshore-watchdog research-acquire \
  --config config/research-full-market.yaml \
  --from-crawl data/research/crawl_jobs/zolo-full-market/20260902-full-market/artifacts/manifest.jsonl \
  --from-crawl data/research/crawl_jobs/realtor-full-market/20260902-full-market/artifacts/manifest.jsonl
```

`research-crawl` reports entrypoint/page/card/detail counts and route coverage;
`research-acquire --from-crawl` feeds the existing normalization and Stage 1
screen. A 403, challenge, robots denial, repeated page, or unverified browser
pagination is retained as `BLOCKED_POLICY`/`PARTIAL`; it is never reported as a
complete market scan. The current real-run handoff is recorded at
`reports/phase10/full-market-acquisition-2026-09-02.md`.

### Phase 2 direct official enrichment

The current Phase 2 increment directly attempts official enrichment for the
bounded in-range Stage 2 research/verification queue, capped at 30 properties per
run. This includes title-unknown candidates so the official title check can happen
before downstream gates; comparables, redevelopment, and scoring still run only
after the normal Stage 1 title gate passes. It does not start a broad crawler.

### Location Provider V2.1 (read-only enrichment)

The location-provider layer produces a versioned, non-school external location
score for every saved property. It does not alter Current Use, Redevelopment,
Dual Value, RALV, MOS, or any production ranking. Walk Score is the only
active provider and supplies walk/transit/bike. Missing provider data remains
`UNKNOWN`, never zero.

Credentials are supplied only through the environment:

```bash
export WALK_SCORE_API_KEY='...'
```

Run the saved 166-property artifact:

```bash
.venv/bin/python scripts/run_location_provider_enrichment.py \
  --input data/research/normalized/portfolio-official-enrichment-2026-09-07.json \
  --output-dir research/location-provider \
  --snapshot-path data/research/history/location-provider-snapshots.jsonl
```

The default cache TTL is 180 days. The command appends provider snapshots and
writes `data/research/normalized/location-provider-enrichment-YYYY-MM-DD.json`
plus the five research artifacts under `research/location-provider/`. Until
the Walk Score key is configured and the six benchmark/stability checks pass,
this layer remains diagnostic only.
The configured source families are:

- BC Assessment public property lookup, including its displayed registered-sales
  history scope;
- municipality-specific official zoning/map entrypoints for Vancouver, Burnaby,
  City of North Vancouver, District of North Vancouver, and West Vancouver;
- official school-district locator entrypoints for SD39, SD41, SD44, and SD45.

BC Assessment is implemented as a bounded public-portal search followed by a
property-detail request. Every response, including a block/challenge response, is
stored as an immutable raw snapshot with URL, timestamp, byte count, and SHA-256.
The zoning and school portals are address-specific interactive systems; an
entrypoint page without a matched parcel or catchment result remains
`NO_MATCH`/`NEED_VERIFICATION` and never becomes an inferred legal or school fact.

Use the following commands after acquisition:

```bash
.venv/bin/northshore-watchdog research-enrich --config config/research-mls-first.yaml
.venv/bin/northshore-watchdog research-verifications --config config/research-mls-first.yaml
```

The verification queue records the source status, identity match method, query
scope, and raw snapshot path. PID/address mismatches create an open data conflict,
mark the property as conflicted, and are excluded from score-derived claims until
resolved. Official-source provenance is kept separate from REALTOR.ca/Zolo listing
observations, and no local Phase 2 fixture is silently used as a fallback.

### Phase 3 comparable engine

The comparable stage uses a deterministic, auditable rule engine. It keeps
like-for-like sold comps separate from the broader same-neighbourhood benchmark,
tries the configured 180-day window before expanding to 365 days, and leaves
expected value unknown when the minimum reliable sample is missing. In the
default profile, an independent sold cross-check is still required; the
real-data provisional profile may calculate a clearly labelled provisional
estimate and queues it for manual verification. Listing prices are never treated as sold prices. A
configured assessment-improvement or finished-square-foot proxy may supply a
current-use building value for the land proxy; the method is retained in the
output. Conflicting sold prices are excluded before medians are computed.

The bundled Phase 3 comparable rows remain local validation fixtures. They are
not a current Greater Vancouver transaction feed and cannot support an
acquisition valuation until replaced with independently verified sold records.

### Filter-first listing discovery (Phase 11A, Stages 0–4)

The discovery profile is the lightweight front door for daily house research.
It operates the selected source's native search filters first, validates that
the returned result set and sample cards actually satisfy the requested
filters, extracts only bounded result-card fields, then deduplicates and ranks
at most the configured shortlist. It does not open detail pages or run
assessment, zoning, school, road, sold-comparable, redevelopment, or final
score analysis.

```bash
.venv/bin/northshore-watchdog research-discover \
  --config config/research-discovery.yaml \
  --preset vancouver-west \
  --dry-run
.venv/bin/northshore-watchdog research-discover \
  --config config/research-discovery.yaml \
  --preset vancouver-west \
  --capture data/samples/discovery_filtered_capture.json
# Real, bounded REALTOR.ca map experiment; requires the optional browser extra.
.venv/bin/northshore-watchdog research-discover \
  --config config/research-discovery.yaml \
  --preset vancouver-east \
  --source realtor-ca-native
```

`config/research-discovery.yaml` contains separate price presets for Vancouver
West, Vancouver East, City/District North Vancouver, and West Vancouver. The
default safety cap is `max_result_cards: 500` and the default shortlist is 10.
If native filtering cannot be proven, a source returns a challenge/403, or the
result set is too large, the run fails closed; it never falls back to an
unfiltered crawler. A successful run writes
`reports/discovery/YYYY-MM-DD.md` and `reports/discovery/latest.md`, and saves
the discovery evidence as append-only source-run history. These outputs are
discovery observations, not verified property records or a complete MLS-market
scan. Individual malformed result cards are skipped and retained as parse
warnings; they do not stop the remaining filtered scan. The existing broad
Scrapy/Playwright acquisition command is not a
fallback for this path. The `realtor-ca-native` source uses a normal visible
Google Chrome session through a local Playwright CDP connection and starts from the configured
`/map#...` search state, rewrites only the native price/bed/bath/
area/type/title controls, waits for the rendered result page, reads result
cards, and records rendered filter-control values. It is intentionally
selected with `--source`; the replay source remains the default for
deterministic offline runs. A missing fragment, blocked page, missing rendered
filter state, unknown result count, or result count above 500 fails closed.
For live Chrome mode, start an isolated Chrome session with
`--remote-debugging-port=9222` before running the command; the source only
reuses the rendered page and does not inspect or close the browser profile.
The `2.5+` bathroom requirement is represented by REALTOR.ca's native `2+`
control and retained as an exact post-filter in the discovery plan.

### Full local property-report replay

After a filtered discovery has been officially enriched, the complete
three-layer report can be regenerated for every saved card without re-fetching
the detail pages. The report command replays saved REALTOR.ca detail HTML under
`data/research/raw/discovery/realtor-ca-native-details/`, when a matching MLS
snapshot exists, and preserves the distinction between:

- BCA official transaction history, including a successful no-sale-in-window result;
- explicit price/sale rows rendered by REALTOR.ca; and
- no history displayed, which remains `NOT_PRESENT`.

`approved_use` is intentionally not inferred from listing text. It remains
unknown until a municipal permit/occupancy record is available. Lot geometry
uses BCA dimensions first, visible listing dimensions second, and `UNKNOWN`
when neither exists.

```bash
.venv/bin/python scripts/run_full_property_reports.py \
  --output-date 2026-09-08 \
  --validate
```

Use `--realtor-detail-dir <directory>` to replay a different local detail
snapshot directory. The command writes one auditable Markdown report per card,
the portfolio index, analysis JSON, and completion metadata.

### Phase 4 redevelopment and product economics

The redevelopment engine emits Keep Existing House, Duplex, and Max Realistic
Multiplex scenarios, with optional detached rebuild, character/infill, and
TOA/assembly option records. Entitlement is official-evidence gated; a listing
claim or a permissive-looking unit cap cannot override an explicit official
prohibition. Duplex layouts, realistic versus theoretical area, split cost
categories, 2–6 unit product options, buyer fit, and all-sale project profit
are retained for audit. The current owner safety floor is CAD 500,000, and
financing is explicitly not modeled.

TOA and assembly remain long-term option value rather than automatic
entitlement. Missing zoning, resale, or cost inputs remain `UNKNOWN`, so an
incomplete feasibility result cannot become an acquisition recommendation.

## CLI

```bash
northshore-watchdog collect --config config/default.yaml
northshore-watchdog enrich --config config/default.yaml
northshore-watchdog score --config config/default.yaml
northshore-watchdog report --format markdown --output outputs/weekly_report.md
northshore-watchdog notify --channel feishu --report outputs/weekly_report.md
northshore-watchdog notify --channel openclaw --report outputs/weekly_report.md
northshore-watchdog run --config config/default.yaml
northshore-watchdog run-weekly --config config/default.yaml
northshore-watchdog run-daily --config config/default.yaml
northshore-watchdog test-notify --channel feishu
northshore-watchdog test-notify --channel openclaw
```

## Data Sources

Collectors are plug-ins under `src/northshore_watchdog/collectors/`:

- `official_feed_stub_collector`: reads `data/samples/official_feed_sample.json`, reserved for CREA DDF / MLS authorized feeds.
- `email_alert_collector`: parses local `.eml`, `.html`, `.txt` saved-search alerts.
- `search_page_snapshot_collector`: parses locally saved HTML search snapshots.
- `manual_links_collector`: accepts temporary URLs or listing dicts from config.
- `third_party_api_collector`: mock interface for Apify, Repliers, RealtyPress, CREA DDF, or authorized APIs.
- `public_page_detail_collector`: disabled by default; low-frequency only and respects robots/rate limits.

Scoring and collection are separate. Enrichers live under `src/northshore_watchdog/enrichers/` and handle detail parsing, assessment import, region classification, and keyword extraction.

`config/real.yaml` is intentionally stricter than the sample config. It does not read sample/mock data and only accepts local snapshots or saved-search email links from:

- `realtor.ca`
- `zolo.ca`

Put saved local HTML snapshots under:

- `data/realtor_snapshots/`
- `data/zolo_snapshots/`

Put saved-search alert emails under:

- `data/email_alerts/`

Third-party IDX pages are not used as trusted real-candidate sources.

## Rental Watchdog

The project also includes a separate rental radar for parent-friendly 2-bedroom condo/apartment rentals. It is intentionally separate from the detached-home purchase scoring.

Scope:

- Trusted listing domains only: `realtor.ca` and `zolo.ca`
- Target rent: CAD `2,500-3,000`; stretch watchlist up to CAD `3,300`
- Target layout: `2 bed 2 bath` preferred, `2 bed 1 bath` acceptable
- Preferred areas: Richmond City Centre / Brighouse / Lansdowne / Aberdeen / West Cambie
- Acceptable backup areas: Marine Gateway / Cambie / Oakridge / Burnaby Metrotown / Edmonds / Brentwood
- Downtown / Yaletown / Olympic Village are opportunity areas because budget pressure is higher
- Parent-friendly signals: Chinese community, SkyTrain or strong transit, grocery/pharmacy/restaurants nearby, elevator, in-suite laundry, pool/gym/concierge/amenity room
- Excluded signals: basement, shared accommodation, room rental, short-term rental, Airbnb-style sublet, fewer than 2 bedrooms, untrusted source domain

Put local saved search snapshots under:

- `data/rentals/snapshots/`

The MVP also includes sample local data:

- `data/rentals/samples/realtor_zolo_rental_snapshot_sample.html`

Run the rental radar:

```bash
.venv/bin/northshore-watchdog rentals-run-weekly --config config/rentals.yaml
```

Generated files:

- `outputs/rental_weekly_report.zh.md`
- `outputs/rental_openclaw_payload_sample.json`
- `outputs/rental_watchdog.sqlite`

Additional rental commands:

```bash
northshore-watchdog rentals-collect --config config/rentals.yaml
northshore-watchdog rentals-score --config config/rentals.yaml
northshore-watchdog rentals-report --config config/rentals.yaml --output outputs/rental_weekly_report.zh.md
```

## Configuration

Edit `config/default.yaml` for:

- West Van and North Van budget thresholds
- target cities, year ranges, lot-size ranges, frontage thresholds
- preferred, acceptable, downgraded, and lifestyle/slope regions
- risk and opportunity keywords
- scoring weights
- collector input paths and rate limits
- notification settings
- alert thresholds and report outputs

Assessment import is configured with:

```yaml
assessment:
  input_path: data/samples/assessment_sample.csv
```

If assessment is missing, the scorer assigns a neutral 10 points for assessment discount, marks `assessment_missing`, and does not give high confidence.

## Feishu And OpenClaw

Copy `.env.example` to `.env` and set:

```bash
FEISHU_WEBHOOK_URL=
FEISHU_WEBHOOK_SECRET=
OPENCLAW_BASE_URL=
OPENCLAW_API_KEY=
```

Feishu direct webhook supports text, post, and card payloads. OpenClaw transport sends:

```json
{
  "route": "property_watchdog",
  "action": "send_weekly_report",
  "payload": {
    "summary": {},
    "top_candidates": [],
    "markdown_report": "...",
    "html_report_path": null,
    "generated_at": "..."
  }
}
```

OpenClaw falls back to Feishu when configured. If both fail, the router writes to `outputs/failed_notifications.jsonl`.

## Scheduling

Cron examples:

```bash
# 每天早上 8 点跑增量
0 8 * * * /path/to/northshore-watchdog run-daily --config /path/to/config/default.yaml

# 每周六早上 9 点跑完整报告
0 9 * * 6 /path/to/northshore-watchdog run-weekly --config /path/to/config/default.yaml

# 研究系统：每周六早上 9 点运行 Phase 5 weekly pipeline
0 9 * * 6 /path/to/northshore-watchdog research-run-weekly --config /path/to/config/research-mls-first.yaml
```

Systemd timer sketch:

```ini
[Unit]
Description=North Shore Property Watchdog weekly report

[Timer]
OnCalendar=Sat 09:00
Persistent=true

[Install]
WantedBy=timers.target
```

## Sample Outcomes

The bundled sample data covers:

- `1000 Sample Ambleside Avenue, West Vancouver`: virtual A / urgent-style template, 1981, Ambleside, ~0.65 list/assessment.
- `1379 Haywood Avenue, West Vancouver`: sold historical reference; excluded by `listing_status`.
- `1531 Inglewood Avenue, West Vancouver`: B/manual review because it is pre-1965 but explicitly retained for review.
- `4670 Willow Creek Road, West Vancouver`: downgraded by creek/ravine/steep/geotechnical risk and oversized lot.
- `321 Queensbury Avenue, North Vancouver`: A candidate.
- Strata sample: excluded.
- Leasehold sample: excluded.
- 2015 new-build sample: excluded by age mismatch.

## TODO

- 接入 CREA DDF / 授权 feed
- 接入 Gmail/IMAP 自动读取 saved search alerts
- 完善官方 zoning/school interactive locator 的地址查询与结果解析
- 接入 OpenClaw skill
- 接入飞书交互按钮：Interested / Ignore / Need Due Diligence
- 把用户反馈写回 SQLite，优化评分模型
- 接入授权的实时挂牌、成交、BC Assessment、municipal zoning/school/road 数据源
- 加入地图/地形风险数据的官方或授权输入
- 在获得授权数据后扩大每周 Stage 1 acquisition 覆盖面
# Greater Vancouver detached-house research

The repository now contains a bounded Phase 2–5 research pipeline in addition to the
legacy purchase/rental watchdogs. It keeps local/authorized source evidence, applies
the title gate before ranking, compares sold properties by local market bucket, and
tests redevelopment products without treating TOA or listing copy as entitlement.

The deterministic `config/research.yaml` profile remains available for Phase 1 and
fixture regression validation. Operational research commands use the MLS-first
profile below:

```bash
northshore-watchdog research-acquire --config config/research-mls-first.yaml --dry-run
northshore-watchdog research-enrich --config config/research-mls-first.yaml
northshore-watchdog research-comps --config config/research-mls-first.yaml
northshore-watchdog research-redevelop --config config/research-mls-first.yaml
northshore-watchdog research-run-weekly --config config/research-mls-first.yaml --dry-run
northshore-watchdog research-conflicts --config config/research-mls-first.yaml
northshore-watchdog research-validate-phase1 --config config/research.yaml
northshore-watchdog research-run-weekly --config config/research-real-single-source.yaml
northshore-watchdog research-verifications --config config/research-real-single-source.yaml
```

No broad crawler is enabled. Public-feed rows and raw response hashes are retained
with their own provenance, while PID/title, zoning, approved use, assessment,
schools, roads, and sale history remain unknown until official or manual
verification is supplied. A blocked public source is reported as a source
limitation, never converted into fabricated candidates. The real-data profile is
an eight-record bounded public capture for pipeline validation, not a complete
MLS universe.

Phase 5 weekly runs are cron-compatible and keep append-only source runs,
observations, score/scenario snapshots, watchlist events, and conflict history.
Use `--observed-at` to replay a capture at a deterministic timestamp. A crawl
manifest can reconcile a missing listing as `REMOVED_OR_UNKNOWN` only when that
manifest proves complete coverage; partial, blocked, or challenge manifests do
not infer removals.
