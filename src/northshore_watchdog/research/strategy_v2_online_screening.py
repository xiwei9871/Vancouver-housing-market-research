"""High-recall online screening for Strategy V2 Phase 2A.

This module is intentionally a selector, not an investment-scoring engine.
It protects candidates that are non-dominated within their own asset track and
then uses a small, explicit screening signal to fill the requested shortlist.
It never produces a V1/V2 final investment rank, RALV, or MOS.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


SCREEN_METRICS: tuple[str, ...] = (
    "online_location_score",
    "online_holding_score",
    "online_land_score",
    "price_attractiveness",
)
RISK_METRIC = "permanent_risk"
DEFAULT_SPECIAL_TRACKS: frozenset[str] = frozenset({"SPECIAL_SITUATION"})
VALID_LABELS: frozenset[str] = frozenset({"MUST_KEEP", "KEEP", "MAYBE", "DROP"})


def _key(candidate: Mapping[str, Any]) -> str:
    """Return the stable identity used by the selector.

    ``property_key`` is mandatory for a reliable result.  Address is a small
    defensive fallback for callers that have not materialized the canonical
    key yet; candidates without either identity are not selectable.
    """

    value = candidate.get("property_key")
    if value is None or str(value).strip() == "":
        value = candidate.get("address")
    return str(value).strip() if value is not None else ""


def _track(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("primary_track") or "UNCLASSIFIED_REVIEW").strip().upper()


def _score(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score != score:  # NaN is not evidence.
        return None
    return max(0.0, min(100.0, score))


def _metric(candidate: Mapping[str, Any], name: str) -> float | None:
    return _score(candidate.get(name))


def transparent_screen_signal(candidate: Mapping[str, Any]) -> float:
    """Calculate a transparent fill signal, never a final investment score.

    The four positive dimensions are maximized.  Lower permanent risk is
    converted to a positive ``100 - risk`` contribution.  Missing values are
    omitted, rather than converted to zero.  A candidate with no usable
    signals receives a neutral 50 for deterministic ordering only.
    """

    values = [
        value
        for name in SCREEN_METRICS
        if (value := _metric(candidate, name)) is not None
    ]
    risk = _metric(candidate, RISK_METRIC)
    if risk is not None:
        values.append(100.0 - risk)
    return sum(values) / len(values) if values else 50.0


def _candidate_copy(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return dict(candidate)


def _deduplicate(candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep one deterministic, strongest observation for each property key."""

    by_key: dict[str, dict[str, Any]] = {}
    for raw in candidates:
        candidate = _candidate_copy(raw)
        key = _key(candidate)
        if not key:
            continue
        candidate["property_key"] = key
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = candidate
            continue
        current_signal = transparent_screen_signal(candidate)
        existing_signal = transparent_screen_signal(existing)
        if (current_signal, _track(candidate), key) > (
            existing_signal,
            _track(existing),
            key,
        ):
            by_key[key] = candidate
    return list(by_key.values())


def _directional_value(candidate: Mapping[str, Any], name: str) -> float | None:
    value = _metric(candidate, name)
    if value is None:
        return None
    return value if name != RISK_METRIC else 100.0 - value


def dominates(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Return whether ``left`` Pareto-dominates ``right``.

    Dominance is conservative around missing data: if either candidate lacks
    any core screening signal, neither may dominate the other.  This prevents
    an unknown value from causing a potentially valuable candidate to be
    removed during the high-recall pass.  Risk is normalized so that higher
    transformed values are always better.
    """

    if _track(left) != _track(right):
        return False

    dimensions = (*SCREEN_METRICS, RISK_METRIC)
    if any(
        _metric(candidate, name) is None
        for candidate in (left, right)
        for name in dimensions
    ):
        return False
    comparable: list[tuple[float, float]] = []
    for name in dimensions:
        left_value = _directional_value(left, name)
        right_value = _directional_value(right, name)
        if left_value is None or right_value is None:
            continue
        comparable.append((left_value, right_value))

    if not comparable:
        return False
    no_worse = all(left_value >= right_value for left_value, right_value in comparable)
    strictly_better = any(left_value > right_value for left_value, right_value in comparable)
    return no_worse and strictly_better


def pareto_frontier(candidates: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Return non-dominated candidates, using only their primary track."""

    frontier: list[Mapping[str, Any]] = []
    for candidate in candidates:
        if any(dominates(other, candidate) for other in candidates if other is not candidate):
            continue
        frontier.append(candidate)
    return frontier


def exceptional_signal(candidate: Mapping[str, Any]) -> bool:
    """Identify one-dimensional high signals worth protecting for review."""

    if bool(candidate.get("exceptional_signal")):
        return True
    explicit = candidate.get("exceptional_signals")
    if isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes)) and explicit:
        return True
    return any(
        (_metric(candidate, name) is not None and _metric(candidate, name) >= 95.0)
        for name in SCREEN_METRICS
    )


def _annotate(
    candidate: Mapping[str, Any],
    *,
    reasons: Sequence[str],
    pareto: bool,
    exceptional: bool,
) -> dict[str, Any]:
    result = _candidate_copy(candidate)
    result["property_key"] = _key(result)
    result["selection_reasons"] = list(dict.fromkeys(reasons))
    result["pareto_frontier"] = bool(pareto)
    result["exceptional_signal"] = bool(exceptional)
    result["transparent_screen_signal"] = round(transparent_screen_signal(result), 4)
    return result


def select_shortlist(
    candidates: Iterable[Mapping[str, Any]],
    *,
    track_targets: Mapping[str, int] | None = None,
    target_range: tuple[int, int] = (25, 35),
    special_tracks: Iterable[str] = DEFAULT_SPECIAL_TRACKS,
    protected_caps: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Select a high-recall, track-aware online shortlist.

    Selection order is deliberately explicit:

    1. deduplicate by canonical property key;
    2. exclude special situations from ordinary capacity;
    3. retain a bounded high-signal portion of the within-track Pareto
       frontier plus exceptional signals;
    4. fill track targets using ``transparent_screen_signal``;
    5. if targets leave the global shortlist below the requested minimum, fill
       from the remaining candidates without inventing a final ranking.

    A Pareto frontier can become enormous when a provider dimension is
    unavailable (or when every candidate trades one dimension for another).
    In that case retaining every frontier member would silently turn a
    shortlist into the whole market.  ``protected_caps`` bounds the number
    of frontier members protected per track; it defaults to the track target
    (with a two-item compatibility floor for a one-item exploratory target).
    Exceptional one-dimensional signals remain protected separately.
    """

    if len(target_range) != 2 or target_range[0] < 0 or target_range[1] < target_range[0]:
        raise ValueError("target_range must be a non-negative (minimum, maximum) pair")
    minimum, maximum = target_range

    normalized_targets = {
        str(track).strip().upper(): max(0, int(target))
        for track, target in (track_targets or {}).items()
    }
    normalized_caps = {
        str(track).strip().upper(): max(0, int(cap))
        for track, cap in (protected_caps or normalized_targets).items()
    }
    special = {str(track).strip().upper() for track in special_tracks}
    unique = _deduplicate(candidates)
    ordinary = [candidate for candidate in unique if _track(candidate) not in special]
    by_track: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in ordinary:
        by_track[_track(candidate)].append(candidate)

    selected: dict[str, dict[str, Any]] = {}
    protected_reasons: dict[str, list[str]] = defaultdict(list)

    for track, group in by_track.items():
        frontier_keys = {_key(candidate) for candidate in pareto_frontier(group)}
        target = normalized_targets.get(track, 0)
        frontier_cap = normalized_caps.get(track, target)
        if target == 1 and frontier_cap == 1 and len(frontier_keys) > 1:
            # Preserve the original exploratory Pareto contract for callers
            # asking for a single slot: two complementary frontier signals
            # are more useful than an arbitrary one-item tie break.  The
            # production config targets are 5/20, so this does not inflate
            # the normal 166-property run.
            frontier_cap = 2
        frontier_candidates = sorted(
            (candidate for candidate in group if _key(candidate) in frontier_keys),
            key=lambda candidate: (-transparent_screen_signal(candidate), _key(candidate)),
        )[:frontier_cap]
        frontier_keep = {_key(candidate) for candidate in frontier_candidates}
        for candidate in group:
            key = _key(candidate)
            is_frontier = key in frontier_keep
            is_exceptional = exceptional_signal(candidate)
            if is_frontier or is_exceptional:
                if is_frontier:
                    protected_reasons[key].append("PARETO_FRONTIER")
                if is_exceptional:
                    protected_reasons[key].append("EXCEPTIONAL_SIGNAL")
                selected[key] = candidate

        remaining = sorted(
            (candidate for candidate in group if _key(candidate) not in selected),
            key=lambda candidate: (-transparent_screen_signal(candidate), _key(candidate)),
        )
        track_selected_count = sum(1 for key in selected if _track(selected[key]) == track)
        for candidate in remaining:
            if track_selected_count >= target:
                break
            if len(selected) >= maximum:
                break
            key = _key(candidate)
            selected[key] = candidate
            protected_reasons[key].append("TRACK_TARGET_FILL")
            track_selected_count += 1

    # A small target can be exhausted by protected candidates from a single
    # track.  Fill the global minimum from all remaining ordinary candidates;
    # this is capacity balancing, not a final investment ranking.
    remaining_global = sorted(
        (candidate for candidate in ordinary if _key(candidate) not in selected),
        key=lambda candidate: (-transparent_screen_signal(candidate), _key(candidate)),
    )
    for candidate in remaining_global:
        if len(selected) >= minimum:
            break
        if len(selected) >= maximum:
            break
        key = _key(candidate)
        selected[key] = candidate
        protected_reasons[key].append("GLOBAL_MINIMUM_FILL")

    # Preserve a stable, auditable output order.  This is shortlist ordering
    # only; ``ranking_status`` belongs to the runner and remains NOT_RUN.
    ordered = sorted(
        selected.values(),
        key=lambda candidate: (
            _track(candidate),
            -transparent_screen_signal(candidate),
            _key(candidate),
        ),
    )
    return [
        _annotate(
            candidate,
            reasons=protected_reasons.get(_key(candidate), ["RETAINED"]),
            pareto="PARETO_FRONTIER" in protected_reasons.get(_key(candidate), []),
            exceptional="EXCEPTIONAL_SIGNAL" in protected_reasons.get(_key(candidate), []),
        )
        for candidate in ordered
    ]


def _normalized_label(value: Any) -> str | None:
    label = str(value or "").strip().upper().replace(" ", "_")
    return label if label in VALID_LABELS else None


def _recall(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def evaluate_recall(
    candidates: Iterable[Mapping[str, Any]],
    *,
    retained_keys: Iterable[str],
    labels: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Evaluate calibration retention without fabricating labels.

    The calibration universe is the intersection of candidate identities and
    supplied labels.  A label key therefore explicitly opts a candidate into
    the calibration universe; unrelated candidates in the wider screening
    input are ignored.  ``MUST_KEEP`` recall and combined ``MUST_KEEP/KEEP``
    recall use separate denominators.  No labels yields ``NOT_AVAILABLE``;
    no labeled candidate in the supplied input does too.
    """

    candidate_keys = {_key(candidate) for candidate in candidates if _key(candidate)}
    retained = {str(key).strip() for key in retained_keys if str(key).strip()}
    base: dict[str, Any] = {
        "universe_count": 0,
        "retained_count": len(candidate_keys & retained),
        "labeled_count": 0,
        "unlabeled_count": 0,
        "must_keep_retained": 0,
        "must_keep_denominator": 0,
        "keep_retained": 0,
        "keep_denominator": 0,
        "must_keep_recall": None,
        "keep_recall": None,
    }
    if not labels:
        return {"status": "NOT_AVAILABLE", **base, "reason": "NO_HUMAN_LABELS"}

    normalized_labels: dict[str, str] = {}
    for key, label in labels.items():
        normalized = _normalized_label(label)
        if normalized is not None:
            normalized_labels[str(key).strip()] = normalized

    universe = candidate_keys & set(normalized_labels)
    # Candidates not present in ``labels`` are outside the supplied
    # calibration universe, not implicit DROP labels.  This permits callers
    # to evaluate a fixed 120-property calibration split while passing a
    # larger screening result set.
    unlabeled_count = 0
    base["universe_count"] = len(universe)
    base["labeled_count"] = len(universe)
    base["unlabeled_count"] = unlabeled_count
    base["retained_count"] = len(universe & retained)
    must_keep = {key for key in universe if normalized_labels[key] == "MUST_KEEP"}
    keep = {
        key
        for key in universe
        if normalized_labels[key] in {"MUST_KEEP", "KEEP"}
    }
    base["must_keep_denominator"] = len(must_keep)
    base["keep_denominator"] = len(keep)
    base["must_keep_retained"] = len(must_keep & retained)
    base["keep_retained"] = len(keep & retained)
    base["must_keep_recall"] = _recall(base["must_keep_retained"], base["must_keep_denominator"])
    base["keep_recall"] = _recall(base["keep_retained"], base["keep_denominator"])

    if not universe:
        return {"status": "NOT_AVAILABLE", **base, "reason": "NO_LABELS_IN_UNIVERSE"}
    return {"status": "AVAILABLE", **base}


__all__ = [
    "DEFAULT_SPECIAL_TRACKS",
    "RISK_METRIC",
    "SCREEN_METRICS",
    "dominates",
    "evaluate_recall",
    "exceptional_signal",
    "pareto_frontier",
    "select_shortlist",
    "transparent_screen_signal",
]
