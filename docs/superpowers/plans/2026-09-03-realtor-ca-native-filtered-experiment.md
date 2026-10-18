# REALTOR.ca Native Filtered Discovery Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing bounded discovery source construct a real REALTOR.ca map search URL from a validated filter plan and use it as the Playwright navigation target.

**Architecture:** Keep URL construction pure and independent from Playwright. The builder preserves the supplied map viewport/search fragment and rewrites only the native filter parameters. The browser source opts into this builder through configuration; the deterministic replay source remains the default for tests and offline runs. No pagination, detail navigation, API bypass, or stealth behavior is added.

**Tech Stack:** Python 3.11, `urllib.parse`, existing Pydantic discovery models, optional Playwright, pytest.

---

## File map

Create:

- `src/northshore_watchdog/research/realtor_map.py` — pure REALTOR.ca map-fragment URL builder.
- `tests/test_research_realtor_map.py` — URL-preservation and filter-encoding tests.

Modify:

- `src/northshore_watchdog/research/discovery_browser.py` — resolve a configured `REALTOR_CA_MAP` entrypoint before the first navigation.
- `tests/test_research_discovery_sources.py` — verify the browser source navigates to the generated URL.
- `config/research-discovery.yaml` — add an explicitly selectable real REALTOR.ca native source while leaving replay as the deterministic default.
- `README.md` — document how to select the real experiment and its fail-closed limitations.

## Tasks

### Task 1: Build the REALTOR.ca map URL

- [x] Add failing tests for preservation of viewport/geography and rewriting of status, house type, freehold, price, beds, baths, and area parameters.
- [x] Implement `build_realtor_ca_map_url(plan, source_url)` using `urlsplit`, `parse_qsl`, and `urlencode`.
- [x] Map the project-level values `ACTIVE`, `DETACHED_HOUSE`, and `FREEHOLD` to the URL values observed in the user-supplied REALTOR.ca route.
- [x] Raise a clear `ValueError` when the configured source URL has no fragment, because a bare `/map` URL does not encode the selected search state.
- [x] Run `rtk .venv/bin/pytest tests/test_research_realtor_map.py -q` and require all tests to pass.

### Task 2: Connect the builder to the bounded Playwright source

- [x] Add a failing test showing `url_builder: REALTOR_CA_MAP` changes the first `goto` URL while preserving the existing no-detail/no-pagination action contract.
- [x] Change only entrypoint resolution in `PlaywrightNativeDiscoverySource`; all existing capture validation and challenge handling remains unchanged.
- [x] Add rendered filter-control capture and skip control reads on blocked pages.
- [x] Run the focused browser-source tests and require all existing tests to remain green.

### Task 3: Add a selectable real-source configuration

- [x] Add `realtor-ca-native` as an enabled source after the replay source so current offline tests retain their deterministic default.
- [x] Configure it with the user-supplied map route, `url_builder: REALTOR_CA_MAP`, rendered-result selectors, and the native field capability mapping including the weaker `2+` bathroom mapping for a desired `2.5+` filter.
- [x] Do not claim the real source has passed until a rendered browser capture provides a result count, cards, and filter evidence.

### Task 4: Verify and report

- [x] Run the new focused tests, all discovery tests, and the complete test suite.
- [x] Attempt the real source through the configured standard browser adapter; the runtime was available.
- [x] Save the exact blocked-page HTML and discovery report; no real listing capture was obtained because REALTOR.ca returned HTTP 403.
- [x] Update README with the command, expected evidence, and blocker semantics.

## Acceptance checks

- The supplied map route remains geographically identical after filter rewriting.
- A desired `2.5` bathroom minimum becomes a native `2+` route only when the plan retains `2.5` as a post-filter.
- Missing URL fragment, challenge, non-2xx response, unknown result count, and over-cap result count fail closed.
- No existing replay or legacy full-market test depends on live REALTOR.ca access.
- No detail page, pagination, CAPTCHA bypass, proxy rotation, or direct API fallback is introduced.
