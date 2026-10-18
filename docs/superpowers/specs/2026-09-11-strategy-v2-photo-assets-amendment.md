# Strategy V2 Phase 2A — Photo Asset and Visual Classification Amendment

**Date:** 2026-09-11  
**Status:** Implemented as an opt-in extension to the approved Phase 2A spec

## Purpose

The original Phase 2A replay stored REALTOR.ca image URLs and gallery order,
but deliberately did not fetch image bytes. This amendment adds a separate,
append-only photo-asset snapshot so image acquisition and visual recognition
can progress without overwriting the URL-only baseline or changing V1/V2
ranking behavior.

## Acquisition contract

`acquire_strategy_v2_photo_assets.py` reads only existing local manifests. It
does not discover pages, bypass access controls, or replace a failed source.
For each image it records:

```text
property_key
source_url
original_order
retrieved_at
sha256
perceptual_hash
perceptual_hash_algorithm
width
height
local_path
source_status
content_type
file_size_bytes
duplicate_of_order
duplicate_reason
```

Exact URL/SHA-256 duplicates are retained as observations but share the first
local asset. Perceptual duplicates use a deterministic dHash where an image
decoder is available. A macOS `sips` fallback supports the current local
environment; if no decoder is available, the hash remains `UNKNOWN` rather
than being replaced by a byte digest masquerading as a visual hash.

The default output is separate from `data/research/raw/photos`:

```text
data/research/raw/photo-assets/<property-key>/manifest.json
reports/strategy-v2-photo-acquisition/<date>/photo-acquisition-summary.json
reports/strategy-v2-photo-acquisition/<date>/photo-acquisition-summary.csv
reports/strategy-v2-photo-acquisition/<date>/photo-acquisition-report.md
```

## Classification contract

The classifier pipeline is ordered per unique local image:

1. classify controlled space/content labels;
2. classify visible maintenance, cosmetic modernization, and visible defects
   using the space labels as context.

The three holding dimensions remain separate. A missing classifier, missing
credential, failed request, or low image coverage produces `UNAVAILABLE`,
`FAILED`, or `UNKNOWN`; it never becomes a poor-condition finding.

The repository includes a provider-neutral JSON HTTP vision adapter for an
OpenAI-compatible multimodal endpoint. It is inert until endpoint, model, and
the configured API-key environment variable are supplied. The current local
run has no such key, so acquisition can be real while visual classification
remains explicitly unavailable.

## Pipeline integration

The existing Phase 2A runner remains backward-compatible. Use
`--acquire-photos` to create a new asset snapshot and `--classify-photos` to
invoke the configured adapter or record `UNAVAILABLE`. The frozen default
configuration keeps both switches disabled, so the 2026-09-11 URL-only
baseline remains reproducible.
