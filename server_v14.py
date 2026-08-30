"""Analiza Cota AI v11.0: API-native Pro historical backtest."""
from fastapi.responses import HTMLResponse

import backtest_engine
import backtest_pro_native
import odds_sports
import server_v7

backtest_pro_native.install()
app = server_v7.app
app.version = "11.0"


def _drop(path):
    app.router.routes[:] = [
        r for r in app.router.routes
        if not (getattr(r, "path", None) == path and "GET" in (getattr(r, "methods", None) or set()))
    ]


for _p in ("/", "/health"):
    _drop(_p)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "11.0",
        "football_provider": "5DollarFootballAPI Pro",
        "backtest": "API-native 60-day reusable dataset, league-range bulk + adaptive full goal odds",
        "backtest_markets": ["1", "X", "2", "goal line Over/Under"],
        "double_chance_backtest": "disabled: no direct historical 1X/X2/12 bookmaker price in provider markets",
        "max_backtest_leagues": backtest_pro_native.MAX_LEAGUES,
        "goal_deep_per_no_ticket_day": backtest_pro_native.DEEP_GOAL_CALLS_PER_DAY,
        "multisport_provider": "The Odds API",
        "the_odds_api_configured": odds_sports.configured(),
        "ui": "pro-multisport-v11",
    }


@app.get("/", response_class=HTMLResponse)
def home():
    response = server_v7.home()
    html = response.body.decode("utf-8")
    html = html.replace("ENGINE v9.6", "ENGINE v11.0").replace("engine v9.6", "engine v11.0")
    html = html.replace(
        "Backtestul folosește cote pre-match/closing disponibile în Pro; tick-history complet este o facilitate separată.",
        "API PRO v11: fără CSV. Încarcă și cache-uiește un set reutilizabil de 60 zile direct din 5DollarFootballAPI Pro, pe ligi, fără limita de 50 meciuri/zi. 1/X/2 folosesc cote Bet365 reale din bulk; dacă nu se poate construi biletul, motorul cere adaptiv cote complete reale pentru goal-line Over/Under. Șansele duble nu sunt inventate: rămân în afara backtestului până există preț istoric direct 1X/X2/12.",
    )
    html = html.replace(
        "Prima rulare poate dura fiindcă planul Pro are limită de request-uri. Zilele deja descărcate se cache-uiesc și următoarele rulări pot fi mult mai rapide.",
        "Prima rulare construiește cache-ul API Pro pe 60 zile; după aceea testele 20/40/60 zile refolosesc același set și sunt mult mai rapide.",
    )
    return HTMLResponse(html)
