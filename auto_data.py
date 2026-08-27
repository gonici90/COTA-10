"""5DollarFootballAPI client/cache for COTA-10. Secrets stay in Render env."""
import os, json
from pathlib import Path
from datetime import date, timedelta, datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from fastapi import APIRouter, Query, HTTPException

router=APIRouter()
STORE=Path(os.getenv('COTA_CACHE_DIR','/tmp/cota10-cache'))/'auto-data'; STORE.mkdir(parents=True,exist_ok=True)
BASE='https://api.5dollarfootballapi.com/v1'

def _key():
    k=os.getenv('FIVEDOLLAR_API_KEY','').strip()
    if not k: raise HTTPException(503,'Lipseste FIVEDOLLAR_API_KEY in Render Environment')
    return k

def _get(path,params=None):
    url=BASE+path
    if params:url+='?'+urlencode({k:v for k,v in params.items() if v is not None})
    req=Request(url,headers={'Authorization':'Bearer '+_key(),'Accept':'application/json','User-Agent':'COTA-10/6.0'})
    try:
        with urlopen(req,timeout=25) as r:data=json.loads(r.read().decode('utf-8'))
    except HTTPError as e:
        body=e.read().decode('utf-8','ignore')[:300]
        raise HTTPException(502,f'5DollarFootballAPI HTTP {e.code}: {body}')
    except (URLError,TimeoutError) as e: raise HTTPException(502,'5DollarFootballAPI indisponibil: '+str(e)[:180])
    except Exception as e: raise HTTPException(502,'5DollarFootballAPI: '+str(e)[:180])
    if isinstance(data,dict) and data.get('success') in (0,False): raise HTTPException(502,str(data.get('message') or data.get('error') or 'API error'))
    return data.get('data',data) if isinstance(data,dict) else data

def _ts(day,end=False):
    d=datetime.fromisoformat(day).replace(tzinfo=timezone.utc)+(timedelta(days=1) if end else timedelta())
    return int(d.timestamp())

def _fixtures(day,include_odds=False):
    params={'start_time':_ts(day),'end_time':_ts(day,True),'per_page':50}; out=[]
    if include_odds:params['include']='odds'
    for page in range(1,11):
        params['page']=page; raw=_get('/fixtures',params)
        if isinstance(raw,dict):rows=raw.get('fixtures') or raw.get('data') or []; pag=raw.get('pagination') or {}
        else:rows=raw or [];pag={}
        out.extend(rows)
        if not rows or (not pag.get('has_more') and len(rows)<50):break
    return out

def sync_day(day,force=False):
    f=STORE/(day+'.json')
    if f.exists() and not force:
        try:return json.loads(f.read_text(encoding='utf-8')),True
        except Exception:pass
    rows=_fixtures(day,True); payload={'source':'5DollarFootballAPI','date':day,'matches':rows,'count':len(rows)}
    f.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8');return payload,False

@router.post('/api/data/sync')
def sync(days:int=Query(1,ge=1,le=14),force:bool=False):
    total=fetched=cached=0;errors=[]
    for i in range(days):
        d=(date.today()-timedelta(days=i)).isoformat()
        try:p,c=sync_day(d,force);total+=p['count'];cached+=int(c);fetched+=int(not c)
        except Exception as e:errors.append({'date':d,'error':str(e)[:220]})
    return {'ok':not errors,'source':'5DollarFootballAPI','days':days,'api_days_fetched':fetched,'cached_days':cached,'matches':total,'errors':errors}

@router.get('/api/data/fixture/{fixture_id}/odds')
def fixture_odds(fixture_id:int):return {'source':'5DollarFootballAPI','fixture_id':fixture_id,'data':_get(f'/fixtures/{fixture_id}/odds',{'bookmakers':'bet365'})}

@router.get('/api/data/status')
def status():
    files=sorted(STORE.glob('*.json'))
    return {'source':'5DollarFootballAPI','configured':bool(os.getenv('FIVEDOLLAR_API_KEY')),'stored_days':len(files),'first_day':files[0].stem if files else None,'last_day':files[-1].stem if files else None}
