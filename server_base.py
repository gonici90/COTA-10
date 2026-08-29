from datetime import date

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse

import market_engine
import odds_sports
from auto_data import router as data_router


app = FastAPI(title="Analiza Cota AI", version="9.0")
app.include_router(data_router)


def _window(target):
    t = float(target)
    if t <= 1.75:
        return 1
    if t <= 6:
        return 2
    if t <= 20:
        return 4
    return 7


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "9.0",
        "football_provider": "5DollarFootballAPI + Bet365",
        "multisport_provider": "The Odds API",
        "the_odds_api_configured": odds_sports.configured(),
        "optimizer": "safety-first-global",
        "ui": "pro-multisport-v1",
    }


@app.get("/api/analyze")
def analyze(
    day: str = Query(default_factory=lambda: date.today().isoformat()),
    target: float = Query(10.0, ge=1.05, le=500.0),
    sport: str = Query("football"),
):
    sport = sport.lower().strip()
    days = _window(target)

    if sport == "football":
        result = market_engine.analyze_period(day, target, days, 200)
        result["sport"] = "football"
        return result
    if sport == "soccer_extra":
        return odds_sports.analyze_group("soccer", day, target, days)
    if sport == "tennis":
        return odds_sports.analyze_group("tennis", day, target, days)
    if sport == "basketball":
        return odds_sports.analyze_group("basketball", day, target, days)
    if sport == "mix":
        return odds_sports.analyze_mix(day, target, days)

    raise HTTPException(400, "Sport necunoscut")


@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(r'''<!doctype html>
<html lang="ro">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#07110d">
<title>Analiza Cota AI • Multi-Sport</title>
<style>
:root{
 --bg:#07110d;--panel:#0d1b15;--panel2:#10241b;--line:#1c382b;
 --text:#eef8f2;--muted:#8fa99a;--accent:#47e889;--accent2:#20c96a;
 --gold:#ffd166;--red:#ff7a86;--blue:#7cc8ff;--purple:#c29cff;
 --shadow:0 18px 48px rgba(0,0,0,.28);
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
.shell{width:min(1050px,100%);margin:auto;padding:18px 14px 80px}
.topbar{display:flex;align-items:center;justify-content:space-between;padding:6px 2px 16px}
.brand{display:flex;align-items:center;gap:10px}
.logo{
 width:40px;height:40px;border-radius:13px;display:grid;place-items:center;font-weight:1000;color:#042313;
 background:linear-gradient(145deg,#76f4a8,#24ca6c);box-shadow:0 8px 28px rgba(71,232,137,.24)
}
.brand-title{font-size:18px;font-weight:950;letter-spacing:.1px}
.brand-sub{font-size:11px;color:var(--muted);margin-top:2px}
.engine{
 font-size:10px;color:#9ccdb1;border:1px solid #214531;background:#0b1b13;
 padding:7px 10px;border-radius:999px;white-space:nowrap
}
.hero{
 border:1px solid var(--line);background:linear-gradient(145deg,rgba(16,36,27,.97),rgba(10,24,18,.97));
 border-radius:24px;padding:20px;box-shadow:var(--shadow);overflow:hidden;position:relative
}
.hero:after{
 content:"";position:absolute;width:190px;height:190px;border-radius:50%;right:-75px;top:-95px;
 background:radial-gradient(circle,rgba(71,232,137,.18),transparent 68%);pointer-events:none
}
.eyebrow{font-size:10px;text-transform:uppercase;letter-spacing:1.45px;color:#75d99f;font-weight:900}
h1{font-size:29px;line-height:1.05;margin:8px 0 10px;letter-spacing:-.7px}
.hero p{margin:0;color:var(--muted);font-size:13px;line-height:1.5;max-width:700px}
.sports{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:18px}
.sport{
 border:1px solid #244331;background:#0a1811;color:#c7dbcf;border-radius:13px;padding:10px 8px;
 font-size:11px;font-weight:900;min-height:44px
}
.sport.active{border-color:var(--accent);color:#08170e;background:linear-gradient(145deg,#75f1a4,#31d879)}
.sport.mix{grid-column:span 2}
.controls{margin-top:14px;display:grid;grid-template-columns:1fr;gap:11px}
.label{font-size:10px;color:var(--muted);font-weight:800;text-transform:uppercase;letter-spacing:.65px;margin-bottom:6px}
input{
 width:100%;border:1px solid #284536;background:#08150f;color:var(--text);
 border-radius:13px;padding:12px 13px;outline:none
}
input:focus{border-color:#3edc82;box-shadow:0 0 0 3px rgba(71,232,137,.08)}
.targets{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.target{
 border:1px solid #244331;background:#0b1912;color:var(--text);border-radius:14px;padding:11px 10px;text-align:left;
 transition:.16s ease;min-height:60px
}
.target.active{border-color:var(--accent);background:linear-gradient(145deg,#123221,#0c2017)}
.target .big{font-size:18px;font-weight:950}
.target .small{font-size:9px;color:var(--muted);margin-top:3px}
.custom{display:grid;grid-template-columns:1fr auto;gap:8px;margin-top:8px}
.custom button{
 border:0;border-radius:13px;padding:0 16px;background:var(--accent);color:#052112;font-weight:950
}
#out{margin-top:16px}
.loading{display:flex;align-items:center;gap:12px;border:1px solid var(--line);background:var(--panel);border-radius:18px;padding:18px;color:#b9d2c4}
.spinner{width:20px;height:20px;border:2px solid #244434;border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin:12px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:13px}
.stat .n{font-size:21px;font-weight:950}
.stat .k{font-size:9px;color:var(--muted);margin-top:3px;text-transform:uppercase;letter-spacing:.55px}
.section-title{display:flex;align-items:center;justify-content:space-between;margin:20px 2px 9px;gap:8px}
.section-title h2{font-size:15px;margin:0}
.section-title span{font-size:10px;color:var(--muted);text-align:right}
.ticket{
 border:1px solid #27563c;background:linear-gradient(145deg,rgba(17,43,29,.98),rgba(9,26,18,.98));
 border-radius:22px;padding:18px;box-shadow:var(--shadow)
}
.ticket-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:13px}
.ticket-label{font-size:10px;color:#85dca9;text-transform:uppercase;font-weight:900;letter-spacing:1px}
.ticket-odds{font-size:35px;font-weight:1000;letter-spacing:-1.3px;margin-top:2px}
.pill{display:inline-flex;align-items:center;border-radius:999px;padding:7px 9px;font-size:9px;font-weight:900;border:1px solid}
.pill.good{color:#8bf0b1;border-color:#2d7549;background:#0c2a1a}
.pill.warn{color:#ffe09a;border-color:#6b572b;background:#2a220f}
.ticket-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-bottom:13px}
.metric{background:rgba(5,16,11,.5);border:1px solid rgba(61,114,82,.38);border-radius:12px;padding:9px}
.metric b{display:block;font-size:13px}
.metric span{display:block;color:var(--muted);font-size:8px;margin-top:2px;text-transform:uppercase}
.legs{display:grid;gap:8px}
.leg{background:#0a1710;border:1px solid #1d3a2a;border-radius:14px;padding:12px}
.leg-top{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
.match{font-weight:850;font-size:13px;line-height:1.3}
.odd{font-size:15px;font-weight:950;color:#b7f3cc;white-space:nowrap}
.pick{font-size:12px;margin-top:7px;color:#dfece4}
.meta{font-size:9px;color:var(--muted);margin-top:6px;display:flex;gap:7px;flex-wrap:wrap;align-items:center}
.badge{font-size:8px;font-weight:900;border-radius:999px;padding:4px 7px;letter-spacing:.4px;border:1px solid}
.badge.safe{color:#80eaaa;border-color:#25623e;background:#0c2518}
.badge.solid{color:#9fd7ff;border-color:#31526b;background:#0c1e2a}
.badge.risk{color:#ffca82;border-color:#6b4d2a;background:#281c0d}
.badge.football{color:#8bf0b1;border-color:#2d7549;background:#0c2a1a}
.badge.tennis{color:#d4c1ff;border-color:#584879;background:#201831}
.badge.basketball{color:#ffd08c;border-color:#74552c;background:#2a1d0c}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:17px;padding:14px;margin:9px 0}
.panel.warn{border-color:#574629;background:#1c170c;color:#ffe3a5}
.error{border-color:#6a2d34;background:#211014;color:#ffc4ca}
.match-card{background:var(--panel);border:1px solid var(--line);border-radius:17px;padding:14px;margin:8px 0}
.match-top{display:flex;justify-content:space-between;gap:9px;align-items:flex-start}
.league{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.45px}
.best{margin-top:8px;display:flex;align-items:end;justify-content:space-between;gap:10px}
.best-pick{font-size:14px;font-weight:900}
.best-prob{font-size:21px;font-weight:950;color:#a6efc0}
.markets{display:flex;gap:5px;flex-wrap:wrap;margin-top:9px}
.tag{border:1px solid #274536;background:#0a1710;border-radius:999px;padding:5px 7px;font-size:9px;color:#b9cfc2}
.tag.value{border-color:#2d7048;color:#8ae6aa;background:#0c2518}
details{margin-top:7px}
summary{list-style:none;cursor:pointer;font-size:10px;color:#9fb5a8;padding:4px 0}
summary::-webkit-details-marker{display:none}
summary:after{content:" +";color:#6dcf93;font-weight:900}
details[open] summary:after{content:" −"}
.empty{text-align:center;padding:25px 17px}
.provider{font-size:9px;color:#6f8d7d;margin-top:10px;text-align:center}
.footer{text-align:center;color:#587064;font-size:9px;margin:26px 0 0}
@media(min-width:720px){
 .shell{padding:28px 22px 90px}.hero{padding:26px}
 .sports{grid-template-columns:repeat(5,1fr)}.sport.mix{grid-column:auto}
 .controls{grid-template-columns:220px 1fr;align-items:end}
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
  <div class="logo">AI</div>
  <div><div class="brand-title">Analiza Cota AI</div><div class="brand-sub">Multi-Sport Intelligence</div></div>
 </div>
 <div class="engine">ENGINE v9.0</div>
</header>

<section class="hero">
 <div class="eyebrow">Safety-first • multi-sport</div>
 <h1>Fotbal. Tenis. Baschet.<br>Un singur motor de bilete.</h1>
 <p>Fotbalul păstrează motorul Bet365. Tenisul și baschetul folosesc consensul cotelor europene. MIX le combină fără să inventeze un model comun pentru sporturi diferite.</p>

 <div class="sports">
  <button class="sport active" data-sport="football" onclick="setSport('football',this)">⚽ Fotbal</button>
  <button class="sport" data-sport="soccer_extra" onclick="setSport('soccer_extra',this)">⚽ Fotbal+</button>
  <button class="sport" data-sport="tennis" onclick="setSport('tennis',this)">🎾 Tenis</button>
  <button class="sport" data-sport="basketball" onclick="setSport('basketball',this)">🏀 Baschet</button>
  <button class="sport mix" data-sport="mix" onclick="setSport('mix',this)">✦ MIX</button>
 </div>

 <div class="controls">
  <div>
   <div class="label">Data de start</div>
   <input id="d" type="date">
  </div>
  <div>
   <div class="label">Cotă țintă</div>
   <div class="targets">
    <button class="target" onclick="go(1.5,this)"><div class="big">1.50</div><div class="small">1 zi</div></button>
    <button class="target" onclick="go(5,this)"><div class="big">5</div><div class="small">max. 2 zile</div></button>
    <button class="target active" onclick="go(10,this)"><div class="big">10</div><div class="small">max. 4 zile</div></button>
    <button class="target" onclick="go(100,this)"><div class="big">100</div><div class="small">max. 7 zile</div></button>
   </div>
   <div class="custom"><input id="customTarget" inputmode="decimal" type="number" min="1.05" max="500" step="0.1" placeholder="Altă cotă, ex. 50"><button onclick="goCustom()">ANALIZEAZĂ</button></div>
  </div>
 </div>
</section>

<div id="out"></div>
<div class="footer">Analiza Cota AI • engine v9.0 • 5DollarFootballAPI + The Odds API</div>
</main>

<script>
const out=document.getElementById('out');
const d=document.getElementById('d');
const customTarget=document.getElementById('customTarget');
let currentSport='football';
let currentTarget=10;

function localDate(){
 const x=new Date(),y=x.getFullYear(),m=String(x.getMonth()+1).padStart(2,'0'),day=String(x.getDate()).padStart(2,'0');
 return `${y}-${m}-${day}`;
}
d.value=localDate();

const names={football:'Fotbal',soccer_extra:'Fotbal+',tennis:'Tenis',basketball:'Baschet',mix:'MIX'};

function esc(v){return String(v??'').replace(/[&<>"']/g,s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));}
function fmtDate(v){
 if(!v)return '';
 try{return new Date(v).toLocaleString('ro-RO',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}
 catch{return v}
}
function sportClass(m){
 const s=String(m.sport||currentSport);
 if(s.includes('tennis'))return 'tennis';
 if(s.includes('basketball'))return 'basketball';
 return 'football';
}
function risk(prob){
 const p=Number(prob||0);
 if(p>=72)return ['SAFE','safe'];
 if(p>=64)return ['SOLID','solid'];
 return ['RISK','risk'];
}
function setSport(s,btn){
 currentSport=s;
 document.querySelectorAll('.sport').forEach(b=>b.classList.remove('active'));
 if(btn)btn.classList.add('active');
 out.innerHTML='';
}
function setTarget(btn){
 document.querySelectorAll('.target').forEach(b=>b.classList.remove('active'));
 if(btn)btn.classList.add('active');
}
function goCustom(){
 const t=Number(customTarget.value);
 if(!t||t<1.05||t>500){customTarget.focus();return}
 document.querySelectorAll('.target').forEach(b=>b.classList.remove('active'));
 go(t,null);
}

async function go(t,btn){
 currentTarget=Number(t);
 if(btn)setTarget(btn);
 const sportName=names[currentSport]||currentSport;
 out.innerHTML='<div class="loading"><div class="spinner"></div><div><b>Analizez '+esc(sportName)+'...</b><br><small>Caut global varianta pentru COTA '+esc(t)+'</small></div></div>';
 try{
  const url='/api/analyze?day='+encodeURIComponent(d.value)+'&target='+encodeURIComponent(t)+'&sport='+encodeURIComponent(currentSport);
  const r=await fetch(url);
  const x=await r.json();
  if(!r.ok)throw Error(x.detail||'Eroare la analiză');

  const dg=x.combo_diagnostics||{};
  let h='<div class="stats">';
  h+='<div class="stat"><div class="n">'+esc(x.analyzed)+'</div><div class="k">Evenimente analizate</div></div>';
  h+='<div class="stat"><div class="n">'+esc(x.api_fixtures)+'</div><div class="k">Evenimente găsite</div></div>';
  h+='<div class="stat"><div class="n">'+esc(x.without_usable_odds)+'</div><div class="k">Fără cote utile</div></div>';
  h+='<div class="stat"><div class="n">'+esc((x.analysis_errors||[]).length)+'</div><div class="k">Erori</div></div>';
  h+='</div>';

  if(x.sources){
   h+='<div class="panel"><div class="meta"><b>MIX:</b><span>⚽ '+esc(x.sources.football||0)+'</span><span>🎾 '+esc(x.sources.tennis||0)+'</span><span>🏀 '+esc(x.sources.basketball||0)+'</span></div></div>';
  }

  if(x.suggested_combo){
   const c=x.suggested_combo,ok=!!c.target_met;
   h+='<div class="section-title"><h2>Bilet recomandat</h2><span>'+esc(x.date)+' → '+esc(x.period_end)+'</span></div>';
   h+='<section class="ticket">';
   h+='<div class="ticket-head"><div><div class="ticket-label">'+esc(sportName)+' • COTA '+esc(t)+' • BEST BUILD</div><div class="ticket-odds">'+esc(c.combined_odds)+'</div></div>';
   h+='<div class="pill '+(ok?'good':'warn')+'">'+(ok?'● ȚINTĂ ATINSĂ':'● VARIANTĂ APROPIATĂ')+'</div></div>';
   h+='<div class="ticket-metrics">';
   h+='<div class="metric"><b>'+esc(c.estimated_joint_probability)+'%</b><span>Prob. comună</span></div>';
   h+='<div class="metric"><b>'+esc(c.matches.length)+'</b><span>Selecții</span></div>';
   h+='<div class="metric"><b>'+esc(c.average_leg_odds)+'</b><span>Cotă medie</span></div>';
   h+='</div><div class="legs">';
   for(const m of c.matches){
    const pp=m.ticket_probability??m.probability,rr=risk(pp);
    h+='<article class="leg"><div class="leg-top"><div class="match">'+esc(m.home)+' – '+esc(m.away)+'</div><div class="odd">@'+esc(m.odds)+'</div></div>';
    h+='<div class="pick">'+esc(m.selection)+'</div>';
    h+='<div class="meta"><span class="badge '+rr[1]+'">'+rr[0]+'</span>';
    if(m.sport){const sc=sportClass(m);h+='<span class="badge '+sc+'">'+esc(sc.toUpperCase())+'</span>';}
    h+='<span>'+esc(pp)+'% conservator</span>';
    if(m.bookmaker)h+='<span>'+esc(m.bookmaker)+'</span>';
    if(m.kickoff)h+='<span>'+esc(fmtDate(m.kickoff))+'</span>';
    h+='</div></article>';
   }
   h+='</div></section>';
  }else{
   h+='<div class="section-title"><h2>Bilet recomandat</h2></div>';
   h+='<div class="panel warn empty"><b>Nu există momentan o combinație suficient de apropiată de COTA '+esc(t)+'.</b>';
   h+='<div class="meta" style="justify-content:center;margin-top:10px">Candidate: '+esc(dg.candidate_matches??0)+' evenimente • '+esc(dg.candidate_selections??0)+' selecții • cea mai apropiată cotă '+esc(dg.closest_reachable_odds??'—')+'</div></div>';
  }

  h+='<div class="section-title"><h2>Analiza piețelor</h2><span>'+esc((x.ranking||[]).length)+' evenimente</span></div>';
  for(const m of (x.ranking||[])){
   const b=m.best_market;if(!b)continue;
   const pp=b.ticket_probability??b.probability,rr=risk(pp),sc=sportClass(m);
   h+='<article class="match-card">';
   h+='<div class="match-top"><div><div class="league">'+esc(m.league||'Competiție')+(m.kickoff?' • '+esc(fmtDate(m.kickoff)):'')+'</div><div class="match" style="margin-top:4px">'+esc(m.home)+' – '+esc(m.away)+'</div></div><div><span class="badge '+sc+'">'+esc(sc.toUpperCase())+'</span> <span class="badge '+rr[1]+'">'+rr[0]+'</span></div></div>';
   h+='<div class="best"><div><div class="label">Recomandarea principală</div><div class="best-pick">'+esc(b.market)+' @'+esc(b.bookmaker_odds)+'</div></div><div class="best-prob">'+esc(pp)+'%</div></div>';
   h+='<div class="meta">';
   if(m.home_xg!==undefined&&m.away_xg!==undefined)h+='<span>xG '+esc(m.home_xg)+' – '+esc(m.away_xg)+'</span>';
   if(b.bookmaker)h+='<span>'+esc(b.bookmaker)+'</span>';
   if(b.quote_count)h+='<span>'+esc(b.quote_count)+' surse</span>';
   if(m.confidence)h+='<span>'+esc(m.confidence)+'</span>';
   h+='</div>';
   h+='<details><summary>Vezi toate piețele</summary><div class="markets">';
   for(const q of (m.markets||[])){
    const qp=q.ticket_probability??q.probability;
    h+='<span class="tag '+(q.value?'value':'')+'">'+esc(q.market)+' • '+esc(qp)+'% @'+esc(q.bookmaker_odds)+'</span>';
   }
   h+='</div></details></article>';
  }

  if(x.leagues_scanned&&x.leagues_scanned.length){
   h+='<details class="panel"><summary>Ligi / turnee scanate ('+x.leagues_scanned.length+')</summary><div class="meta">'+x.leagues_scanned.map(esc).join(' • ')+'</div></details>';
  }
  if(x.quota&&x.quota.remaining!==null){
   h+='<div class="provider">The Odds API • credite rămase: '+esc(x.quota.remaining)+' • ultimul request: '+esc(x.quota.last??'—')+'</div>';
  }else{
   h+='<div class="provider">'+esc(x.provider||'')+'</div>';
  }

  if((x.analysis_errors||[]).length){
   h+='<details class="panel error"><summary>Detalii erori ('+x.analysis_errors.length+')</summary>';
   for(const e of x.analysis_errors)h+='<div class="meta">'+esc(e.match||e.league||e.fixture||'')+' • '+esc(e.error||'')+'</div>';
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
