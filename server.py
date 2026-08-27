import math
import app as core
import market_engine
from fastapi import Query
from fastapi.responses import HTMLResponse
from ticket_engine import router
from auto_data import router as data_router

app=core.app
core._cacheable=lambda path:path.startswith('/matches')

# For large targets, prefer many safer low-odds legs instead of a short ticket
# made from aggressive prices. We still maximize the estimated joint probability.
def safer_build_combo(rows,target):
    target=float(target)
    candidates=[]
    high_target=target>=50
    for r in rows:
        good=[]
        for p in r.get('markets',[]):
            odd=p.get('bookmaker_odds'); prob=p.get('probability',0)/100
            if not odd: continue
            odd=float(odd)
            if high_target:
                # COTA 100: deliberately search the safer 1.10-1.65 band.
                minp=.72 if odd<1.20 else .68 if odd<1.35 else .64 if odd<1.50 else .61
                allowed=1.10<=odd<=1.65
            else:
                minp=.70 if odd<1.20 else .66 if odd<1.35 else .62 if odd<1.60 else .58
                allowed=1.05<=odd<=4.0
            if allowed and prob>=minp and not p.get('suspicious'):
                good.append({**p,'home':r['home'],'away':r['away'],'kickoff':r.get('kickoff'),'combo_prob':prob})
        if good:
            # One selection per match; choose safety first, score second.
            candidates.append(max(good,key=lambda x:(x['combo_prob'],x['recommendation_score'])))

    # DP keeps the best probability for each odds bucket AND leg count.
    # This lets a 15-leg low-odds ticket compete fairly with an 8-leg ticket.
    states={(0,0):(1.0,1.0,[])}
    max_legs=20 if high_target else min(12,len(candidates))
    for x in candidates:
        nxt=dict(states)
        for (legs,_),(odd,joint,path) in states.items():
            if legs>=max_legs: continue
            no=odd*x['bookmaker_odds']
            if no>target*1.18: continue
            nj=joint*x['combo_prob']; nl=legs+1
            bucket=round(math.log(max(no,1.0))*100)
            key=(nl,bucket); old=nxt.get(key)
            if old is None or nj>old[1]: nxt[key]=(no,nj,path+[x])
        states=nxt

    valid=[]
    for (legs,_),v in states.items():
        if not v[2] or not (target*.90<=v[0]<=target*1.12): continue
        if high_target and not (8<=legs<=20): continue
        valid.append(v)
    if not valid:return None

    # Primary objective is the probability that the whole ticket wins.
    # Closeness to the requested target is only the tie-breaker.
    odd,joint,path=max(valid,key=lambda v:(v[1],-abs(math.log(v[0]/target))))
    return {'combined_odds':round(odd,2),'estimated_joint_probability':round(joint*100,1),'matches':[{'home':x['home'],'away':x['away'],'kickoff':x.get('kickoff'),'selection':x['market'],'probability':x['probability'],'odds':x['bookmaker_odds'],'ev':x['ev'],'score':x['recommendation_score']} for x in path]}

market_engine.build_combo=safer_build_combo

_old=next((r for r in app.router.routes if getattr(r,'path',None)=='/api/analyze'),None)
if _old:app.router.routes.remove(_old)

WINDOWS={1.5:1,5.0:2,10.0:4,100.0:7}
@app.get('/api/analyze')
async def analyze_multimarket(day:str=Query(default_factory=lambda:core.date.today().isoformat()),target:float=10.0):
    supported=min(WINDOWS,key=lambda x:abs(x-target)); days=WINDOWS[supported]
    return market_engine.analyze_period(day=day,target=supported,days=days,limit=200)

app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None)!='/api/backtest']
app.include_router(router);app.include_router(data_router)
_old_home=next((r for r in app.router.routes if getattr(r,'path',None)=='/'),None)
if _old_home:app.router.routes.remove(_old_home)

@app.get('/',response_class=HTMLResponse)
def home():
    return HTMLResponse('''<!doctype html><html lang="ro"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>COTA 10</title><style>body{font-family:system-ui;background:#0b0f14;color:#fff;margin:0;padding:22px}main{max-width:900px;margin:auto}.card{background:#151b23;border:1px solid #29313d;border-radius:16px;padding:18px;margin:14px 0}.targets{display:grid;grid-template-columns:repeat(2,1fr);gap:9px;margin-top:12px}button{background:#35c46a;border:0;border-radius:12px;padding:15px 10px;font-weight:900;font-size:16px}input{width:100%;box-sizing:border-box;padding:12px;border-radius:9px;border:1px solid #394453;background:#0b0f14;color:white}small{color:#9ba8b7}.prob{font-size:22px;font-weight:800}.warn{color:#ffcc66}.tag{display:inline-block;padding:4px 8px;border-radius:8px;background:#263241;margin:4px}.value{background:#174a32;color:#76f0aa}.hero{font-size:13px;color:#9ba8b7;margin-top:4px}</style></head><body><main><h1>COTA 10</h1><p>Alege cota. Motorul caută automat în intervalul potrivit și prioritizează selecțiile cu probabilitatea cea mai mare.</p><div class="card"><label>De la data</label><input id="d" type="date"><div class="targets"><button onclick="go(1.5)">COTA 1.50<div class="hero">1 zi</div></button><button onclick="go(5)">COTA 5<div class="hero">max. 2 zile</div></button><button onclick="go(10)">COTA 10<div class="hero">max. 4 zile</div></button><button onclick="go(100)">COTA 100<div class="hero">max. 7 zile</div></button></div></div><div id="out"></div><script>const val=(v,f='—')=>(v===undefined||v===null)?f:v;d.value=new Date().toISOString().slice(0,10);async function go(target){out.innerHTML='<p>Analizez COTA '+target+'...</p>';try{let r=await fetch('/api/analyze?day='+d.value+'&target='+target),x=await r.json();if(!r.ok)throw Error(x.detail||'Eroare');let h='<div class="card"><b>COTA '+target+' · '+x.analyzed+' meciuri analizate</b><br><small>Interval '+x.date+' → '+x.period_end+' · '+x.days+' '+(x.days==1?'zi':'zile')+' · '+x.api_fixtures+' primite · '+x.attempted+' verificate · '+x.without_usable_odds+' fără cote utilizabile · '+x.analysis_errors.length+' erori</small></div>';if(x.suggested_combo){h+='<h2>Bilet recomandat</h2><div class="card"><div class="prob">Cotă combinată '+x.suggested_combo.combined_odds+'</div><small>Probabilitate comună estimată '+val(x.suggested_combo.estimated_joint_probability)+'% · '+x.suggested_combo.matches.length+' selecții</small><br><br>'+x.suggested_combo.matches.map(m=>'<b>'+m.home+' – '+m.away+'</b><br>'+m.selection+' · '+m.probability+'% @'+m.odds+(m.kickoff?'<br><small>'+new Date(m.kickoff).toLocaleString('ro-RO')+'</small>':'')).join('<br><br>')+'</div>'}else h+='<div class="card warn"><b>Nu am construit COTA '+target+'.</b> Au fost verificate '+x.attempted+' meciuri; '+x.without_usable_odds+' nu au avut piețe/cote utilizabile și '+x.analysis_errors.length+' au produs erori.</div>';h+='<h2>Analiza multi-piață</h2>';for(let m of x.ranking){let b=m.best_market;h+='<div class="card"><small>'+m.league+(m.kickoff?' · '+new Date(m.kickoff).toLocaleString('ro-RO'):'')+'</small><h3>'+m.home+' – '+m.away+'</h3><div class="prob">'+b.market+' · '+b.probability+'%'+(b.bookmaker_odds?' @'+b.bookmaker_odds:'')+'</div><small>xG '+m.home_xg+' - '+m.away_xg+' · încredere '+m.confidence+'</small><div>'+m.markets.map(q=>'<span class="tag '+(q.value?'value':'')+'">'+q.market+' '+q.probability+'%'+(q.bookmaker_odds?' @'+q.bookmaker_odds:'')+'</span>').join('')+'</div></div>'}out.innerHTML=h}catch(e){out.innerHTML='<div class="card warn">'+e.message+'</div>'}}</script></main></body></html>''')
