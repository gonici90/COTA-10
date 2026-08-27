"""Automatic 5DollarFootballAPI feed for COTA-10.
The API key is read ONLY from Render environment: FIVEDOLLAR_API_KEY.
No secret is stored in GitHub.
"""
import os, json
from pathlib import Path
from datetime import date, timedelta, datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from fastapi import APIRouter, Query, HTTPException
import app as core

router = APIRouter()
STORE = Path(core.CACHE_DIR) / 'auto-data'
STORE.mkdir(parents=True, exist_ok=True)
BASE = 'https://api.5dollarfootballapi.com/v1'


def _key():
    k = os.getenv('FIVEDOLLAR_API_KEY', '').strip()
    if not k:
        raise HTTPException(503, 'Lipseste FIVEDOLLAR_API_KEY in Render Environment')
    return k


def _get(path, params=None):
    url = BASE + path
    if params:
        url += '?' + urlencode({k:v for k,v in params.items() if v is not None})
    req = Request(url, headers={'Authorization':'Bearer ' + _key(), 'Accept':'application/json', 'User-Agent':'COTA-10/1.0'})
    try:
        with urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode('utf-8'))
    except Exception as e:
        raise HTTPException(502, '5DollarFootballAPI: ' + str(e)[:180])
    if isinstance(data, dict) and data.get('success') in (0, False):
        raise HTTPException(502, str(data.get('message') or data.get('error') or 'API error'))
    return data.get('data', data) if isinstance(data, dict) else data


def _ts(day, end=False):
    d = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    if end: d += timedelta(days=1)
    return int(d.timestamp())


def _fixtures(day, include_odds=True):
    params={'start_time':_ts(day), 'end_time':_ts(day, True), 'per_page':50}
    if include_odds: params['include']='odds'
    out=[]; page=1
    while page <= 10:
        params['page']=page
        raw=_get('/fixtures', params)
        if isinstance(raw, dict):
            rows=raw.get('fixtures') or raw.get('data') or []
            pag=raw.get('pagination') or {}
        else:
            rows=raw or []; pag={}
        out.extend(rows)
        if not pag.get('has_more') and len(rows)<50: break
        page += 1
    return out


def _side_price(obj, *names):
    if not isinstance(obj, dict): return None
    for n in names:
        v=obj.get(n)
        try:
            if v is not None: return float(v)
        except Exception: pass
    return None


def _market_snapshot(market):
    if not isinstance(market, dict): return None
    snap=market.get('closing') or market.get('current') or market.get('opening')
    return snap if isinstance(snap, dict) else None


def _extract_odds(fixture):
    raw=fixture.get('odds') or {}
    # include=odds may return the market map directly or under bookmaker(s).
    if isinstance(raw, dict) and 'bookmakers' in raw:
        books=raw.get('bookmakers') or []
        b=next((x for x in books if x.get('slug')=='bet365'), books[0] if books else {})
        raw=b.get('odds') or {}
    markets={}
    aliases={
        '1x2':('1x2',), 'asian_handicap':('asian_handicap','asian'),
        'goal_line':('goal_line','goalline'), 'corner_line':('corner_line','corner'),
        'corner_asian':('corner_asian',), 'card_line':('card_line','cards'),
        'card_asian':('card_asian','cards_asian'), 'btts':('btts',)
    }
    for dst,names in aliases.items():
        obj=None
        for n in names:
            if isinstance(raw,dict) and raw.get(n) is not None: obj=raw.get(n); break
        if obj is not None: markets[dst]=obj
    return markets


def _normalize(m):
    teams=m.get('teams') or {}; h=teams.get('home') or {}; a=teams.get('away') or {}
    goals=m.get('goals') or {}; corners=m.get('corners') or {}; cards=m.get('cards') or {}
    league=m.get('league') or {}; odds=_extract_odds(m)
    return {
        'id':m.get('id'), 'kickoff_utc':m.get('kickoff_utc'), 'status':m.get('status'),
        'league_id':league.get('id'), 'league':league.get('name'),
        'home_id':h.get('id'), 'home':h.get('name'), 'away_id':a.get('id'), 'away':a.get('name'),
        'home_goals':goals.get('home'), 'away_goals':goals.get('away'),
        'corners':corners, 'cards':cards, 'odds':odds,
        'finished':m.get('status')=='finished' or (goals.get('home') is not None and goals.get('away') is not None)
    }


def sync_day(day, force=False):
    f=STORE/(day+'.json')
    if f.exists() and not force:
        try:return json.loads(f.read_text(encoding='utf-8')), True
        except Exception:pass
    rows=[_normalize(x) for x in _fixtures(day, include_odds=True)]
    payload={'source':'5DollarFootballAPI','date':day,'matches':rows,'count':len(rows)}
    f.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    return payload,False


@router.post('/api/data/sync')
def sync(days:int=Query(1,ge=1,le=14),force:bool=False):
    today=date.today(); total=finished=markets=fetched=cached=0; errors=[]
    for i in range(days):
        d=(today-timedelta(days=i)).isoformat()
        try:
            p,was_cached=sync_day(d,force)
            total+=p['count']; finished+=sum(1 for m in p['matches'] if m['finished'])
            markets+=sum(len(m.get('odds') or {}) for m in p['matches'])
            cached+=int(was_cached); fetched+=int(not was_cached)
        except Exception as e: errors.append({'date':d,'error':str(e)[:220]})
    return {'ok':not errors,'source':'5DollarFootballAPI','days':days,'api_days_fetched':fetched,
            'cached_days':cached,'matches':total,'finished':finished,'market_snapshots':markets,'errors':errors}


@router.get('/api/data/fixture/{fixture_id}/odds')
def fixture_odds(fixture_id:int):
    # Full Bet365 prices: Asian handicap, goal line, corners, corner AH, cards, card AH and BTTS.
    return {'source':'5DollarFootballAPI','fixture_id':fixture_id,'data':_get('/fixtures/%s/odds'%fixture_id, {'bookmakers':'bet365'})}


@router.get('/api/data/status')
def status():
    files=sorted(STORE.glob('*.json')); total=finished=markets=0
    for f in files:
        try:
            p=json.loads(f.read_text(encoding='utf-8')); total+=p.get('count',0)
            finished+=sum(1 for m in p.get('matches',[]) if m.get('finished'))
            markets+=sum(len(m.get('odds') or {}) for m in p.get('matches',[]))
        except Exception:pass
    return {'source':'5DollarFootballAPI','configured':bool(os.getenv('FIVEDOLLAR_API_KEY')),
            'stored_days':len(files),'matches':total,'finished':finished,'market_snapshots':markets,
            'first_day':files[0].stem if files else None,'last_day':files[-1].stem if files else None}
