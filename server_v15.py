"""Analiza Cota AI v12.0: independent API Pro walk-forward backtest."""
from fastapi.responses import HTMLResponse

import backtest_pro_walkforward
import odds_sports
import server_v7

backtest_pro_walkforward.install()
app = server_v7.app
app.version = "12.0"


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
        "version": "12.0",
        "football_provider": "5DollarFootballAPI Pro",
        "backtest": "independent walk-forward: form + home/away goals + Elo, then Bet365 edge",
        "warmup_days": backtest_pro_walkforward.WARMUP_DAYS,
        "range_days": backtest_pro_walkforward.TOTAL_RANGE_DAYS,
        "max_leagues": backtest_pro_walkforward.MAX_LEAGUES,
        "markets": ["1", "X", "2", "goal line Over/Under when direct Bet365 price is fetched"],
        "lookahead": False,
        "odds_used_as_model_input": False,
        "multisport_provider": "The Odds API",
        "the_odds_api_configured": odds_sports.configured(),
        "ui": "pro-multisport-v12",
    }


@app.get("/", response_class=HTMLResponse)
def home():
    response = server_v7.home()
    html = response.body.decode("utf-8")
    html = html.replace("ENGINE v9.6", "ENGINE v12.0").replace("engine v9.6", "engine v12.0")
    html = html.replace(
        "Backtestul folosește cote pre-match/closing disponibile în Pro; tick-history complet este o facilitate separată.",
        "API PRO WALK-FORWARD v12: probabilitățile NU mai sunt construite din cote. Motorul învață cronologic din rezultate anterioare: formă, goluri acasă/deplasare și Elo. Abia după predicție compară cu probabilitatea de-vig Bet365 și joacă doar când modelul are edge. Rezultatele zilei sunt introduse în model numai după ce toate predicțiile acelei zile au fost făcute.",
    )
    html = html.replace(
        "Prima rulare poate dura fiindcă planul Pro are limită de request-uri. Zilele deja descărcate se cache-uiesc și următoarele rulări pot fi mult mai rapide.",
        "Prima rulare descarcă și cache-uiește istoricul de warm-up + 60 zile din API Pro pe ligile selectate; după cache, testele următoare sunt mult mai rapide.",
    )
    return HTMLResponse(html)
