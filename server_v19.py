"""COTA 10 v20: sportsbook browse-first UI + on-demand single-match analysis."""
from fastapi.responses import HTMLResponse
import server_v18
import market_engine, auto_data
import football_independent_live_fast
import ticket_optimizer_v162
import sportsbook_ui_v20

football_independent_live_fast.install(market_engine,auto_data)
ticket_optimizer_v162.install(market_engine)
app=server_v18.app
sportsbook_ui_v20.install(app)
app.version='20.0'

def _drop(path):
    app.router.routes[:]=[r for r in app.router.routes if not (getattr(r,'path',None)==path and 'GET' in (getattr(r,'methods',None) or set()))]
for _p in ('/','/health'): _drop(_p)

@app.get('/health')
def health():
    return {'status':'ok','version':'20.0','mode':'SPORTSBOOK_BROWSE_FIRST','football_provider':'5DollarFootballAPI Pro','live_model':'independent team-data model','single_match_analysis':'on demand','routes':['/sportsbook','/api/fixtures','/api/match-analysis'],'lookahead':False}

@app.get('/',response_class=HTMLResponse)
def home():
    # Make the new sportsbook the primary experience while keeping the old ticket
    # constructor reachable from its existing API/UI code during migration.
    from fastapi.responses import RedirectResponse
    return RedirectResponse('/sportsbook',status_code=307)
