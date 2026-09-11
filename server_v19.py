"""Analiza Cota AI v16.2: fast independent LIVE model + all-market optimizer."""
from fastapi.responses import HTMLResponse
import server_v18
import market_engine, auto_data
import football_independent_live_fast
import ticket_optimizer_v162

football_independent_live_fast.install(market_engine,auto_data)
ticket_optimizer_v162.install(market_engine)
app=server_v18.app
app.version='16.2'

def _drop(path):
    app.router.routes[:]=[r for r in app.router.routes if not (getattr(r,'path',None)==path and 'GET' in (getattr(r,'methods',None) or set()))]
for _p in ('/','/health'): _drop(_p)

@app.get('/health')
def health():
    return {'status':'ok','version':'16.2','football_provider':'5DollarFootballAPI Pro','live_model':'INDEPENDENT: recent form + home/away goals + league environment + Elo + H2H','odds_role':'PRICE/EV ONLY - never probability input','live_markets':['1','X','2','Goals Over/Under','BTTS','Asian Handicap'],'optimizer':'ALL MARKETS: maximize whole-ticket joint probability; max one selection per match; no forced market quotas','history_loader':'fast persistent cache + max 2 pages per uncached league','backtest':'Pro multimarket walk-forward','lookahead':False}

@app.get('/',response_class=HTMLResponse)
def home():
    response=server_v18.home(); html=response.body.decode('utf-8')
    html=html.replace('ENGINE v15.0','ENGINE v16.2').replace('engine v15.0','engine v16.2')
    html=html.replace('API PRO v15 MULTIMARKET: motorul nu mai caută doar solist. Pentru fiecare meci compară 1/X/2 și Over/Under pe goal-line atunci când istoricul Pro conține cota Bet365 reală. Toate piețele concurează în același optimizer; maximum o selecție pe meci, fără cote inventate și fără look-ahead.','LIVE v16.2: model independent din datele echipelor. Pentru bilet, optimizerul compară GLOBAL toate piețele disponibile — 1X2, Goals Over/Under, GG/NG și Asian Handicap — și maximizează probabilitatea comună a întregului bilet pentru cota cerută. Nu favorizează și nu impune soliste; maximum o selecție pe meci.')
    return HTMLResponse(html)
