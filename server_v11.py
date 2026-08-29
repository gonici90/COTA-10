"""Analiza Cota AI v10.0: independent walk-forward football backtest."""
from fastapi.responses import HTMLResponse
import server_v7
import backtest_engine
import market_engine
import backtest_walkforward_patch
import odds_sports

backtest_walkforward_patch.install(backtest_engine, market_engine)
app = server_v7.app
app.version = '10.0'


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
        'version': '10.0',
        'football_provider': '5DollarFootballAPI Pro bulk',
        'multisport_provider': 'The Odds API',
        'the_odds_api_configured': odds_sports.configured(),
        'backtest': 'walk-forward independent Elo + form + goals; 1X2/DC/goals',
        'backtest_warmup_days': 21,
        'ui': 'pro-multisport-v10',
    }


@app.get('/', response_class=HTMLResponse)
def home():
    response = server_v7.home()
    html = response.body.decode('utf-8')
    html = html.replace('ENGINE v9.6', 'ENGINE v10.0').replace('engine v9.6', 'engine v10.0')
    html = html.replace(
        'Backtestul folosește cote pre-match/closing disponibile în Pro; tick-history complet este o facilitate separată.',
        'WALK-FORWARD v10: modelul calculează singur probabilitățile din Elo, formă și golurile meciurilor ANTERIOARE. Cota bookmakerului este folosită abia după predicție, pentru preț și comparație. Piețe: 1/X/2, șanse duble și goluri. Are 21 zile de warm-up și nu forțează bilet dacă nu găsește avantaj.'
    )
    return HTMLResponse(html)
