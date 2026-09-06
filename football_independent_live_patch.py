"""Odds-independent LIVE football model.

Prediction is built ONLY from finished-match history: recent form, home/away
scoring/defending, Elo and a small H2H component. Bookmaker prices are read only
after probabilities exist, for price/EV and ticket construction.
"""
from collections import defaultdict, deque
from datetime import date, timedelta
import math

import backtest_pro_walkforward as hist

LOOKBACK_DAYS = 240
MAX_LEAGUES = 18


def _history(end_day):
    old = hist.MAX_LEAGUES
    try:
        hist.MAX_LEAGUES = max(old, MAX_LEAGUES)
        meta = hist._load("live-independent", min(60, hist.TEST_MAX_DAYS))
    finally:
        hist.MAX_LEAGUES = old
    cutoff = end_day - timedelta(days=LOOKBACK_DAYS)
    rows = []
    for f in meta.get("matches") or []:
        d = hist._day(f)
        if d and cutoff.isoformat() <= d < end_day.isoformat() and hist._score(f)[0] is not None:
            rows.append(f)
    return rows


def _build_state(rows):
    state = hist.State()
    h2h = defaultdict(lambda: deque(maxlen=8))
    for f in sorted(rows, key=lambda x: (hist._day(x) or "", x.get("kickoff_ts") or 0)):
        h, a = hist._score(f); home, away = hist._teams(f)
        if home and away and h is not None:
            h2h[(home.lower(), away.lower())].append((h, a))
            h2h[(away.lower(), home.lower())].append((a, h))
        state.update(f)
    return state, h2h


def _h2h_adjust(model, home, away, h2h):
    rows = list(h2h.get((home.lower(), away.lower())) or [])
    if len(rows) < 2:
        return model, 0
    pts = sum(3 if h > a else 1 if h == a else 0 for h, a in rows) / (3.0 * len(rows))
    # H2H is intentionally small: max +/- 4 percentage points transferred between sides.
    shift = max(-.04, min(.04, (pts - .5) * .08))
    out = dict(model)
    out["1"] = max(.02, out["1"] + shift)
    out["2"] = max(.02, out["2"] - shift)
    z = out["1"] + out["X"] + out["2"]
    out["1"], out["X"], out["2"] = out["1"]/z, out["X"]/z, out["2"]/z
    return out, len(rows)


def install(engine):
    original_period = engine.analyze_period

    def analyze_period(day, target=10, days=1, limit=200):
        # Let existing provider layer fetch fixtures and REAL prices only.
        result = original_period(day, target, days, limit)
        start = date.fromisoformat(day)
        state, h2h = _build_state(_history(start))
        ranking = []

        # Re-score every already-fetched fixture independently of bookmaker odds.
        # Existing rows are used only as carriers for fixture identity + quoted markets.
        for row in result.get("ranking") or []:
            home, away = str(row.get("home") or ""), str(row.get("away") or "")
            fixture = None
            # State.predict needs the original fixture shape; reconstruct the minimal shape.
            fixture = {"teams":{"home":{"name":home},"away":{"name":away}}, "league": row.get("league") or {}}
            model = state.predict(fixture)
            if model is None:
                continue
            model, h2hn = _h2h_adjust(model, home, away, h2h)
            nr = dict(row)
            nr["independent_model"] = {
                "home": round(model["1"]*100,1), "draw": round(model["X"]*100,1), "away": round(model["2"]*100,1),
                "expected_goals_home": round(model["lh"],2), "expected_goals_away": round(model["la"],2),
                "h2h_matches": h2hn, "uses_odds_for_prediction": False,
            }
            # Replace probabilities of quoted 1X2 markets with independent probabilities.
            markets=[]
            for m in nr.get("markets") or []:
                q=dict(m); name=str(q.get("selection") or q.get("market") or q.get("name") or "")
                key = "1" if name in {"1","Home","home"} else "X" if name in {"X","Draw","draw"} else "2" if name in {"2","Away","away"} else None
                if key:
                    p=model[key]; odd=float(q.get("odds") or q.get("odd") or 0)
                    q["probability"]=round(p*100,1); q["ticket_probability"]=p
                    q["ev"]=round(p*odd-1,4) if odd>1 else None
                    q["prediction_source"]="FORM+HOME/AWAY+GOALS+ELO+H2H"
                markets.append(q)
            nr["markets"]=markets
            # Do not trust the old best_market because its probability was bookmaker-anchored.
            usable=[m for m in markets if m.get("prediction_source") and float(m.get("ev") or -9)>0]
            usable.sort(key=lambda m:(float(m.get("ticket_probability") or 0),float(m.get("ev") or 0)), reverse=True)
            nr["best_market"]=usable[0] if usable else None
            if nr["best_market"]:
                ranking.append(nr)

        ranking.sort(key=lambda r:(r.get("best_market") or {}).get("ticket_probability",0), reverse=True)
        combo, diag = engine.build_combo(ranking, target)
        result["ranking"] = ranking
        result["analyzed"] = len(ranking)
        result["suggested_combo"] = combo
        result["combo_diagnostics"] = diag
        result["prediction_core"] = "ODDS-INDEPENDENT: form + home/away + goals + Elo + H2H; odds only for EV/price"
        return result

    engine.analyze_period = analyze_period
    engine.analyze_day = lambda day, target=10, limit=12: analyze_period(day, target, 1, limit)
