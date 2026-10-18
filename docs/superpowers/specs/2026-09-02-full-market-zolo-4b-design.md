# Full-Market Zolo Spider Boundary Design

## Scope

Implement only the Zolo portion of Task 4B in
`src/northshore_watchdog/research/crawling/spider.py`. The change extends the
existing optional Scrapy boundary and pure Task 4A helpers. Realtor callbacks,
the crawl runner, and CLI remain out of scope.

## Design

`FullMarketSpider` copies the source/context contract used by the existing
tests: source identity, allowlisted domains, entrypoints, and context run
metadata/crawl caps are exposed on the spider. The artifact writer is injected
as `artifact_writer=` (with `writer=` and `context["artifact_writer"]` kept as
compatibility paths). State is stored in the existing JSON-safe shape so
`state_snapshot()` and `restore_state()` preserve page URLs, body fingerprints,
route completion, and detail-request keys.

Zolo entrypoint requests and detail requests carry the same required identity
metadata and use `playwright=True`. `parse_zolo_search()` canonicalizes the
response URL, fingerprints the rendered body, handles duplicate/challenge
short-circuits, parses rows with `parse_public_html()`, persists one SEARCH
artifact, schedules policy-approved unique details up to the detail cap, and
emits at most one DOM-derived next-page request. It records explicit coverage
states for repeated, blocked, capped, and naturally exhausted routes.

`parse_zolo_detail()` preserves the originating search rows in the artifact
stream, invokes the existing conservative detail parser, and records either a
successful DETAIL artifact or a DETAIL error artifact when parsing fails.

## Testing

The existing Zolo framework tests are the acceptance contract. The first RED
is the missing `start_requests()` failure. Implementation proceeds in small
RED/GREEN cycles, followed by `tests/test_research_crawling.py -k zolo` and the
full test suite. When the optional crawler extra is absent, framework tests
remain skipped while the missing-extra boundary and pure helpers still run.
