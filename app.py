import math
import os
from datetime import date
from itertools import combinations

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

app = FastAPI(title="COTA 10 Football Analyzer", version="1.2")
API_KEY = os.getenv("API_FOOTBALL_KEY", "")
API_BASE = "https://v3.football.api-sports.io"


def poisson_over_25(expected_goals: float) -> float:
    p_under = sum(math.exp(-expected_goals) * expected_goals**k / math.factorial(k) for k in range(3))
    return max(0.0, min(1.0, 1.0 - p_under))


async def api_get_full(path: str, params: dict):
    if not API_KEY:
        raise HTTPException(500, "API_FOOTBALL_KEY is not configured on Render")
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.get(API_BASE + path, params=params, headers={"x-apisports-key": API_KEY})
    if r.status_code != 200:
        raise HTTPException(502, f"API-Football returned HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    if data.get("errors"):
        raise HTTPException(502, f"API-Football error: {data['errors']}")
    return data


async def api_get(path: str, params: dict):
    return (await api_get_full(path, params)).get("response", [])


async def recent_team_stats(team_id: int, before_day: str, last: int = 8):
    # Free API-Football plans reject the `last` parameter. Fetch a bounded
    # historical date range instead and keep the newest completed fixtures.
    year = int(before_day[:4])
    games = await api_get("/fixtures", {"team": team_id, "from": f"{year-1}-01-01", "to": before_day})
    completed = []
    for g in games:
        goals = g.get("goals", {})
        if goals.get("home") is None or goals.get("away") is None:
            continue
        status = g.get("fixture", {}).get("status", {}).get("short")
        if status in {"NS", "TBD", "PST", "CANC", "ABD"}:
            continue
        completed.append(g)
    completed.sort(key=lambda g: g.get("fixture", {}).get("timestamp", 0), reverse=True)
    games = completed[:last]
    scored = conceded = played = over25 = 0
    for g in games:
        h, a = g["goals"]["home"], g["goals"]["away"]
        home_id = g["teams"]["home"]["id"]
        gf, ga = (h, a) if home_id == team_id else (a, h)
        scored += gf
        conceded += ga
        played += 1
        over25 += int(h + a >= 3)
    if not played:
        return {"played": 0, "gf": 1.2, "ga": 1.2, "over25_rate": 0.5}
    return {"played": played, "gf": scored / played, "ga": conceded / played, "over25_rate": over25 / played}


async def analyze_fixture(f, day: str):
    home = f["teams"]["home"]
    away = f["teams"]["away"]
    hs = await recent_team_stats(home["id"], day)
    aws = await recent_team_stats(away["id"], day)
    home_xg = (hs["gf"] + aws["ga"]) / 2
    away_xg = (aws["gf"] + hs["ga"]) / 2
    expected = max(0.4, min(5.5, home_xg + away_xg))
    poisson = poisson_over_25(expected)
    form_rate = (hs["over25_rate"] + aws["over25_rate"]) / 2
    probability = max(0.05, min(0.95, 0.70 * poisson + 0.30 * form_rate))
    return {"fixture_id": f["fixture"]["id"], "kickoff": f["fixture"]["date"], "league": f["league"]["name"], "country": f["league"]["country"], "home": home["name"], "away": away["name"], "expected_goals": round(expected, 2), "over25_probability": round(probability * 100, 1), "fair_odds": round(1 / probability, 2), "home_last": hs["played"], "away_last": aws["played"]}


def build_target_combo(matches, target=10.0):
    candidates = sorted(matches, key=lambda x: x["over25_probability"], reverse=True)[:16]
    best = None
    for size in range(2, min(8, len(candidates)) + 1):
        for combo in combinations(candidates, size):
            product = math.prod(x["fair_odds"] for x in combo)
            distance = abs(math.log(max(product, .01) / target))
            confidence = sum(x["over25_probability"] for x in combo) / size
            score = distance - confidence / 1000
            if best is None or score < best[0]: best = (score, product, combo)
    if not best: return None
    return {"estimated_combined_fair_odds": round(best[1], 2), "matches": list(best[2])}


@app.get("/health")
def health():
    return {"status": "ok", "api_key_configured": bool(API_KEY), "version": "1.2"}


@app.get("/api/analyze")
async def analyze(day: str = Query(default_factory=lambda: date.today().isoformat()), limit: int = 25, target: float = 10.0):
    raw = await api_get_full("/fixtures", {"date": day})
    fixtures = raw.get("response", [])
    status_counts = {}
    for f in fixtures:
        s = f.get("fixture", {}).get("status", {}).get("short", "UNKNOWN")
        status_counts[s] = status_counts.get(s, 0) + 1
    upcoming = [f for f in fixtures if f.get("fixture", {}).get("status", {}).get("short") in {"NS", "TBD", "PST"}][:max(1, min(limit, 40))]
    results, errors = [], []
    for f in upcoming:
        try: results.append(await analyze_fixture(f, day))
        except Exception as exc: errors.append({"fixture": f"{f.get('teams',{}).get('home',{}).get('name','?')} - {f.get('teams',{}).get('away',{}).get('name','?')}", "error": str(exc)[:250]})
    results.sort(key=lambda x: x["over25_probability"], reverse=True)
    return {"date": day, "market": "Over 2.5 goals", "api_fixtures": len(fixtures), "status_counts": status_counts, "eligible": len(upcoming), "analyzed": len(results), "analysis_errors": errors[:10], "api_paging": raw.get("paging", {}), "target": target, "ranking": results, "suggested_combo": build_target_combo(results, target)}


@app.get("/", response_class=HTMLResponse)
def home():
    return '''<!doctype html><html lang="ro"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>COTA 10</title><style>body{font-family:system-ui;background:#0b0f14;color:#fff;margin:0;padding:24px}main{max-width:900px;margin:auto}.card{background:#151b23;border:1px solid #29313d;border-radius:16px;padding:18px;margin:14px 0}button{background:#35c46a;border:0;border-radius:10px;padding:13px 18px;font-weight:800}input{padding:12px;border-radius:9px;border:1px solid #394453;background:#0b0f14;color:white}small{color:#9ba8b7}.prob{font-size:24px;font-weight:800}.warn{color:#ffcc66}</style></head><body><main><h1>COTA 10</h1><p>Analizor statistic pentru Over 2.5 goluri. Estimările nu garantează rezultate.</p><div class="card"><input id="d" type="date"><input id="t" type="number" value="10" step="0.1"><button onclick="go()">Analizează</button></div><div id="out"></div></main><script>document.getElementById('d').value=new Date().toISOString().slice(0,10);async function go(){let o=document.getElementById('out');o.innerHTML='<p>Analizez meciurile...</p>';try{let r=await fetch('/api/analyze?day='+d.value+'&target='+t.value);let x=await r.json();if(!r.ok)throw new Error(x.detail||'Eroare');let h='<div class="card"><b>'+x.analyzed+' meciuri analizate</b><br><small>API: '+x.api_fixtures+' meciuri · eligibile: '+x.eligible+' · statusuri: '+JSON.stringify(x.status_counts)+'</small></div>';if(x.analysis_errors&&x.analysis_errors.length){h+='<div class="card warn"><b>Diagnostic:</b> '+x.analysis_errors.length+' erori la analiză<br><small>'+x.analysis_errors.map(e=>e.fixture+': '+e.error).join('<br>')+'</small></div>'}if(x.suggested_combo){h+='<h2>Combinație țintă ~ cota '+t.value+'</h2><div class="card"><b>Cotă fair estimată: '+x.suggested_combo.estimated_combined_fair_odds+'</b></div>'}h+='<h2>Clasament Over 2.5</h2>';for(let m of x.ranking){h+='<div class="card"><small>'+m.country+' · '+m.league+'</small><h3>'+m.home+' – '+m.away+'</h3><div class="prob">'+m.over25_probability+'%</div><small>xG total estimat '+m.expected_goals+' · cotă fair '+m.fair_odds+' · istoric '+m.home_last+'/'+m.away_last+'</small></div>'}o.innerHTML=h}catch(e){o.innerHTML='<div class="card warn"><b>Eroare:</b> '+e.message+'</div>'}}</script></body></html>'''
