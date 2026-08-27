"""Automatic live-data ingestion for COTA-10.
Uses the already configured footballdata.io API; no Flashscore scraping.
Stores normalized snapshots on Render's cache filesystem so repeated runs do
not waste API quota. This is the data-feed layer used before ML training.
"""
import json
from pathlib import Path
from datetime import date, timedelta
from fastapi import APIRouter, Query, HTTPException
import app as core

router = APIRouter()
STORE = Path(core.CACHE_DIR) / 'auto-data'
STORE.mkdir(parents=True, exist_ok=True)


def _day_file(day):
    return STORE / (day + '.json')


def _normalize(m):
    h = core.team_info(m, 'home'); a = core.team_info(m, 'away')
    hg, ag = core.score_values(m)
    league = m.get('league') or {}
    return {
        'id': m.get('match_id') or m.get('id'),
        'date': str(m.get('date') or m.get('match_date') or m.get('kickoff') or '')[:10],
        'league': league.get('name') if isinstance(league, dict) else str(league),
        'country': league.get('country') if isinstance(league, dict) else '',
        'home_id': h.get('id'), 'home': h.get('name'),
        'away_id': a.get('id'), 'away': a.get('name'),
        'home_goals': hg, 'away_goals': ag,
        'finished': hg is not None and ag is not None,
        'status': m.get('status'),
    }


async def sync_day(day, force=False):
    f = _day_file(day)
    if f.exists() and not force:
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
            return data, True
        except Exception:
            pass
    raw = await core.api_get('/matches/date/' + day, {'limit': 100})
    matches = [_normalize(m) for m in core.extract_matches(raw)]
    payload = {'date': day, 'matches': matches, 'count': len(matches)}
    f.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return payload, False


@router.post('/api/data/sync')
async def sync(days: int = Query(1, ge=1, le=14), force: bool = False):
    today = date.today(); total = 0; fetched = 0; cached = 0; finished = 0; errors = []
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        try:
            payload, was_cached = await sync_day(d, force=force)
            total += payload['count']; finished += sum(1 for m in payload['matches'] if m['finished'])
            cached += int(was_cached); fetched += int(not was_cached)
        except Exception as e:
            errors.append({'date': d, 'error': str(e)[:200]})
    return {'ok': not errors, 'days': days, 'api_days_fetched': fetched,
            'cached_days': cached, 'matches': total, 'finished': finished,
            'store': str(STORE), 'errors': errors}


@router.get('/api/data/status')
def status():
    files = sorted(STORE.glob('*.json'))
    total = finished = 0
    for f in files:
        try:
            p = json.loads(f.read_text(encoding='utf-8')); total += p.get('count', 0)
            finished += sum(1 for m in p.get('matches', []) if m.get('finished'))
        except Exception:
            pass
    return {'stored_days': len(files), 'matches': total, 'finished': finished,
            'first_day': files[0].stem if files else None,
            'last_day': files[-1].stem if files else None}
