"""Analiza Cota AI v9.10: fast quality backtest for 1X2, double chance and goals."""
from fastapi.responses import HTMLResponse
import server_v7
import backtest_engine
import market_engine
import backtest_fast_patch
import odds_sports

backtest_fast_patch.install(backtest_engine, market_engine)
app = server_v7.app
app.version = '9.10'


def _drop(path):
    app.router.routes[:] = [r for r in app.router.routes if not (getattr(r, 'path', None) == path and 'GET' in (getattr(r, 'methods', None) or set()))]


for p in ('/', '/health'):
    _drop(p)


@app.get('/health')
def health():
    return {
        'status': 'ok',
        'version': '9.10',
        'football_provider': '5DollarFootballAPI Pro bulk',
        'multisport_provider': 'The Odds API',
        'the_odds_api_configured': odds_sports.configured(),
        'backtest': 'QUALITY FAST v2: 1X2 + double chance + goals; no forced daily ticket',
        'ui': 'pro-multisport-v9',
    }


@app.get('/', response_class=HTMLResponse)
def home():
    response = server_v7.home()
    html = response.body.decode('utf-8')
    html = html.replace('ENGINE v9.6', 'ENGINE v9.10').replace('engine v9.6', 'engine v9.10')
    html = html.replace(
        'Backtestul folosește cote pre-match/closing disponibile în Pro; tick-history complet este o facilitate separată.',
        'QUALITY FAST v2: doar 1/X/2, șanse duble și goluri. Nu mai forțează bilet zilnic; o zi trebuie să conțină cel puțin un semnal de calitate. Folosește bulk + cache, fără deep requests. Cotele de șansă dublă pot fi estimate din 1X2 când nu există preț direct.'
    )
    return HTMLResponse(html)
