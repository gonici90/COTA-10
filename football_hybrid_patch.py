"""Hybrid football enrichment for 5DollarFootballAPI Pro.

Fast path: scan all fixtures with include=odds bulk data.
Deep path: fetch full Bet365 odds only for a small shortlist so Goals/AH/BTTS/
Corners/Cards return without going back to one request per every fixture.
"""
import time
from datetime import datetime, timedelta


DETAIL_LIMIT_SHORT = 4
DETAIL_LIMIT_LONG = 3


def install(engine, fd):
    def _fixture_key(f):
        fid = f.get("id") or f.get("fixture_id")
        h, a = engine._teams(f)
        return fid or (h.get("name"), a.get("name"), engine._kickoff_ts(f))

    def _full_odds(f):
        fid = f.get("id") or f.get("fixture_id")
        if not fid:
            return None

        cached = engine._ODDS_CACHE.get(fid)
        if cached and time.time() - cached[0] < engine.ODDS_CACHE_TTL and engine._has_prices(cached[1]):
            return cached[1]

        try:
            raw = fd._get(f"/fixtures/{fid}/odds", {"bookmakers": "bet365"})
            odds = engine._normalize_odds(raw)
        except Exception:
            return None

        if not engine._has_prices(odds):
            return None
        engine._ODDS_CACHE[fid] = (time.time(), odds)
        return odds

    def analyze_period(day, target=10, days=1, limit=200):
        days = max(1, min(int(days), 7))
        start = datetime.fromisoformat(day).date()
        fs, by_day, seen = [], {}, set()

        for i in range(days):
            d = (start + timedelta(days=i)).isoformat()
            got = engine._day_fixtures(d)
            by_day[d] = len(got)
            for f in got:
                if not isinstance(f, dict):
                    continue
                key = _fixture_key(f)
                if key not in seen:
                    seen.add(key)
                    fs.append(f)

        attempt = min(len(fs), max(1, min(int(limit), 200)))
        bulk_rows = []
        no_rows = []
        errors = []
        fixture_by_key = {}

        for f in fs[:attempt]:
            fixture_by_key[_fixture_key(f)] = f
            try:
                row = engine.analyze_fixture(f)
                if row.get("best_market"):
                    bulk_rows.append(row)
                else:
                    no_rows.append(row)
            except Exception as exc:
                h, a = engine._teams(f)
                errors.append({
                    "fixture": f.get("id") or f.get("fixture_id"),
                    "match": h.get("name", "?") + " - " + a.get("name", "?"),
                    "error": type(exc).__name__ + ": " + str(exc)[:180],
                })

        # Pick the safest bulk candidates first. Rows with only 1X2 are exactly the
        # ones that benefit most from a deep odds request.
        def score(row):
            best = row.get("best_market") or {}
            prob = float(best.get("ticket_probability") or best.get("probability") or 0)
            few_markets = 1 if len(row.get("markets") or []) <= 3 else 0
            return (few_markets, prob, float(best.get("recommendation_score") or 0))

        bulk_rows.sort(key=score, reverse=True)
        detail_limit = DETAIL_LIMIT_SHORT if days <= 3 else DETAIL_LIMIT_LONG
        shortlist = []
        shortlisted = set()

        # Most promising current rows.
        for row in bulk_rows:
            if len(shortlist) >= detail_limit:
                break
            key = row.get("fixture_id") or (row.get("home"), row.get("away"), row.get("kickoff"))
            f = fixture_by_key.get(row.get("fixture_id"))
            if f is None:
                # Fallback lookup because fixture_by_key may use a composite key.
                f = next((x for x in fs[:attempt] if (x.get("id") or x.get("fixture_id")) == row.get("fixture_id")), None)
            if f is not None and _fixture_key(f) not in shortlisted:
                shortlist.append(f)
                shortlisted.add(_fixture_key(f))

        # Reserve one slot for a fixture that had no usable bulk prices; full odds can
        # recover totals/AH/BTTS even when 1X2 is absent/incomplete.
        if detail_limit and no_rows:
            candidate = None
            for row in no_rows:
                f = next((x for x in fs[:attempt] if (x.get("id") or x.get("fixture_id")) == row.get("fixture_id")), None)
                if f is not None and _fixture_key(f) not in shortlisted:
                    candidate = f
                    break
            if candidate is not None:
                if len(shortlist) >= detail_limit:
                    shortlist[-1] = candidate
                else:
                    shortlist.append(candidate)

        # Replace bulk analysis only for shortlisted fixtures with full-market analysis.
        replacements = {}
        enriched = 0
        for f in shortlist:
            odds = _full_odds(f)
            if not odds:
                continue
            ef = dict(f)
            ef.pop("_pro_bulk_odds", None)
            ef["odds"] = odds
            try:
                row = engine.analyze_fixture(ef)
            except Exception:
                continue
            if row.get("best_market"):
                replacements[row.get("fixture_id") or _fixture_key(f)] = row
                enriched += 1

        rows = []
        used = set()
        for row in bulk_rows:
            key = row.get("fixture_id") or (row.get("home"), row.get("away"), row.get("kickoff"))
            new_row = replacements.get(key, row)
            rows.append(new_row)
            used.add(key)
        for key, row in replacements.items():
            if key not in used:
                rows.append(row)

        rows.sort(key=lambda x: (x.get("best_market") or {}).get("recommendation_score", 0), reverse=True)
        combo, combo_diag = engine.build_combo(rows, target)

        return {
            "date": day,
            "days": days,
            "period_end": (start + timedelta(days=days - 1)).isoformat(),
            "provider": "5DollarFootballAPI Pro hybrid + Bet365",
            "fixtures_by_day": by_day,
            "api_fixtures": len(fs),
            "eligible": len(fs),
            "attempted": attempt,
            "analyzed": len(rows),
            "without_usable_odds": max(0, attempt - len(rows)),
            "no_odds_examples": [
                {"fixture": x.get("fixture_id"), "match": str(x.get("home", "?")) + " - " + str(x.get("away", "?"))}
                for x in no_rows[:20]
            ],
            "analysis_errors": errors,
            "ranking": rows,
            "suggested_combo": combo,
            "combo_diagnostics": combo_diag,
            "hybrid": {
                "bulk_scan": attempt,
                "deep_odds_requested": len(shortlist),
                "deep_odds_enriched": enriched,
                "deep_limit": detail_limit,
            },
        }

    engine.analyze_period = analyze_period
    engine.analyze_day = lambda day, target=10, limit=12: analyze_period(day, target, 1, limit)
