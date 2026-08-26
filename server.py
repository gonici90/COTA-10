import json
from pathlib import Path

import app as core
from fastapi import Query
from offline_backtest import router

app = core.app

# Cache EVERY read-only match request, including odds. Previously odds were
# fetched again on every click even when the fixture had already been analysed.
core._cacheable = lambda path: path.startswith('/matches')

# Keep the expensive live analysis shared between COTA 5 and COTA 10.
# The target only changes combo construction; rankings/fixture analysis are identical.
ANALYSIS_CACHE = Path(core.CACHE_DIR) / 'live-analysis'
ANALYSIS_CACHE.mkdir(parents=True, exist_ok=True)

_old_analyze_route = next(
    (r for r in app.router.routes if getattr(r, 'path', None) == '/api/analyze'),
    None,
)
_old_analyze = _old_analyze_route.endpoint if _old_analyze_route else None

if _old_analyze_route:
    app.router.routes.remove(_old_analyze_route)

@app.get('/api/analyze')
async def analyze_cached(
    day: str = Query(default_factory=lambda: core.date.today().isoformat()),
    limit: int = 8,
    target: float = 10.0,
):
    limit = max(1, min(int(limit), 8))
    cache_file = ANALYSIS_CACHE / f'{day}-{limit}.json'
    base = None

    if cache_file.exists():
        try:
            base = json.loads(cache_file.read_text(encoding='utf-8'))
        except Exception:
            base = None

    if base is None:
        # One expensive analysis only. target is irrelevant for ranking itself.
        base = await _old_analyze(day=day, limit=limit, target=10.0)
        # Do not persist the target-specific combo; rebuild it cheaply below.
        base = dict(base)
        base.pop('suggested_combo', None)
        try:
            cache_file.write_text(json.dumps(base, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass

    result = dict(base)
    result['target'] = target
    result['suggested_combo'] = core.build_target_combo(result.get('ranking', []), target)
    result['cache_reused'] = cache_file.exists()
    return result

# Remove the old API-backed backtest route, then register the offline one.
app.router.routes[:] = [r for r in app.router.routes if getattr(r, 'path', None) != '/api/backtest']
app.include_router(router)
