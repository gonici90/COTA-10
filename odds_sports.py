"""Multi-sport odds engine for Analiza Cota AI.

Uses The Odds API for tennis/basketball/extra soccer and reuses the football
ticket optimiser from market_engine. Probabilities are conservative consensus
estimates from bookmaker prices; no fake performance model is invented.
"""
import json
import math
import os
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from fastapi import HTTPException
import market_engine

BASE = "https://api.the-odds-api.com/v4"
_CACHE = {}
_CACHE_LOCK = threading.Lock()
_LAST_QUOTA = {"remaining": None, "used": None, "last": None}

GROUP_NAMES = {
    "tennis": "Tennis",
    "basketball": "Basketball",
    "soccer": "Soccer",
}

# Full featured markets cost one quota credit per region/market.
FEATURED_MARKETS = "h2h,spreads,totals"

# Keep a useful breadth without draining a small monthly plan on a single tap.
MAX_LEAGUES = {
    "tennis": 4,
    "basketball": 4,
    "soccer": 6,
}

PRIORITY = {
    "basketball": (
        "Euroleague", "NBA", "WNBA", "NCAAB", "NBL",
    ),
    "tennis": (
        "US Open", "Wimbledon", "French Open", "Australian Open",
        "ATP", "WTA",
    ),
    "soccer": (
        "UEFA", "Champions League", "Europa", "Conference",
        "Premier League", "La Liga", "Bundesliga", "Serie A", "Ligue 1",
    ),
}


def configured():
    return bool(os.getenv("THE_ODDS_API_KEY", "").strip())


def _key():
    key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not key:
        raise HTTPException(503, "Lipsește THE_ODDS_API_KEY în Render Environment")
    return key


def _cache_get(key, ttl):
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if item and time.time() - item[0] <= ttl:
            return item[1]
    return None


def _cache_set(key, value):
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)


def _get(path, params=None, ttl=0):
    params = dict(params or {})
    cache_key = (path, tuple(sorted((str(k), str(v)) for k, v in params.items())))
    if ttl:
        hit = _cache_get(cache_key, ttl)
        if hit is not None:
            return hit

    params["apiKey"] = _key()
    url = BASE + path + "?" + urlencode(params)
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "Analiza-Cota-AI/9.0"})
    try:
        with urlopen(req, timeout=25) as response:
            data = json.loads(response.read().decode("utf-8"))
            _LAST_QUOTA["remaining"] = response.headers.get("x-requests-remaining")
            _LAST_QUOTA["used"] = response.headers.get("x-requests-used")
            _LAST_QUOTA["last"] = response.headers.get("x-requests-last")
    except HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:400]
        if e.code == 401:
            raise HTTPException(502, "The Odds API: cheia API nu este validă")
        if e.code == 429:
            raise HTTPException(429, "The Odds API: limita de request-uri a fost atinsă")
        raise HTTPException(502, f"The Odds API HTTP {e.code}: {body}")
    except (URLError, TimeoutError) as e:
        raise HTTPException(502, "The Odds API indisponibil: " + str(e)[:180])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, "The Odds API: " + str(e)[:180])

    if ttl:
        _cache_set(cache_key, data)
    return data


def quota_status():
    return dict(_LAST_QUOTA)


def active_sports():
    rows = _get("/sports/", {"all": "false"}, ttl=900)
    return [x for x in rows if isinstance(x, dict) and x.get("active", True)]


def _iso_window(day, days):
    start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=max(1, min(int(days), 7)))
    return start, end


def _event_rows(sport_key, start, end):
    try:
        rows = _get(
            f"/sports/{sport_key}/events",
            {
                "dateFormat": "iso",
                "commenceTimeFrom": start.isoformat().replace("+00:00", "Z"),
                "commenceTimeTo": end.isoformat().replace("+00:00", "Z"),
            },
            ttl=600,
        )
    except Exception:
        return []
    out = []
    now = datetime.now(timezone.utc)
    for x in rows if isinstance(rows, list) else []:
        try:
            dt = datetime.fromisoformat(str(x.get("commence_time")).replace("Z", "+00:00"))
        except Exception:
            continue
        if start <= dt < end and dt > now - timedelta(minutes=2):
            out.append(x)
    return out


def _priority_score(group, sport):
    title = str(sport.get("title") or "")
    score = 0
    for i, token in enumerate(PRIORITY.get(group, ())):
        if token.lower() in title.lower():
            score += 100 - i * 4
    return score


def _choose_leagues(group, start, end, limit=None):
    label = GROUP_NAMES[group]
    sports = [s for s in active_sports() if str(s.get("group")) == label and not s.get("has_outrights")]
    if not sports:
        return []

    limit = max(1, min(int(limit or MAX_LEAGUES[group]), 10))
    counts = {}

    # Events endpoint is quota-free. Use it to spend paid credits only on leagues
    # that actually have matches in the requested window.
    with ThreadPoolExecutor(max_workers=min(8, len(sports))) as pool:
        future_map = {
            pool.submit(_event_rows, s["key"], start, end): s
            for s in sports
            if s.get("key")
        }
        for fut in as_completed(future_map):
            sport = future_map[fut]
            try:
                events = fut.result()
            except Exception:
                events = []
            if events:
                counts[sport["key"]] = (sport, len(events))

    chosen = list(counts.values())
    chosen.sort(key=lambda pair: (pair[1], _priority_score(group, pair[0])), reverse=True)
    return [sport for sport, _ in chosen[:limit]]


def _odds_for_league(sport_key, start, end):
    return _get(
        f"/sports/{sport_key}/odds/",
        {
            "regions": "eu",
            "markets": FEATURED_MARKETS,
            "oddsFormat": "decimal",
            "dateFormat": "iso",
            "commenceTimeFrom": start.isoformat().replace("+00:00", "Z"),
            "commenceTimeTo": end.isoformat().replace("+00:00", "Z"),
        },
        ttl=600,
    )


def _safe_float(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _median(values):
    vals = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return statistics.median(vals) if vals else None


def _pair_probs(outcomes):
    prices = [_safe_float(x.get("price")) for x in outcomes]
    if len(prices) != 2 or any(not p or p <= 1.0 for p in prices):
        return None
    inv = [1.0 / p for p in prices]
    z = sum(inv)
    if z <= 0:
        return None
    return [x / z for x in inv]


def _market_key(market, outcome):
    key = market.get("key")
    name = str(outcome.get("name") or "")
    point = _safe_float(outcome.get("point"))
    if key == "h2h":
        return (key, name, None)
    if key == "spreads":
        return (key, name, point)
    if key == "totals":
        return (key, name, point)
    return None


def _selection_label(key):
    market, name, point = key
    if market == "h2h":
        return "Câștigător: " + name
    if market == "spreads":
        sign = "+" if point is not None and point > 0 else ""
        return f"Handicap: {name} {sign}{point:g}" if point is not None else "Handicap: " + name
    if market == "totals":
        return f"{name} {point:g}" if point is not None else name
    return name


def _event_to_row(event, sport_group):
    # Gather no-vig probabilities from each bookmaker and retain the best available
    # decimal price for the exact same line.
    pool = {}
    for book in event.get("bookmakers") or []:
        if not isinstance(book, dict):
            continue
        book_name = str(book.get("title") or book.get("key") or "Bookmaker")
        for market in book.get("markets") or []:
            if not isinstance(market, dict) or market.get("key") not in {"h2h", "spreads", "totals"}:
                continue
            outcomes = [x for x in (market.get("outcomes") or []) if isinstance(x, dict)]
            probs = _pair_probs(outcomes)
            if not probs:
                continue
            for outcome, p in zip(outcomes, probs):
                k = _market_key(market, outcome)
                price = _safe_float(outcome.get("price"))
                if not k or not price or price <= 1.01:
                    continue
                item = pool.setdefault(k, {"probs": [], "offers": []})
                item["probs"].append(p)
                item["offers"].append((price, book_name))

    markets = []
    for key, item in pool.items():
        consensus = _median(item["probs"])
        if consensus is None:
            continue
        best_price, best_book = max(item["offers"], key=lambda x: x[0])
        implied_best = 1.0 / best_price

        # Conservative estimate: bookmaker consensus, lightly anchored to the actual
        # best offered price, then penalised when only one/two books quote the line.
        p = 0.80 * consensus + 0.20 * implied_best
        quote_count = len(item["probs"])
        uncertainty = 0.025 if quote_count <= 1 else 0.015 if quote_count == 2 else 0.008
        ticket_p = max(0.02, min(0.98, p - uncertainty))
        ev = (ticket_p * best_price - 1.0) * 100.0
        suspicious = best_price > 8.0 or abs(consensus - implied_best) > 0.22
        markets.append(
            {
                "market": _selection_label(key),
                "probability": round(consensus * 100.0, 1),
                "ticket_probability": round(ticket_p * 100.0, 1),
                "bookmaker_odds": round(best_price, 2),
                "fair_odds": round(1.0 / max(ticket_p, 1e-9), 2),
                "ev": round(ev, 1),
                "safe": ticket_p >= 0.60 and not suspicious,
                "value": ev >= 1.0 and not suspicious,
                "suspicious": suspicious,
                "source": f"The Odds API consensus • {best_book}",
                "bookmaker": best_book,
                "quote_count": quote_count,
                "recommendation_score": round(ticket_p * 100.0 - max(0.0, best_price - 1.0) * 0.4, 2),
            }
        )

    markets.sort(
        key=lambda x: (not x["suspicious"], x["ticket_probability"], x["recommendation_score"]),
        reverse=True,
    )
    usable = [x for x in markets if not x["suspicious"]]
    best = usable[0] if usable else (markets[0] if markets else None)

    return {
        "fixture_id": event.get("id"),
        "kickoff": event.get("commence_time"),
        "league": event.get("sport_title") or event.get("sport_key") or sport_group.title(),
        "country": "",
        "sport": sport_group,
        "home": event.get("home_team") or "?",
        "away": event.get("away_team") or "?",
        "confidence": "consensus piață",
        "markets": markets,
        "best_market": best,
        "best_value": next((x for x in usable if x["value"]), None),
        "odds_markets": sorted({str(x.get("market", "")).split(":")[0] for x in markets}),
    }


def analyze_group(group, day, target=10.0, days=2, league_limit=None):
    group = str(group).lower()
    if group not in GROUP_NAMES:
        raise HTTPException(400, "Sport necunoscut")

    start, end = _iso_window(day, days)
    leagues = _choose_leagues(group, start, end, league_limit)
    rows = []
    errors = []
    raw_events = 0

    # Calls in parallel keep UI responsive; quota cost is still market-count based.
    with ThreadPoolExecutor(max_workers=min(5, max(1, len(leagues)))) as pool:
        future_map = {pool.submit(_odds_for_league, s["key"], start, end): s for s in leagues}
        for fut in as_completed(future_map):
            sport = future_map[fut]
            try:
                events = fut.result()
            except Exception as e:
                errors.append({"league": sport.get("title"), "error": str(e)[:180]})
                continue
            if not isinstance(events, list):
                continue
            raw_events += len(events)
            for event in events:
                if not isinstance(event, dict):
                    continue
                row = _event_to_row(event, group)
                if row["best_market"]:
                    rows.append(row)

    # One event id can occasionally appear more than once around API refreshes.
    dedup = {}
    for row in rows:
        dedup[row["fixture_id"] or (row["home"], row["away"], row["kickoff"])] = row
    rows = list(dedup.values())
    rows.sort(key=lambda x: x["best_market"]["recommendation_score"], reverse=True)

    combo, diag = market_engine.build_combo(rows, target)
    if combo:
        by_event = {(r["home"], r["away"], r.get("kickoff")): r for r in rows}
        for leg in combo.get("matches", []):
            row = by_event.get((leg.get("home"), leg.get("away"), leg.get("kickoff")))
            leg["sport"] = group
            if row:
                pick = next((p for p in row.get("markets", []) if p.get("market") == leg.get("selection")), None)
                if pick:
                    leg["bookmaker"] = pick.get("bookmaker")
                    leg["source"] = pick.get("source")
    return {
        "date": day,
        "days": days,
        "period_end": (start + timedelta(days=days - 1)).date().isoformat(),
        "provider": "The Odds API • EU bookmaker consensus",
        "sport": group,
        "api_fixtures": raw_events,
        "eligible": len(rows),
        "attempted": raw_events,
        "analyzed": len(rows),
        "without_usable_odds": max(0, raw_events - len(rows)),
        "analysis_errors": errors,
        "ranking": rows,
        "suggested_combo": combo,
        "combo_diagnostics": diag,
        "leagues_scanned": [s.get("title") for s in leagues],
        "quota": quota_status(),
    }


def analyze_mix(day, target=10.0, days=4):
    # Keep football as the richer 5Dollar/Bet365 engine; add the strongest current
    # tennis and basketball markets from The Odds API.
    football = market_engine.analyze_period(day, target, days, 200)
    tennis = analyze_group("tennis", day, target, days, league_limit=3)
    basketball = analyze_group("basketball", day, target, days, league_limit=3)

    rows = []
    for r in football.get("ranking") or []:
        r["sport"] = "football"
        rows.append(r)
    rows.extend(tennis.get("ranking") or [])
    rows.extend(basketball.get("ranking") or [])
    rows.sort(key=lambda x: x["best_market"]["recommendation_score"], reverse=True)

    combo, diag = market_engine.build_combo(rows, target)
    if combo:
        by_event = {(r["home"], r["away"], r.get("kickoff")): r for r in rows}
        for leg in combo.get("matches", []):
            row = by_event.get((leg.get("home"), leg.get("away"), leg.get("kickoff")))
            if row:
                leg["sport"] = row.get("sport", "football")
                pick = next((p for p in row.get("markets", []) if p.get("market") == leg.get("selection")), None)
                if pick:
                    leg["bookmaker"] = pick.get("bookmaker")
                    leg["source"] = pick.get("source")
    errors = []
    errors.extend(football.get("analysis_errors") or [])
    errors.extend(tennis.get("analysis_errors") or [])
    errors.extend(basketball.get("analysis_errors") or [])

    return {
        "date": day,
        "days": days,
        "period_end": football.get("period_end"),
        "provider": "5DollarFootballAPI + Bet365 + The Odds API",
        "sport": "mix",
        "api_fixtures": (
            int(football.get("api_fixtures") or 0)
            + int(tennis.get("api_fixtures") or 0)
            + int(basketball.get("api_fixtures") or 0)
        ),
        "eligible": len(rows),
        "attempted": (
            int(football.get("attempted") or 0)
            + int(tennis.get("attempted") or 0)
            + int(basketball.get("attempted") or 0)
        ),
        "analyzed": len(rows),
        "without_usable_odds": (
            int(football.get("without_usable_odds") or 0)
            + int(tennis.get("without_usable_odds") or 0)
            + int(basketball.get("without_usable_odds") or 0)
        ),
        "analysis_errors": errors,
        "ranking": rows,
        "suggested_combo": combo,
        "combo_diagnostics": diag,
        "quota": quota_status(),
        "sources": {
            "football": football.get("analyzed", 0),
            "tennis": tennis.get("analyzed", 0),
            "basketball": basketball.get("analyzed", 0),
        },
    }
