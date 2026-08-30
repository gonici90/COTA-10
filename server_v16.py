"""Analiza Cota AI v13.0: calibrated independent API-Pro walk-forward."""
from fastapi.responses import HTMLResponse

import backtest_pro_calibrated
import backtest_pro_walkforward
import odds_sports
import server_v7

backtest_pro_calibrated.install()
app = server_v7.app
app.version = "13.0"


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
        "version": "13.0",
        "football_provider": "5DollarFootballAPI Pro",
        "backtest": "independent model + online residual calibration against Bet365",
        "warmup_days": backtest_pro_walkforward.WARMUP_DAYS,
        "range_days": backtest_pro_walkforward.TOTAL_RANGE_DAYS,
        "max_leagues": backtest_pro_walkforward.MAX_LEAGUES,
        "markets": ["1", "X", "2"],
        "lookahead": False,
        "raw_odds_used_as_model_input": False,
        "calibration_min_samples": backtest_pro_calibrated.MIN_CAL_SAMPLES,
        "multisport_provider": "The Odds API",
        "the_odds_api_configured": odds_sports.configured(),
        "ui": "pro-multisport-v13",
    }


@app.get("/", response_class=HTMLResponse)
def home():
    response = server_v7.home()
    html = response.body.decode("utf-8")
    html = html.replace("ENGINE v9.6", "ENGINE v13.0").replace("engine v9.6", "engine v13.0")
    html = html.replace(
        "Backtestul folosește cote pre-match/closing disponibile în Pro; tick-history complet este o facilitate separată.",
        "API PRO v13: predicția de bază rămâne independentă (formă, goluri home/away, Elo). Un strat separat, antrenat numai pe meciuri deja încheiate, măsoară cât din abaterea modelului față de Bet365 a fost reală și micșorează edge-ul când modelul este supraîncrezător. Biletul intră numai dacă probabilitatea calibrată încă depășește pragul de break-even. Fără look-ahead.",
    )
    html = html.replace(
        "Prima rulare poate dura fiindcă planul Pro are limită de request-uri. Zilele deja descărcate se cache-uiesc și următoarele rulări pot fi mult mai rapide.",
        "Istoricul Pro deja cache-uit este refolosit. v13 nu mai face deep-fetch pentru goals: întâi validăm corect 1/X/2 și calibrarea, fără să mascăm un model slab cu mai multe piețe.",
    )
    return HTMLResponse(html)
