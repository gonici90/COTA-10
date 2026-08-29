"""Analiza Cota AI v9.5: fast bulk scan + shortlisted full football markets."""
from fastapi.responses import HTMLResponse

import server_v5
import market_engine
import auto_data
import odds_sports
import football_hybrid_patch

football_hybrid_patch.install(market_engine, auto_data)

app = server_v5.app
app.version = "9.5"


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
        "version": "9.5",
        "football_provider": "5DollarFootballAPI Pro hybrid + Bet365",
        "multisport_provider": "The Odds API",
        "the_odds_api_configured": odds_sports.configured(),
        "optimizer": "safety-first-global + ticket/per-match constraints",
        "football_fetch": "bulk scan + 3/4 shortlisted full-odds requests",
        "football_markets": "1X2 + Goals + Asian Handicap + BTTS + Corners + Cards when quoted",
        "ui": "pro-multisport-v5",
    }


@app.get("/", response_class=HTMLResponse)
def home():
    response = server_v5.home()
    html = response.body.decode("utf-8")
    html = html.replace("ENGINE v9.4", "ENGINE v9.5")
    html = html.replace("engine v9.4", "engine v9.5")
    return HTMLResponse(html)
