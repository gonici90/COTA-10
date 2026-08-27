from datetime import date
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
import market_engine
from auto_data import router as data_router

app = FastAPI(title="COTA 10", version="7.0")
app.include_router(data_router)

WINDOWS = {1.5: 1, 5.0: 2, 10.0: 4, 100.0: 7}

@app.get("/health")
def health():
    return {"status": "ok", "version": "7.0", "provider": "5DollarFootballAPI + Bet365"}

@app.get("/api/analyze")
def analyze_multimarket(
    day: str = Query(default_factory=lambda: date.today().isoformat()),
    target: float = 10.0,
):
    supported = min(WINDOWS, key=lambda x: abs(x - target))
    return market_engine.analyze_period(day, supported, WINDOWS[supported], 200)

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(r'''<!doctype html>
<html lang="ro">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>COTA 10</title>
<style>
body{font-family:system-ui;background:#0b0f14;color:#fff;margin:0;padding:22px}
main{max-width:900px;margin:auto}
.card{background:#151b23;border:1px solid #29313d;border-radius:16px;padding:18px;margin:14px 0}
.targets{display:grid;grid-template-columns:repeat(2,1fr);gap:9px}
button{background:#35c46a;border:0;border-radius:12px;padding:15px 10px;font-weight:900;font-size:16px}
input{width:100%;box-sizing:border-box;padding:12px;border-radius:9px;border:1px solid #394453;background:#0b0f14;color:white}
small{color:#9ba8b7}.prob{font-size:22px;font-weight:800}.warn{color:#ffcc66}
.tag{display:inline-block;padding:4px 8px;border-radius:8px;background:#263241;margin:4px}
.value{background:#174a32;color:#76f0aa}.hero{font-size:13px;color:#9ba8b7}.good{color:#55df8b}
</style>
</head>
<body><main>
<h1>COTA 10</h1>
<p>Motorul verifică toate meciurile cu cote Bet365 din interval și construiește global combinația cea mai potrivită pentru ținta aleasă.</p>
<div class="card">
<label>De la data</label><input id="d" type="date">
<div class="targets">
<button onclick="go(1.5)">COTA 1.50<div class="hero">1 zi</div></button>
<button onclick="go(5)">COTA 5<div class="hero">max. 2 zile</div></button>
<button onclick="go(10)">COTA 10<div class="hero">max. 4 zile</div></button>
<button onclick="go(100)">COTA 100<div class="hero">max. 7 zile</div></button>
</div></div>
<div id="out"></div>
<script>
function localDate(){
 const x=new Date(), y=x.getFullYear(), m=String(x.getMonth()+1).padStart(2,'0'), d=String(x.getDate()).padStart(2,'0');
 return `${y}-${m}-${d}`;
}
document.getElementById('d').value=localDate();
async function go(t){
 out.innerHTML='<p>Analizez...</p>';
 try{
  const r=await fetch('/api/analyze?day='+d.value+'&target='+t);
  const x=await r.json();
  if(!r.ok)throw Error(x.detail||'Eroare');
  const dg=x.combo_diagnostics||{};
  let h='<div class="card"><b>COTA '+t+' · '+x.analyzed+' meciuri cu piețe analizate</b><br><small>'+x.date+' → '+x.period_end+' · '+x.api_fixtures+' meciuri primite · '+x.attempted+' verificate · '+x.without_usable_odds+' fără cote Bet365 utilizabile · '+x.analysis_errors.length+' erori</small></div>';
  if(x.suggested_combo){
    const c=x.suggested_combo;
    const status=c.target_met?'Ținta atinsă':'Cea mai bună variantă apropiată';
    h+='<h2>Bilet recomandat</h2><div class="card"><div class="prob">Cotă combinată '+c.combined_odds+'</div><small class="'+(c.target_met?'good':'warn')+'">'+status+' · probabilitate comună estimată '+c.estimated_joint_probability+'% · '+c.matches.length+' selecții · cotă medie/selecție '+c.average_leg_odds+'</small><br><br>'+c.matches.map(m=>'<b>'+m.home+' – '+m.away+'</b><br>'+m.selection+' · '+m.probability+'% @'+m.odds+(m.kickoff?'<br><small>'+new Date(m.kickoff).toLocaleString('ro-RO')+'</small>':'')).join('<br><br>')+'</div>';
  }else{
    h+='<div class="card warn"><b>Nu există o combinație matematică suficient de apropiată de COTA '+t+'.</b><br><small>Meciuri candidate: '+(dg.candidate_matches??0)+' · selecții candidate: '+(dg.candidate_selections??0)+' · cea mai apropiată cotă găsită: '+(dg.closest_reachable_odds??'—')+'</small></div>';
  }
  h+='<h2>Analiza multi-piață</h2>';
  for(const m of x.ranking){
    const b=m.best_market;
    h+='<div class="card"><small>'+m.league+(m.kickoff?' · '+new Date(m.kickoff).toLocaleString('ro-RO'):'')+'</small><h3>'+m.home+' – '+m.away+'</h3><div class="prob">'+b.market+' · '+b.probability+'% @'+b.bookmaker_odds+'</div><small>xG '+m.home_xg+' - '+m.away_xg+'</small><div>'+m.markets.map(q=>'<span class="tag '+(q.value?'value':'')+'">'+q.market+' '+q.probability+'% @'+q.bookmaker_odds+'</span>').join('')+'</div></div>';
  }
  out.innerHTML=h;
 }catch(e){out.innerHTML='<div class="card warn">'+e.message+'</div>'}
}
</script>
</main></body></html>''')
