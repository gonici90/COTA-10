"""Analiza Cota AI v14.0: practical residual-bucket calibrated API-Pro walk-forward."""
from fastapi.responses import HTMLResponse

import backtest_pro_bucketcal
import backtest_pro_walkforward
import odds_sports
import server_v7

backtest_pro_bucketcal.install()
app = server_v7.app
app.version = "14.0"


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
        "version": "14.0",
        "football_provider": "5DollarFootballAPI Pro",
        "backtest": "independent model + empirical residual-bucket calibration",
        "warmup_days": backtest_pro_walkforward.WARMUP_DAYS,
        "range_days": backtest_pro_walkforward.TOTAL_RANGE_DAYS,
        "max_leagues": backtest_pro_walkforward.MAX_LEAGUES,
        "markets": ["1", "X", "2"],
        "lookahead": False,
        "odds_used_as_base_model_input": False,
        "calibration": "walk-forward residual buckets with Bayesian shrinkage",
        "multisport_provider": "The Odds API",
        "the_odds_api_configured": odds_sports.configured(),
        "ui": "pro-multisport-v14",
    }


@app.get("/", response_class=HTMLResponse)
def home():
    response = server_v7.home()
    html = response.body.decode("utf-8")
    html = html.replace("ENGINE v9.6", "ENGINE v14.0").replace("engine v9.6", "engine v14.0")
    html = html.replace(
        "Backtestul folosește cote pre-match/closing disponibile în Pro; tick-history complet este o facilitate separată.",
        "API PRO v14: model independent (formă, goluri home/away, Elo) + calibrare walk-forward pe bucket-uri ale diferenței model vs Bet365. Nu mai există gate-ul global din v13 care putea bloca toate biletele. Fiecare bucket învață doar din rezultate anterioare și este shrink-uit către probabilitatea bookmakerului. Fără look-ahead.",
    )
    html = html.replace(
        "Prima rulare poate dura fiindcă planul Pro are limită de request-uri. Zilele deja descărcate se cache-uiesc și următoarele rulări pot fi mult mai rapide.",
        "Istoricul API Pro este cache-uit și refolosit. v14 folosește până la 16 ligi pentru mai multă acoperire și calibrează separat dimensiunea abaterii față de Bet365, fără să forțeze un edge global.",
    )
    return HTMLResponse(html)
