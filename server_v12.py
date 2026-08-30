"""Analiza Cota AI v10.1: CSV-seeded independent walk-forward football backtest."""
from fastapi.responses import HTMLResponse
import server_v7
import backtest_engine
import market_engine
import backtest_walkforward_v2
import odds_sports

backtest_walkforward_v2.install(backtest_engine, market_engine)
app = server_v7.app
app.version = '10.1'


def _drop(path):
    app.router.routes[:] = [
        r for r in app.router.routes
        if not (getattr(r, 'path', None) == path and 'GET' in (getattr(r, 'methods', None) or set()))
    ]


for p in ('/', '/health'):
    _drop(p)


@app.get('/health')
def health():
    return {
        'status': 'ok',
        'version': '10.1',
        'football_provider': '5DollarFootballAPI Pro bulk',
        'multisport_provider': 'The Odds API',
        'the_odds_api_configured': odds_sports.configured(),
        'backtest': 'CSV-seeded walk-forward Elo + form + goals; 1X2/DC/goals',
        'backtest_api_warmup_days': 10,
        'backtest_min_team_history': 8,
        'ui': 'pro-multisport-v10.1',
    }


@app.get('/', response_class=HTMLResponse)
def home():
    response = server_v7.home()
    html = response.body.decode('utf-8')
    html = html.replace('ENGINE v9.6', 'ENGINE v10.1').replace('engine v9.6', 'engine v10.1')
    html = html.replace(
        'Backtestul folosește cote pre-match/closing disponibile în Pro; tick-history complet este o facilitate separată.',
        'WALK-FORWARD v10.1: Elo, forma și golurile sunt inițializate din istoricul CSV al ligilor, numai cu meciuri anterioare perioadei testate. Apoi modelul se actualizează cronologic din rezultate API. Bookmakerul este folosit doar după predicție, pentru preț/comparație. Piețe: 1/X/2, șanse duble și goluri. Nu forțează bilet.'
    )
    return HTMLResponse(html)
