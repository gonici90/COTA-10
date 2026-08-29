"""Analiza Cota AI v9.4: fast Pro bulk football odds."""
from fastapi.responses import HTMLResponse

import server_v4
import market_engine
import auto_data
import odds_sports
import football_pro_patch

football_pro_patch.install(market_engine, auto_data)

app = server_v4.app
app.version = "9.4"


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
        "version": "9.4",
        "football_provider": "5DollarFootballAPI Pro bulk odds + Bet365",
        "multisport_provider": "The Odds API",
        "the_odds_api_configured": odds_sports.configured(),
        "optimizer": "safety-first-global + ticket/per-match constraints",
        "football_fetch": "bulk include=odds; no per-match fallback for bulk rows",
        "ui": "pro-multisport-v4",
    }


@app.get("/", response_class=HTMLResponse)
def home():
    response = server_v4.home()
    html = response.body.decode("utf-8")
    html = html.replace("ENGINE v9.3", "ENGINE v9.4")
    html = html.replace("engine v9.3", "engine v9.4")
    return HTMLResponse(html)
