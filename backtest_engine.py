"""Historical football ticket backtest for 5DollarFootballAPI Pro.

The job runs in the background because the Pro plan is limited to 10 requests/min.
It uses one historical bulk page (max 50 finished fixtures) per day with include=odds,
keeps only pre-match/closing stages when available, builds one ticket for the requested
odds target and settles it against final results. Historical day payloads are cached.
"""
import json
import math
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import auto_data as fd
import market_engine


CACHE = Path("/tmp/cota10-cache/backtest-pro")
CACHE.mkdir(parents=True, exist_ok=True)
JOBS = {}
JOBS_LOCK = threading.Lock()
STAKE = 100.0


def _day_ts(day):
    start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())


def _fetch_finished_day(day):
    cache_file = CACHE / f"{day}.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and isinstance(cached.get("matches"), list):
                return cached
        except Exception:
            pass

    start, end = _day_ts(day)
    raw = fd._get(
        "/fixtures",
        {
            "start_time": start,
            "end_time": end,
            "status": "finished",
            "include": "odds",
            "per_page": 50,
            "page": 1,
            "lang": "ro",
        },
    )
    if isinstance(raw, dict):
        rows = raw.get("fixtures") or raw.get("data") or []
        pag = raw.get("pagination") or {}
    else:
        rows, pag = raw or [], {}
    payload = {
        "day": day,
        "matches": [x for x in rows if isinstance(x, dict)],
        "truncated": bool(pag.get("has_more")),
    }
    try:
        cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return payload


def _extract_inline_odds(fixture):
    inline = fixture.get("odds") or fixture.get("markets") or fixture.get("bookmakers")
    candidates = []
    if isinstance(inline, dict):
        candidates.extend((inline, {"odds": inline}, {"markets": inline}))
    elif isinstance(inline, list):
        candidates.append({"bookmakers": inline})
    for payload in candidates:
        try:
            parsed = market_engine._normalize_odds(payload)
        except Exception:
            parsed = {}
        if isinstance(parsed, dict) and parsed:
            return parsed
    return {}


def _prematch_only(odds):
    """Prefer closing, then opening. Never deliberately choose an in-play stage."""
    if not isinstance(odds, dict):
        return {}
    out = {}
    direct_keys = {"home", "draw", "away", "over", "under", "yes", "no", "line"}
    for name, market in odds.items():
        if not isinstance(market, dict):
            continue
        if isinstance(market.get("closing"), dict):
            out[name] = {"closing": market["closing"]}
        elif isinstance(market.get("opening"), dict):
            out[name] = {"opening": market["opening"]}
        elif any(k in market for k in direct_keys):
            out[name] = market
        elif isinstance(market.get("current"), dict):
            # Some historical bulk rows expose only a single pre-match snapshot as current.
            out[name] = {"current": market["current"]}
    return out


def _score_values(fixture):
    goals = fixture.get("goals") or fixture.get("score") or {}
    if not isinstance(goals, dict):
        return None, None
    h = goals.get("home")
    a = goals.get("away")
    try:
        h = int(h)
        a = int(a)
        return h, a
    except (TypeError, ValueError):
        return None, None


def _corner_values(fixture):
    c = fixture.get("corners") or {}
    if not isinstance(c, dict):
        return None
    try:
        return int(c.get("home")) + int(c.get("away"))
    except (TypeError, ValueError):
        return None


def _card_values(fixture):
    c = fixture.get("cards") or {}
    if not isinstance(c, dict):
        return None
    try:
        home = c.get("home") or {}
        away = c.get("away") or {}
        return (
            int(home.get("yellow") or 0)
            + int(home.get("red") or 0)
            + int(away.get("yellow") or 0)
            + int(away.get("red") or 0)
        )
    except (TypeError, ValueError):
        return None


def _market_data_available(name, fixture):
    name = str(name or "")
    h, a = _score_values(fixture)
    if name in {"1", "X", "2", "GG", "NG"} or name.startswith("Over ") or name.startswith("Under ") or name.startswith("AH "):
        return h is not None and a is not None
    if name.startswith("Corners "):
        return _corner_values(fixture) is not None
    if name.startswith("Cards "):
        return _card_values(fixture) is not None
    return False


def _half_lines(line):
    line = float(line)
    doubled = line * 2.0
    if abs(doubled - round(doubled)) < 1e-9:
        return [line]
    lower = math.floor(doubled) / 2.0
    return [lower, lower + 0.5]


def _factor_from_cmp(value, odd):
    if value > 1e-9:
        return float(odd)
    if value < -1e-9:
        return 0.0
    return 1.0


def _line_return(observed, line, odd, over=True):
    vals = []
    for part in _half_lines(line):
        diff = (observed - part) if over else (part - observed)
        vals.append(_factor_from_cmp(diff, odd))
    return sum(vals) / len(vals)


def _ah_return(margin, line, odd):
    vals = [_factor_from_cmp(margin + part, odd) for part in _half_lines(line)]
    return sum(vals) / len(vals)


def _settle_selection(selection, odd, fixture):
    name = str(selection or "").strip()
    h, a = _score_values(fixture)
    if name == "1" and h is not None:
        return float(odd) if h > a else 0.0
    if name == "X" and h is not None:
        return float(odd) if h == a else 0.0
    if name == "2" and h is not None:
        return float(odd) if a > h else 0.0
    if name == "GG" and h is not None:
        return float(odd) if h > 0 and a > 0 else 0.0
    if name == "NG" and h is not None:
        return float(odd) if h == 0 or a == 0 else 0.0

    if (name.startswith("Over ") or name.startswith("Under ")) and h is not None:
        try:
            line = float(name.split()[-1])
        except ValueError:
            return None
        return _line_return(h + a, line, odd, over=name.startswith("Over "))

    if name.startswith("Corners "):
        total = _corner_values(fixture)
        if total is None:
            return None
        parts = name.split()
        try:
            line = float(parts[-1])
        except ValueError:
            return None
        return _line_return(total, line, odd, over="Over" in parts)

    if name.startswith("Cards "):
        total = _card_values(fixture)
        if total is None:
            return None
        parts = name.split()
        try:
            line = float(parts[-1])
        except ValueError:
            return None
        return _line_return(total, line, odd, over="Over" in parts)

    if name.startswith("AH ") and h is not None:
        parts = name.split()
        if len(parts) < 3:
            return None
        side = parts[1]
        try:
            line = float(parts[2])
        except ValueError:
            return None
        margin = (h - a) if side == "Home" else (a - h)
        return _ah_return(margin, line, odd)
    return None


def _fixture_key_from_row(row):
    return (str(row.get("home")), str(row.get("away")), str(row.get("kickoff")))


def _analyze_day(day, target):
    payload = _fetch_finished_day(day)
    fixtures = payload["matches"]
    rows = []
    row_to_fixture = {}
    for fixture in fixtures:
        if _score_values(fixture)[0] is None:
            continue
        ef = dict(fixture)
        ef["_pro_bulk_odds"] = True
        parsed = _prematch_only(_extract_inline_odds(fixture))
        if parsed:
            ef["odds"] = parsed
        try:
            row = market_engine.analyze_fixture(ef)
        except Exception:
            continue
        markets = [m for m in (row.get("markets") or []) if _market_data_available(m.get("market"), fixture)]
        if not markets:
            continue
        row["markets"] = markets
        row["best_market"] = max(
            markets,
            key=lambda x: (
                not bool(x.get("suspicious")),
                float(x.get("ticket_probability") or x.get("probability") or 0),
                float(x.get("recommendation_score") or 0),
            ),
        )
        rows.append(row)
        row_to_fixture[_fixture_key_from_row(row)] = fixture

    rows.sort(key=lambda x: float((x.get("best_market") or {}).get("recommendation_score") or 0), reverse=True)
    combo, diag = market_engine.build_combo(rows, float(target))
    base = {
        "date": day,
        "requested_odds": round(float(target), 2),
        "fixtures": len(fixtures),
        "analyzed": len(rows),
        "truncated": bool(payload.get("truncated")),
    }
    if not combo:
        return {**base, "status": "NO_TICKET", "actual_odds": None, "legs": 0, "profit": 0.0, "return_factor": None, "diagnostics": diag}

    factor = 1.0
    leg_results = []
    unresolved = False
    for leg in combo.get("matches") or []:
        key = (str(leg.get("home")), str(leg.get("away")), str(leg.get("kickoff")))
        fixture = row_to_fixture.get(key)
        if fixture is None:
            unresolved = True
            break
        leg_factor = _settle_selection(leg.get("selection"), leg.get("odds"), fixture)
        if leg_factor is None:
            unresolved = True
            break
        factor *= leg_factor
        h, a = _score_values(fixture)
        leg_results.append({
            "match": f"{leg.get('home')} - {leg.get('away')}",
            "selection": leg.get("selection"),
            "odds": leg.get("odds"),
            "score": f"{h}-{a}",
            "return_factor": round(leg_factor, 3),
        })

    if unresolved:
        return {**base, "status": "UNSETTLED", "actual_odds": combo.get("combined_odds"), "legs": len(combo.get("matches") or []), "profit": 0.0, "return_factor": None, "leg_results": leg_results}

    profit = STAKE * (factor - 1.0)
    if profit > 0.005:
        status = "WIN"
    elif profit < -0.005:
        status = "LOSE"
    else:
        status = "PUSH"
    return {
        **base,
        "status": status,
        "actual_odds": combo.get("combined_odds"),
        "legs": len(combo.get("matches") or []),
        "estimated_probability": combo.get("estimated_joint_probability"),
        "return_factor": round(factor, 4),
        "profit": round(profit, 2),
        "leg_results": leg_results,
    }


def _summary(days, target, daily):
    settled = [x for x in daily if x.get("status") in {"WIN", "LOSE", "PUSH"}]
    wins = sum(x["status"] == "WIN" for x in settled)
    losses = sum(x["status"] == "LOSE" for x in settled)
    pushes = sum(x["status"] == "PUSH" for x in settled)
    stake = STAKE * len(settled)
    profit = sum(float(x.get("profit") or 0) for x in settled)
    avg_odds_rows = [float(x["actual_odds"]) for x in settled if x.get("actual_odds")]
    decided = wins + losses
    return {
        "days_requested": days,
        "ticket_odds_requested": round(float(target), 2),
        "tickets": len(settled),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "no_ticket_days": sum(x.get("status") == "NO_TICKET" for x in daily),
        "unsettled_days": sum(x.get("status") == "UNSETTLED" for x in daily),
        "hit_rate": round(wins / decided * 100.0, 1) if decided else 0.0,
        "stake_per_ticket": STAKE,
        "total_stake": round(stake, 2),
        "profit": round(profit, 2),
        "roi": round(profit / stake * 100.0, 1) if stake else 0.0,
        "average_actual_odds": round(sum(avg_odds_rows) / len(avg_odds_rows), 2) if avg_odds_rows else None,
        "fixtures_seen": sum(int(x.get("fixtures") or 0) for x in daily),
        "fixtures_analyzed": sum(int(x.get("analyzed") or 0) for x in daily),
        "truncated_days": sum(bool(x.get("truncated")) for x in daily),
        "note": "Simulare istorică pe cote pre-match/closing disponibile; max. 50 meciuri/zi pentru a respecta limita Pro.",
    }


def _run(job_id, days, target):
    try:
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=days - 1)
        daily = []
        for i in range(days):
            d = (start + timedelta(days=i)).isoformat()
            with JOBS_LOCK:
                job = JOBS[job_id]
                job["current_day"] = d
                job["progress"] = i
            result = _analyze_day(d, target)
            daily.append(result)
            with JOBS_LOCK:
                job = JOBS[job_id]
                job["progress"] = i + 1
                job["partial"] = _summary(days, target, daily)
        result = {
            "summary": _summary(days, target, daily),
            "daily": list(reversed(daily)),
        }
        with JOBS_LOCK:
            JOBS[job_id].update({"status": "done", "result": result, "finished_at": time.time(), "current_day": None})
    except Exception as exc:
        with JOBS_LOCK:
            JOBS[job_id].update({"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:300]}", "finished_at": time.time()})


def start(days, target):
    days = int(days)
    target = float(target)
    if days not in {20, 40, 60}:
        raise ValueError("Backtestul acceptă 20, 40 sau 60 de zile")
    if not 1.05 <= target <= 500:
        raise ValueError("Cota dorită trebuie să fie între 1.05 și 500")

    with JOBS_LOCK:
        running = next((jid for jid, j in JOBS.items() if j.get("status") == "running"), None)
        if running:
            return running, False
        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = {
            "id": job_id,
            "status": "running",
            "days": days,
            "target": target,
            "progress": 0,
            "current_day": None,
            "started_at": time.time(),
            "partial": None,
        }
    threading.Thread(target=_run, args=(job_id, days, target), daemon=True).start()
    return job_id, True


def status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else None
