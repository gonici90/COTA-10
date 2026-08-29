"""Configurable ticket constraints for Analiza Cota AI.

This layer keeps the existing sport analysis intact and only changes how the final
multi-event ticket is built when the user explicitly sets limits.
"""
import math

import market_engine


def _bands(target):
    target = float(target)
    if target >= 50:
        return (0.98, 1.02, 0.95, 1.05, 2.40)
    if target >= 10:
        return (0.95, 1.05, 0.90, 1.10, 3.00)
    if target >= 5:
        return (0.94, 1.06, 0.90, 1.10, 3.30)
    return (0.97, 1.05, 0.93, 1.08, 3.50)


def _positive_float(value):
    try:
        x = float(value or 0)
        return x if math.isfinite(x) and x > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _positive_int(value):
    try:
        x = int(value or 0)
        return x if x > 0 else 0
    except (TypeError, ValueError):
        return 0


def has_custom_constraints(odds_min=0, odds_max=0, min_legs=0, max_legs=0):
    return any(
        (
            _positive_float(odds_min),
            _positive_float(odds_max),
            _positive_int(min_legs),
            _positive_int(max_legs),
        )
    )


def build_combo(rows, target, odds_min=0, odds_max=0, min_legs=0, max_legs=0):
    """Build the safest ticket inside explicit total-odds and leg-count limits.

    At most one selection is used from each event. Joint probability remains the
    primary objective, then closeness to the requested target and lower max leg odds.
    """
    target = max(1.01, float(target))
    odds_min = _positive_float(odds_min)
    odds_max = _positive_float(odds_max)
    min_legs = _positive_int(min_legs) or 1
    max_legs = _positive_int(max_legs) or 20
    max_legs = min(max_legs, 30)

    if min_legs > max_legs:
        raise ValueError("Numărul minim de meciuri nu poate fi mai mare decât maximul")
    if odds_min and odds_max and odds_min > odds_max:
        raise ValueError("Cota minimă nu poate fi mai mare decât cota maximă")

    preferred_lo, preferred_hi, fallback_lo, fallback_hi, max_leg_odd = _bands(target)
    explicit_odds = bool(odds_min or odds_max)

    if explicit_odds:
        low = odds_min or 1.01
        high = odds_max or 500.0
        high = min(high, 500.0)
        low = max(1.01, low)
        preferred_low = low
        preferred_high = high
    else:
        low = target * fallback_lo
        high = target * fallback_hi
        preferred_low = target * preferred_lo
        preferred_high = target * preferred_hi

    if low > high:
        raise ValueError("Intervalul de cotă este invalid")

    fixtures = market_engine._combo_candidates(rows, target)
    # Five alternatives/event are enough for range fitting and keep custom searches fast.
    fixtures = [opts[:5] for opts in fixtures if opts]

    diag = {
        "candidate_matches": len(fixtures),
        "candidate_selections": sum(len(x) for x in fixtures),
        "target_low": round(preferred_low, 2),
        "target_high": round(preferred_high, 2),
        "requested_odds_min": round(low, 2),
        "requested_odds_max": round(high, 2),
        "requested_min_legs": min_legs,
        "requested_max_legs": max_legs,
        "closest_reachable_odds": None,
        "max_leg_odds_used": None,
        "best_joint_probability": None,
        "constraint_mode": True,
    }
    if not fixtures:
        return None, diag

    # Keep a separate state for each odds bucket + leg count. Without leg count in
    # the key, a high-probability 2-leg path can incorrectly erase a needed 4-leg path.
    scale = 120
    states = {(0, 0): (1.0, 1.0, [], 1.0)}

    for fixture_opts in fixtures:
        nxt = dict(states)  # skipping the event is allowed
        for cur_odd, joint, path, path_max_leg in list(states.values()):
            if len(path) >= max_legs:
                continue
            for x in fixture_opts:
                odd = float(x.get("bookmaker_odds") or 0)
                if odd <= 1.01 or odd > max_leg_odd:
                    continue
                no = cur_odd * odd
                if no > high:
                    continue
                nj = joint * float(x.get("combo_prob") or 0)
                if nj <= 0:
                    continue
                new_legs = len(path) + 1
                bucket = round(math.log(max(no, 1.0)) * scale)
                key = (bucket, new_legs)
                nmax = max(path_max_leg, odd)
                old = nxt.get(key)
                if (
                    old is None
                    or nj > old[1] + 1e-12
                    or (abs(nj - old[1]) <= 1e-12 and nmax < old[3])
                    or (
                        abs(nj - old[1]) <= 1e-12
                        and abs(nmax - old[3]) <= 1e-12
                        and abs(no - target) < abs(old[0] - target)
                    )
                ):
                    nxt[key] = (no, nj, path + [x], nmax)
        states = nxt

    paths = [v for v in states.values() if v[2] and min_legs <= len(v[2]) <= max_legs]
    if paths:
        ideal = min(max(target, low), high)
        closest = min(paths, key=lambda v: abs(math.log(max(v[0], 1e-12) / ideal)))
        diag["closest_reachable_odds"] = round(closest[0], 2)

    valid = [v for v in paths if low <= v[0] <= high]
    if not valid:
        return None, diag

    ideal = min(max(target, low), high)
    preferred = [v for v in valid if preferred_low <= v[0] <= preferred_high]
    pool = preferred or valid
    best = max(
        pool,
        key=lambda v: (
            v[1],
            -abs(math.log(max(v[0], 1e-12) / ideal)),
            -v[3],
            len(v[2]),
        ),
    )

    odd, joint, path, used_max_leg = best
    diag["max_leg_odds_used"] = round(used_max_leg, 2)
    diag["best_joint_probability"] = round(joint * 100, 3)

    return (
        {
            "combined_odds": round(odd, 2),
            "estimated_joint_probability": round(joint * 100, 3),
            "target_met": low <= odd <= high,
            "requested_target": target,
            "requested_odds_min": round(low, 2),
            "requested_odds_max": round(high, 2),
            "requested_min_legs": min_legs,
            "requested_max_legs": max_legs,
            "average_leg_odds": round(odd ** (1.0 / len(path)), 2),
            "max_leg_odds": round(max(x["bookmaker_odds"] for x in path), 2),
            "matches": [
                {
                    "home": x["home"],
                    "away": x["away"],
                    "kickoff": x.get("kickoff"),
                    "selection": x["market"],
                    "probability": x["probability"],
                    "ticket_probability": x.get("ticket_probability"),
                    "odds": x["bookmaker_odds"],
                    "ev": x.get("ev"),
                    "score": x.get("recommendation_score"),
                }
                for x in path
            ],
        },
        diag,
    )


def apply(result, target, odds_min=0, odds_max=0, min_legs=0, max_legs=0):
    """Replace only the final combo; preserve all sport-specific analysis/ranking."""
    if not has_custom_constraints(odds_min, odds_max, min_legs, max_legs):
        return result

    combo, diag = build_combo(
        result.get("ranking") or [],
        target,
        odds_min=odds_min,
        odds_max=odds_max,
        min_legs=min_legs,
        max_legs=max_legs,
    )
    result["suggested_combo"] = combo
    result["combo_diagnostics"] = diag
    result["ticket_constraints"] = {
        "odds_min": diag["requested_odds_min"],
        "odds_max": diag["requested_odds_max"],
        "min_legs": diag["requested_min_legs"],
        "max_legs": diag["requested_max_legs"],
    }
    return result
