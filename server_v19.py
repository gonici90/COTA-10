"""Analiza Cota AI v16.1: fast odds-independent LIVE football model."""
from fastapi.responses import HTMLResponse
import server_v18
import market_engine, auto_data
import football_independent_live_fast

football_independent_live_fast.install(market_engine,auto_data)
app=server_v18.app
app.version='16.1'

def _drop(path):
    app.router.routes[:]=[r for r in app.router.routes if not (getattr(r,'path',None)==path and 'GET' in (getattr(r,'methods',None) or set()))]
for _p in ('/','/health'): _drop(_p)

@app.get('/health')
def health():
    return {'status':'ok','version':'16.1','football_provider':'5DollarFootballAPI Pro','live_model':'INDEPENDENT: recent form + home/away goals + league environment + Elo + H2H','odds_role':'PRICE/EV ONLY - never probability input','live_markets':['1','X','2','Goals Over/Under','BTTS','Asian Handicap'],'history_loader':'fast persistent cache + max 2 pages per uncached league','backtest':'Pro multimarket walk-forward','lookahead':False}

@app.get('/',response_class=HTMLResponse)
def home():
    response=server_v18.home(); html=response.body.decode('utf-8')
    html=html.replace('ENGINE v15.0','ENGINE v16.1').replace('engine v15.0','engine v16.1')
    html=html.replace('API PRO v15 MULTIMARKET: motorul nu mai caută doar solist. Pentru fiecare meci compară 1/X/2 și Over/Under pe goal-line atunci când istoricul Pro conține cota Bet365 reală. Toate piețele concurează în același optimizer; maximum o selecție pe meci, fără cote inventate și fără look-ahead.','LIVE v16.1 INDEPENDENT: probabilitatea este calculată exclusiv din istoricul echipelor: formă, home/away, goluri marcate/primite, nivelul ligii, Elo și H2H cu pondere mică. Cotele Bet365 sunt citite numai DUPĂ predicție, pentru preț/EV și construirea biletului. Istoricul live este cache-uit și încărcat rapid, fără scanări de 9 pagini pe ligă. Piețe live: 1X2, Goals, BTTS și Asian Handicap când există preț real.')
    return HTMLResponse(html)
