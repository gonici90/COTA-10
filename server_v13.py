"""Analiza Cota AI v10.2.1: Big-5 CSV-native walk-forward backtest."""
from fastapi.responses import HTMLResponse
import server_v7, offline_backtest, odds_sports, backtest_engine, time
app=server_v7.app
app.version='10.2.1'

def _summary(days,target,data,tb):
 tickets=int(tb.get('tickets') or 0); wins=int(tb.get('wins') or 0); losses=int(tb.get('losses') or 0)
 return {'days_requested':int(days),'ticket_odds_requested':round(float(target),2),'tickets':tickets,'wins':wins,'losses':losses,'pushes':0,'hit_rate':float(tb.get('hit_rate') or 0),'profit':round(float(tb.get('profit') or 0)*100,2),'roi':float(tb.get('roi') or 0),'average_actual_odds':float(tb.get('avg_odds') or 0) or None,'no_ticket_days':max(0,int(days)-tickets),'unsettled_days':0,'fixtures_seen':int(data.get('considered') or 0),'fixtures_analyzed':int(data.get('considered') or 0),'truncated_days':0,'note':'Big-5 CSV walk-forward; fara limita API 50/zi.'}

def _run_csv_job(job_id,days,target):
 try:
  with backtest_engine.JOBS_LOCK:
   backtest_engine.JOBS[job_id]['current_day']='Big 5 CSV walk-forward';backtest_engine.JOBS[job_id]['progress']=1
  data=offline_backtest.run_backtest(max(20,int(days)));tb=offline_backtest._ticket_backtest(data.get('picks') or [],float(target));rows=[]
  for t in tb.get('recent_tickets') or []:
   legs=[{'match':q.get('match'),'selection':q.get('market'),'odds':q.get('odds'),'score':'OK' if q.get('won') else 'MISS'} for q in (t.get('selections') or [])]
   rows.append({'date':t.get('date'),'requested_odds':round(float(target),2),'actual_odds':t.get('odds'),'legs':t.get('legs',0),'status':'WIN' if t.get('won') else 'LOSE','profit':round(float(t.get('profit') or 0)*100,2),'leg_results':legs})
  result={'summary':_summary(days,target,data,tb),'daily':rows}
  with backtest_engine.JOBS_LOCK:backtest_engine.JOBS[job_id].update({'status':'done','progress':int(days),'partial':result['summary'],'result':result,'finished_at':time.time(),'current_day':None})
 except Exception as exc:
  with backtest_engine.JOBS_LOCK:backtest_engine.JOBS[job_id].update({'status':'error','error':f'{type(exc).__name__}: {str(exc)[:500]}','finished_at':time.time()})
backtest_engine._run=_run_csv_job

def _drop(path):app.router.routes[:]=[r for r in app.router.routes if not(getattr(r,'path',None)==path and 'GET' in (getattr(r,'methods',None) or set()))]
for p in ('/','/health'):_drop(p)
@app.get('/health')
def health():return {'status':'ok','version':'10.2.1','backtest':'Big-5 CSV-native walk-forward','backtest_leagues':['Premier League','La Liga','Serie A','Bundesliga','Ligue 1'],'multisport_provider':'The Odds API','the_odds_api_configured':odds_sports.configured(),'ui':'pro-multisport-v10.2.1'}
@app.get('/',response_class=HTMLResponse)
def home():
 response=server_v7.home();html=response.body.decode('utf-8');html=html.replace('ENGINE v9.6','ENGINE v10.2.1').replace('engine v9.6','engine v10.2.1');html=html.replace('Backtestul folosește cote pre-match/closing disponibile în Pro; tick-history complet este o facilitate separată.','WALK-FORWARD v10.2.1: backtest direct pe istoricul CSV Big 5. Fără ligi API fără istoric și fără limita de 50 meciuri/zi. Predicția folosește numai meciurile anterioare celui testat.');return HTMLResponse(html)
