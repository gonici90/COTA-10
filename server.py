from app import app
from offline_backtest import router

# Remove the old API-backed backtest route, then register the offline one.
app.router.routes[:] = [r for r in app.router.routes if getattr(r, 'path', None) != '/api/backtest']
app.include_router(router)
