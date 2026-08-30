"""Practical API-Pro walk-forward backtest with empirical residual-bucket calibration.

The independent football model stays separate from Bet365.  Instead of requiring a
single global residual slope to be positive (v13, which could correctly but
unhelpfully shut betting down completely), this layer learns which *sizes* of
model-vs-market disagreement have historically been useful.  Calibration is
updated only after the whole day is predicted, so there is no look-ahead.
"""

import math
import time
from collections import defaultdict, deque
from datetime import timedelta
from itertools import combinations

import backtest_engine as bt
import backtest_pro_walkforward as base

# More leagues gives the calibrator enough summer + European coverage while still
# respecting Pro pacing and the existing range cache.
base.MAX_LEAGUES = max(base.MAX_LEAGUES, 16)

PRIOR_STRENGTH = 28.0
MIN_BUCKET_SAMPLES = 12
MAX_CORRECTION = 0.06


def _bucket(delta):
    d = float(delta)
    if d <= -0.10:
        return "<=-10"
    if d <= -0.05:
        return "-10:-5"
    if d <= -0.02:
        return "-5:-2"
    if d < 0.02:
        return "-2:+2"
    if d < 0.05:
        return "+2:+5"
    if d < 0.10:
        return "+5:+10"
    return ">=+10"


class BucketCalibrator:
    def __init__(self):
        self.rows = defaultdict(lambda: deque(maxlen=1200))
        self.global_rows = defaultdict(lambda: deque(maxlen=3000))

    def update(self, market, model_p, book_p, won):
        key = (str(market), _bucket(float(model_p) - float(book_p)))
        row = (float(book_p), 1.0 if won else 0.0)
        self.rows[key].append(row)
        self.global_rows[key[1]].append(row)

    @staticmethod
    def _estimate(rows, current_book):
        rows = list(rows)
        n = len(rows)
        if not n:
            return float(current_book), 0, 0.0
        avg_book = sum(x[0] for x in rows) / n
        wins = sum(x[1] for x in rows)
        # Bayesian posterior centered on the bookmaker probability.  We estimate
        # only the historical *correction* for this residual bucket, not a fresh
        # probability from a tiny sample.
        posterior = (wins + PRIOR_STRENGTH * avg_book) / (n + PRIOR_STRENGTH)
        correction = posterior - avg_book
        correction = max(-MAX_CORRECTION, min(MAX_CORRECTION, correction))
        q = max(0.01, min(0.99, float(current_book) + correction))
        return q, n, correction

    def probability(self, market, model_p, book_p):
        b = _bucket(float(model_p) - float(book_p))
        local = self.rows[(str(market), b)]
        if len(local) >= MIN_BUCKET_SAMPLES:
            q, n, corr = self._estimate(local, book_p)
            return q, n, corr, b, "market"
        global_rows = self.global_rows[b]
        if len(global_rows) >= MIN_BUCKET_SAMPLES:
            q, n, corr = self._estimate(global_rows, book_p)
            return q, n, corr, b, "pooled"
        # Before a bucket matures, stay very close to the market but allow a tiny,
        # heavily-shrunk contribution from the independent model instead of
        # forbidding every bet.
        d = max(-0.03, min(0.03, float(model_p) - float(book_p)))
        q = max(0.01, min(0.99, float(book_p) + 0.08 * d))
        return q, len(global_rows), q - float(book_p), b, "cold"

    def diagnostics(self):
        out = {}
        for b in ("<=-10", "-10:-5", "-5:-2", "-2:+2", "+2:+5", "+5:+10", ">=+10"):
            rows = self.global_rows[b]
            if rows:
                avg_book = sum(x[0] for x in rows) / len(rows)
                hit = sum(x[1] for x in rows) / len(rows)
                out[b] = {
                    "samples": len(rows),
                    "book": round(avg_book * 100, 1),
                    "actual": round(hit * 100, 1),
                    "correction_pp": round((hit - avg_book) * 100, 1),
                }
        return out


def _candidate(market, model_p, odd, book_p, fixture, cal, target):
    q, sample, corr, bucket, source = cal.probability(market, model_p, book_p)
    edge = q - float(book_p)
    ev = q * float(odd) - 1.0
    raw_edge = float(model_p) - float(book_p)

    # A mature negative bucket is rejected naturally.  For usable candidates we
    # demand a real post-calibration advantage, but not the v13 all-or-nothing
    # global slope gate.
    if edge < 0.0025 or ev < 0.004:
        return None
    if target <= 1.60:
        if not (1.10 <= odd <= 1.75 and q >= .60):
            return None
    elif target <= 2.30:
        if not (1.10 <= odd <= 2.20 and q >= .50):
            return None
    else:
        if not (1.10 <= odd <= 2.60 and q >= .46):
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
        "cal_sample": sample,
        "cal_correction": corr,
        "cal_bucket": bucket,
        "cal_source": source,
        "fixture": fixture,
        "fixture_id": fixture.get("id") or fixture.get("fixture_id"),
    }


def _ticket(candidates, target):
    target = float(target)
    if target <= 1.60:
        lo, hi, maxlegs, min_return = target * .95, target * 1.05, 2, 1.004
    elif target <= 2.30:
        lo, hi, maxlegs, min_return = target * .95, target * 1.05, 3, 1.006
    else:
        lo, hi, maxlegs, min_return = target * .92, target * 1.08, 5, 1.008

    byfix = defaultdict(list)
    for c in candidates:
        byfix[c["fixture_id"]].append(c)
    pool = []
    for opts in byfix.values():
        opts.sort(key=lambda x: (x["ev"], x["edge"], x["prob"]), reverse=True)
        pool.append(opts[0])
    pool.sort(key=lambda x: (x["ev"], x["edge"], x["prob"]), reverse=True)
    pool = pool[:30]

    best = None
    for z in range(1, min(maxlegs, len(pool)) + 1):
        for combo in combinations(pool, z):
            odd = math.prod(x["odd"] for x in combo)
            if not (lo <= odd <= hi):
                continue
            joint = math.prod(x["prob"] for x in combo)
            expected_return = joint * odd
            if expected_return < min_return:
                continue
            # Prefer positive calibrated expectation, then safer joint probability,
            # then closeness to the requested final price.
            score = (
                (expected_return - 1.0) * 2.0
                + joint * .04
                - abs(math.log(odd / target)) * .02
                - max(0, z - 2) * .002
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
            "bucket": c["cal_bucket"],
            "bucket_sample": c["cal_sample"],
            "calibration_source": c["cal_source"],
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
    avg = sum(odds) / len(odds) if odds else None
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
        "mode": "Pro independent + residual buckets v14",
        "note": "Model independent + calibrare walk-forward pe bucket-uri ale diferentei fata de Bet365. Fara look-ahead. Nu mai exista gate-ul global care putea bloca 100% din bilete.",
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
        cal = BucketCalibrator()
        daily = []

        for d in sorted(byday):
            fixtures = byday[d]
            in_test = start_test.isoformat() <= d <= end_day.isoformat()
            day_records = []
            candidates = []
            modeled = 0

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

            # Reveal outcomes only after all predictions for the day were made.
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
