"""Analiza Cota AI v9.8: strict non-solo low-odds historical backtest."""
from fastapi.responses import HTMLResponse

import server_v8
import odds_sports

app = server_v8.app
app.version = "9.8"


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
        "version": "9.8",
        "football_provider": "5DollarFootballAPI Pro hybrid + Bet365",
        "multisport_provider": "The Odds API",
        "the_odds_api_configured": odds_sports.configured(),
        "backtest": "quality-v2 multi-market 20/40/60 days",
        "backtest_rules": "target <=3: zero 1X2, min 2 legs, 8 deep fixtures/day; target >3: max 1 straight 1X2",
        "ui": "pro-multisport-v8",
    }


@app.get("/", response_class=HTMLResponse)
def home():
    response = server_v8.home()
    html = response.body.decode("utf-8")
    html = html.replace("ENGINE v9.7", "ENGINE v9.8")
    html = html.replace("engine v9.7", "engine v9.8")
    html = html.replace(
        "Mod QUALITY: pentru cote normale construiește minimum 2 selecții, permite cel mult un solist 1X2 pe bilet și caută piețe complete Bet365 pe un shortlist istoric. La prima rulare poate dura mai mult; deep odds se cache-uiesc.",
        "QUALITY v2: la cotă cerută <=3.00 nu folosește deloc 1/X/2. Construiește minimum 2 selecții din Goals/AH/BTTS/Corners/Cards și verifică până la 8 meciuri/zi cu cote Bet365 complete. La cote mai mari permite maximum un 1X2. Prima rulare poate dura mai mult; deep odds se cache-uiesc.",
        1,
    )
    return HTMLResponse(html)
