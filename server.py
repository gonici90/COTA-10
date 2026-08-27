from datetime import date

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

import market_engine
from auto_data import router as data_router


app = FastAPI(title="COTA 10", version="8.0")
app.include_router(data_router)

WINDOWS = {1.5: 1, 5.0: 2, 10.0: 4, 100.0: 7}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "8.0",
        "provider": "5DollarFootballAPI + Bet365",
        "optimizer": "safety-first-global",
        "ui": "pro-mobile-v1",
    }


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
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#07110d">
<title>COTA 10 • Analytics</title>
<style>
:root{
 --bg:#07110d;--panel:#0d1b15;--panel2:#10241b;--line:#1c382b;
 --text:#eef8f2;--muted:#8fa99a;--accent:#47e889;--accent2:#20c96a;
 --gold:#ffd166;--red:#ff7a86;--blue:#7cc8ff;--shadow:0 18px 48px rgba(0,0,0,.28);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
 margin:0;color:var(--text);font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",sans-serif;
 background:
 radial-gradient(900px 500px at 50% -120px,rgba(71,232,137,.12),transparent 60%),
 linear-gradient(180deg,#06100c 0%,#08130f 45%,#07110d 100%);
 min-height:100vh;
}
button,input{font:inherit}
button{cursor:pointer;-webkit-tap-highlight-color:transparent}
.shell{width:min(1040px,100%);margin:auto;padding:18px 14px 80px}
.topbar{display:flex;align-items:center;justify-content:space-between;padding:6px 2px 16px}
.brand{display:flex;align-items:center;gap:10px}
.logo{
 width:38px;height:38px;border-radius:12px;display:grid;place-items:center;font-weight:1000;color:#042313;
 background:linear-gradient(145deg,#76f4a8,#24ca6c);box-shadow:0 8px 28px rgba(71,232,137,.24)
}
.brand-title{font-size:18px;font-weight:900;letter-spacing:.2px}
.brand-sub{font-size:11px;color:var(--muted);margin-top:2px}
.engine{
 font-size:11px;color:#9ccdb1;border:1px solid #214531;background:#0b1b13;
 padding:7px 10px;border-radius:999px;white-space:nowrap
}
.hero{
 border:1px solid var(--line);background:linear-gradient(145deg,rgba(16,36,27,.96),rgba(10,24,18,.96));
 border-radius:24px;padding:20px;box-shadow:var(--shadow);overflow:hidden;position:relative
}
.hero:after{
 content:"";position:absolute;width:180px;height:180px;border-radius:50%;right:-75px;top:-90px;
 background:radial-gradient(circle,rgba(71,232,137,.18),transparent 68%);pointer-events:none
}
.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:1.4px;color:#75d99f;font-weight:800}
h1{font-size:30px;line-height:1.05;margin:8px 0 10px;letter-spacing:-.7px}
.hero p{margin:0;color:var(--muted);font-size:14px;line-height:1.5;max-width:650px}
.control{margin-top:20px;display:grid;grid-template-columns:1fr;gap:12px}
.date-wrap{display:flex;flex-direction:column;gap:6px}
.label{font-size:11px;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.7px}
input[type=date]{
 width:100%;border:1px solid #284536;background:#08150f;color:var(--text);border-radius:14px;padding:13px 14px;outline:none
}
input[type=date]:focus{border-color:#3edc82;box-shadow:0 0 0 3px rgba(71,232,137,.08)}
.targets{display:grid;grid-template-columns:repeat(2,1fr);gap:9px}
.target{
 border:1px solid #244331;background:#0b1912;color:var(--text);border-radius:15px;padding:12px 10px;text-align:left;
 transition:.18s ease;min-height:62px;position:relative;overflow:hidden
}
.target:hover{transform:translateY(-1px);border-color:#3b7654}
.target.active{border-color:var(--accent);background:linear-gradient(145deg,#123221,#0c2017);box-shadow:0 0 0 1px rgba(71,232,137,.15) inset}
.target .big{font-size:18px;font-weight:950}
.target .small{font-size:10px;color:var(--muted);margin-top:3px}
.target.active .small{color:#91e4b2}
#out{margin-top:16px}
.loading{
 display:flex;align-items:center;gap:12px;border:1px solid var(--line);background:var(--panel);border-radius:18px;padding:18px;color:#b9d2c4
}
.spinner{width:20px;height:20px;border:2px solid #244434;border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:9px;margin:12px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:17px;padding:14px}
.stat .n{font-size:22px;font-weight:950;letter-spacing:-.4px}
.stat .k{font-size:10px;color:var(--muted);margin-top:3px;text-transform:uppercase;letter-spacing:.6px}
.section-title{display:flex;align-items:center;justify-content:space-between;margin:20px 2px 10px}
.section-title h2{font-size:15px;margin:0;letter-spacing:.1px}
.section-title span{font-size:11px;color:var(--muted)}
.ticket{
 border:1px solid #27563c;background:
 linear-gradient(145deg,rgba(17,43,29,.98),rgba(9,26,18,.98));
 border-radius:22px;padding:18px;box-shadow:var(--shadow)
}
.ticket-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin-bottom:14px}
.ticket-label{font-size:11px;color:#85dca9;text-transform:uppercase;font-weight:850;letter-spacing:1px}
.ticket-odds{font-size:36px;font-weight:1000;letter-spacing:-1.4px;margin-top:2px}
.pill{
 display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:7px 10px;font-size:10px;font-weight:900;border:1px solid
}
.pill.good{color:#8bf0b1;border-color:#2d7549;background:#0c2a1a}
.pill.warn{color:#ffe09a;border-color:#6b572b;background:#2a220f}
.ticket-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:14px}
.metric{background:rgba(5,16,11,.5);border:1px solid rgba(61,114,82,.38);border-radius:13px;padding:10px}
.metric b{display:block;font-size:14px}
.metric span{display:block;color:var(--muted);font-size:9px;margin-top:2px;text-transform:uppercase;letter-spacing:.45px}
.legs{display:grid;gap:8px}
.leg{background:#0a1710;border:1px solid #1d3a2a;border-radius:15px;padding:12px}
.leg-top{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}
.match{font-weight:850;font-size:14px;line-height:1.3}
.odd{font-size:16px;font-weight:950;color:#b7f3cc;white-space:nowrap}
.pick{font-size:13px;margin-top:7px;color:#dfece4}
.meta{font-size:10px;color:var(--muted);margin-top:6px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.badge{font-size:9px;font-weight:900;border-radius:999px;padding:4px 7px;letter-spacing:.45px;border:1px solid}
.badge.safe{color:#80eaaa;border-color:#25623e;background:#0c2518}
.badge.solid{color:#9fd7ff;border-color:#31526b;background:#0c1e2a}
.badge.risk{color:#ffca82;border-color:#6b4d2a;background:#281c0d}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:14px;margin:10px 0}
.panel.warn{border-color:#574629;background:#1c170c;color:#ffe3a5}
.error{border-color:#6a2d34;background:#211014;color:#ffc4ca}
.match-card{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:15px;margin:9px 0}
.match-top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
.league{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.best{margin-top:8px;display:flex;align-items:end;justify-content:space-between;gap:12px}
.best-pick{font-size:15px;font-weight:900}
.best-prob{font-size:22px;font-weight:950;color:#a6efc0}
.markets{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.tag{border:1px solid #274536;background:#0a1710;border-radius:999px;padding:6px 8px;font-size:10px;color:#b9cfc2}
.tag.value{border-color:#2d7048;color:#8ae6aa;background:#0c2518}
details{margin-top:8px}
summary{list-style:none;cursor:pointer;font-size:11px;color:#9fb5a8;padding:4px 0}
summary::-webkit-details-marker{display:none}
summary:after{content:" +";color:#6dcf93;font-weight:900}
details[open] summary:after{content:" −"}
.empty{text-align:center;padding:26px 18px}
.empty .icon{font-size:28px;margin-bottom:8px}
.footer{text-align:center;color:#587064;font-size:10px;margin:26px 0 0}
@media(min-width:720px){
 .shell{padding:28px 22px 90px}.hero{padding:26px}
 .control{grid-template-columns:220px 1fr;align-items:end}
 .targets{grid-template-columns:repeat(4,1fr)}
 .stats{grid-template-columns:repeat(4,1fr)}
 .legs{grid-template-columns:repeat(2,1fr)}
 h1{font-size:38px}
}
</style>
</head>
<body>
<main class="shell">
 <header class="topbar">
  <div class="brand">
   <div class="logo">C10</div>
   <div><div class="brand-title">COTA 10</div><div class="brand-sub">Football Intelligence</div></div>
  </div>
  <div class="engine">ENGINE v8.0</div>
 </header>

 <section class="hero">
  <div class="eyebrow">Safety-first analytics</div>
  <h1>Construiește biletul.<br>Lasă motorul să filtreze riscul.</h1>
  <p>Analizează piețele Bet365 disponibile, compară probabilitățile și caută global combinația cea mai bună pentru ținta aleasă.</p>
  <div class="control">
   <div class="date-wrap">
    <div class="label">Data de start</div>
    <input id="d" type="date">
   </div>
   <div class="targets">
    <button class="target" data-target="1.5" onclick="go(1.5,this)"><div class="big">1.50</div><div class="small">1 zi • conservator</div></button>
    <button class="target" data-target="5" onclick="go(5,this)"><div class="big">5</div><div class="small">max. 2 zile</div></button>
    <button class="target" data-target="10" onclick="go(10,this)"><div class="big">10</div><div class="small">max. 4 zile</div></button>
    <button class="target" data-target="100" onclick="go(100,this)"><div class="big">100</div><div class="small">max. 7 zile</div></button>
   </div>
  </div>
 </section>

 <div id="out"></div>
 <div class="footer">COTA 10 • engine v8.0 • 5DollarFootballAPI + Bet365</div>
</main>

<script>
const out=document.getElementById('out');
const d=document.getElementById('d');

function localDate(){
 const x=new Date(),y=x.getFullYear(),m=String(x.getMonth()+1).padStart(2,'0'),day=String(x.getDate()).padStart(2,'0');
 return `${y}-${m}-${day}`;
}
d.value=localDate();

function esc(v){
 return String(v??'').replace(/[&<>"']/g,s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));
}
function fmtDate(v){
 if(!v)return '';
 try{return new Date(v).toLocaleString('ro-RO',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}
 catch{return v}
}
function risk(prob){
 const p=Number(prob||0);
 if(p>=72)return ['SAFE','safe'];
 if(p>=64)return ['SOLID','solid'];
 return ['RISK','risk'];
}
function setActive(btn){
 document.querySelectorAll('.target').forEach(b=>b.classList.remove('active'));
 if(btn)btn.classList.add('active');
}

async function go(t,btn){
 setActive(btn);
 out.innerHTML='<div class="loading"><div class="spinner"></div><div><b>Analizez piețele disponibile...</b><br><small>Construiesc varianta optimă pentru COTA '+t+'</small></div></div>';
 try{
  const r=await fetch('/api/analyze?day='+encodeURIComponent(d.value)+'&target='+encodeURIComponent(t));
  const x=await r.json();
  if(!r.ok)throw Error(x.detail||'Eroare la analiză');

  const dg=x.combo_diagnostics||{};
  let h='<div class="stats">';
  h+='<div class="stat"><div class="n">'+esc(x.analyzed)+'</div><div class="k">Meciuri analizate</div></div>';
  h+='<div class="stat"><div class="n">'+esc(x.api_fixtures)+'</div><div class="k">Meciuri găsite</div></div>';
  h+='<div class="stat"><div class="n">'+esc(x.without_usable_odds)+'</div><div class="k">Fără cote utile</div></div>';
  h+='<div class="stat"><div class="n">'+esc((x.analysis_errors||[]).length)+'</div><div class="k">Erori</div></div>';
  h+='</div>';

  if(x.suggested_combo){
   const c=x.suggested_combo;
   const ok=!!c.target_met;
   h+='<div class="section-title"><h2>Bilet recomandat</h2><span>'+esc(x.date)+' → '+esc(x.period_end)+'</span></div>';
   h+='<section class="ticket">';
   h+='<div class="ticket-head"><div><div class="ticket-label">COTA '+esc(t)+' • BEST BUILD</div><div class="ticket-odds">'+esc(c.combined_odds)+'</div></div>';
   h+='<div class="pill '+(ok?'good':'warn')+'">'+(ok?'● ȚINTĂ ATINSĂ':'● VARIANTĂ APROPIATĂ')+'</div></div>';
   h+='<div class="ticket-metrics">';
   h+='<div class="metric"><b>'+esc(c.estimated_joint_probability)+'%</b><span>Prob. comună</span></div>';
   h+='<div class="metric"><b>'+esc(c.matches.length)+'</b><span>Selecții</span></div>';
   h+='<div class="metric"><b>'+esc(c.average_leg_odds)+'</b><span>Cotă medie</span></div>';
   h+='</div><div class="legs">';
   for(const m of c.matches){
    const rr=risk(m.probability);
    h+='<article class="leg"><div class="leg-top"><div class="match">'+esc(m.home)+' – '+esc(m.away)+'</div><div class="odd">@'+esc(m.odds)+'</div></div>';
    h+='<div class="pick">'+esc(m.selection)+'</div>';
    h+='<div class="meta"><span class="badge '+rr[1]+'">'+rr[0]+'</span><span>'+esc(m.probability)+'% estimat</span>';
    if(m.kickoff)h+='<span>'+esc(fmtDate(m.kickoff))+'</span>';
    h+='</div></article>';
   }
   h+='</div></section>';
  }else{
   h+='<div class="section-title"><h2>Bilet recomandat</h2></div>';
   h+='<div class="panel warn empty"><div class="icon">◎</div><b>Nu există momentan o combinație suficient de apropiată de COTA '+esc(t)+'.</b>';
   h+='<div class="meta" style="justify-content:center;margin-top:10px">Candidate: '+esc(dg.candidate_matches??0)+' meciuri • '+esc(dg.candidate_selections??0)+' selecții • cea mai apropiată cotă '+esc(dg.closest_reachable_odds??'—')+'</div></div>';
  }

  h+='<div class="section-title"><h2>Analiza multi-piață</h2><span>'+esc((x.ranking||[]).length)+' meciuri</span></div>';
  for(const m of (x.ranking||[])){
   const b=m.best_market;
   if(!b)continue;
   const rr=risk(b.probability);
   h+='<article class="match-card">';
   h+='<div class="match-top"><div><div class="league">'+esc(m.league||'Competiție')+(m.kickoff?' • '+esc(fmtDate(m.kickoff)):'')+'</div><div class="match" style="margin-top:4px">'+esc(m.home)+' – '+esc(m.away)+'</div></div><span class="badge '+rr[1]+'">'+rr[0]+'</span></div>';
   h+='<div class="best"><div><div class="label">Recomandarea principală</div><div class="best-pick">'+esc(b.market)+' @'+esc(b.bookmaker_odds)+'</div></div><div class="best-prob">'+esc(b.probability)+'%</div></div>';
   h+='<div class="meta"><span>xG '+esc(m.home_xg)+' – '+esc(m.away_xg)+'</span><span>'+esc(m.confidence||'')+'</span></div>';
   h+='<details><summary>Vezi toate piețele</summary><div class="markets">';
   for(const q of (m.markets||[])){
    h+='<span class="tag '+(q.value?'value':'')+'">'+esc(q.market)+' • '+esc(q.probability)+'% @'+esc(q.bookmaker_odds)+'</span>';
   }
   h+='</div></details></article>';
  }

  if((x.analysis_errors||[]).length){
   h+='<details class="panel error"><summary>Detalii erori ('+x.analysis_errors.length+')</summary>';
   for(const e of x.analysis_errors)h+='<div class="meta">'+esc(e.match||e.fixture||'')+' • '+esc(e.error||'')+'</div>';
   h+='</details>';
  }

  out.innerHTML=h;
 }catch(e){
  out.innerHTML='<div class="panel error"><b>Analiza nu a putut fi finalizată.</b><div class="meta">'+esc(e.message)+'</div></div>';
 }
}
</script>
</body>
</html>''')
