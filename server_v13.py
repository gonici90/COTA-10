"""Analiza Cota AI v10.2: Big-5 CSV-native walk-forward backtest."""
from fastapi.responses import HTMLResponse
import server_v7
import offline_backtest
import odds_sports

app = server_v7.app
app.version = '10.2'

# Keep the existing async Backtest PRO UI contract, but run the historical model
# directly on Big-5 CSV data instead of mixing API leagues without history.
import backtest_engine
import threading, time, uuid


def _summary_from_offline(days, target, data):
    tb = (data.get('ticket_backtests') or {}).get(str(float(target))) or (data.get('ticket_backtests') or {}).get(str(target))
    if tb is None:
        # offline engine exposes standard targets; build requested target directly.
        tb = offline_backtest._ticket_backtest(data.get('picks') or [], float(target))
    tickets = int(tb.get('tickets') or 0)
    wins = int(tb.get('wins') or 0)
    losses = int(tb.get('losses') or 0)
    return {
        'days': days, 'requested_odds': float(target), 'tickets': tickets,
        'wins': wins, 'losses': losses, 'pushes': 0,
        'hit_rate': float(tb.get('hit_rate') or 0), 'profit': round(float(tb.get('profit') or 0)*100, 2),
        'roi': float(tb.get('roi') or 0), 'average_odds': float(tb.get('avg_odds') or 0) or None,
        'no_ticket_days': max(0, days - tickets), 'fixtures': int(data.get('considered') or 0),
        'analyzed': int(data.get('considered') or 0), 'truncated_days': 0,
        'mode': 'BIG-5 CSV WALK-FORWARD v10.2',
        'note': 'Backtest direct pe CSV: Premier League, La Liga, Serie A, Bundesliga, Ligue 1. Fara ligi API fara istoric si fara limita de 50 meciuri/zi.'
    }


def _run_csv_job(job_id, days, target):
    try:
        with backtest_engine.JOBS_LOCK:
            backtest_engine.JOBS[job_id]['current_day'] = 'Big 5 CSV walk-forward'
            backtest_engine.JOBS[job_id]['progress'] = 1
        data = offline_backtest.run_backtest(max(20, int(days)))
        picks = data.get('picks') or []
        tb = offline_backtest._ticket_backtest(picks, float(target))
        # Convert recent tickets to the UI's daily row format.
        rows=[]
        for t in tb.get('recent_tickets') or []:
            rows.append({'date':t.get('date'),'requested_odds':float(target),'actual_odds':t.get('odds'),'legs':t.get('legs',0),'status':'WIN' if t.get('won') else 'LOSE','profit':round(float(t.get('profit') or 0)*100,2),'leg_results':t.get('selections') or []})
        ticket_dates={r['date'] for r in rows}
        summary=_summary_from_offline(days,target,data)
        # UI detail table is intentionally ticket-only; summary carries no-ticket count.
        result={'summary':summary,'daily':rows}
        with backtest_engine.JOBS_LOCK:
            backtest_engine.JOBS[job_id].update({'status':'done','progress':days,'result':result,'finished_at':time.time(),'current_day':None})
    except Exception as exc:
        with backtest_engine.JOBS_LOCK:
            backtest_engine.JOBS[job_id].update({'status':'error','error':f'{type(exc).__name__}: {str(exc)[:500]}','finished_at':time.time()})

# Replace runner used by server_v7 endpoints.
backtest_engine._run = _run_csv_job


def _drop(path):
    app.router.routes[:] = [r for r in app.router.routes if not (getattr(r,'path',None)==path and 'GET' in (getattr(r,'methods',None) or set()))]
for p in ('/','/health'):
    _drop(p)

@app.get('/health')
def health():
    return {'status':'ok','version':'10.2','backtest':'Big-5 CSV-native walk-forward','backtest_leagues':['Premier League','La Liga','Serie A','Bundesliga','Ligue 1'],'multisport_provider':'The Odds API','the_odds_api_configured':odds_sports.configured(),'ui':'pro-multisport-v10.2'}

@app.get('/',response_class=HTMLResponse)
def home():
    response=server_v7.home(); html=response.body.decode('utf-8')
    html=html.replace('ENGINE v9.6','ENGINE v10.2').replace('engine v9.6','engine v10.2')
    html=html.replace('Backtestul folosește cote pre-match/closing disponibile în Pro; tick-history complet este o facilitate separată.','WALK-FORWARD v10.2: backtest direct pe istoricul CSV Big 5 (Premier League, La Liga, Serie A, Bundesliga, Ligue 1). Nu mai amestecă ligi API fără istoric și nu mai este limitat la 50 meciuri/zi. Predicția folosește numai meciurile anterioare celui testat.')
    return HTMLResponse(html)
