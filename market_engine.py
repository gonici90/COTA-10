"""COTA-10 live multi-market engine backed by 5DollarFootballAPI / Bet365."""
import math
from datetime import datetime, timezone, timedelta
import auto_data as fd

_ODDS_CACHE = {}

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
        h, a = m.get("home_team") or m.get("home") or {}, m.get("away_team") or m.get("away") or {}
    if not isinstance(h, dict):
        h = {"name": str(h)}
    if not isinstance(a, dict):
        a = {"name": str(a)}
    return h, a

def _kickoff_ts(m):
    v = m.get("kickoff_ts") or m.get("timestamp") or m.get("start_time") or m.get("kickoff") or m.get("kickoff_utc") or m.get("date")
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
    """Latest usable pre-match stage; never prefer in-play for scheduled tickets."""
    if not isinstance(m, dict):
        return None
    for k in ("closing", "current", "opening"):
        if isinstance(m.get(k), dict):
            return m[k]
    if any(k in m for k in ("home", "away", "over", "under", "yes", "no", "line")):
        return m
    return None

def _normalize_odds(data):
    if not isinstance(data, dict):
        return {}
    for k in ("data", "response", "result"):
        if isinstance(data.get(k), dict):
            data = data[k]
    books = data.get("bookmakers") or data.get("books") or []
    if isinstance(books, dict):
        books = list(books.values())
    if isinstance(books, list) and books:
        b = next(
            (x for x in books if isinstance(x, dict) and "365" in str(x.get("slug") or x.get("name") or "").lower()),
            books[0],
        )
        if isinstance(b, dict):
            return b.get("odds") or b.get("markets") or {}
    return data.get("odds") or data.get("markets") or {}

def _has_prices(odds):
    if not isinstance(odds, dict):
        return False
    x = odds.get("1x2")
    if isinstance(x, dict) and any(isinstance(x.get(k), dict) for k in ("opening", "closing", "current")):
        return True
    for key in ("asian_handicap", "goal_line", "corner_line", "card_line", "btts"):
        s = _stage(odds.get(key))
        if isinstance(s, dict) and any(_num(s.get(k)) for k in ("home", "away", "over", "under", "yes", "no")):
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
    if fid in _ODDS_CACHE:
        return _ODDS_CACHE[fid]
    try:
        raw = fd._get(f"/fixtures/{fid}/odds", {"bookmakers": "bet365"})
        out = _normalize_odds(raw)
    except Exception:
        out = {}
    _ODDS_CACHE[fid] = out
    return out

def _grid(lh, la):
    g = [[_pois(h, lh) * _pois(a, la) for a in range(11)] for h in range(11)]
    z = sum(map(sum, g)) or 1.0
    return [[x / z for x in r] for r in g]

def _ah(g, line, home):
    p = 0.0
    for h in range(11):
        for a in range(11):
            x = ((h - a) if home else (a - h)) + line
            p += g[h][a] if x > 0 else 0.5 * g[h][a] if abs(x) < 1e-9 else 0.0
    return p

def _total(lam, line, over):
    p = 0.0
    for k in range(30):
        pk = _pois(k, lam)
        if (k > line) if over else (k < line):
            p += pk
        elif abs(k - line) < 1e-9:
            p += 0.5 * pk
    return p

def _calibrated_probs(model_probs, odds):
    inv = [(1.0 / o if o and o > 1.0 else 0.0) for o in odds]
    z = sum(inv) or 1.0
    book = [x / z for x in inv]
    out = []
    for p, b in zip(model_probs, book):
        p = max(0.01, min(0.99, p))
        q = max(0.02, min(0.98, 0.70 * p + 0.30 * b))
        out.append((q, b, abs(p - b)))
    return out

def _add_group(out, names, model_probs, odds, source):
    valid = [(_num(o) or 0.0) for o in odds]
    if not valid or any(o <= 1.01 for o in valid):
        return
    cal = _calibrated_probs(model_probs, valid)
    for idx, (name, odd, (q, book_p, gap)) in enumerate(zip(names, valid, cal)):
        ev = (q * odd - 1.0) * 100.0
        suspicious = gap > 0.30 or ev > 45.0
        out.append({
            "market": name,
            "probability": round(q * 100, 1),
            "raw_probability": round(max(0.01, min(0.99, model_probs[idx])) * 100, 1),
            "book_probability": round(book_p * 100, 1),
            "bookmaker_odds": round(odd, 2),
            "fair_odds": round(1.0 / q, 2),
            "ev": round(ev, 1),
            "safe": q >= 0.56 and not suspicious,
            "value": ev >= 2.0 and not suspicious,
            "suspicious": suspicious,
            "source": source,
            "recommendation_score": round(q * 100 + max(-6.0, min(10.0, ev)) * 0.12, 1),
        })

def analyze_fixture(f):
    h, a = _teams(f)
    ts = _kickoff_ts(f)
    odds = _odds_payload(f)
    picks = []

    mx = _stage(odds.get("1x2") or odds.get("match_winner"))
    one_models = None
    lam = 2.55
    lh = la = lam / 2.0
    if mx:
        o1, ox, o2 = (_num(mx.get(k)) for k in ("home", "draw", "away"))
        if o1 and ox and o2:
            inv = [1 / o1, 1 / ox, 1 / o2]
            z = sum(inv)
            bh, bd, ba = [x / z for x in inv]
            diff = max(-1.35, min(1.35, (bh - ba) * 2.8))
            lh, la = max(0.25, lam / 2 + diff / 2), max(0.25, lam / 2 - diff / 2)
            g = _grid(lh, la)
            ph = sum(g[i][j] for i in range(11) for j in range(11) if i > j)
            pd = sum(g[i][i] for i in range(11))
            pa = max(0.0, 1.0 - ph - pd)
            one_models = [ph, pd, pa]
            _add_group(picks, ["1", "X", "2"], one_models, [o1, ox, o2], "Bet365 1X2")

    gm = _stage(odds.get("goal_line") or odds.get("goalline") or odds.get("goals") or odds.get("total_goals"))
    if gm:
        gl = _num(gm.get("line"))
        over, under = _num(gm.get("over")), _num(gm.get("under"))
        if gl is not None and over and under:
            lam = max(0.65, min(5.5, gl))
            if one_models is not None:
                total0 = max(0.5, lh + la)
                share = lh / total0
                lh, la = max(0.2, lam * share), max(0.2, lam * (1 - share))
            _add_group(
                picks,
                [f"Over {gl:g}", f"Under {gl:g}"],
                [_total(lam, gl, True), _total(lam, gl, False)],
                [over, under],
                "Bet365 Goals",
            )

    am = _stage(odds.get("asian_handicap") or odds.get("asian"))
    if am:
        line, oh, oa = _num(am.get("line")), _num(am.get("home")), _num(am.get("away"))
        if line is not None and oh and oa:
            g = _grid(lh, la)
            _add_group(
                picks,
                [f"AH Home {line:+g}", f"AH Away {-line:+g}"],
                [_ah(g, line, True), _ah(g, -line, False)],
                [oh, oa],
                "Bet365 AH",
            )

    cm = _stage(odds.get("corner_line") or odds.get("corner") or odds.get("corners"))
    if cm:
        line, over, under = _num(cm.get("line")), _num(cm.get("over")), _num(cm.get("under"))
        if line is not None and over and under:
            corner_lam = max(5.0, min(16.0, line))
            _add_group(
                picks,
                [f"Corners Over {line:g}", f"Corners Under {line:g}"],
                [_total(corner_lam, line, True), _total(corner_lam, line, False)],
                [over, under],
                "Bet365 Corners",
            )

    cardm = _stage(odds.get("card_line") or odds.get("cards"))
    if cardm:
        line, over, under = _num(cardm.get("line")), _num(cardm.get("over")), _num(cardm.get("under"))
        if line is not None and over and under:
            card_lam = max(1.5, min(9.0, line))
            _add_group(
                picks,
                [f"Cards Over {line:g}", f"Cards Under {line:g}"],
                [_total(card_lam, line, True), _total(card_lam, line, False)],
                [over, under],
                "Bet365 Cards",
            )

    bm = _stage(odds.get("btts") or odds.get("both_teams_to_score"))
    if bm:
        yes, no = _num(bm.get("yes")), _num(bm.get("no"))
        if yes and no:
            py = (1 - math.exp(-lh)) * (1 - math.exp(-la))
            _add_group(picks, ["GG", "NG"], [py, 1 - py], [yes, no], "Bet365 BTTS")

    picks.sort(key=lambda x: (not x["suspicious"], x["probability"], x["recommendation_score"]), reverse=True)
    usable = [x for x in picks if not x["suspicious"]]
    best = usable[0] if usable else (picks[0] if picks else None)
    league = f.get("league") or {}
    if not isinstance(league, dict):
        league = {"name": str(league)}
    return {
        "fixture_id": f.get("id") or f.get("fixture_id"),
        "kickoff": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
        "league": league.get("name", ""),
        "country": "",
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
            {"start_time": start, "end_time": start + 86400, "status": "scheduled", "per_page": 50, "page": page},
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
    min_prob = 0.50 if target >= 50 else 0.54 if target >= 10 else 0.56 if target >= 5 else 0.58
    max_odd = 2.40 if target >= 50 else 3.0 if target >= 10 else 3.3 if target >= 5 else 3.5
    for r in rows:
        opts = []
        for p in r.get("markets", []):
            odd = _num(p.get("bookmaker_odds"))
            prob = _num(p.get("probability"))
            if odd is None or prob is None or p.get("suspicious"):
                continue
            prob /= 100.0
            if not (1.02 <= odd <= max_odd) or prob < min_prob:
                continue
            opts.append({
                **p,
                "home": r["home"],
                "away": r["away"],
                "kickoff": r.get("kickoff"),
                "combo_prob": prob,
            })
        if opts:
            opts.sort(key=lambda x: (x["combo_prob"], -x["bookmaker_odds"], x.get("recommendation_score", 0)), reverse=True)
            fixtures.append(opts[:8])
    return fixtures

def build_combo(rows, target):
    """Global target optimiser. One pick per fixture, no fixed leg-count rules."""
    target = float(target)
    fixtures = _combo_candidates(rows, target)
    diag = {
        "candidate_matches": len(fixtures),
        "candidate_selections": sum(len(x) for x in fixtures),
        "target_low": round(target * (0.90 if target >= 50 else 0.92), 2),
        "target_high": round(target * 1.10, 2),
        "closest_reachable_odds": None,
        "max_leg_odds_used": None,
    }
    if not fixtures:
        return None, diag

    scale = 320

    def solve(max_leg_odd, lo, hi):
        states = {(0, 0): (1.0, 1.0, [])}
        for fixture_opts in fixtures:
            opts = [x for x in fixture_opts if x["bookmaker_odds"] <= max_leg_odd]
            if not opts:
                continue
            nxt = dict(states)
            for (legs, _), (cur_odd, joint, path) in list(states.items()):
                for x in opts:
                    no = cur_odd * x["bookmaker_odds"]
                    if no > target * hi:
                        continue
                    nj = joint * x["combo_prob"]
                    nl = legs + 1
                    bucket = round(math.log(max(no, 1.0)) * scale)
                    key = (nl, bucket)
                    old = nxt.get(key)
                    if old is None or nj > old[1]:
                        nxt[key] = (no, nj, path + [x])
            states = nxt
        valid = [v for (legs, _), v in states.items() if legs > 0 and target * lo <= v[0] <= target * hi]
        if not valid:
            return None, states
        best = max(valid, key=lambda v: (-abs(math.log(v[0] / target)), v[1], len(v[2])))
        return best, states

    bands = [(0.97, 1.03), (0.95, 1.05), (0.90, 1.10)]
    odd_tiers = [1.50, 1.65, 1.85, 2.40] if target >= 50 else [2.00, 2.50, 3.00, 3.50]
    best = None
    last_states = {}
    used_tier = None
    for lo, hi in bands:
        for tier in odd_tiers:
            found, states = solve(tier, lo, hi)
            last_states = states
            if found:
                best, used_tier = found, tier
                break
        if best:
            break

    all_paths = [v for (legs, _), v in last_states.items() if legs > 0]
    if all_paths:
        closest = min(all_paths, key=lambda v: abs(math.log(max(v[0], 1e-12) / target)))
        diag["closest_reachable_odds"] = round(closest[0], 2)

    target_met = True
    if best is None:
        _, states = solve(odd_tiers[-1], 0.80 if target >= 50 else 0.85, 1.10)
        paths = [v for (legs, _), v in states.items() if legs > 0 and v[0] >= target * (0.80 if target >= 50 else 0.85)]
        if not paths:
            return None, diag
        best = min(paths, key=lambda v: abs(math.log(v[0] / target)))
        used_tier = odd_tiers[-1]
        target_met = False

    odd, joint, path = best
    diag["max_leg_odds_used"] = used_tier
    return {
        "combined_odds": round(odd, 2),
        "estimated_joint_probability": round(joint * 100, 2),
        "target_met": target_met and (0.90 * target <= odd <= 1.10 * target),
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
                "odds": x["bookmaker_odds"],
                "ev": x.get("ev"),
                "score": x.get("recommendation_score"),
            }
            for x in path
        ],
    }, diag

def analyze_period(day, target=10, days=1, limit=200):
    days = max(1, min(int(days), 7))
    start = datetime.fromisoformat(day).date()
    fs, by_day = [], {}
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        got = _day_fixtures(d)
        by_day[d] = len(got)
        fs.extend(got)

    rows, errors, no = [], [], []
    attempt = min(len(fs), max(1, min(int(limit), 200)))
    for f in fs[:attempt]:
        try:
            r = analyze_fixture(f)
            (rows if r["best_market"] else no).append(r)
        except Exception as e:
            h, a = _teams(f)
            errors.append({
                "fixture": f.get("id") or f.get("fixture_id"),
                "match": h.get("name", "?") + " - " + a.get("name", "?"),
                "error": type(e).__name__ + ": " + str(e)[:180],
            })

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
        "no_odds_examples": [{"fixture": x["fixture_id"], "match": x["home"] + " - " + x["away"]} for x in no[:20]],
        "analysis_errors": errors[:20],
        "ranking": rows,
        "suggested_combo": combo,
        "combo_diagnostics": combo_diag,
    }

def analyze_day(day, target=10, limit=12):
    return analyze_period(day, target, 1, limit)
