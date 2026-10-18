# Combined Location Premium Stability Research V1.3

This is research-only. It does not modify production scoring, RALV, MOS, or candidate ranking.

## Interpretation

The value below is the combined location residual (a combined neighbourhood/location residual). School is not separately priced because it is not identified independently from neighbourhood, buyer-pool, and other correlated location effects. Road class is controlled in this V1.3 sensitivity run; it is not added again to the combined residual.

- Eligible Vancouver detached/freehold sales: `5048`
- Reference end date: `2026-09-06`
- Status: `READY_FOR_REVIEW`
- Automatic application: `DISABLED`
- School split: `FALSE`

## Stable summary

The reported value is the median of available time-window/cohort estimates. The sensitivity interval is the observed min/max across those variants; it is not a formal confidence interval.

| Neighbourhood | Median residual | 36M | 24M | 12M | Old-house cohort | Sensitivity range | Samples (36M) | Stability | Confidence |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| West Point Grey | $570,000 | $650,000 | $595,000 | $544,000 | $225,000 | $425,000 | 219 | UNSTABLE | LOW |
| Shaughnessy | $551,000 | $551,000 | $575,000 | $351,000 | UNKNOWN | $224,000 | 114 | MODERATELY_STABLE | MEDIUM |
| Kitsilano | $504,000 | $532,000 | $520,000 | $487,000 | $262,000 | $270,000 | 199 | UNSTABLE | LOW |
| South Cambie | $380,000 | $494,000 | $439,000 | $321,000 | $152,000 | $342,000 | 60 | UNSTABLE | LOW |
| Dunbar-Southlands | $378,000 | $387,000 | $377,000 | $380,000 | $133,000 | $254,000 | 395 | UNSTABLE | LOW |
| Oakridge | $298,000 | $313,000 | $298,000 | $173,000 | UNKNOWN | $140,000 | 89 | STABLE | MEDIUM |
| Kerrisdale | $186,000 | $204,000 | $186,000 | $175,000 | UNKNOWN | $29,000 | 223 | STABLE | HIGH |
| Arbutus Ridge | $183,000 | $219,000 | $183,000 | $128,000 | UNKNOWN | $91,000 | 174 | STABLE | MEDIUM |
| Riley Park | $148,000 | $115,000 | $170,000 | $168,000 | $128,000 | $55,000 | 236 | STABLE | HIGH |
| Marpole | $-5,000 | $20,000 | $-5,000 | $-14,000 | UNKNOWN | $34,000 | 144 | STABLE | MEDIUM |
| Mount Pleasant | $-66,000 | $-126,000 | $-66,000 | $42,000 | UNKNOWN | $168,000 | 63 | MODERATELY_STABLE | MEDIUM |
| Grandview-Woodland | $-152,000 | $-174,000 | $-130,000 | $-44,000 | $-237,000 | $193,000 | 184 | MODERATELY_STABLE | MEDIUM |
| Strathcona | $-159,000 | $-144,000 | $-174,000 | UNKNOWN | UNKNOWN | $30,000 | 41 | STABLE | LOW |
| Kensington-Cedar Cottage | $-274,000 | $-325,000 | $-273,000 | $-275,000 | $-247,000 | $78,000 | 441 | STABLE | HIGH |
| Killarney | $-274,000 | $-310,000 | $-274,000 | $-159,000 | UNKNOWN | $151,000 | 131 | MODERATELY_STABLE | MEDIUM |
| Victoria-Fraserview | $-292,000 | $-334,000 | $-292,000 | $-214,000 | UNKNOWN | $120,000 | 225 | STABLE | HIGH |
| Renfrew-Collingwood | $-307,000 | $-342,000 | $-307,000 | $-231,000 | UNKNOWN | $111,000 | 332 | STABLE | HIGH |
| Hastings-Sunrise | $-316,000 | $-354,000 | $-323,000 | $-250,000 | $-309,000 | $104,000 | 388 | STABLE | HIGH |
| Sunset | $-319,000 | $-344,000 | $-319,000 | $-264,000 | UNKNOWN | $80,000 | 239 | STABLE | HIGH |

## Variants

| Variant | Rows | Definition |
|---|---:|---|
| FULL_36M | 3908 | observed transactions within 36 months before reference_end |
| RECENT_24M | 2403 | observed transactions within 24 months before reference_end |
| RECENT_12M | 1017 | observed transactions within 12 months before reference_end |
| OLD_HOUSE_STANDARD_LOT | 176 | 36-month rows with 3,700-4,300 sqft, 30-36 ft frontage, 1,500-2,500 sqft living area, pre-1985 house |

## Limitations

- The BCA sales layer currently exposes an observed window beginning in 2023-01-01 for this run; requesting an earlier start date did not produce earlier observations. This is therefore an expanded price-range sample, not a verified five-year history.
- BCA provides sale price and property/assessment controls, but transaction MLS identifiers remain unavailable for the bulk sample.
- Current school catchments and current zoning are spatial context, not necessarily historical as-of-sale attributes.
- The residual is not a causal school premium and must not be applied together with an independently estimated school premium.
