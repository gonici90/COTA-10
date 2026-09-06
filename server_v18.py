"""Analiza Cota AI v15.0: API-Pro multimarket walk-forward."""
from fastapi.responses import HTMLResponse
import backtest_pro_multimarket
import backtest_pro_walkforward
import odds_sports
import server_v7

backtest_pro_multimarket.install()
app=server_v7.app
app.version="15.0"

def _drop(path):
    app.router.routes[:]=[r for r in app.router.routes if not (getattr(r,"path",None)==path and "GET" in (getattr(r,"methods",None) or set()))]
for _p in ("/","/health"): _drop(_p)

@app.get("/health")
def health():
    return {"status":"ok","version":"15.0","football_provider":"5DollarFootballAPI Pro","backtest":"independent multimarket walk-forward","markets":["1","X","2","Goals Over/Under"],"odds":"real Bet365 historical only","lookahead":False,"multisport_provider":"The Odds API","the_odds_api_configured":odds_sports.configured()}

@app.get("/",response_class=HTMLResponse)
def home():
    response=server_v7.home(); html=response.body.decode("utf-8")
    html=html.replace("ENGINE v9.6","ENGINE v15.0").replace("engine v9.6","engine v15.0")
    html=html.replace("Backtestul folosește cote pre-match/closing disponibile în Pro; tick-history complet este o facilitate separată.","API PRO v15 MULTIMARKET: motorul nu mai caută doar solist. Pentru fiecare meci compară 1/X/2 și Over/Under pe goal-line atunci când istoricul Pro conține cota Bet365 reală. Toate piețele concurează în același optimizer; maximum o selecție pe meci, fără cote inventate și fără look-ahead.")
    return HTMLResponse(html)
