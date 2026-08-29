"""Configurable ticket constraints for Analiza Cota AI.

This layer keeps sport analysis intact and changes only how the final multi-event
 ticket is built when the user sets ticket/selection limits.
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


def has_custom_constraints(
    odds_min=0,
    odds_max=0,
    min_legs=0,
    max_legs=0,
    leg_odds_min=0,
    leg_odds_max=0,
):
    return any(
        (
            _positive_float(odds_min),
            _positive_float(odds_max),
            _positive_int(min_legs),
            _positive_int(max_legs),
            _positive_float(leg_odds_min),
            _positive_float(leg_odds_max),
        )
    )


def _candidate_fixtures(rows, target, leg_odds_min=0, leg_odds_max=0):
    """Return at most one option set per event, respecting per-selection odds."""
    target = float(target)
    leg_odds_min = _positive_float(leg_odds_min) or 1.02
    explicit_max = _positive_float(leg_odds_max)

    if target >= 50:
        min_prob, default_max = 0.49, 2.40
    elif target >= 10:
        min_prob, default_max = 0.53, 3.00
    elif target >= 5:
        min_prob, default_max = 0.55, 3.30
    else:
        min_prob, default_max = 0.57, 3.50

    max_odd = explicit_max or default_max
    fixtures = []
    for row in rows:
        opts = []
        for pick in row.get("markets", []):
            try:
                odd = float(pick.get("bookmaker_odds") or 0)
                prob = float(pick.get("ticket_probability") or pick.get("probability") or 0) / 100.0
            except (TypeError, ValueError):
                continue
            if pick.get("suspicious") or prob < min_prob:
                continue
            if odd < leg_odds_min or odd > max_odd:
                continue
            opts.append(
                {
                    **pick,
                    "home": row["home"],
                    "away": row["away"],
                    "kickoff": row.get("kickoff"),
                    "combo_prob": prob,
                }
            )
        if opts:
            opts.sort(
                key=lambda x: (
                    x["combo_prob"],
                    x.get("recommendation_score", 0),
                    -x["bookmaker_odds"],
                ),
                reverse=True,
            )
            fixtures.append(opts[:8])
    return fixtures


def build_combo(
    rows,
    target,
    odds_min=0,
    odds_max=0,
    min_legs=0,
    max_legs=0,
    leg_odds_min=0,
    leg_odds_max=0,
):
    """Build the safest ticket inside explicit total and per-selection limits."""
    target = max(1.01, float(target))
    odds_min = _positive_float(odds_min)
    odds_max = _positive_float(odds_max)
    min_legs = _positive_int(min_legs) or 1
    max_legs = min(_positive_int(max_legs) or 20, 30)
    leg_odds_min = _positive_float(leg_odds_min)
    leg_odds_max = _positive_float(leg_odds_max)

    if min_legs > max_legs:
        raise ValueError("Numărul minim de meciuri nu poate fi mai mare decât maximul")
    if odds_min and odds_max and odds_min > odds_max:
        raise ValueError("Cota minimă a biletului nu poate fi mai mare decât cota maximă")
    if leg_odds_min and leg_odds_max and leg_odds_min > leg_odds_max:
        raise ValueError("Cota minimă per meci nu poate fi mai mare decât cota maximă")

    preferred_lo, preferred_hi, fallback_lo, fallback_hi, default_leg_max = _bands(target)
    explicit_odds = bool(odds_min or odds_max)

    if explicit_odds:
        low = max(1.01, odds_min or 1.01)
        high = min(500.0, odds_max or 500.0)
        preferred_low, preferred_high = low, high
    else:
        low = target * fallback_lo
        high = target * fallback_hi
        preferred_low = target * preferred_lo
        preferred_high = target * preferred_hi

    if low > high:
        raise ValueError("Intervalul de cotă al biletului este invalid")

    fixtures = _candidate_fixtures(rows, target, leg_odds_min, leg_odds_max)
    effective_leg_min = leg_odds_min or 1.02
    effective_leg_max = leg_odds_max or default_leg_max

    diag = {
        "candidate_matches": len(fixtures),
        "candidate_selections": sum(len(x) for x in fixtures),
        "target_low": round(preferred_low, 2),
        "target_high": round(preferred_high, 2),
        "requested_odds_min": round(low, 2),
        "requested_odds_max": round(high, 2),
        "requested_min_legs": min_legs,
        "requested_max_legs": max_legs,
        "requested_leg_odds_min": round(effective_leg_min, 2),
        "requested_leg_odds_max": round(effective_leg_max, 2),
        "closest_reachable_odds": None,
        "max_leg_odds_used": None,
        "best_joint_probability": None,
        "constraint_mode": True,
    }
    if not fixtures:
        return None, diag

    # State key includes odds bucket and number of legs so leg-count constraints
    # cannot be erased by a shorter path in the same odds bucket.
    scale = 120
    states = {(0, 0): (1.0, 1.0, [], 1.0)}

    for fixture_opts in fixtures:
        nxt = dict(states)
        for cur_odd, joint, path, path_max_leg in list(states.values()):
            if len(path) >= max_legs:
                continue
            for x in fixture_opts:
                odd = float(x.get("bookmaker_odds") or 0)
                if odd < effective_leg_min or odd > effective_leg_max:
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
            "requested_leg_odds_min": round(effective_leg_min, 2),
            "requested_leg_odds_max": round(effective_leg_max, 2),
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


def apply(
    result,
    target,
    odds_min=0,
    odds_max=0,
    min_legs=0,
    max_legs=0,
    leg_odds_min=0,
    leg_odds_max=0,
):
    if not has_custom_constraints(
        odds_min,
        odds_max,
        min_legs,
        max_legs,
        leg_odds_min,
        leg_odds_max,
    ):
        return result

    combo, diag = build_combo(
        result.get("ranking") or [],
        target,
        odds_min=odds_min,
        odds_max=odds_max,
        min_legs=min_legs,
        max_legs=max_legs,
        leg_odds_min=leg_odds_min,
        leg_odds_max=leg_odds_max,
    )
    result["suggested_combo"] = combo
    result["combo_diagnostics"] = diag
    result["ticket_constraints"] = {
        "odds_min": diag["requested_odds_min"],
        "odds_max": diag["requested_odds_max"],
        "min_legs": diag["requested_min_legs"],
        "max_legs": diag["requested_max_legs"],
        "leg_odds_min": diag["requested_leg_odds_min"],
        "leg_odds_max": diag["requested_leg_odds_max"],
    }
    return result
