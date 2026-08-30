"""API-native historical backtest for 5DollarFootballAPI Pro.

Design goals:
- no CSV dependency;
- use the provider's 12-month historical window and Bet365 pre-match snapshots;
- fetch a reusable 60-day dataset by league (range endpoint, not one request/day);
- paginate every selected league, so there is no 50-matches/day truncation;
- use bulk closing 1X2 prices immediately;
- fetch full Bet365 goal-line prices only adaptively when 1X2 alone cannot make
  a ticket near the requested target;
- cache both league ranges and per-fixture full odds.

Double-chance is deliberately NOT backtested here: the provider's documented
full-price Bet365 markets do not expose a direct 1X/X2/12 market, and this engine
will not invent a historical bookmaker price.
"""

import json
import math
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import auto_data as fd
import backtest_engine as bt
import market_engine


CACHE = Path(os.getenv("COTA_CACHE_DIR", "/tmp/cota10-cache")) / "backtest-pro-native-v11"
LEAGUE_CACHE = CACHE / "leagues"
ODDS_CACHE = CACHE / "full-odds"
for _p in (CACHE, LEAGUE_CACHE, ODDS_CACHE):
    _p.mkdir(parents=True, exist_ok=True)

MAX_LEAGUES = max(5, min(30, int(os.getenv("BACKTEST_PRO_LEAGUES", "18"))))
DEEP_GOAL_CALLS_PER_DAY = max(0, min(4, int(os.getenv("BACKTEST_PRO_GOAL_DEEP", "2"))))
DATASET_DAYS = 60

_PRIORITY_NAMES = (
    "premier league", "la liga", "serie a", "bundesliga", "ligue 1",
    "champions league", "europa league", "conference league",
    "eredivisie", "primeira liga", "super lig", "mls", "major league soccer",
    "championship", "scottish premiership", "liga mx", "brazil", "argentina",
)


def _json_read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _json_write(path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _rows_and_pagination(raw):
    if isinstance(raw, dict):
        rows = raw.get("fixtures") or raw.get("leagues") or raw.get("data") or []
        pag = raw.get("pagination") or {}
    else:
        rows, pag = raw or [], {}
    if isinstance(rows, dict):
        rows = rows.get("fixtures") or rows.get("leagues") or rows.get("data") or []
    return [x for x in rows if isinstance(x, dict)], pag


def _league_score(x, index):
    name = str(x.get("name") or "").lower()
    score = 0
    if x.get("is_popular") is True or str(x.get("is_popular")).lower() in {"1", "true", "yes"}:
        score += 1000
    if any(k in name for k in _PRIORITY_NAMES):
        score += 500
    if x.get("has_standings"):
        score += 40
    # Preserve provider ordering as the final tie-breaker; their list is already curated.
    return score - index / 10000.0


def _list_leagues():
    cache_file = CACHE / "pro-leagues.json"
    cached = _json_read(cache_file)
    if isinstance(cached, dict) and time.time() - float(cached.get("saved_at") or 0) < 86400:
        rows = cached.get("rows") or []
        if rows:
            return rows

    out = []
    for page in range(1, 6):
        raw = fd._get("/leagues", {"page": page, "per_page": 100, "lang": "ro"})
        rows, pag = _rows_and_pagination(raw)
        out.extend(rows)
        if not pag.get("has_more"):
            break
    # unique by id
    seen, unique = set(), []
    for x in out:
        lid = x.get("id")
        if lid in seen or lid is None:
            continue
        seen.add(lid)
        unique.append(x)
    _json_write(cache_file, {"saved_at": time.time(), "rows": unique})
    return unique


def _selected_leagues():
    leagues = _list_leagues()
    ranked = sorted(enumerate(leagues), key=lambda z: _league_score(z[1], z[0]), reverse=True)
    return [x for _, x in ranked[:MAX_LEAGUES]]


def _range_ts(end_day):
    # end_day is the final included UTC date (normally yesterday).
    end_exclusive = datetime.combine(end_day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    start = end_exclusive - timedelta(days=DATASET_DAYS)
    return int(start.timestamp()), int(end_exclusive.timestamp())


def _league_range(league, end_day):
    lid = int(league["id"])
    start_ts, end_ts = _range_ts(end_day)
    cache_file = LEAGUE_CACHE / f"{end_day.isoformat()}-{DATASET_DAYS}d-{lid}.json"
    cached = _json_read(cache_file)
    if isinstance(cached, dict) and isinstance(cached.get("matches"), list):
        return cached.get("matches") or [], True, int(cached.get("pages") or 0)

    matches, pages = [], 0
    for page in range(1, 8):
        raw = fd._get(
            f"/leagues/{lid}/fixtures",
            {
                "start_time": start_ts,
                "end_time": end_ts,
                "status": "finished",
                "include": "odds",
                "per_page": 50,
                "page": page,
                "lang": "ro",
            },
        )
        rows, pag = _rows_and_pagination(raw)
        pages += 1
        matches.extend(rows)
        if not pag.get("has_more"):
            break

    seen, unique = set(), []
    for f in matches:
        fid = f.get("id") or f.get("fixture_id")
        key = fid or (
            str((f.get("teams") or {}).get("home")),
            str((f.get("teams") or {}).get("away")),
            str(f.get("kickoff_ts") or f.get("kickoff_utc")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    _json_write(cache_file, {"league": league, "pages": pages, "matches": unique})
    return unique, False, pages


def _fixture_day(f):
    v = f.get("kickoff_ts") or f.get("timestamp") or f.get("start_time")
    try:
        if v is not None:
            return datetime.fromtimestamp(int(v), timezone.utc).date().isoformat()
    except Exception:
        pass
    v = f.get("kickoff_utc") or f.get("kickoff") or f.get("date")
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc).date().isoformat()
        except Exception:
            pass
    return None


def _load_dataset(job_id, days):
    end_day = date.today() - timedelta(days=1)
    leagues = _selected_leagues()
    all_matches = []
    cache_hits = 0
    api_pages = 0
    errors = []

    for idx, league in enumerate(leagues):
        name = str(league.get("name") or league.get("id"))
        with bt.JOBS_LOCK:
            job = bt.JOBS.get(job_id)
            if job:
                job["current_day"] = f"API Pro: {name}"
                job["progress"] = min(days - 1, int(idx / max(1, len(leagues)) * days))
        try:
            rows, cached, pages = _league_range(league, end_day)
            all_matches.extend(rows)
            cache_hits += int(cached)
            api_pages += 0 if cached else pages
        except Exception as exc:
            errors.append({"league": name, "error": f"{type(exc).__name__}: {str(exc)[:180]}"})

    seen, unique = set(), []
    for f in all_matches:
        fid = f.get("id") or f.get("fixture_id")
        key = fid or (_fixture_day(f), json.dumps(f.get("teams") or {}, sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)

    return {
        "end_day": end_day,
        "leagues": leagues,
        "matches": unique,
        "league_cache_hits": cache_hits,
        "league_api_pages": api_pages,
        "errors": errors,
    }


def _bulk_goal_line(f):
    odds = f.get("odds") or {}
    if not isinstance(odds, dict):
        return None
    gm = odds.get("goal_line") or odds.get("goalline") or odds.get("goals")
    if not isinstance(gm, dict):
        return None
    for k in ("closing", "current", "opening"):
        v = gm.get(k)
        if isinstance(v, dict):
            v = v.get("line")
        try:
            if v is not None:
                return float(v)
        except Exception:
            pass
    try:
        if gm.get("line") is not None:
            return float(gm.get("line"))
    except Exception:
        pass
    return None


def _full_odds(f):
    fid = f.get("id") or f.get("fixture_id")
    if not fid:
        return {}, True
    path = ODDS_CACHE / f"{fid}.json"
    cached = _json_read(path)
    if isinstance(cached, dict) and isinstance(cached.get("odds"), dict):
        return cached.get("odds") or {}, True
    raw = fd._get(f"/fixtures/{fid}/odds", {"bookmakers": "bet365"})
    odds = market_engine._normalize_odds(raw)
    _json_write(path, {"fixture_id": fid, "odds": odds})
    return odds, False


def _row_for_fixture(f, full_odds=None):
    ef = dict(f)
    if full_odds is None:
        ef["_pro_bulk_odds"] = True
        parsed = bt._prematch_only(bt._extract_inline_odds(f))
        if parsed:
            ef["odds"] = parsed
    else:
        ef.pop("_pro_bulk_odds", None)
        ef["odds"] = full_odds

    row = market_engine.analyze_fixture(ef)
    allowed = []
    for m in row.get("markets") or []:
        name = str(m.get("market") or "")
        src = str(m.get("source") or "")
        if name in {"1", "X", "2"} and src == "Bet365 1X2":
            allowed.append(m)
        elif (name.startswith("Over ") or name.startswith("Under ")) and src == "Bet365 Goals":
            allowed.append(m)
    if not allowed:
        return None
    row["markets"] = allowed
    usable = [m for m in allowed if not m.get("suspicious")]
    row["best_market"] = max(
        usable or allowed,
        key=lambda m: (
            float(m.get("ticket_probability") or m.get("probability") or 0),
            float(m.get("recommendation_score") or 0),
        ),
    )
    return row


def _ticket_band(target):
    target = float(target)
    if target <= 1.60:
        return target * .94, target * 1.06
    if target <= 2.30:
        return target * .95, target * 1.05
    if target <= 5:
        return target * .93, target * 1.07
    return target * .92, target * 1.08


def _combo(rows, target):
    combo, diag = market_engine.build_combo(rows, float(target))
    if not combo:
        return None, diag
    lo, hi = _ticket_band(target)
    try:
        actual = float(combo.get("combined_odds") or 0)
    except Exception:
        actual = 0
    if not (lo <= actual <= hi):
        return None, diag
    return combo, diag


def _fixture_key(row):
    return (str(row.get("home")), str(row.get("away")), str(row.get("kickoff")))


def _settle_combo(combo, row_to_fixture):
    factor = 1.0
    legs = []
    mix = defaultdict(int)
    for leg in combo.get("matches") or []:
        key = (str(leg.get("home")), str(leg.get("away")), str(leg.get("kickoff")))
        f = row_to_fixture.get(key)
        if f is None:
            return None, legs, dict(mix)
        ret = bt._settle_selection(leg.get("selection"), leg.get("odds"), f)
        if ret is None:
            return None, legs, dict(mix)
        factor *= ret
        h, a = bt._score_values(f)
        sel = str(leg.get("selection") or "")
        kind = "GOALS" if sel.startswith("Over ") or sel.startswith("Under ") else "1X2"
        mix[kind] += 1
        legs.append({
            "match": f"{leg.get('home')} - {leg.get('away')}",
            "selection": sel,
            "odds": leg.get("odds"),
            "score": f"{h}-{a}",
            "return_factor": round(float(ret), 3),
        })
    return factor, legs, dict(mix)


def _analyze_day(day, fixtures, target):
    rows_by_key = {}
    row_to_fixture = {}
    bulk_errors = 0

    for f in fixtures:
        if bt._score_values(f)[0] is None:
            continue
        try:
            row = _row_for_fixture(f)
        except Exception:
            bulk_errors += 1
            continue
        if not row:
            continue
        key = _fixture_key(row)
        rows_by_key[key] = row
        row_to_fixture[key] = f

    rows = list(rows_by_key.values())
    combo, diag = _combo(rows, target)
    deep_requests = 0
    deep_cache_hits = 0

    # Only spend extra Pro requests when the bulk 1X2 screen cannot make a valid
    # ticket. Full goal-line prices are fetched fixture-by-fixture and then cached.
    if combo is None and DEEP_GOAL_CALLS_PER_DAY:
        candidates = []
        for f in fixtures:
            if bt._score_values(f)[0] is None or _bulk_goal_line(f) is None:
                continue
            try:
                tmp = _row_for_fixture(f)
                bp = float((tmp or {}).get("best_market", {}).get("ticket_probability") or 0)
            except Exception:
                bp = 0
            candidates.append((bp, f))
        candidates.sort(key=lambda z: z[0], reverse=True)

        for _, f in candidates[:DEEP_GOAL_CALLS_PER_DAY]:
            try:
                odds, cached = _full_odds(f)
                deep_cache_hits += int(cached)
                deep_requests += int(not cached)
                if not odds:
                    continue
                row = _row_for_fixture(f, odds)
                if not row:
                    continue
                key = _fixture_key(row)
                rows_by_key[key] = row
                row_to_fixture[key] = f
                rows = list(rows_by_key.values())
                combo, diag = _combo(rows, target)
                if combo is not None:
                    break
            except Exception:
                continue

    base = {
        "date": day,
        "requested_odds": round(float(target), 2),
        "fixtures": len(fixtures),
        "analyzed": len(rows_by_key),
        "truncated": False,
        "deep_requests": deep_requests,
        "deep_cache_hits": deep_cache_hits,
        "analysis_errors": bulk_errors,
    }
    if combo is None:
        return {**base, "status": "NO_TICKET", "actual_odds": None, "legs": 0, "profit": 0.0, "diagnostics": diag, "market_mix": {}}

    factor, leg_results, mix = _settle_combo(combo, row_to_fixture)
    if factor is None:
        return {**base, "status": "UNSETTLED", "actual_odds": combo.get("combined_odds"), "legs": len(combo.get("matches") or []), "profit": 0.0, "leg_results": leg_results, "market_mix": mix}

    profit = bt.STAKE * (factor - 1.0)
    status = "WIN" if profit > .005 else "LOSE" if profit < -.005 else "PUSH"
    return {
        **base,
        "status": status,
        "actual_odds": combo.get("combined_odds"),
        "legs": len(combo.get("matches") or []),
        "estimated_probability": combo.get("estimated_joint_probability"),
        "profit": round(profit, 2),
        "return_factor": round(factor, 4),
        "leg_results": leg_results,
        "market_mix": mix,
    }


def _summary(days, target, daily, meta):
    settled = [x for x in daily if x.get("status") in {"WIN", "LOSE", "PUSH"}]
    wins = sum(x.get("status") == "WIN" for x in settled)
    losses = sum(x.get("status") == "LOSE" for x in settled)
    pushes = sum(x.get("status") == "PUSH" for x in settled)
    decided = wins + losses
    stake = bt.STAKE * len(settled)
    profit = sum(float(x.get("profit") or 0) for x in settled)
    odds = [float(x.get("actual_odds")) for x in settled if x.get("actual_odds")]
    mix = defaultdict(int)
    for x in daily:
        for k, n in (x.get("market_mix") or {}).items():
            mix[k] += int(n or 0)
    return {
        "days_requested": int(days),
        "ticket_odds_requested": round(float(target), 2),
        "tickets": len(settled),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": round(100.0 * wins / decided, 1) if decided else 0.0,
        "profit": round(profit, 2),
        "roi": round(100.0 * profit / stake, 1) if stake else 0.0,
        "average_actual_odds": round(sum(odds) / len(odds), 2) if odds else None,
        "no_ticket_days": sum(x.get("status") == "NO_TICKET" for x in daily),
        "unsettled_days": sum(x.get("status") == "UNSETTLED" for x in daily),
        "fixtures_seen": sum(int(x.get("fixtures") or 0) for x in daily),
        "fixtures_analyzed": sum(int(x.get("analyzed") or 0) for x in daily),
        "truncated_days": 0,
        "market_mix": dict(mix),
        "api_leagues": len(meta.get("leagues") or []),
        "league_cache_hits": int(meta.get("league_cache_hits") or 0),
        "league_api_pages": int(meta.get("league_api_pages") or 0),
        "deep_odds_requests": sum(int(x.get("deep_requests") or 0) for x in daily),
        "deep_odds_cache_hits": sum(int(x.get("deep_cache_hits") or 0) for x in daily),
        "api_errors": meta.get("errors") or [],
        "mode": "5DollarFootballAPI Pro native v11",
        "note": "API Pro direct: league-range bulk + Bet365 closing/opening. 1X2 uses real bulk prices; goal-line uses real full prices adaptively. Double-chance is excluded because no direct historical 1X/X2/12 price is exposed by the provider.",
    }


def run_job(job_id, days, target):
    try:
        days = int(days)
        target = float(target)
        meta = _load_dataset(job_id, days)
        end_day = meta["end_day"]
        start_day = end_day - timedelta(days=days - 1)
        wanted = {(start_day + timedelta(days=i)).isoformat() for i in range(days)}
        by_day = defaultdict(list)
        for f in meta.get("matches") or []:
            d = _fixture_day(f)
            if d in wanted:
                by_day[d].append(f)

        daily = []
        for i in range(days):
            d = (start_day + timedelta(days=i)).isoformat()
            with bt.JOBS_LOCK:
                job = bt.JOBS.get(job_id)
                if job:
                    job["current_day"] = d
                    # Dataset loading already consumed part of the bar; daily analysis fills it.
                    job["progress"] = min(days - 1, max(job.get("progress") or 0, int(i / max(1, days) * days)))
            r = _analyze_day(d, by_day.get(d) or [], target)
            daily.append(r)
            with bt.JOBS_LOCK:
                job = bt.JOBS.get(job_id)
                if job:
                    job["progress"] = i + 1
                    job["partial"] = _summary(days, target, daily, meta)

        result = {"summary": _summary(days, target, daily, meta), "daily": list(reversed(daily))}
        with bt.JOBS_LOCK:
            bt.JOBS[job_id].update({"status": "done", "progress": days, "result": result, "partial": result["summary"], "finished_at": time.time(), "current_day": None})
    except Exception as exc:
        with bt.JOBS_LOCK:
            if job_id in bt.JOBS:
                bt.JOBS[job_id].update({"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:500]}", "finished_at": time.time()})


def install():
    bt._run = run_job
