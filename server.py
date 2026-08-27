import app as core
import market_engine
from fastapi import Query
from fastapi.responses import HTMLResponse
from ticket_engine import router
from auto_data import router as data_router

app = core.app
core._cacheable = lambda path: path.startswith('/matches')

_old_analyze_route = next((r for r in app.router.routes if getattr(r, 'path', None) == '/api/analyze'), None)
if _old_analyze_route:
    app.router.routes.remove(_old_analyze_route)

@app.get('/api/analyze')
async def analyze_multimarket(day: str = Query(default_factory=lambda: core.date.today().isoformat()), limit: int = 12, target: float = 10.0):
    return market_engine.analyze_day(day=day, target=target, limit=limit)

app.router.routes[:] = [r for r in app.router.routes if getattr(r, 'path', None) != '/api/backtest']
app.include_router(router)
app.include_router(data_router)

_old_home_route = next((r for r in app.router.routes if getattr(r, 'path', None) == '/'), None)
_old_home = _old_home_route.endpoint if _old_home_route else None
if _old_home_route:
    app.router.routes.remove(_old_home_route)

@app.get('/', response_class=HTMLResponse)
def home_extended():
    base = _old_home()
    html = base.body.decode('utf-8') if hasattr(base, 'body') else str(base)
    marker = '<button onclick="bt(90)">Backtest 90 zile</button>'
    extra = marker + '<br><button onclick="ticketBt(1.5,90)">90 zile · COTA 1.50</button><button onclick="ticketBt(2,90)">90 zile · COTA 2</button><button onclick="ticketBt(5,90)">90 zile · COTA 5</button><button onclick="ticketBt(10,90)">90 zile · COTA 10</button><br><button onclick="ticketBt(1.5,180)">180 zile · COTA 1.50</button><button onclick="ticketBt(1.5,365)">365 zile · COTA 1.50</button><br><button onclick="syncData()">Actualizează date</button>'
    html = html.replace(marker, extra, 1)
    js = r'''async function syncData(){let o=out;o.innerHTML='<p>Actualizez baza 5Dollar...</p>';try{let r=await fetch('/api/data/sync?days=7&force=true',{method:'POST'}),x=await r.json();if(!r.ok)throw Error(x.detail||'Eroare');o.innerHTML='<div class="card"><div class="prob">Date 5Dollar actualizate</div><b>'+val(x.matches,0)+' meciuri</b> · '+val(x.finished,0)+' terminate<br><small>'+val(x.market_snapshots,0)+' piețe stocate · '+val(x.api_days_fetched,0)+' zile din API</small></div>'}catch(e){o.innerHTML='<div class="card warn">Eroare actualizare: '+e.message+'</div>'}}
async function ticketBt(target,days=90){let o=out;o.innerHTML='<p>Simulez biletele COTA '+target+' pe '+days+' zile...</p>';try{let r=await fetch('/api/backtest?days='+days+'&per_day=100'),x=await r.json();if(!r.ok)throw Error(x.detail||'Eroare');let b=(x.ticket_backtests||{})[String(target)],c=x.coverage||{};if(!b)throw Error('Backtestul de bilete nu este disponibil');let h='<h2>Backtest '+days+' zile · COTA '+target+'</h2><div class="card"><div class="prob">'+val(b.hit_rate,0)+'% bilete câștigate</div><b>'+val(b.wins,0)+' câștigate / '+val(b.tickets,0)+' bilete</b><br>Cotă medie '+val(b.avg_odds,0)+' · ROI '+val(b.roi,0)+'% · profit '+val(b.profit,0)+' unități</div>';h+='<h2>Acoperire reală</h2><div class="card"><b>Interval cerut:</b> '+(c.requested_start||'?')+' → '+(c.dataset_end||'?')+'<br><b>Date efectiv disponibile:</b> '+(c.actual_start||'—')+' → '+(c.actual_end||'—')+'<br><b>Zile calendaristice acoperite:</b> '+val(c.calendar_days_covered,0)+' / '+val(c.days_requested,days)+'<br><b>Zile cu meciuri:</b> '+val(c.match_days_available,0)+' · <b>Meciuri:</b> '+val(c.fixtures_found,0)+'<br><b>Meciuri analizabile:</b> '+val(c.fixtures_analyzed,0)+' · <b>Selecții:</b> '+val(c.selections,0)+'<br><b>Zile cu selecție:</b> '+val(c.days_with_selection,0)+(c.full_requested_window?'':'<br><span class="warn">ATENȚIE: datasetul nu acoperă integral perioada cerută.</span>')+'</div>';h+='<h2>Ultimele bilete</h2>';h+=(b.recent_tickets||[]).map(t=>'<div class="card"><small>'+t.date+' · '+t.legs+' selecții</small><br><b>Cotă '+t.odds+' · <span class="'+(t.won?'good':'bad')+'">'+(t.won?'CÂȘTIGAT':'PIERDUT')+'</span></b><br>'+t.selections.map(s=>s.match+': '+s.market+' @'+s.odds+' '+(s.won?'✓':'✗')).join('<br>')+'</div>').join('');if(!b.tickets)h+='<div class="card warn">Nu s-au putut construi bilete suficient de apropiate de COTA '+target+' în această perioadă.</div>';o.innerHTML=h}catch(e){o.innerHTML='<div class="card warn">Eroare backtest COTA '+target+': '+e.message+'</div>'}}'''
    html = html.replace('</script>', js + '</script>', 1)
    return HTMLResponse(html)
