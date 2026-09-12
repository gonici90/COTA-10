"""History loader with fast scanner and deep single-match analysis.
Team history matching prefers stable provider team IDs and falls back to normalized names.
"""
import json, os, time, re, unicodedata
from pathlib import Path
import football_independent_live as base
CACHE=Path(os.getenv('COTA_CACHE_DIR','/tmp/cota10-cache'))/'live-independent-history';CACHE.mkdir(parents=True,exist_ok=True)
DISK_TTL=24*3600;MIN_TEAM_MATCHES=6

def _rows(raw):
    if isinstance(raw,dict):rows=raw.get('fixtures') or raw.get('data') or [];pag=raw.get('pagination') or {}
    else:rows,pag=raw or [],{}
    if isinstance(rows,dict):rows=rows.get('fixtures') or rows.get('data') or []
    return [x for x in rows if isinstance(x,dict)],pag

def _team_id(t):
    if not isinstance(t,dict):return None
    v=t.get('id') or t.get('team_id') or t.get('teamId');return str(v) if v not in(None,'') else None
def _norm(v):
    s=unicodedata.normalize('NFKD',str(v or '')).encode('ascii','ignore').decode().lower();s=s.replace('&',' and ');s=re.sub(r'\b(fc|cf|afc|sc|ac|fk|nk|sk|sv|club|football|calcio)\b',' ',s);s=re.sub(r'[^a-z0-9]+',' ',s);aliases={'man utd':'manchester united','man city':'manchester city','tottenham hotspur':'tottenham','spurs':'tottenham','paris saint germain':'psg','internazionale':'inter','inter milan':'inter','bayern munchen':'bayern munich','borussia monchengladbach':'monchengladbach'};s=' '.join(s.split());return aliases.get(s,s)
def _identity(engine,f):
    h,a=engine._teams(f);return ((_team_id(h),_norm(h.get('name') if isinstance(h,dict) else h)),(_team_id(a),_norm(a.get('name') if isinstance(a,dict) else a)))
def _same(t,c):
    ti,tn=t;ci,cn=c
    if ti and ci:return ti==ci
    if tn and cn:
        if tn==cn:return True
        a,b=set(tn.split()),set(cn.split());return bool(a and b) and (a<=b or b<=a)
    return False
def _usable(engine,rows,f):
    wanted=list(_identity(engine,f));counts=[0,0]
    for r in rows:
        rh,ra=_identity(engine,r)
        for i,w in enumerate(wanted):
            if _same(w,rh) or _same(w,ra):counts[i]+=1
    return all(v>=MIN_TEAM_MATCHES for v in counts)
def _read(p):
    try:
        o=json.loads(p.read_text(encoding='utf-8'))
        if time.time()-float(o.get('saved_at') or 0)<=DISK_TTL:return o.get('matches') or []
    except Exception:pass
def _write(p,rows):
    try:p.write_text(json.dumps({'saved_at':time.time(),'matches':rows},ensure_ascii=False),encoding='utf-8')
    except Exception:pass
def _backtest(engine,lid,f):
    root=Path(os.getenv('COTA_CACHE_DIR','/tmp/cota10-cache'))/'backtest-pro-wf-v12'/'ranges';cutoff=engine._kickoff_ts(f)
    try:ps=sorted(root.glob(f'*-{lid}.json'),key=lambda p:p.stat().st_mtime,reverse=True)
    except Exception:return None
    for p in ps[:4]:
        try:
            o=json.loads(p.read_text(encoding='utf-8'));rows=[x for x in(o.get('matches') or[]) if isinstance(x,dict) and engine._kickoff_ts(x)<cutoff];rows.sort(key=engine._kickoff_ts)
            if _usable(engine,rows,f):return rows
        except Exception:continue
def _fetch(engine,fd,f,pages):
    lid=base._league_id(f);ko=engine._kickoff_ts(f);end=max(1,ko-1);start=end-base.HISTORY_DAYS*86400;out=[]
    for page in range(1,pages+1):
        try:raw=fd._get(f'/leagues/{lid}/fixtures',{'start_time':start,'end_time':end,'status':'finished','per_page':50,'page':page,'lang':'en'});rows,pag=_rows(raw)
        except Exception:break
        out.extend(rows)
        if _usable(engine,out,f) or not pag.get('has_more'):break
    out.sort(key=engine._kickoff_ts);return out
def fast_history(engine,fd,f):
    lid=base._league_id(f)
    if not lid:return []
    key=str(lid);now=time.time();c=base._HISTORY.get(key)
    if c and now-c[0]<base.TTL and _usable(engine,c[1],f):return c[1]
    disk=CACHE/f'{key}.json';rows=_read(disk)
    if rows is not None and _usable(engine,rows,f):base._HISTORY[key]=(now,rows);return rows
    rows=_backtest(engine,lid,f)
    if rows:base._HISTORY[key]=(now,rows);_write(disk,rows);return rows
    rows=_fetch(engine,fd,f,1);base._HISTORY[key]=(now,rows);_write(disk,rows);return rows
def deep_history(engine,fd,f,max_pages=6):
    lid=base._league_id(f)
    if not lid:return []
    key=str(lid);now=time.time();c=base._HISTORY.get(key)
    if c and _usable(engine,c[1],f):return c[1]
    disk=CACHE/f'{key}.json';rows=_read(disk)
    if rows is not None and _usable(engine,rows,f):base._HISTORY[key]=(now,rows);return rows
    rows=_backtest(engine,lid,f)
    if rows:base._HISTORY[key]=(now,rows);_write(disk,rows);return rows
    rows=_fetch(engine,fd,f,max_pages)
    if rows:base._HISTORY[key]=(now,rows);_write(disk,rows)
    return rows
base._history=fast_history
install=base.install
