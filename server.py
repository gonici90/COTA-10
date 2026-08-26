import json
from pathlib import Path

import app as core
from fastapi import Query
from fastapi.responses import HTMLResponse
from ticket_engine import router

app = core.app

# Cache EVERY read-only match request, including odds.
core._cacheable = lambda path: path.startswith('/matches')

ANALYSIS_CACHE = Path(core.CACHE_DIR) / 'live-analysis'
ANALYSIS_CACHE.mkdir(parents=True, exist_ok=True)

_old_analyze_route = next((r for r in app.router.routes if getattr(r, 'path', None) == '/api/analyze'), None)
_old_analyze = _old_analyze_route.endpoint if _old_analyze_route else None
if _old_analyze_route:
    app.router.routes.remove(_old_analyze_route)

@app.get('/api/analyze')
async def analyze_cached(day: str = Query(default_factory=lambda: core.date.today().isoformat()), limit: int = 8, target: float = 10.0):
    limit = max(1, min(int(limit), 8))
    cache_file = ANALYSIS_CACHE / f'{day}-{limit}.json'
    base = None
    if cache_file.exists():
        try: base = json.loads(cache_file.read_text(encoding='utf-8'))
        except Exception: base = None
    if base is None:
        base = await _old_analyze(day=day, limit=limit, target=10.0)
        base = dict(base); base.pop('suggested_combo', None)
        try: cache_file.write_text(json.dumps(base, ensure_ascii=False), encoding='utf-8')
        except Exception: pass
    result = dict(base)
    result['target'] = target
    result['suggested_combo'] = core.build_target_combo(result.get('ranking', []), target)
    result['cache_reused'] = cache_file.exists()
    return result

# Offline backtest API.
app.router.routes[:] = [r for r in app.router.routes if getattr(r, 'path', None) != '/api/backtest']
app.include_router(router)

# Extend the existing UI without duplicating the large HTML page from app.py.
_old_home_route = next((r for r in app.router.routes if getattr(r, 'path', None) == '/'), None)
_old_home = _old_home_route.endpoint if _old_home_route else None
if _old_home_route:
    app.router.routes.remove(_old_home_route)

@app.get('/', response_class=HTMLResponse)
def home_extended():
    base = _old_home()
    html = base.body.decode('utf-8') if hasattr(base, 'body') else str(base)
    marker = '<button onclick="bt(90)">Backtest 90 zile</button>'
    extra = marker + '<br><button onclick="ticketBt(2)">90 zile · COTA 2</button><button onclick="ticketBt(5)">90 zile · COTA 5</button><button onclick="ticketBt(10)">90 zile · COTA 10</button>'
    html = html.replace(marker, extra, 1)
    js = r'''async function ticketBt(target){let o=out;o.innerHTML='<p>Simulez biletele COTA '+target+' pe 90 zile...</p>';try{let r=await fetch('/api/backtest?days=90&per_day=100'),x=await r.json();if(!r.ok)throw Error(x.detail||'Eroare');let b=(x.ticket_backtests||{})[String(target)];if(!b)throw Error('Backtestul de bilete nu este disponibil');let h='<h2>Backtest 90 zile · COTA '+target+'</h2><div class="card"><div class="prob">'+val(b.hit_rate,0)+'% bilete câștigate</div><b>'+val(b.wins,0)+' câștigate / '+val(b.tickets,0)+' bilete</b><br>Cotă medie '+val(b.avg_odds,0)+' · ROI '+val(b.roi,0)+'% · profit '+val(b.profit,0)+' unități</div>';h+='<h2>Ultimele bilete</h2>';h+=(b.recent_tickets||[]).map(t=>'<div class="card"><small>'+t.date+' · '+t.legs+' selecții</small><br><b>Cotă '+t.odds+' · <span class="'+(t.won?'good':'bad')+'">'+(t.won?'CÂȘTIGAT':'PIERDUT')+'</span></b><br>'+t.selections.map(s=>s.match+': '+s.market+' @'+s.odds+' '+(s.won?'✓':'✗')).join('<br>')+'</div>').join('');if(!b.tickets)h+='<div class="card warn">Nu s-au putut construi bilete suficient de apropiate de COTA '+target+' în această perioadă.</div>';o.innerHTML=h}catch(e){o.innerHTML='<div class="card warn">Eroare backtest COTA '+target+': '+e.message+'</div>'}}'''
    html = html.replace('</script>', js + '</script>', 1)
    return HTMLResponse(html)
