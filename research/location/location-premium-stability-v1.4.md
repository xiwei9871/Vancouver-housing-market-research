# Combined Location Premium Stability Research V1.4

This is research-only. It does not modify production scoring, RALV, MOS, or candidate ranking.

## Interpretation

The value below is the combined location residual (a combined neighbourhood/location residual) for one fixed standard property. School is not separately priced because it is not identified independently from neighbourhood, buyer-pool, and other correlated location effects. Road class is controlled in this sensitivity run; it is not added again to the combined residual.

- Eligible Vancouver detached/freehold sales: `5048`
- Reference end date: `2026-09-06`
- Status: `READY_FOR_REVIEW`
- Automatic application: `DISABLED`
- School split: `FALSE`

## Standard property and neutral value

- Profile: `VANCOUVER_STANDARD_PROPERTY_V2`
- Standard property: `{"baths": 2, "bca_improvement": 219000.0, "bca_improvement_reference": "ELIGIBLE_SAMPLE_MEDIAN_CONTROL", "beds": 3, "condition": "NEUTRAL", "condition_definition": "Normally habitable, ordinary maintenance, no luxury renovation premium, no major deferred maintenance.", "depth_ft": 122, "frontage_ft": 33, "living_area_sqft": 1700, "lot_area_sqft": 4026, "lot_shape": "REGULAR_RECTANGULAR", "park_facing": false, "profile_name": "VANCOUVER_STANDARD_PROPERTY_V2", "property_type": "DETACHED_FREEHOLD", "road_class": "E", "sale_date": "2026-07-01", "view": "NONE", "waterfront": false, "year_built": 1960, "year_built_reference": 1960, "year_built_sensitivity": [1940, 1960, 1980], "zoning": "R1-1", "zoning_group": "R"}`
- Standard geometry/use: `4,026 sqft; 33×122 ft; 1,700 sqft; 3 beds / 2 baths`
- Neutral standard value (FULL_36M common anchor): `$2,064,000`
- Neutral age sensitivity: `{"1940": 2072000.0, "1960": 2064000.0, "1980": 2056000.0}`

## Stable summary

The reported value is the median of available time-window/cohort estimates. The sensitivity interval is the observed min/max across those variants; it is not a formal confidence interval.

| Neighbourhood | Standard property value | Standard value range | Median residual | 36M | 24M | 12M | Old-house cohort | Samples (36M) | Stability | Confidence |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| West Point Grey | $2,627,000 | $2,303,000–$2,696,000 | $563,000 | $632,000 | $588,000 | $538,000 | $239,000 | 219 | UNSTABLE | LOW |
| Shaughnessy | $2,600,000 | $2,411,000–$2,633,000 | $536,000 | $536,000 | $569,000 | $347,000 | UNKNOWN | 114 | MODERATELY_STABLE | MEDIUM |
| Kitsilano | $2,562,000 | $2,342,000–$2,582,000 | $498,000 | $518,000 | $514,000 | $482,000 | $278,000 | 199 | MODERATELY_STABLE | MEDIUM |
| South Cambie | $2,440,000 | $2,226,000–$2,545,000 | $376,000 | $481,000 | $434,000 | $318,000 | $162,000 | 60 | UNSTABLE | LOW |
| Dunbar-Southlands | $2,438,000 | $2,205,000–$2,440,000 | $374,000 | $376,000 | $373,000 | $376,000 | $141,000 | 395 | MODERATELY_STABLE | MEDIUM |
| Oakridge | $2,358,000 | $2,235,000–$2,369,000 | $294,000 | $305,000 | $294,000 | $171,000 | UNKNOWN | 89 | STABLE | MEDIUM |
| Kerrisdale | $2,248,000 | $2,237,000–$2,263,000 | $184,000 | $199,000 | $184,000 | $173,000 | UNKNOWN | 223 | STABLE | HIGH |
| Arbutus Ridge | $2,245,000 | $2,190,000–$2,277,000 | $181,000 | $213,000 | $181,000 | $126,000 | UNKNOWN | 174 | STABLE | MEDIUM |
| Riley Park | $2,216,000 | $2,176,000–$2,232,000 | $152,000 | $112,000 | $168,000 | $167,000 | $136,000 | 236 | STABLE | HIGH |
| Marpole | $2,059,000 | $2,050,000–$2,084,000 | $-5,000 | $20,000 | $-5,000 | $-14,000 | UNKNOWN | 144 | STABLE | MEDIUM |
| Mount Pleasant | $1,999,000 | $1,941,000–$2,105,000 | $-65,000 | $-123,000 | $-65,000 | $41,000 | UNKNOWN | 63 | MODERATELY_STABLE | MEDIUM |
| Grandview-Woodland | $1,915,000 | $1,812,000–$2,021,000 | $-149,000 | $-169,000 | $-129,000 | $-43,000 | $-252,000 | 184 | MODERATELY_STABLE | MEDIUM |
| Strathcona | $1,908,000 | $1,892,000–$1,924,000 | $-156,000 | $-140,000 | $-172,000 | UNKNOWN | UNKNOWN | 41 | STABLE | LOW |
| Killarney | $1,793,000 | $1,762,000–$1,907,000 | $-271,000 | $-302,000 | $-271,000 | $-157,000 | UNKNOWN | 131 | STABLE | MEDIUM |
| Kensington-Cedar Cottage | $1,792,000 | $1,748,000–$1,802,000 | $-272,000 | $-316,000 | $-271,000 | $-272,000 | $-262,000 | 441 | STABLE | HIGH |
| Victoria-Fraserview | $1,775,000 | $1,740,000–$1,853,000 | $-289,000 | $-324,000 | $-289,000 | $-211,000 | UNKNOWN | 225 | STABLE | HIGH |
| Renfrew-Collingwood | $1,760,000 | $1,731,000–$1,836,000 | $-304,000 | $-333,000 | $-304,000 | $-228,000 | UNKNOWN | 332 | STABLE | HIGH |
| Sunset | $1,749,000 | $1,730,000–$1,803,000 | $-315,000 | $-334,000 | $-315,000 | $-261,000 | UNKNOWN | 239 | STABLE | HIGH |
| Hastings-Sunrise | $1,740,000 | $1,720,000–$1,817,000 | $-324,000 | $-344,000 | $-319,000 | $-247,000 | $-328,000 | 388 | STABLE | HIGH |

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
