"""Independent walk-forward backtest using 5DollarFootballAPI Pro history.

Unlike the earlier v11 engine, this model does NOT use bookmaker prices to create
its probabilities. Team form, home/away scoring and Elo are calculated only from
results available before the tested day. Bet365 prices are used afterwards only
to decide whether the model has enough edge and to settle simulated tickets.
"""

import json
import math
import os
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

import auto_data as fd
import backtest_engine as bt
import market_engine

CACHE = Path(os.getenv("COTA_CACHE_DIR", "/tmp/cota10-cache")) / "backtest-pro-wf-v12"
RANGE_CACHE = CACHE / "ranges"
ODDS_CACHE = CACHE / "odds"
for _p in (CACHE, RANGE_CACHE, ODDS_CACHE):
    _p.mkdir(parents=True, exist_ok=True)

TEST_MAX_DAYS = 60
WARMUP_DAYS = max(120, min(240, int(os.getenv("BACKTEST_WF_WARMUP_DAYS", "180"))))
TOTAL_RANGE_DAYS = TEST_MAX_DAYS + WARMUP_DAYS
MAX_LEAGUES = max(6, min(14, int(os.getenv("BACKTEST_WF_LEAGUES", "10"))))
MAX_GOAL_DEEP_PER_DAY = max(0, min(2, int(os.getenv("BACKTEST_WF_GOAL_DEEP", "1"))))

PREFERRED = (
    "premier league", "la liga", "serie a", "bundesliga", "ligue 1",
    "eredivisie", "primeira liga", "super lig", "championship",
    "major league soccer", "mls", "brazil", "argentina",
)


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write(path, obj):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _rows_pag(raw):
    if isinstance(raw, dict):
        rows = raw.get("fixtures") or raw.get("leagues") or raw.get("data") or []
        pag = raw.get("pagination") or {}
    else:
        rows, pag = raw or [], {}
    if isinstance(rows, dict):
        rows = rows.get("fixtures") or rows.get("leagues") or rows.get("data") or []
    return [x for x in rows if isinstance(x, dict)], pag


def _leagues():
    p = CACHE / "leagues.json"
    cached = _read(p)
    if isinstance(cached, dict) and time.time() - float(cached.get("saved_at") or 0) < 86400:
        rows = cached.get("rows") or []
        if rows:
            return rows
    out = []
    for page in range(1, 5):
        raw = fd._get("/leagues", {"page": page, "per_page": 100, "lang": "en"})
        rows, pag = _rows_pag(raw)
        out.extend(rows)
        if not pag.get("has_more"):
            break
    seen, uniq = set(), []
    for x in out:
        lid = x.get("id")
        if lid is None or lid in seen:
            continue
        seen.add(lid); uniq.append(x)
    _write(p, {"saved_at": time.time(), "rows": uniq})
    return uniq


def _choose_leagues():
    rows = _leagues()
    def score(pair):
        i, x = pair
        name = str(x.get("name") or "").lower()
        s = 0
        for n, key in enumerate(PREFERRED):
            if key in name:
                s = max(s, 1000 - n * 20)
        if x.get("is_popular") is True or str(x.get("is_popular")).lower() in {"1", "true", "yes"}:
            s += 250
        if x.get("has_standings"):
            s += 30
        return s - i / 10000
    ranked = [x for _, x in sorted(enumerate(rows), key=score, reverse=True)]
    return ranked[:MAX_LEAGUES]


def _range_bounds(end_day):
    end_ex = datetime.combine(end_day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    start = end_ex - timedelta(days=TOTAL_RANGE_DAYS)
    return int(start.timestamp()), int(end_ex.timestamp())


def _league_range(league, end_day):
    lid = int(league["id"])
    start_ts, end_ts = _range_bounds(end_day)
    p = RANGE_CACHE / f"{end_day.isoformat()}-{TOTAL_RANGE_DAYS}d-{lid}.json"
    cached = _read(p)
    if isinstance(cached, dict) and isinstance(cached.get("matches"), list):
        return cached["matches"], True, int(cached.get("pages") or 0)
    out, pages = [], 0
    for page in range(1, 14):
        raw = fd._get(
            f"/leagues/{lid}/fixtures",
            {"start_time": start_ts, "end_time": end_ts, "status": "finished", "include": "odds", "per_page": 50, "page": page, "lang": "en"},
        )
        rows, pag = _rows_pag(raw)
        pages += 1; out.extend(rows)
        if not pag.get("has_more"):
            break
    seen, uniq = set(), []
    for f in out:
        fid = f.get("id") or f.get("fixture_id")
        if fid in seen:
            continue
        seen.add(fid); uniq.append(f)
    _write(p, {"league": league, "pages": pages, "matches": uniq})
    return uniq, False, pages


def _day(f):
    v = f.get("kickoff_ts") or f.get("timestamp") or f.get("start_time")
    try:
        if v is not None:
            return datetime.fromtimestamp(int(v), timezone.utc).date().isoformat()
    except Exception:
        pass
    v = f.get("kickoff_utc") or f.get("kickoff") or f.get("date")
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc).date().isoformat()
    except Exception:
        return None


def _teams(f):
    t = f.get("teams") or {}
    h = t.get("home") if isinstance(t, dict) else None
    a = t.get("away") if isinstance(t, dict) else None
    if isinstance(h, dict): h = h.get("name")
    if isinstance(a, dict): a = a.get("name")
    return str(h or ""), str(a or "")


def _league_key(f):
    l = f.get("league") or {}
    if isinstance(l, dict):
        return str(l.get("id") or l.get("name") or "")
    return str(l or "")


def _score(f):
    return bt._score_values(f)


def _load(job_id, days):
    end_day = date.today() - timedelta(days=1)
    leagues = _choose_leagues()
    matches, api_pages, cache_hits, errors = [], 0, 0, []
    for idx, league in enumerate(leagues):
        name = str(league.get("name") or league.get("id"))
        with bt.JOBS_LOCK:
            job = bt.JOBS.get(job_id)
            if job:
                job["current_day"] = f"Istoric Pro: {name}"
                job["progress"] = min(days - 1, int((idx / max(1, len(leagues))) * max(1, days // 3)))
        try:
            rows, cached, pages = _league_range(league, end_day)
            matches.extend(rows)
            cache_hits += int(cached); api_pages += 0 if cached else pages
        except Exception as exc:
            errors.append({"league": name, "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
    seen, uniq = set(), []
    for f in matches:
        fid = f.get("id") or f.get("fixture_id")
        k = fid or (_day(f), _teams(f), _league_key(f))
        if k in seen:
            continue
        seen.add(k); uniq.append(f)
    uniq.sort(key=lambda f: (str(_day(f) or ""), int(f.get("kickoff_ts") or 0)))
    return {"end_day": end_day, "leagues": leagues, "matches": uniq, "api_pages": api_pages, "cache_hits": cache_hits, "errors": errors}


def _wavg(vals, decay=.90):
    if not vals:
        return None
    sw = sx = 0.0
    for i, v in enumerate(reversed(vals)):
        w = decay ** i; sw += w; sx += float(v) * w
    return sx / sw if sw else None


def _poisson(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _grid(lh, la):
    g = [[_poisson(h, lh) * _poisson(a, la) for a in range(10)] for h in range(10)]
    z = sum(map(sum, g)) or 1.0
    return [[x / z for x in r] for r in g]


def _total_prob(lam, line, over):
    p = 0.0
    for k in range(20):
        pk = _poisson(k, lam)
        if (k > line) if over else (k < line):
            p += pk
        elif abs(k - line) < 1e-9:
            p += .5 * pk
    return max(.01, min(.99, p))


class State:
    def __init__(self):
        self.hist = defaultdict(lambda: defaultdict(lambda: deque(maxlen=12)))
        self.elo = defaultdict(lambda: defaultdict(lambda: 1500.0))
        self.league_scores = defaultdict(lambda: deque(maxlen=120))

    def predict(self, f):
        league = _league_key(f); home, away = _teams(f)
        hh = list(self.hist[league][home]); ah = list(self.hist[league][away])
        if len(hh) < 6 or len(ah) < 6:
            return None

        def stats(rows, venue):
            gf = [x[0] for x in rows]; ga = [x[1] for x in rows]
            vr = [x for x in rows if x[2] == venue]
            ogf, oga = _wavg(gf), _wavg(ga)
            if len(vr) >= 3:
                vgf = _wavg([x[0] for x in vr]); vga = _wavg([x[1] for x in vr])
                ogf = .65 * ogf + .35 * vgf; oga = .65 * oga + .35 * vga
            pts = _wavg([x[3] for x in rows[-5:]], .86) or 1.0
            return ogf, oga, pts

        hgf, hga, hpts = stats(hh, "H"); agf, aga, apts = stats(ah, "A")
        lg = list(self.league_scores[league])
        if len(lg) >= 20:
            base_h = sum(x[0] for x in lg) / len(lg)
            base_a = sum(x[1] for x in lg) / len(lg)
        else:
            base_h, base_a = 1.45, 1.18

        lh = .34 * base_h + .33 * hgf + .33 * aga
        la = .34 * base_a + .33 * agf + .33 * hga
        elo_diff = self.elo[league][home] + 55.0 - self.elo[league][away]
        shift = max(-.32, min(.32, elo_diff / 900.0))
        form_shift = max(-.12, min(.12, (hpts - apts) / 12.0))
        lh = max(.20, min(3.6, lh + shift + form_shift))
        la = max(.20, min(3.6, la - shift - form_shift))

        g = _grid(lh, la)
        ph = sum(g[i][j] for i in range(10) for j in range(10) if i > j)
        pd = sum(g[i][i] for i in range(10))
        pa = max(.01, 1.0 - ph - pd)
        z = ph + pd + pa
        return {"1": ph / z, "X": pd / z, "2": pa / z, "lh": lh, "la": la}

    def update(self, f):
        h, a = _score(f); home, away = _teams(f); league = _league_key(f)
        if h is None or not home or not away or not league:
            return
        hp = 3 if h > a else 1 if h == a else 0
        ap = 3 if a > h else 1 if h == a else 0
        self.hist[league][home].append((h, a, "H", hp))
        self.hist[league][away].append((a, h, "A", ap))
        self.league_scores[league].append((h, a))

        eh = self.elo[league][home]; ea = self.elo[league][away]
        exp_h = 1.0 / (1.0 + 10 ** (-(eh + 55.0 - ea) / 400.0))
        actual_h = 1.0 if h > a else .5 if h == a else 0.0
        margin = abs(h - a)
        k = 18.0 * (1.0 + min(3, margin) * .12)
        delta = k * (actual_h - exp_h)
        self.elo[league][home] += delta; self.elo[league][away] -= delta


def _inline_odds(f):
    try:
        odds = bt._prematch_only(bt._extract_inline_odds(f))
    except Exception:
        odds = {}
    return odds if isinstance(odds, dict) else {}


def _three_prices(odds):
    s = market_engine._stage(odds.get("1x2") or odds.get("match_winner")) if odds else None
    if not isinstance(s, dict):
        return None
    try:
        vals = tuple(float(s.get(k)) for k in ("home", "draw", "away"))
    except Exception:
        return None
    return vals if all(x > 1.01 for x in vals) else None


def _goal_stage(odds):
    if not odds:
        return None
    s = market_engine._stage(odds.get("goal_line") or odds.get("goalline") or odds.get("goals") or odds.get("total_goals"))
    if not isinstance(s, dict):
        return None
    try:
        line = float(s.get("line")); over = float(s.get("over")); under = float(s.get("under"))
    except Exception:
        return None
    if over <= 1.01 or under <= 1.01:
        return None
    return line, over, under


def _devig(prices):
    inv = [1.0 / float(x) for x in prices]; z = sum(inv)
    return [x / z for x in inv]


def _full_odds(f):
    fid = f.get("id") or f.get("fixture_id")
    if not fid:
        return {}, True
    p = ODDS_CACHE / f"{fid}.json"
    cached = _read(p)
    if isinstance(cached, dict) and isinstance(cached.get("odds"), dict):
        return cached["odds"], True
    raw = fd._get(f"/fixtures/{fid}/odds", {"bookmakers": "bet365"})
    odds = market_engine._normalize_odds(raw)
    _write(p, {"odds": odds})
    return odds, False


def _candidate(name, p, odd, bookp, fixture, model):
    ev = p * odd - 1.0
    edge = p - bookp
    return {
        "market": name, "prob": p, "odd": odd, "bookp": bookp,
        "edge": edge, "ev": ev, "fixture": fixture,
        "fixture_id": fixture.get("id") or fixture.get("fixture_id"),
        "model": model,
    }


def _passes(c, target):
    p, o, edge, ev = c["prob"], c["odd"], c["edge"], c["ev"]
    if target <= 1.6:
        return .64 <= p and 1.12 <= o <= 1.70 and edge >= .012 and ev >= .01
    if target <= 2.3:
        return .57 <= p and 1.12 <= o <= 2.25 and edge >= .015 and ev >= .012
    return .53 <= p and 1.12 <= o <= 2.70 and edge >= .018 and ev >= .015


def _fixture_candidates(f, model, include_goals=False, full_odds=None, target=2.0):
    odds = full_odds if full_odds is not None else _inline_odds(f)
    out = []
    prices = _three_prices(odds)
    if prices:
        book = _devig(prices)
        for name, p, o, bp in zip(("1", "X", "2"), (model["1"], model["X"], model["2"]), prices, book):
            c = _candidate(name, p, o, bp, f, model)
            if _passes(c, target): out.append(c)

    if include_goals:
        gs = _goal_stage(odds)
        if gs:
            line, over, under = gs; book = _devig((over, under)); lam = model["lh"] + model["la"]
            for name, p, o, bp in (
                (f"Over {line:g}", _total_prob(lam, line, True), over, book[0]),
                (f"Under {line:g}", _total_prob(lam, line, False), under, book[1]),
            ):
                c = _candidate(name, p, o, bp, f, model)
                if _passes(c, target): out.append(c)
    return out


def _ticket(cands, target):
    target = float(target)
    if target <= 1.60:
        lo, hi, maxlegs = target * .96, target * 1.04, 2
    elif target <= 2.30:
        lo, hi, maxlegs = target * .95, target * 1.05, 3
    else:
        lo, hi, maxlegs = target * .93, target * 1.07, 5

    # Keep the best two options per fixture and only the strongest fixtures.
    byfix = defaultdict(list)
    for c in cands:
        byfix[c["fixture_id"]].append(c)
    pool = []
    for opts in byfix.values():
        opts.sort(key=lambda x: (x["prob"], x["edge"], x["ev"]), reverse=True)
        pool.extend(opts[:2])
    pool.sort(key=lambda x: (x["prob"] + min(.08, x["edge"]) * .7, x["ev"]), reverse=True)
    pool = pool[:20]

    best = None
    for z in range(1, min(maxlegs, len(pool)) + 1):
        for combo in combinations(pool, z):
            ids = [x["fixture_id"] for x in combo]
            if len(set(ids)) != len(ids):
                continue
            odd = math.prod(x["odd"] for x in combo)
            if not (lo <= odd <= hi):
                continue
            joint = math.prod(x["prob"] for x in combo)
            avg_edge = sum(x["edge"] for x in combo) / z
            score = joint + min(.08, avg_edge) * .15 - abs(math.log(odd / target)) * .015
            if best is None or score > best[0]:
                best = (score, odd, joint, combo)
    if best is None:
        return None
    _, odd, joint, combo = best
    return {"odds": round(odd, 2), "joint": joint, "legs": list(combo)}


def _settle(ticket):
    factor = 1.0; rows = []; mix = defaultdict(int)
    for c in ticket["legs"]:
        ret = bt._settle_selection(c["market"], c["odd"], c["fixture"])
        if ret is None:
            return None, rows, dict(mix)
        factor *= ret
        h, a = _score(c["fixture"])
        kind = "GOALS" if c["market"].startswith(("Over ", "Under ")) else "1X2"
        mix[kind] += 1
        home, away = _teams(c["fixture"])
        rows.append({"match": f"{home} - {away}", "selection": c["market"], "odds": round(c["odd"], 2), "score": f"{h}-{a}", "model_probability": round(c["prob"] * 100, 1), "market_probability": round(c["bookp"] * 100, 1), "edge_pp": round(c["edge"] * 100, 1)})
    return factor, rows, dict(mix)


def _summary(days, target, daily, meta):
    settled = [x for x in daily if x.get("status") in {"WIN", "LOSE", "PUSH"}]
    wins = sum(x["status"] == "WIN" for x in settled); losses = sum(x["status"] == "LOSE" for x in settled)
    decided = wins + losses; stake = bt.STAKE * len(settled); profit = sum(float(x.get("profit") or 0) for x in settled)
    odds = [float(x["actual_odds"]) for x in settled if x.get("actual_odds")]
    mix = defaultdict(int)
    for x in daily:
        for k, v in (x.get("market_mix") or {}).items(): mix[k] += int(v or 0)
    return {
        "days_requested": days, "ticket_odds_requested": round(float(target), 2), "tickets": len(settled),
        "wins": wins, "losses": losses, "pushes": sum(x["status"] == "PUSH" for x in settled),
        "hit_rate": round(100 * wins / decided, 1) if decided else 0.0,
        "profit": round(profit, 2), "roi": round(100 * profit / stake, 1) if stake else 0.0,
        "average_actual_odds": round(sum(odds) / len(odds), 2) if odds else None,
        "no_ticket_days": sum(x.get("status") == "NO_TICKET" for x in daily), "unsettled_days": sum(x.get("status") == "UNSETTLED" for x in daily),
        "fixtures_seen": sum(int(x.get("fixtures") or 0) for x in daily), "fixtures_analyzed": sum(int(x.get("analyzed") or 0) for x in daily), "truncated_days": 0,
        "market_mix": dict(mix), "api_leagues": len(meta.get("leagues") or []), "api_pages": meta.get("api_pages") or 0, "range_cache_hits": meta.get("cache_hits") or 0,
        "deep_odds_requests": sum(int(x.get("deep_requests") or 0) for x in daily), "deep_odds_cache_hits": sum(int(x.get("deep_cache_hits") or 0) for x in daily),
        "api_errors": meta.get("errors") or [], "mode": "Pro walk-forward independent v12",
        "note": "Probabilitatile vin din rezultate anterioare (forma home/away + goluri + Elo), nu din cote. Bet365 este folosit doar dupa predictie pentru edge si pretul biletului. Fara look-ahead pe ziua testata."
    }


def run_job(job_id, days, target):
    try:
        days = int(days); target = float(target); meta = _load(job_id, days)
        end_day = meta["end_day"]; start_test = end_day - timedelta(days=days - 1)
        byday = defaultdict(list)
        for f in meta["matches"]:
            d = _day(f)
            if d: byday[d].append(f)
        state = State(); daily = []
        all_days = sorted(byday)

        for d in all_days:
            fixtures = byday[d]
            in_test = start_test.isoformat() <= d <= end_day.isoformat()
            candidates = []; modeled = []; deep_requests = deep_hits = 0
            if in_test:
                for f in fixtures:
                    if _score(f)[0] is None: continue
                    model = state.predict(f)
                    if model is None: continue
                    modeled.append((f, model))
                    candidates.extend(_fixture_candidates(f, model, False, None, target))

                ticket = _ticket(candidates, target)
                if ticket is None and MAX_GOAL_DEEP_PER_DAY:
                    # Only enrich the strongest modelled fixtures and stop as soon as a valid ticket exists.
                    ranked = sorted(modeled, key=lambda z: max(z[1]["1"], z[1]["2"], _total_prob(z[1]["lh"] + z[1]["la"], 2.5, True), _total_prob(z[1]["lh"] + z[1]["la"], 2.5, False)), reverse=True)
                    for f, model in ranked[:MAX_GOAL_DEEP_PER_DAY]:
                        try:
                            odds, cached = _full_odds(f); deep_hits += int(cached); deep_requests += int(not cached)
                            candidates.extend(_fixture_candidates(f, model, True, odds, target))
                            ticket = _ticket(candidates, target)
                            if ticket is not None: break
                        except Exception:
                            continue

                base = {"date": d, "requested_odds": round(target, 2), "fixtures": len(fixtures), "analyzed": len(modeled), "truncated": False, "deep_requests": deep_requests, "deep_cache_hits": deep_hits}
                if ticket is None:
                    daily.append({**base, "status": "NO_TICKET", "actual_odds": None, "legs": 0, "profit": 0.0, "market_mix": {}})
                else:
                    factor, legs, mix = _settle(ticket)
                    if factor is None:
                        daily.append({**base, "status": "UNSETTLED", "actual_odds": ticket["odds"], "legs": len(ticket["legs"]), "profit": 0.0, "leg_results": legs, "market_mix": mix})
                    else:
                        profit = bt.STAKE * (factor - 1.0); status = "WIN" if profit > .005 else "LOSE" if profit < -.005 else "PUSH"
                        daily.append({**base, "status": status, "actual_odds": ticket["odds"], "legs": len(ticket["legs"]), "estimated_probability": round(ticket["joint"] * 100, 2), "profit": round(profit, 2), "return_factor": round(factor, 4), "leg_results": legs, "market_mix": mix})

                with bt.JOBS_LOCK:
                    job = bt.JOBS.get(job_id)
                    if job:
                        job["current_day"] = d; job["progress"] = min(days, len(daily)); job["partial"] = _summary(days, target, daily, meta)

            # Critical: update only AFTER every prediction for this day has been made.
            for f in fixtures:
                state.update(f)

        # Include calendar days with no covered fixtures as NO_TICKET rows.
        present = {x["date"] for x in daily}
        for i in range(days):
            d = (start_test + timedelta(days=i)).isoformat()
            if d not in present:
                daily.append({"date": d, "requested_odds": round(target, 2), "fixtures": 0, "analyzed": 0, "status": "NO_TICKET", "actual_odds": None, "legs": 0, "profit": 0.0, "market_mix": {}, "truncated": False, "deep_requests": 0, "deep_cache_hits": 0})
        daily.sort(key=lambda x: x["date"])
        result = {"summary": _summary(days, target, daily, meta), "daily": list(reversed(daily))}
        with bt.JOBS_LOCK:
            bt.JOBS[job_id].update({"status": "done", "progress": days, "result": result, "partial": result["summary"], "finished_at": time.time(), "current_day": None})
    except Exception as exc:
        with bt.JOBS_LOCK:
            if job_id in bt.JOBS:
                bt.JOBS[job_id].update({"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:500]}", "finished_at": time.time()})


def install():
    bt._run = run_job
