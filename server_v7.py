"""Analiza Cota AI v9.6: Pro historical backtest UI (20/40/60 days)."""
from fastapi import HTTPException, Query
from fastapi.responses import HTMLResponse

import server_v6
import backtest_engine
import odds_sports

app = server_v6.app
app.version = "9.6"


def _drop_get(path):
    app.router.routes[:] = [
        route for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and "GET" in (getattr(route, "methods", None) or set())
        )
    ]


for _path in ("/", "/health"):
    _drop_get(_path)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "9.6",
        "football_provider": "5DollarFootballAPI Pro hybrid + Bet365",
        "multisport_provider": "The Odds API",
        "the_odds_api_configured": odds_sports.configured(),
        "optimizer": "safety-first-global + ticket/per-match constraints",
        "football_fetch": "bulk scan + shortlisted full odds",
        "backtest": "20/40/60 day Pro historical ticket simulation",
        "ui": "pro-multisport-v6",
    }


@app.get("/api/backtest/start")
def start_backtest(
    days: int = Query(...),
    target: float = Query(10.0, ge=1.05, le=500.0),
):
    try:
        job_id, created = backtest_engine.start(days, target)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"job_id": job_id, "created": created}


@app.get("/api/backtest/status")
def backtest_status(job_id: str = Query(..., min_length=1, max_length=64)):
    job = backtest_engine.status(job_id)
    if not job:
        raise HTTPException(404, "Backtest inexistent sau expirat")
    return job


BT_CSS = r'''
.bt-panel{margin-top:14px;border:1px solid #294b38;background:linear-gradient(145deg,#0d1d15,#0a1711);border-radius:20px;padding:15px;box-shadow:var(--shadow)}
.bt-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}.bt-title{font-size:14px;font-weight:950}.bt-sub{font-size:9px;color:var(--muted);margin-top:3px}
.bt-controls{display:grid;grid-template-columns:1fr;gap:8px}.bt-input label{display:block;font-size:9px;color:var(--muted);font-weight:800;text-transform:uppercase;margin-bottom:5px}.bt-buttons{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.bt-buttons button{border:1px solid #2b5d40;background:#0b2116;color:#bdf4d0;border-radius:12px;padding:11px 7px;font-weight:950;font-size:11px}.bt-buttons button:disabled{opacity:.45;cursor:not-allowed}
.bt-progress{margin-top:10px;font-size:10px;color:#a7c7b3}.bt-bar{height:8px;background:#07110d;border:1px solid #244331;border-radius:999px;overflow:hidden;margin-top:6px}.bt-fill{height:100%;width:0;background:var(--accent);transition:width .25s ease}.bt-note{font-size:8px;color:#678273;line-height:1.4;margin-top:9px}
.bt-summary{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-top:12px}.bt-stat{background:#08160f;border:1px solid #1e3d2b;border-radius:13px;padding:10px}.bt-stat b{display:block;font-size:18px}.bt-stat span{font-size:8px;color:var(--muted);text-transform:uppercase}.bt-table-wrap{overflow-x:auto;margin-top:12px}.bt-table{width:100%;border-collapse:collapse;min-width:650px;font-size:10px}.bt-table th,.bt-table td{padding:9px 7px;border-bottom:1px solid #1b3527;text-align:left}.bt-table th{color:#8da89a;text-transform:uppercase;font-size:8px}.bt-win{color:#7ee6a6;font-weight:900}.bt-loss{color:#ff9a9f;font-weight:900}.bt-push{color:#ffd98e;font-weight:900}.bt-no{color:#8fa99a}.bt-details{font-size:9px;color:#9db5a7;line-height:1.45;max-width:320px}
@media(min-width:720px){.bt-controls{grid-template-columns:220px 1fr;align-items:end}.bt-summary{grid-template-columns:repeat(6,1fr)}}
'''

BT_HTML = r'''
<section class="bt-panel">
 <div class="bt-head"><div><div class="bt-title">Backtest PRO</div><div class="bt-sub">Testează retrospectiv ce s-ar fi întâmplat dacă cereai zilnic o anumită cotă.</div></div><div class="engine">20 / 40 / 60 ZILE</div></div>
 <div class="bt-controls">
  <div class="bt-input"><label>Cotă dorită pe bilet</label><input id="btTarget" type="number" inputmode="decimal" min="1.05" max="500" step="0.1" value="10"></div>
  <div class="bt-buttons"><button onclick="runBacktest(20)">20 ZILE</button><button onclick="runBacktest(40)">40 ZILE</button><button onclick="runBacktest(60)">60 ZILE</button></div>
 </div>
 <div id="btProgress"></div>
 <div id="btOut"></div>
 <div class="bt-note">Prima rulare poate dura fiindcă planul Pro are limită de request-uri. Zilele deja descărcate se cache-uiesc și următoarele rulări pot fi mult mai rapide. Backtestul folosește cote pre-match/closing disponibile în Pro; tick-history complet este o facilitate separată.</div>
</section>
'''

BT_JS = r'''
let btPollTimer=null;
function btEsc(v){return String(v??'—').replace(/[&<>"']/g,s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));}
function btStatusClass(s){return s==='WIN'?'bt-win':s==='LOSE'?'bt-loss':s==='PUSH'?'bt-push':'bt-no';}
function btStatusText(s){return s==='WIN'?'CÂȘTIGAT':s==='LOSE'?'PIERDUT':s==='PUSH'?'PUSH':s==='NO_TICKET'?'FĂRĂ BILET':s==='UNSETTLED'?'NEEVALUAT':s;}
function setBtButtons(disabled){document.querySelectorAll('.bt-buttons button').forEach(b=>b.disabled=disabled);}
async function runBacktest(days){
 const target=Number(document.getElementById('btTarget').value||0);
 const p=document.getElementById('btProgress'),o=document.getElementById('btOut');
 if(!target||target<1.05||target>500){document.getElementById('btTarget').focus();return}
 if(btPollTimer)clearTimeout(btPollTimer);
 setBtButtons(true);o.innerHTML='';
 p.innerHTML='<div class="bt-progress"><b>Pornesc backtest '+days+' zile pentru cotă '+btEsc(target)+'...</b><div class="bt-bar"><div class="bt-fill" style="width:1%"></div></div></div>';
 try{
  const r=await fetch('/api/backtest/start?days='+encodeURIComponent(days)+'&target='+encodeURIComponent(target));
  const x=await r.json();if(!r.ok)throw Error(x.detail||'Eroare backtest');
  pollBacktest(x.job_id);
 }catch(e){setBtButtons(false);p.innerHTML='<div class="panel error">'+btEsc(e.message)+'</div>'}
}
async function pollBacktest(jobId){
 const p=document.getElementById('btProgress');
 try{
  const r=await fetch('/api/backtest/status?job_id='+encodeURIComponent(jobId));const x=await r.json();if(!r.ok)throw Error(x.detail||'Eroare status');
  if(x.status==='error'){throw Error(x.error||'Backtest eșuat')}
  const pct=Math.max(1,Math.min(100,Math.round((Number(x.progress||0)/Number(x.days||1))*100)));
  const part=x.partial||{};
  p.innerHTML='<div class="bt-progress"><b>'+pct+'% · '+btEsc(x.progress||0)+'/'+btEsc(x.days||0)+' zile</b>'+(x.current_day?' · '+btEsc(x.current_day):'')+(part.tickets!==undefined?' · bilete evaluate '+btEsc(part.tickets):'')+'<div class="bt-bar"><div class="bt-fill" style="width:'+pct+'%"></div></div></div>';
  if(x.status==='done'){setBtButtons(false);renderBacktest(x.result);return}
  btPollTimer=setTimeout(()=>pollBacktest(jobId),1800);
 }catch(e){setBtButtons(false);p.innerHTML='<div class="panel error">'+btEsc(e.message)+'</div>'}
}
function renderBacktest(result){
 const s=result.summary||{},rows=result.daily||[];let h='<div class="bt-summary">';
 h+='<div class="bt-stat"><b>'+btEsc(s.ticket_odds_requested)+'</b><span>Cotă dorită</span></div>';
 h+='<div class="bt-stat"><b>'+btEsc(s.average_actual_odds)+'</b><span>Cotă medie obținută</span></div>';
 h+='<div class="bt-stat"><b>'+btEsc(s.hit_rate)+'%</b><span>Hit-rate bilete</span></div>';
 h+='<div class="bt-stat"><b>'+btEsc(s.wins)+'/'+btEsc(s.tickets)+'</b><span>Bilete câștigate</span></div>';
 h+='<div class="bt-stat"><b>'+btEsc(s.profit)+' lei</b><span>Profit la 100 lei/bilet</span></div>';
 h+='<div class="bt-stat"><b>'+btEsc(s.roi)+'%</b><span>ROI</span></div></div>';
 h+='<div class="panel"><div class="meta"><span>'+btEsc(s.days_requested)+' zile</span><span>'+btEsc(s.fixtures_seen)+' meciuri găsite</span><span>'+btEsc(s.fixtures_analyzed)+' analizate</span><span>'+btEsc(s.no_ticket_days)+' zile fără bilet</span><span>'+btEsc(s.truncated_days)+' zile limitate la 50 meciuri</span></div></div>';
 h+='<div class="bt-table-wrap"><table class="bt-table"><thead><tr><th>Data</th><th>Cotă dorită</th><th>Cotă obținută</th><th>Selecții</th><th>Rezultat</th><th>Profit</th><th>Bilet</th></tr></thead><tbody>';
 for(const d of rows){
  const legs=(d.leg_results||[]).map(q=>btEsc(q.match)+' — '+btEsc(q.selection)+' @'+btEsc(q.odds)+' ['+btEsc(q.score)+']').join('<br>');
  h+='<tr><td>'+btEsc(d.date)+'</td><td>'+btEsc(d.requested_odds)+'</td><td>'+btEsc(d.actual_odds)+'</td><td>'+btEsc(d.legs)+'</td><td class="'+btStatusClass(d.status)+'">'+btStatusText(d.status)+'</td><td>'+((d.status==='WIN'||d.status==='LOSE'||d.status==='PUSH')?btEsc(d.profit)+' lei':'—')+'</td><td class="bt-details">'+(legs||'—')+'</td></tr>';
 }
 h+='</tbody></table></div>';
 document.getElementById('btOut').innerHTML=h;
 document.getElementById('btProgress').innerHTML='<div class="bt-progress"><b>100% · Backtest terminat</b><div class="bt-bar"><div class="bt-fill" style="width:100%"></div></div></div>';
}
'''


@app.get("/", response_class=HTMLResponse)
def home():
    response = server_v6.home()
    html = response.body.decode("utf-8")
    html = html.replace("ENGINE v9.5", "ENGINE v9.6")
    html = html.replace("engine v9.5", "engine v9.6")
    html = html.replace("</style>", BT_CSS + "\n</style>", 1)
    html = html.replace("\n<div id=\"out\"></div>", "\n" + BT_HTML + "\n<div id=\"out\"></div>", 1)
    html = html.replace("</script>", BT_JS + "\n</script>", 1)
    return HTMLResponse(html)
