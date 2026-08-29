"""Analiza Cota AI v9.7: quality-first, multi-market historical backtest."""
from fastapi.responses import HTMLResponse

import server_v7
import backtest_engine
import market_engine
import auto_data
import odds_sports
import backtest_quality_patch

backtest_quality_patch.install(backtest_engine, market_engine, auto_data)

app = server_v7.app
app.version = "9.7"


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
        "version": "9.7",
        "football_provider": "5DollarFootballAPI Pro hybrid + Bet365",
        "multisport_provider": "The Odds API",
        "the_odds_api_configured": odds_sports.configured(),
        "backtest": "quality-first multi-market 20/40/60 days",
        "backtest_rules": "min 2 legs for normal targets; max 1 straight 1X2; deep historical markets; up to 100 fixtures/day",
        "ui": "pro-multisport-v7",
    }


@app.get("/", response_class=HTMLResponse)
def home():
    response = server_v7.home()
    html = response.body.decode("utf-8")
    html = html.replace("ENGINE v9.6", "ENGINE v9.7")
    html = html.replace("engine v9.6", "engine v9.7")

    # Expose the new quality diagnostics in the result grid.
    old = "h+='<div class=\"bt-stat\"><b>'+btEsc(s.roi)+'%</b><span>ROI</span></div></div>';"
    new = "h+='<div class=\"bt-stat\"><b>'+btEsc(s.roi)+'%</b><span>ROI</span></div>';\n" \
          " h+='<div class=\"bt-stat\"><b>'+btEsc(s.average_legs)+'</b><span>Selecții medii/bilet</span></div>';\n" \
          " h+='<div class=\"bt-stat\"><b>'+btEsc(s.non_solist_share)+'%</b><span>Selecții non-solist</span></div></div>';"
    html = html.replace(old, new, 1)

    old_panel = "<span>'+btEsc(s.truncated_days)+' zile limitate la 50 meciuri</span>"
    new_panel = "<span>'+btEsc(s.page2_days||0)+' zile extinse la pagina 2</span><span>'+btEsc(s.deep_success||0)+' deep-market reușite</span><span>'+btEsc(s.solist_legs||0)+' soliste 1X2</span><span>'+btEsc(s.non_solist_legs||0)+' non-soliste</span>"
    html = html.replace(old_panel, new_panel, 1)

    html = html.replace(
        "Backtestul folosește cote pre-match/closing disponibile în Pro; tick-history complet este o facilitate separată.",
        "Mod QUALITY: pentru cote normale construiește minimum 2 selecții, permite cel mult un solist 1X2 pe bilet și caută piețe complete Bet365 pe un shortlist istoric. La prima rulare poate dura mai mult; deep odds se cache-uiesc.",
        1,
    )
    return HTMLResponse(html)
