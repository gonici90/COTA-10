import math
import os
from datetime import date
from itertools import combinations

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

app = FastAPI(title="COTA 10 Football Analyzer", version="4.3")
API_KEY = os.getenv("FOOTBALLDATA_API_KEY") or os.getenv("API_FOOTBALL_KEY", "")
API_BASE = "https://footballdata.io/api/v1"
MIN_COMBO_PROBABILITY = 60.0
MIN_RECOMMENDATION_PROBABILITY = 60.0
MIN_VALUE_EV = 3.0
MAX_MODEL_BOOK_GAP = 0.18
MAX_ACCEPTED_EV = 45.0


def pois(k, lam):
    return math.exp(-lam) * lam**k / math.factorial(k)


def market_probabilities(hl, al):
    grid = [[pois(h, hl) * pois(a, al) for a in range(11)] for h in range(11)]
    mass = sum(map(sum, grid)) or 1
    home = sum(grid[h][a] for h in range(11) for a in range(11) if h > a) / mass
    draw = sum(grid[h][h] for h in range(11)) / mass
    away = sum(grid[h][a] for h in range(11) for a in range(11) if h < a) / mass
    btts = (1 - math.exp(-hl)) * (1 - math.exp(-al))
    total = hl + al
    markets = {"1": home, "X": draw, "2": away, "1X": home + draw, "X2": away + draw,
               "12": home + away, "GG": btts, "NG": 1 - btts}
    for n in (1, 2, 3):
        under = sum(pois(k, total) for k in range(n + 1))
        markets[f"Over {n}.5"] = 1 - under
        markets[f"Under {n}.5"] = under
    return markets


async def api_get(path, params=None):
    if not API_KEY:
        raise HTTPException(500, "FOOTBALLDATA_API_KEY is not configured on Render")
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.get(API_BASE + path, params=params or {}, headers={"Authorization": f"Bearer {API_KEY}"})
    if r.status_code != 200:
        raise HTTPException(502, f"Footballdata.io returned HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    if data.get("success") is False:
        raise HTTPException(502, f"Footballdata.io error: {data.get('error', data)}")
    return data


def extract_matches(data):
    payload = data.get("data", [])
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("matches", "fixtures", "results"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def team_info(match, side):
    obj = match.get(f"{side}_team") or match.get(side) or {}
    return {"id": obj.get("team_id") or obj.get("id"), "name": obj.get("team_name") or obj.get("name") or "?"}


def score_values(match):
    score = match.get("score") or match.get("scores") or {}
    h = score.get("home") if isinstance(score, dict) else None
    a = score.get("away") if isinstance(score, dict) else None
    if h is None: h = match.get("home_score")
    if a is None: a = match.get("away_score")
    try: h = int(h) if h is not None else None
    except: h = None
    try: a = int(a) if a is not None else None
    except: a = None
    return h, a


async def recent_team_stats(team_id, before_day, venue, last=10):
    data = await api_get("/matches", {"team_id": team_id, "to": before_day, "limit": 40})
    games = []
    for g in extract_matches(data):
        h, a = score_values(g)
        if h is not None and a is not None:
            games.append(g)
    games.sort(key=lambda g: str(g.get("date") or g.get("match_date") or g.get("kickoff") or ""), reverse=True)
    overall = games[:last]
    venue_games = []
    for g in games:
        is_home = team_info(g, "home")["id"] == team_id
        if (venue == "home" and is_home) or (venue == "away" and not is_home):
            venue_games.append(g)
        if len(venue_games) >= 5: break

    def calc(sample):
        if not sample: return None
        gf = ga = ws = 0.0
        for i, g in enumerate(sample):
            h, a = score_values(g)
            is_home = team_info(g, "home")["id"] == team_id
            s, c = (h, a) if is_home else (a, h)
            w = .88 ** i
            gf += s * w; ga += c * w; ws += w
        return {"gf": gf / ws, "ga": ga / ws, "n": len(sample)}

    o, v = calc(overall), calc(venue_games)
    if not o: return {"played": 0, "gf": 1.2, "ga": 1.2, "venue_n": 0}
    gf = .65 * o["gf"] + .35 * v["gf"] if v else o["gf"]
    ga = .65 * o["ga"] + .35 * v["ga"] if v else o["ga"]
    return {"played": o["n"], "gf": gf, "ga": ga, "venue_n": v["n"] if v else 0}


def odds_map(data):
    payload = data.get("data") or {}
    odds = payload.get("odds") if isinstance(payload, dict) else {}
    if not isinstance(odds, dict): return {}
    out = {}
    def add(name, val):
        try:
            v = float(val)
            if v > 1: out[name] = v
        except: pass
    mw = odds.get("match_winner") or {}; add("1", mw.get("home")); add("X", mw.get("draw")); add("2", mw.get("away"))
    dc = odds.get("double_chance") or {}; add("1X", dc.get("home_or_draw")); add("12", dc.get("home_or_away")); add("X2", dc.get("draw_or_away"))
    bt = odds.get("both_teams_to_score") or {}; add("GG", bt.get("yes")); add("NG", bt.get("no"))
    tg = odds.get("total_goals") or {}
    for n in (1, 2, 3):
        add(f"Over {n}.5", tg.get(f"over_{n}_5")); add(f"Under {n}.5", tg.get(f"under_{n}_5"))
    return out


def sanity_market(p, book):
    if not book: return p, None, False, None
    implied = 1.0 / book
    suspicious = abs(p - implied) > MAX_MODEL_BOOK_GAP
    calibrated = max(.05, min(.95, .65 * p + .35 * implied))
    ev = ((calibrated * book) - 1) * 100
    if suspicious or ev > MAX_ACCEPTED_EV:
        return calibrated, ev, True, "model/bookmaker divergence"
    return calibrated, ev, False, None


def recommendation_score(market, confidence):
    """Safety first: probability dominates; EV is only a secondary bonus."""
    p = market["probability"]
    ev = market["ev"] if market["ev"] is not None else 0
    conf_bonus = 5 if confidence == "ridicată" else 2 if confidence == "medie" else -10
    odds = market["bookmaker_odds"] or market["fair_odds"]
    odds_penalty = max(0, odds - 3.0) * 4
    return p + min(max(ev, 0), 20) * .25 + conf_bonus - odds_penalty


async def analyze_fixture(f, day):
    home, away = team_info(f, "home"), team_info(f, "away")
    fid = f.get("match_id") or f.get("id")
    if not home["id"] or not away["id"]: raise ValueError("team_id lipsă")
    hs = await recent_team_stats(home["id"], day, "home")
    aws = await recent_team_stats(away["id"], day, "away")
    hl = max(.15, ((hs["gf"] + aws["ga"]) / 2) * 1.07)
    al = max(.15, ((aws["gf"] + hs["ga"]) / 2) * .96)
    sample = min(hs["played"], aws["played"])
    confidence = "ridicată" if sample >= 8 and hs["venue_n"] >= 3 and aws["venue_n"] >= 3 else "medie" if sample >= 5 else "scăzută"
    bookmaker = {}
    if fid:
        try: bookmaker = odds_map(await api_get(f"/matches/{fid}/odds"))
        except Exception: bookmaker = {}
    markets = []
    for name, p in market_probabilities(hl, al).items():
        if sample < 5: p = .75 * p + .25 * .5
        p = max(.05, min(.95, p)); book = bookmaker.get(name); raw_p = p
        p, ev, suspicious, reason = sanity_market(p, book)
        fair = 1 / p
        prob = round(p * 100, 1)
        value = bool(ev is not None and ev >= MIN_VALUE_EV and not suspicious and confidence != "scăzută")
        safe = bool(prob >= MIN_RECOMMENDATION_PROBABILITY and not suspicious and confidence != "scăzută")
        item = {"market": name, "probability": prob, "raw_probability": round(raw_p * 100, 1),
                "fair_odds": round(fair, 2), "bookmaker_odds": round(book, 2) if book else None,
                "bookmaker_implied": round(100 / book, 1) if book else None,
                "ev": round(ev, 1) if ev is not None else None, "value": value, "safe": safe,
                "suspicious": suspicious, "warning": reason}
        item["recommendation_score"] = round(recommendation_score(item, confidence), 1)
        markets.append(item)
    markets.sort(key=lambda x: (x["safe"], x["recommendation_score"], x["probability"]), reverse=True)
    safe_markets = [x for x in markets if x["safe"]]
    best_market = max(safe_markets, key=lambda x: x["recommendation_score"]) if safe_markets else max([x for x in markets if not x["suspicious"]] or markets, key=lambda x: x["probability"])
    value_markets = [x for x in markets if x["value"]]
    best_value = max(value_markets, key=lambda x: (x["ev"], x["probability"])) if value_markets else None
    league = f.get("league") or {}
    return {"fixture_id": fid, "league": league.get("name") or f.get("league_name") or "", "country": league.get("country") or f.get("country") or "",
            "home": home["name"], "away": away["name"], "home_xg": round(hl, 2), "away_xg": round(al, 2), "confidence": confidence,
            "markets": markets, "best_market": best_market, "best_value": best_value, "home_last": hs["played"], "away_last": aws["played"], "odds_available": bool(bookmaker)}


def build_target_combo(matches, target=10.0):
    candidates = []
    for m in matches:
        if m["confidence"] == "scăzută": continue
        choices = [x for x in m["markets"] if x["safe"] and not x["suspicious"] and x["probability"] >= MIN_COMBO_PROBABILITY and x["bookmaker_odds"]]
        if choices:
            best = max(choices, key=lambda x: (x["recommendation_score"], x["ev"] if x["ev"] is not None else -999))
            candidates.append({"home": m["home"], "away": m["away"], "selection": best["market"], "probability": best["probability"],
                               "odds": best["bookmaker_odds"], "fair_odds": best["fair_odds"], "ev": best["ev"], "value": best["value"], "score": best["recommendation_score"]})
    candidates = sorted(candidates, key=lambda x: (x["score"], x["probability"]), reverse=True)[:16]
    best = None
    for size in range(2, min(8, len(candidates)) + 1):
        for combo in combinations(candidates, size):
            product = math.prod(x["odds"] for x in combo)
            if product < target * .70: continue
            avg = sum(x["probability"] for x in combo) / size
            distance = abs(math.log(max(product, .01) / target))
            score = distance - avg / 600
            if best is None or score < best[0]: best = (score, product, combo)
    if not best: return None
    return {"combined_odds": round(best[1], 2), "matches": list(best[2]), "uses_bookmaker_odds": True}


@app.get("/health")
def health():
    return {"status": "ok", "provider": "footballdata.io", "api_key_configured": bool(API_KEY), "version": "4.3"}


@app.get("/api/analyze")
async def analyze(day: str = Query(default_factory=lambda: date.today().isoformat()), limit: int = 8, target: float = 10.0):
    raw = await api_get(f"/matches/date/{day}", {"limit": 100})
    fixtures = extract_matches(raw)
    upcoming = [f for f in fixtures if str(f.get("status", "")).lower() not in {"complete", "finished", "ft", "cancelled", "canceled", "postponed"}][:max(1, min(limit, 8))]
    results, errors = [], []
    for f in upcoming:
        try: results.append(await analyze_fixture(f, day))
        except Exception as exc:
            h, a = team_info(f, "home"), team_info(f, "away")
            errors.append({"fixture": f"{h['name']} - {a['name']}", "error": str(exc)[:250]})
            if "429" in str(exc): break
    results.sort(key=lambda x: (x["best_market"]["recommendation_score"], x["best_market"]["probability"]), reverse=True)
    return {"date": day, "market": "Safety + Value separated", "api_fixtures": len(fixtures), "eligible": len(upcoming), "analyzed": len(results),
            "analysis_errors": errors[:10], "target": target, "ranking": results, "suggested_combo": build_target_combo(results, target), "min_value_ev": MIN_VALUE_EV}


@app.get("/", response_class=HTMLResponse)
def home():
    return '''<!doctype html><html lang="ro"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>COTA 10</title><style>
body{font-family:system-ui;background:#0b0f14;color:#fff;margin:0;padding:24px}main{max-width:900px;margin:auto}.card{background:#151b23;border:1px solid #29313d;border-radius:16px;padding:18px;margin:14px 0}button{background:#35c46a;border:0;border-radius:10px;padding:13px 18px;font-weight:800}input{padding:12px;border-radius:9px;border:1px solid #394453;background:#0b0f14;color:white}small{color:#9ba8b7}.prob{font-size:24px;font-weight:800}.warn{color:#ffcc66}.good{color:#55df8b}.tag{display:inline-block;padding:4px 8px;border-radius:8px;background:#263241;margin:4px}.value{background:#174a32;color:#76f0aa}.sus{background:#503b19;color:#ffd27a}.safe{background:#173b4a;color:#8be2ff}.spec{color:#ffcc66}
</style></head><body><main><h1>COTA 10</h1><p>Recomandarea principală prioritizează probabilitatea și siguranța. VALUE este afișat separat.</p><div class="card"><input id="d" type="date"><input id="t" type="number" value="10" step="0.1"><button onclick="go()">Analizează</button></div><div id="out"></div></main><script>
document.getElementById('d').value=new Date().toISOString().slice(0,10);
async function go(){let o=document.getElementById('out');o.innerHTML='<p>Analizez...</p>';try{let r=await fetch('/api/analyze?day='+d.value+'&target='+t.value);let x=await r.json();if(!r.ok)throw new Error(x.detail||'Eroare');let h='<div class="card"><b>'+x.analyzed+' meciuri analizate</b><br><small>API: '+x.api_fixtures+' · eligibile: '+x.eligible+'</small></div>';
if(x.suggested_combo){let c=x.suggested_combo;h+='<h2>Bilet inteligent ~ cota '+t.value+'</h2><div class="card"><b>Cotă bookmaker combinată: '+c.combined_odds+'</b><br>'+c.matches.map(m=>'<b>'+m.home+' – '+m.away+'</b>: '+m.selection+' · '+m.probability+'% · @'+m.odds+(m.ev!==null?' · EV '+(m.ev>=0?'+':'')+m.ev+'%':'')).join('<br>')+'</div>'}else h+='<div class="card warn"><b>Nu există o combinație suficient de sigură pentru ținta aleasă.</b></div>';
h+='<h2>Analiza multi-piață</h2>';for(let m of x.ranking){let b=m.best_market;h+='<div class="card"><small>'+m.country+' · '+m.league+'</small><h3>'+m.home+' – '+m.away+'</h3><div class="prob">Recomandare: '+b.market+' · '+b.probability+'%</div><small>xG '+m.home_xg+' - '+m.away_xg+' · istoric '+m.home_last+'/'+m.away_last+' · încredere '+m.confidence+' · scor '+b.recommendation_score+'/100</small>';
if(m.best_value && m.best_value.market!==b.market)h+='<p class="spec"><b>Value agresiv:</b> '+m.best_value.market+' · '+m.best_value.probability+'% @'+m.best_value.bookmaker_odds+' · EV +'+m.best_value.ev+'%</p>';
h+='<div>'+m.markets.map(q=>'<span class="tag '+(q.suspicious?'sus':q.market===b.market?'safe':q.value?'value':'')+'">'+q.market+' '+q.probability+'%'+(q.bookmaker_odds?' @'+q.bookmaker_odds:'')+(q.ev!==null?' EV '+(q.ev>=0?'+':'')+q.ev+'%':'')+(q.suspicious?' ⚠ filtrat':'')+'</span>').join('')+'</div></div>'}o.innerHTML=h}catch(e){o.innerHTML='<div class="card warn"><b>Eroare:</b> '+e.message+'</div>'}}
</script></body></html>'''
