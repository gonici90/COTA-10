import app as core
import market_engine
from fastapi import Query
from fastapi.responses import HTMLResponse
from ticket_engine import router
from auto_data import router as data_router

app=core.app
core._cacheable=lambda path:path.startswith('/matches')
_old=next((r for r in app.router.routes if getattr(r,'path',None)=='/api/analyze'),None)
if _old:app.router.routes.remove(_old)

WINDOWS={1.5:1,5.0:2,10.0:4,100.0:7}
@app.get('/api/analyze')
async def analyze_multimarket(day:str=Query(default_factory=lambda:core.date.today().isoformat()),target:float=10.0):
    supported=min(WINDOWS,key=lambda x:abs(x-target)); days=WINDOWS[supported]
    return market_engine.analyze_period(day=day,target=supported,days=days,limit=80)

app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None)!='/api/backtest']
app.include_router(router);app.include_router(data_router)
_old_home=next((r for r in app.router.routes if getattr(r,'path',None)=='/'),None)
if _old_home:app.router.routes.remove(_old_home)

@app.get('/',response_class=HTMLResponse)
def home():
    return HTMLResponse('''<!doctype html><html lang="ro"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>COTA 10</title><style>body{font-family:system-ui;background:#0b0f14;color:#fff;margin:0;padding:22px}main{max-width:900px;margin:auto}.card{background:#151b23;border:1px solid #29313d;border-radius:16px;padding:18px;margin:14px 0}.targets{display:grid;grid-template-columns:repeat(2,1fr);gap:9px;margin-top:12px}button{background:#35c46a;border:0;border-radius:12px;padding:15px 10px;font-weight:900;font-size:16px}input{width:100%;box-sizing:border-box;padding:12px;border-radius:9px;border:1px solid #394453;background:#0b0f14;color:white}small{color:#9ba8b7}.prob{font-size:22px;font-weight:800}.warn{color:#ffcc66}.tag{display:inline-block;padding:4px 8px;border-radius:8px;background:#263241;margin:4px}.value{background:#174a32;color:#76f0aa}.hero{font-size:13px;color:#9ba8b7;margin-top:4px}</style></head><body><main><h1>COTA 10</h1><p>Alege cota. Motorul caută automat în intervalul potrivit și prioritizează selecțiile cu probabilitatea cea mai mare.</p><div class="card"><label>De la data</label><input id="d" type="date"><div class="targets"><button onclick="go(1.5)">COTA 1.50<div class="hero">1 zi</div></button><button onclick="go(5)">COTA 5<div class="hero">max. 2 zile</div></button><button onclick="go(10)">COTA 10<div class="hero">max. 4 zile</div></button><button onclick="go(100)">COTA 100<div class="hero">max. 7 zile</div></button></div></div><div id="out"></div><script>const val=(v,f='—')=>(v===undefined||v===null)?f:v;d.value=new Date().toISOString().slice(0,10);async function go(target){out.innerHTML='<p>Analizez COTA '+target+'...</p>';try{let r=await fetch('/api/analyze?day='+d.value+'&target='+target),x=await r.json();if(!r.ok)throw Error(x.detail||'Eroare');let h='<div class="card"><b>COTA '+target+' · '+x.analyzed+' meciuri analizate</b><br><small>Interval '+x.date+' → '+x.period_end+' · '+x.days+' '+(x.days==1?'zi':'zile')+' · '+x.api_fixtures+' meciuri primite din API</small></div>';if(x.suggested_combo){h+='<h2>Bilet recomandat</h2><div class="card"><div class="prob">Cotă combinată '+x.suggested_combo.combined_odds+'</div><small>Probabilitate comună estimată '+val(x.suggested_combo.estimated_joint_probability)+'%</small><br><br>'+x.suggested_combo.matches.map(m=>'<b>'+m.home+' – '+m.away+'</b><br>'+m.selection+' · '+m.probability+'% @'+m.odds+(m.kickoff?'<br><small>'+new Date(m.kickoff).toLocaleString('ro-RO')+'</small>':'')).join('<br><br>')+'</div>'}else h+='<div class="card warn"><b>Nu am construit COTA '+target+'.</b> În intervalul acesta nu sunt suficiente selecții care trec filtrele de siguranță.</div>';h+='<h2>Analiza multi-piață</h2>';for(let m of x.ranking){let b=m.best_market;h+='<div class="card"><small>'+m.league+(m.kickoff?' · '+new Date(m.kickoff).toLocaleString('ro-RO'):'')+'</small><h3>'+m.home+' – '+m.away+'</h3><div class="prob">'+b.market+' · '+b.probability+'%'+(b.bookmaker_odds?' @'+b.bookmaker_odds:'')+'</div><small>xG '+m.home_xg+' - '+m.away_xg+' · încredere '+m.confidence+'</small><div>'+m.markets.map(q=>'<span class="tag '+(q.value?'value':'')+'">'+q.market+' '+q.probability+'%'+(q.bookmaker_odds?' @'+q.bookmaker_odds:'')+'</span>').join('')+'</div></div>'}if(x.analysis_errors&&x.analysis_errors.length)h+='<div class="card warn"><small>'+x.analysis_errors.length+' meciuri nu au putut fi analizate complet.</small></div>';out.innerHTML=h}catch(e){out.innerHTML='<div class="card warn">'+e.message+'</div>'}}</script></main></body></html>''')
