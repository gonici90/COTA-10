"""Calibrated API-Pro walk-forward backtest.

The football model stays independent of bookmaker prices. A second online layer
learns, only from prior settled matches, how much of the model-vs-Bet365 residual
is trustworthy. This prevents raw model overconfidence from becoming fake edge.
"""

import math
import time
from collections import defaultdict, deque
from datetime import timedelta
from itertools import combinations

import backtest_engine as bt
import backtest_pro_walkforward as base


MIN_CAL_SAMPLES = 70


class ResidualCalibrator:
    def __init__(self):
        self.rows = defaultdict(lambda: deque(maxlen=1800))

    def update(self, market, model_p, book_p, won):
        self.rows[str(market)].append((float(model_p), float(book_p), 1.0 if won else 0.0))

    def weight(self, market):
        rows = list(self.rows[str(market)])
        n = len(rows)
        if n < MIN_CAL_SAMPLES:
            return 0.0, n
        num = den = 0.0
        for model_p, book_p, y in rows:
            x = model_p - book_p
            num += x * (y - book_p)
            den += x * x
        raw = (num / den) if den > 1e-9 else 0.0
        # The market is the baseline. We only trust the independent model in the
        # same direction, never invert it from a short historical sample.
        raw = max(0.0, min(1.0, raw))
        shrink = n / (n + 120.0)
        return raw * shrink, n

    def probability(self, market, model_p, book_p):
        w, n = self.weight(market)
        q = float(book_p) + w * (float(model_p) - float(book_p))
        # Never allow the overlay to move more than 8 percentage points away
        # from the de-vig market baseline.
        q = max(float(book_p) - 0.04, min(float(book_p) + 0.08, q))
        return max(0.01, min(0.99, q)), w, n

    def diagnostics(self):
        out = {}
        for m in ("1", "X", "2"):
            w, n = self.weight(m)
            out[m] = {"samples": n, "model_weight": round(w, 3)}
        return out


def _candidate(market, model_p, odd, book_p, fixture, cal, target):
    q, weight, sample = cal.probability(market, model_p, book_p)
    raw_edge = float(model_p) - float(book_p)
    edge = q - float(book_p)
    ev = q * float(odd) - 1.0

    # No bet unless the independent model originally disagreed with the market
    # in the profitable direction and prior calibration says that disagreement
    # deserves non-zero weight.
    if raw_edge <= 0 or weight <= 0 or sample < MIN_CAL_SAMPLES:
        return None

    if target <= 1.60:
        ok = q >= .64 and 1.12 <= odd <= 1.72 and edge >= .004 and ev >= .010
    elif target <= 2.30:
        ok = q >= .57 and 1.12 <= odd <= 2.12 and edge >= .005 and ev >= .012
    else:
        ok = q >= .53 and 1.12 <= odd <= 2.50 and edge >= .006 and ev >= .015
    if not ok:
        return None

    return {
        "market": market,
        "prob": q,
        "raw_prob": float(model_p),
        "odd": float(odd),
        "bookp": float(book_p),
        "edge": edge,
        "raw_edge": raw_edge,
        "ev": ev,
        "cal_weight": weight,
        "cal_sample": sample,
        "fixture": fixture,
        "fixture_id": fixture.get("id") or fixture.get("fixture_id"),
    }


def _ticket(candidates, target):
    target = float(target)
    if target <= 1.60:
        lo, hi, maxlegs, min_ticket_ev = target * .97, target * 1.03, 2, 1.015
    elif target <= 2.30:
        lo, hi, maxlegs, min_ticket_ev = target * .975, target * 1.025, 2, 1.020
    else:
        lo, hi, maxlegs, min_ticket_ev = target * .95, target * 1.05, 4, 1.025

    # One strongest selection per fixture. Multiple alternatives from the same
    # match made the old optimiser look more confident than it really was.
    byfix = defaultdict(list)
    for c in candidates:
        byfix[c["fixture_id"]].append(c)
    pool = []
    for opts in byfix.values():
        opts.sort(key=lambda x: (x["ev"], x["edge"], x["prob"]), reverse=True)
        pool.append(opts[0])
    pool.sort(key=lambda x: (x["ev"], x["edge"], x["prob"]), reverse=True)
    pool = pool[:24]

    best = None
    for z in range(1, min(maxlegs, len(pool)) + 1):
        for combo in combinations(pool, z):
            odd = math.prod(x["odd"] for x in combo)
            if not (lo <= odd <= hi):
                continue
            joint = math.prod(x["prob"] for x in combo)
            expected_return = joint * odd
            if expected_return < min_ticket_ev:
                continue
            avg_edge = sum(x["edge"] for x in combo) / z
            avg_weight = sum(x["cal_weight"] for x in combo) / z
            score = (
                (expected_return - 1.0)
                + avg_edge * .30
                + avg_weight * .015
                - abs(math.log(odd / target)) * .01
                - max(0, z - 2) * .004
            )
            if best is None or score > best[0]:
                best = (score, odd, joint, expected_return, combo)
    if best is None:
        return None
    _, odd, joint, expected_return, combo = best
    return {
        "odds": round(odd, 2),
        "joint": joint,
        "expected_return": expected_return,
        "legs": list(combo),
    }


def _settle(ticket):
    factor = 1.0
    legs = []
    for c in ticket["legs"]:
        ret = bt._settle_selection(c["market"], c["odd"], c["fixture"])
        if ret is None:
            return None, legs
        factor *= ret
        h, a = base._score(c["fixture"])
        home, away = base._teams(c["fixture"])
        legs.append({
            "match": f"{home} - {away}",
            "selection": c["market"],
            "odds": round(c["odd"], 2),
            "score": f"{h}-{a}",
            "model_probability": round(c["raw_prob"] * 100, 1),
            "market_probability": round(c["bookp"] * 100, 1),
            "calibrated_probability": round(c["prob"] * 100, 1),
            "edge_pp": round(c["edge"] * 100, 1),
            "calibration_weight": round(c["cal_weight"], 3),
        })
    return factor, legs


def _summary(days, target, daily, meta, cal):
    settled = [x for x in daily if x.get("status") in {"WIN", "LOSE", "PUSH"}]
    wins = sum(x["status"] == "WIN" for x in settled)
    losses = sum(x["status"] == "LOSE" for x in settled)
    decided = wins + losses
    stake = bt.STAKE * len(settled)
    profit = sum(float(x.get("profit") or 0) for x in settled)
    odds = [float(x["actual_odds"]) for x in settled if x.get("actual_odds")]
    avg = (sum(odds) / len(odds)) if odds else None
    return {
        "days_requested": int(days),
        "ticket_odds_requested": round(float(target), 2),
        "tickets": len(settled),
        "wins": wins,
        "losses": losses,
        "pushes": sum(x["status"] == "PUSH" for x in settled),
        "hit_rate": round(100 * wins / decided, 1) if decided else 0.0,
        "profit": round(profit, 2),
        "roi": round(100 * profit / stake, 1) if stake else 0.0,
        "average_actual_odds": round(avg, 2) if avg else None,
        "break_even_hit_rate": round(100 / avg, 1) if avg else None,
        "no_ticket_days": sum(x.get("status") == "NO_TICKET" for x in daily),
        "unsettled_days": sum(x.get("status") == "UNSETTLED" for x in daily),
        "fixtures_seen": sum(int(x.get("fixtures") or 0) for x in daily),
        "fixtures_analyzed": sum(int(x.get("analyzed") or 0) for x in daily),
        "truncated_days": 0,
        "api_leagues": len(meta.get("leagues") or []),
        "api_pages": meta.get("api_pages") or 0,
        "range_cache_hits": meta.get("cache_hits") or 0,
        "api_errors": meta.get("errors") or [],
        "calibration": cal.diagnostics(),
        "mode": "Pro independent + online residual calibration v13",
        "note": "Model independent (forma/goluri/Elo), apoi calibrare walk-forward pe erorile istorice fata de Bet365. Cotele nu genereaza predictia; ele sunt benchmark-ul. Fara look-ahead. Numai 1/X/2 pana cand acest strat este validat.",
    }


def run_job(job_id, days, target):
    try:
        days = int(days)
        target = float(target)
        meta = base._load(job_id, days)
        end_day = meta["end_day"]
        start_test = end_day - timedelta(days=days - 1)
        byday = defaultdict(list)
        for f in meta["matches"]:
            d = base._day(f)
            if d:
                byday[d].append(f)

        state = base.State()
        cal = ResidualCalibrator()
        daily = []

        for d in sorted(byday):
            fixtures = byday[d]
            in_test = start_test.isoformat() <= d <= end_day.isoformat()
            day_records = []
            candidates = []
            modeled = 0

            # Predict every warm-up and test fixture before any result from the
            # current day is fed back. Warm-up predictions train the calibrator.
            for f in fixtures:
                h, a = base._score(f)
                if h is None:
                    continue
                model = state.predict(f)
                if model is None:
                    continue
                modeled += int(in_test)
                prices = base._three_prices(base._inline_odds(f))
                if not prices:
                    continue
                book = base._devig(prices)
                day_records.append((f, model, prices, book))
                if in_test:
                    for market, mp, odd, bp in zip(("1", "X", "2"), (model["1"], model["X"], model["2"]), prices, book):
                        c = _candidate(market, mp, odd, bp, f, cal, target)
                        if c:
                            candidates.append(c)

            if in_test:
                ticket = _ticket(candidates, target)
                base_row = {
                    "date": d,
                    "requested_odds": round(target, 2),
                    "fixtures": len(fixtures),
                    "analyzed": modeled,
                    "truncated": False,
                    "deep_requests": 0,
                    "deep_cache_hits": 0,
                }
                if ticket is None:
                    daily.append({**base_row, "status": "NO_TICKET", "actual_odds": None, "legs": 0, "profit": 0.0, "market_mix": {}})
                else:
                    factor, legs = _settle(ticket)
                    if factor is None:
                        daily.append({**base_row, "status": "UNSETTLED", "actual_odds": ticket["odds"], "legs": len(ticket["legs"]), "profit": 0.0, "leg_results": legs, "market_mix": {"1X2": len(ticket["legs"])}})
                    else:
                        profit = bt.STAKE * (factor - 1.0)
                        status = "WIN" if profit > .005 else "LOSE" if profit < -.005 else "PUSH"
                        daily.append({
                            **base_row,
                            "status": status,
                            "actual_odds": ticket["odds"],
                            "legs": len(ticket["legs"]),
                            "estimated_probability": round(ticket["joint"] * 100, 2),
                            "expected_return": round(ticket["expected_return"], 4),
                            "profit": round(profit, 2),
                            "return_factor": round(factor, 4),
                            "leg_results": legs,
                            "market_mix": {"1X2": len(ticket["legs"])},
                        })

                with bt.JOBS_LOCK:
                    job = bt.JOBS.get(job_id)
                    if job:
                        job["current_day"] = d
                        job["progress"] = min(days, len(daily))
                        job["partial"] = _summary(days, target, daily, meta, cal)

            # Only now reveal today's outcomes to both calibration and team state.
            for f, model, prices, book in day_records:
                h, a = base._score(f)
                outcomes = (h > a, h == a, a > h)
                for market, mp, bp, won in zip(("1", "X", "2"), (model["1"], model["X"], model["2"]), book, outcomes):
                    cal.update(market, mp, bp, won)
            for f in fixtures:
                state.update(f)

        present = {x["date"] for x in daily}
        for i in range(days):
            d = (start_test + timedelta(days=i)).isoformat()
            if d not in present:
                daily.append({"date": d, "requested_odds": round(target, 2), "fixtures": 0, "analyzed": 0, "status": "NO_TICKET", "actual_odds": None, "legs": 0, "profit": 0.0, "market_mix": {}, "truncated": False, "deep_requests": 0, "deep_cache_hits": 0})
        daily.sort(key=lambda x: x["date"])
        result = {"summary": _summary(days, target, daily, meta, cal), "daily": list(reversed(daily))}
        with bt.JOBS_LOCK:
            bt.JOBS[job_id].update({"status": "done", "progress": days, "result": result, "partial": result["summary"], "finished_at": time.time(), "current_day": None})
    except Exception as exc:
        with bt.JOBS_LOCK:
            if job_id in bt.JOBS:
                bt.JOBS[job_id].update({"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:500]}", "finished_at": time.time()})


def install():
    bt._run = run_job
