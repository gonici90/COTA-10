"""COTA-10 live multi-market engine backed by 5DollarFootballAPI / Bet365.

The engine deliberately separates three jobs:
1) parse the bookmaker payload robustly;
2) estimate conservative market probabilities;
3) optimise a ticket globally, with at most one selection per fixture.
"""

import math
import time
from datetime import datetime, timezone, timedelta

import auto_data as fd


ODDS_CACHE_TTL = 15 * 60
_ODDS_CACHE = {}

# Conservative reliability multipliers used only by the ticket optimiser.
# Displayed probabilities remain the calibrated market probabilities.
MARKET_RELIABILITY = {
    "Bet365 1X2": 1.00,
    "Bet365 Goals": 0.995,
    "Bet365 AH": 0.990,
    "Bet365 BTTS": 0.985,
    "Bet365 Corners": 0.970,
    "Bet365 Cards": 0.960,
}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pois(k, lam):
    return math.exp(-lam) * lam**k / math.factorial(k)


def _teams(m):
    t = m.get("teams") or {}
    if isinstance(t, dict) and (t.get("home") or t.get("away")):
        h, a = t.get("home") or {}, t.get("away") or {}
    else:
        h = m.get("home_team") or m.get("home") or {}
        a = m.get("away_team") or m.get("away") or {}
    if not isinstance(h, dict):
        h = {"name": str(h)}
    if not isinstance(a, dict):
        a = {"name": str(a)}
    return h, a


def _kickoff_ts(m):
    v = (
        m.get("kickoff_ts")
        or m.get("timestamp")
        or m.get("start_time")
        or m.get("kickoff")
        or m.get("kickoff_utc")
        or m.get("date")
    )
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        try:
            return int(float(v))
        except ValueError:
            pass
        try:
            return int(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
    return int(datetime.now(timezone.utc).timestamp())


def _stage(m):
    """Return the freshest usable pre-match price stage."""
    if not isinstance(m, dict):
        return None
    # Some payloads expose current, some expose closing. Never prefer in-play here.
    for k in ("current", "closing", "opening"):
        if isinstance(m.get(k), dict):
            return m[k]
    if any(k in m for k in ("home", "draw", "away", "over", "under", "yes", "no", "line")):
        return m
    return None


def _normalize_odds(data):
    """Normalise supported 5Dollar envelopes to one Bet365 odds dict."""
    if not isinstance(data, dict):
        return {}

    # Unwrap common API envelopes. Stop when the next layer is not a dict.
    for _ in range(3):
        moved = False
        for k in ("data", "response", "result"):
            if isinstance(data.get(k), dict):
                data = data[k]
                moved = True
                break
        if not moved:
            break

    books = data.get("bookmakers") or data.get("books") or []
    if isinstance(books, dict):
        books = list(books.values())
    if isinstance(books, list) and books:
        bet365 = next(
            (
                x
                for x in books
                if isinstance(x, dict)
                and "365" in str(x.get("slug") or x.get("name") or "").lower()
            ),
            None,
        )
        b = bet365 or next((x for x in books if isinstance(x, dict)), None)
        if isinstance(b, dict):
            out = b.get("odds") or b.get("markets")
            return out if isinstance(out, dict) else {}

    out = data.get("odds") or data.get("markets")
    return out if isinstance(out, dict) else {}


def _has_prices(odds):
    if not isinstance(odds, dict):
        return False

    one = _stage(odds.get("1x2") or odds.get("match_winner"))
    if one and all((_num(one.get(k)) or 0) > 1.01 for k in ("home", "draw", "away")):
        return True

    for key in (
        "asian_handicap",
        "asian",
        "goal_line",
        "goalline",
        "corner_line",
        "corner",
        "corners",
        "card_line",
        "cards",
        "btts",
        "both_teams_to_score",
    ):
        s = _stage(odds.get(key))
        if isinstance(s, dict) and any((_num(s.get(k)) or 0) > 1.01 for k in ("home", "away", "over", "under", "yes", "no")):
            return True
    return False


def _odds_payload(f):
    fid = f.get("id") or f.get("fixture_id")
    inline = f.get("odds") or f.get("markets")
    if inline:
        parsed = _normalize_odds({"odds": inline})
        if _has_prices(parsed):
            return parsed

    if not fid:
        return {}

    now = time.time()
    cached = _ODDS_CACHE.get(fid)
    if cached and now - cached[0] < ODDS_CACHE_TTL:
        return cached[1]

    try:
        raw = fd._get(f"/fixtures/{fid}/odds", {"bookmakers": "bet365"})
        out = _normalize_odds(raw)
    except Exception:
        out = {}

    _ODDS_CACHE[fid] = (now, out)
    return out


def _grid(lh, la):
    g = [[_pois(h, lh) * _pois(a, la) for a in range(12)] for h in range(12)]
    z = sum(map(sum, g)) or 1.0
    return [[x / z for x in row] for row in g]


def _quarter_split(line):
    """Return the two half-lines represented by an Asian quarter line."""
    q = round(line * 4)
    if abs(line * 4 - q) < 1e-8 and q % 2:
        lower = math.floor(line * 2) / 2.0
        return lower, lower + 0.5
    return None


def _ah_single(g, line, home):
    p = 0.0
    n = len(g)
    for h in range(n):
        for a in range(n):
            x = ((h - a) if home else (a - h)) + line
            p += g[h][a] if x > 0 else 0.5 * g[h][a] if abs(x) < 1e-9 else 0.0
    return p


def _ah(g, line, home):
    split = _quarter_split(line)
    if split:
        return 0.5 * (_ah_single(g, split[0], home) + _ah_single(g, split[1], home))
    return _ah_single(g, line, home)


def _total_single(lam, line, over):
    p = 0.0
    for k in range(40):
        pk = _pois(k, lam)
        if (k > line) if over else (k < line):
            p += pk
        elif abs(k - line) < 1e-9:
            p += 0.5 * pk
    return max(0.0, min(1.0, p))


def _total(lam, line, over):
    split = _quarter_split(line)
    if split:
        return 0.5 * (_total_single(lam, split[0], over) + _total_single(lam, split[1], over))
    return _total_single(lam, line, over)


def _devig_pair(a, b):
    a, b = _num(a), _num(b)
    if not a or not b or a <= 1.01 or b <= 1.01:
        return None
    x, y = 1.0 / a, 1.0 / b
    z = x + y
    return x / z, y / z


def _devig_three(a, b, c):
    vals = [_num(a), _num(b), _num(c)]
    if any(not x or x <= 1.01 for x in vals):
        return None
    inv = [1.0 / x for x in vals]
    z = sum(inv)
    return tuple(x / z for x in inv)


def _fit_lambda(line, target_over, low=0.10, high=30.0):
    """Fit a Poisson mean to the de-vigged Over probability at the quoted line."""
    if line is None or target_over is None:
        return None
    target_over = max(0.01, min(0.99, target_over))
    lo, hi = low, high
    for _ in range(55):
        mid = (lo + hi) / 2.0
        p = _total(mid, line, True)
        if p < target_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _book_probs(odds):
    inv = [1.0 / o for o in odds]
    z = sum(inv) or 1.0
    return [x / z for x in inv]


def _add_group(out, names, model_probs, odds, source, model_weight=0.35):
    prices = [_num(o) for o in odds]
    if any(not o or o <= 1.01 for o in prices):
        return

    book = _book_probs(prices)
    reliability = MARKET_RELIABILITY.get(source, 0.97)

    for idx, (name, odd) in enumerate(zip(names, prices)):
        p = max(0.01, min(0.99, float(model_probs[idx])))
        b = book[idx]
        q = max(0.02, min(0.98, model_weight * p + (1.0 - model_weight) * b))
        gap = abs(p - b)
        ev = (q * odd - 1.0) * 100.0
        suspicious = gap > 0.25 or ev > 35.0
        combo_q = max(0.01, min(q, q * reliability))
        out.append(
            {
                "market": name,
                "probability": round(q * 100, 1),
                "ticket_probability": round(combo_q * 100, 2),
                "raw_probability": round(p * 100, 1),
                "book_probability": round(b * 100, 1),
                "bookmaker_odds": round(odd, 2),
                "fair_odds": round(1.0 / q, 2),
                "ev": round(ev, 1),
                "safe": q >= 0.55 and not suspicious,
                "value": ev >= 2.0 and not suspicious,
                "suspicious": suspicious,
                "source": source,
                "recommendation_score": round(
                    combo_q * 100.0 + max(-6.0, min(10.0, ev)) * 0.10,
                    1,
                ),
            }
        )


def analyze_fixture(f):
    h, a = _teams(f)
    ts = _kickoff_ts(f)
    odds = _odds_payload(f)
    picks = []

    mx = _stage(odds.get("1x2") or odds.get("match_winner"))
    gm = _stage(odds.get("goal_line") or odds.get("goalline") or odds.get("goals") or odds.get("total_goals"))
    am = _stage(odds.get("asian_handicap") or odds.get("asian"))
    cm = _stage(odds.get("corner_line") or odds.get("corner") or odds.get("corners"))
    cardm = _stage(odds.get("card_line") or odds.get("cards"))
    bm = _stage(odds.get("btts") or odds.get("both_teams_to_score"))

    # Anchor total-goal intensity to the actual quoted goal line and prices when available.
    lam = 2.55
    if gm:
        gl = _num(gm.get("line"))
        pair = _devig_pair(gm.get("over"), gm.get("under"))
        if gl is not None and pair:
            fitted = _fit_lambda(gl, pair[0], 0.35, 6.5)
            if fitted is not None:
                lam = fitted

    # Anchor team-strength split to de-vigged 1X2 prices.
    lh = la = lam / 2.0
    one_book = None
    if mx:
        one_book = _devig_three(mx.get("home"), mx.get("draw"), mx.get("away"))
        if one_book:
            bh, _, ba = one_book
            diff = max(-1.6, min(1.6, (bh - ba) * 3.0))
            lh = max(0.20, lam / 2.0 + diff / 2.0)
            la = max(0.20, lam / 2.0 - diff / 2.0)
            total = lh + la
            if total > 0:
                lh, la = lam * lh / total, lam * la / total

    g = _grid(lh, la)

    # 1X2: independent Poisson shape blended conservatively with market consensus.
    if mx and one_book:
        o1, ox, o2 = (_num(mx.get(k)) for k in ("home", "draw", "away"))
        ph = sum(g[i][j] for i in range(len(g)) for j in range(len(g)) if i > j)
        pd = sum(g[i][i] for i in range(len(g)))
        pa = max(0.0, 1.0 - ph - pd)
        _add_group(picks, ["1", "X", "2"], [ph, pd, pa], [o1, ox, o2], "Bet365 1X2", 0.40)

    # Goals.
    if gm:
        gl = _num(gm.get("line"))
        over, under = _num(gm.get("over")), _num(gm.get("under"))
        if gl is not None and over and under:
            _add_group(
                picks,
                [f"Over {gl:g}", f"Under {gl:g}"],
                [_total(lam, gl, True), _total(lam, gl, False)],
                [over, under],
                "Bet365 Goals",
                0.25,
            )

    # Asian handicap.
    if am:
        line, oh, oa = _num(am.get("line")), _num(am.get("home")), _num(am.get("away"))
        if line is not None and oh and oa:
            _add_group(
                picks,
                [f"AH Home {line:+g}", f"AH Away {-line:+g}"],
                [_ah(g, line, True), _ah(g, -line, False)],
                [oh, oa],
                "Bet365 AH",
                0.35,
            )

    # Corners: fit the event mean to the bookmaker line instead of pretending lambda == line.
    if cm:
        line, over, under = _num(cm.get("line")), _num(cm.get("over")), _num(cm.get("under"))
        pair = _devig_pair(over, under)
        if line is not None and pair:
            corner_lam = _fit_lambda(line, pair[0], 2.0, 24.0) or max(5.0, min(16.0, line))
            _add_group(
                picks,
                [f"Corners Over {line:g}", f"Corners Under {line:g}"],
                [_total(corner_lam, line, True), _total(corner_lam, line, False)],
                [over, under],
                "Bet365 Corners",
                0.15,
            )

    # Cards: same conservative market-anchored treatment.
    if cardm:
        line, over, under = _num(cardm.get("line")), _num(cardm.get("over")), _num(cardm.get("under"))
        pair = _devig_pair(over, under)
        if line is not None and pair:
            card_lam = _fit_lambda(line, pair[0], 0.3, 12.0) or max(1.5, min(9.0, line))
            _add_group(
                picks,
                [f"Cards Over {line:g}", f"Cards Under {line:g}"],
                [_total(card_lam, line, True), _total(card_lam, line, False)],
                [over, under],
                "Bet365 Cards",
                0.15,
            )

    # Both teams to score.
    if bm:
        yes, no = _num(bm.get("yes")), _num(bm.get("no"))
        if yes and no:
            py = (1.0 - math.exp(-lh)) * (1.0 - math.exp(-la))
            _add_group(picks, ["GG", "NG"], [py, 1.0 - py], [yes, no], "Bet365 BTTS", 0.35)

    picks.sort(
        key=lambda x: (
            not x["suspicious"],
            x["ticket_probability"],
            x["recommendation_score"],
        ),
        reverse=True,
    )
    usable = [x for x in picks if not x["suspicious"]]
    best = usable[0] if usable else (picks[0] if picks else None)

    league = f.get("league") or {}
    if not isinstance(league, dict):
        league = {"name": str(league)}

    return {
        "fixture_id": f.get("id") or f.get("fixture_id"),
        "kickoff": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
        "league": league.get("name", ""),
        "country": league.get("country", "") if isinstance(league, dict) else "",
        "home": h.get("name", "?"),
        "away": a.get("name", "?"),
        "home_xg": round(lh, 2),
        "away_xg": round(la, 2),
        "confidence": "medie",
        "markets": picks,
        "best_market": best,
        "best_value": next((x for x in usable if x["value"]), None),
        "odds_markets": list(odds) if isinstance(odds, dict) else [],
    }


def _day_fixtures(day):
    start = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())
    out, seen = [], set()
    for page in range(1, 21):
        raw = fd._get(
            "/fixtures",
            {
                "start_time": start,
                "end_time": start + 86400 - 1,
                "status": "scheduled",
                "per_page": 50,
                "page": page,
            },
        )
        if isinstance(raw, dict):
            rows = raw.get("fixtures") or raw.get("data") or []
            pag = raw.get("pagination") or {}
        else:
            rows, pag = raw or [], {}

        for x in rows:
            if not isinstance(x, dict):
                continue
            fid = x.get("id") or x.get("fixture_id")
            h, a = _teams(x)
            key = fid or (h.get("name"), a.get("name"), _kickoff_ts(x))
            if key not in seen:
                seen.add(key)
                out.append(x)

        if not rows or (not pag.get("has_more") and len(rows) < 50):
            break
    return out


def _combo_candidates(rows, target):
    target = float(target)
    fixtures = []

    # Wide enough to find a target, conservative enough not to turn the optimiser
    # into a collection of long-shot legs.
    if target >= 50:
        min_prob, max_odd = 0.49, 2.40
    elif target >= 10:
        min_prob, max_odd = 0.53, 3.00
    elif target >= 5:
        min_prob, max_odd = 0.55, 3.30
    else:
        min_prob, max_odd = 0.57, 3.50

    for r in rows:
        opts = []
        for p in r.get("markets", []):
            odd = _num(p.get("bookmaker_odds"))
            prob = _num(p.get("ticket_probability") or p.get("probability"))
            if odd is None or prob is None or p.get("suspicious"):
                continue
            prob /= 100.0
            if not (1.02 <= odd <= max_odd) or prob < min_prob:
                continue
            opts.append(
                {
                    **p,
                    "home": r["home"],
                    "away": r["away"],
                    "kickoff": r.get("kickoff"),
                    "combo_prob": prob,
                }
            )

        if opts:
            # Keep alternatives because the globally safest ticket may need a slightly
            # larger price from one fixture to avoid adding another weaker fixture.
            opts.sort(
                key=lambda x: (
                    x["combo_prob"],
                    x.get("recommendation_score", 0),
                    -x["bookmaker_odds"],
                ),
                reverse=True,
            )
            fixtures.append(opts[:10])

    return fixtures


def build_combo(rows, target):
    """Optimise globally for maximum joint probability near the requested odds.

    There is no fixed number of legs. At most one market is selected from each fixture.
    """
    target = float(target)
    fixtures = _combo_candidates(rows, target)

    if target >= 50:
        preferred_lo, preferred_hi = 0.98, 1.02
        fallback_lo, fallback_hi = 0.95, 1.05
        max_leg_odd = 2.40
    elif target >= 10:
        preferred_lo, preferred_hi = 0.95, 1.05
        fallback_lo, fallback_hi = 0.90, 1.10
        max_leg_odd = 3.00
    elif target >= 5:
        preferred_lo, preferred_hi = 0.94, 1.06
        fallback_lo, fallback_hi = 0.90, 1.10
        max_leg_odd = 3.30
    else:
        preferred_lo, preferred_hi = 0.97, 1.05
        fallback_lo, fallback_hi = 0.93, 1.08
        max_leg_odd = 3.50

    diag = {
        "candidate_matches": len(fixtures),
        "candidate_selections": sum(len(x) for x in fixtures),
        "target_low": round(target * preferred_lo, 2),
        "target_high": round(target * preferred_hi, 2),
        "closest_reachable_odds": None,
        "max_leg_odds_used": None,
        "best_joint_probability": None,
    }
    if not fixtures:
        return None, diag

    # Multiple-choice knapsack in log-odds space. One state per odds bucket is
    # sufficient because leg count is not a constraint: at approximately the same
    # accumulated odds, the path with the higher joint probability dominates.
    scale = 500
    absolute_hi = target * fallback_hi
    states = {0: (1.0, 1.0, [], 1.0)}  # odd, joint, path, max_leg

    for fixture_opts in fixtures:
        nxt = dict(states)  # skipping this fixture is allowed
        for cur_odd, joint, path, path_max_leg in list(states.values()):
            for x in fixture_opts:
                if x["bookmaker_odds"] > max_leg_odd:
                    continue
                no = cur_odd * x["bookmaker_odds"]
                if no > absolute_hi:
                    continue
                nj = joint * x["combo_prob"]
                nmax = max(path_max_leg, x["bookmaker_odds"])
                bucket = round(math.log(max(no, 1.0)) * scale)
                old = nxt.get(bucket)
                if (
                    old is None
                    or nj > old[1] + 1e-12
                    or (abs(nj - old[1]) <= 1e-12 and nmax < old[3])
                    or (abs(nj - old[1]) <= 1e-12 and abs(nmax - old[3]) <= 1e-12 and no < old[0])
                ):
                    nxt[bucket] = (no, nj, path + [x], nmax)
        states = nxt

    paths = [v for v in states.values() if v[2]]
    if paths:
        closest = min(paths, key=lambda v: abs(math.log(max(v[0], 1e-12) / target)))
        diag["closest_reachable_odds"] = round(closest[0], 2)

    def choose(lo, hi):
        valid = [v for v in paths if target * lo <= v[0] <= target * hi]
        if not valid:
            return None
        # Safety is the primary objective. Exact target is secondary. Smaller maximum
        # leg odds is the next tie-breaker, then more legs when still effectively tied.
        return max(
            valid,
            key=lambda v: (
                v[1],
                -abs(math.log(v[0] / target)),
                -v[3],
                len(v[2]),
            ),
        )

    best = choose(preferred_lo, preferred_hi)
    target_met = best is not None
    if best is None:
        best = choose(fallback_lo, fallback_hi)
    if best is None:
        return None, diag

    odd, joint, path, used_max_leg = best
    diag["max_leg_odds_used"] = round(used_max_leg, 2)
    diag["best_joint_probability"] = round(joint * 100, 3)

    return (
        {
            "combined_odds": round(odd, 2),
            "estimated_joint_probability": round(joint * 100, 3),
            "target_met": target_met,
            "requested_target": target,
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


def analyze_period(day, target=10, days=1, limit=200):
    days = max(1, min(int(days), 7))
    start = datetime.fromisoformat(day).date()
    fs, by_day, seen = [], {}, set()

    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        got = _day_fixtures(d)
        by_day[d] = len(got)
        for f in got:
            fid = f.get("id") or f.get("fixture_id")
            h, a = _teams(f)
            key = fid or (h.get("name"), a.get("name"), _kickoff_ts(f))
            if key not in seen:
                seen.add(key)
                fs.append(f)

    rows, errors, no = [], [], []
    attempt = min(len(fs), max(1, min(int(limit), 200)))

    for f in fs[:attempt]:
        try:
            r = analyze_fixture(f)
            (rows if r["best_market"] else no).append(r)
        except Exception as e:
            h, a = _teams(f)
            errors.append(
                {
                    "fixture": f.get("id") or f.get("fixture_id"),
                    "match": h.get("name", "?") + " - " + a.get("name", "?"),
                    "error": type(e).__name__ + ": " + str(e)[:180],
                }
            )

    rows.sort(key=lambda x: x["best_market"]["recommendation_score"], reverse=True)
    combo, combo_diag = build_combo(rows, target)

    return {
        "date": day,
        "days": days,
        "period_end": (start + timedelta(days=days - 1)).isoformat(),
        "provider": "5DollarFootballAPI + Bet365",
        "fixtures_by_day": by_day,
        "api_fixtures": len(fs),
        "eligible": len(fs),
        "attempted": attempt,
        "analyzed": len(rows),
        "without_usable_odds": len(no),
        "no_odds_examples": [
            {"fixture": x["fixture_id"], "match": x["home"] + " - " + x["away"]}
            for x in no[:20]
        ],
        "analysis_errors": errors,
        "ranking": rows,
        "suggested_combo": combo,
        "combo_diagnostics": combo_diag,
    }


def analyze_day(day, target=10, limit=12):
    return analyze_period(day, target, 1, limit)
