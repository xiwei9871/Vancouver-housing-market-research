# Greater Vancouver Detached House Research System V1 — Public Feed Acquisition Design

**Date:** 2026-08-31

**Status:** Approved by the project owner in conversation. This is the Phase 2
acquisition addendum to the accepted Phase 1 and Phase 2–5 baseline designs.

## Objective

Turn the existing bounded local-snapshot acquisition path into a repeatable
read-only public-feed path for REALTOR.ca and Zolo. The path attempts sources
in the existing order `MLS_PRIMARY`, `REALTOR_CA_PUBLIC`, `ZOLO_PUBLIC`, keeps
the source role visible, and feeds the existing normalization, title gate,
Stage 1 scan, SQLite history, and weekly delta code.

This phase does not implement a private MLS/VOW feed, comparable valuation,
municipal-record automation, or redevelopment entitlement inference.

## Architecture

```text
configured public URLs / public JSON requests
                    |
                    v
          PublicFeedSource (one source run)
          - robots check
          - domain/request limits
          - HTTP status and challenge detection
          - raw response snapshot + sha256
          - source-specific parser
                    |
                    v
          SourceRunResult + normalized listing rows
                    |
                    v
       existing MLS-first merge and Stage 1 pipeline
                    |
                    v
       append-only source_snapshots/source_runs/history
```

`PublicFeedSource` is a source adapter, not a browser automation layer. It
uses ordinary HTTP GET/POST requests configured by the user, follows no login,
CAPTCHA, WAF, proxy, fingerprint, or anti-bot workaround, and fails closed if
robots information cannot be retrieved or disallows the target. A transport
can be injected in tests or supplied by a separately authorized application;
the default runtime uses direct public HTTP only.

## Public feed contract

Each public source definition contains:

- `type: PUBLIC_HTTP`;
- source role, registry ID, priority, and source type from the registry;
- one or more same-domain URL requests;
- `method` (`GET` or `POST`), optional form/query data, and parser name;
- allowed domains, user agent, timeout, per-run request cap, and delay;
- `respect_robots: true` by default;
- `raw_snapshot_dir`, `source_version`, and `coverage_complete: false`.

The initial profile uses REALTOR.ca's public listing search surface and its
public JSON search request shape, plus Zolo public search pages where the
configured URL is reachable. It is intentionally a bounded regional scan,
not an assertion that the feed is a complete MLS universe.

## Parsing and trust boundary

The parsers normalize only observable listing fields: MLS/listing ID, current
ask, address, municipality/neighbourhood when exposed, listing status/date,
DOM when explicitly supplied, house/lot measurements, bedrooms/bathrooms,
property type, public description, and canonical listing URL.

Public text claims for PID, legal description, ownership, approved use,
zoning, assessment, permits, schools, road risk, and sold price remain raw
provenance claims and cannot establish official evidence in the existing
normalizer. A listing price is never treated as a sale price.

The REALTOR.ca JSON parser accepts the public search response envelope
(`Results` and `Paging`) and maps nested `Property`, `Building`, and `Address`
values. The HTML parser supports listing cards, JSON-LD/embedded JSON where
available, and link/text fallback. Zolo uses the same conservative HTML/JSON
parser contract; unsupported markup produces a visible parse failure rather
than guessed rows.

## Safety, freshness, and failure behavior

- A robots fetch that is unavailable is `BLOCKED_POLICY` for that host; no
  target request is made to that host. A separately configured allowed host may
  still be attempted once under the same bounded source run.
- HTTP 401/403/429, CAPTCHA, Cloudflare/Imperva challenge, or an explicit
  block page stops that domain for the current source run.
- Redirects to an unlisted domain are rejected.
- Network errors are `FAILED`; malformed rows are quarantined and make the run
  `PARTIAL` when valid rows remain.
- Every HTTP response, including an error/challenge response, is saved under
  the configured raw snapshot directory with URL, request kind (`robots` or
  `target`), status, content type, observed/retrieved timestamps, byte count,
  SHA-256, and source version.
- A run never deletes an earlier raw response, source run, property
  observation, or watchlist event.
- A blocked or partial live source leaves the prior property projection intact
  and exposes the coverage gap in the run report.

## Storage and pipeline integration

Add an append-only `source_snapshots` table and `SourceSnapshot` model. A
`SourceRunResult` carries target and robots snapshot metadata;
`ResearchStorage.save_source_run` persists both the run and its snapshots.
`build_source_adapters` recognizes `PUBLIC_HTTP` and passes the existing config
and source registry envelope to `PublicFeedSource`.

The public profile changes only acquisition source definitions. It keeps the
disabled MLS primary slot first, then public REALTOR.ca and Zolo sources, and
does not enable any official enrichment provider. The existing fallback,
identity, conflict, title-gate, scoring, and report semantics remain intact.

## Acceptance criteria

1. A fake public JSON response produces normalized rows, source metadata, and
   a raw snapshot hash without making an external request.
2. A fake public HTML response produces listing rows from both REALTOR.ca-style
   and Zolo-style cards.
3. Robots disallow, missing robots, 403, 429, and challenge pages stop the
   source and are visible in `SourceRunResult`.
4. Per-run request limits prevent a second request after the configured cap.
5. `source_snapshots` and `source_runs` are append-only across two runs.
6. The MLS-first profile can run through the existing pipeline; live blocking
   results in `PARTIAL`/`FAILED` with no fabricated candidates.
7. All Phase 1 and existing Phase 2–5 tests remain green.
