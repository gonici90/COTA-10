"""History loader with two modes: fast scanner and deep single-match analysis."""
import json, os, time
from pathlib import Path
import football_independent_live as base
CACHE=Path(os.getenv('COTA_CACHE_DIR','/tmp/cota10-cache'))/'live-independent-history';CACHE.mkdir(parents=True,exist_ok=True)
DISK_TTL=24*3600;MIN_TEAM_MATCHES=3

def _rows(raw):
    if isinstance(raw,dict):rows=raw.get('fixtures') or raw.get('data') or [];pag=raw.get('pagination') or {}
    else:rows,pag=raw or [],{}
    if isinstance(rows,dict):rows=rows.get('fixtures') or rows.get('data') or []
    return [x for x in rows if isinstance(x,dict)],pag

def _names(engine,f):
    h,a=engine._teams(f);return str(h.get('name') or '').lower(),str(a.get('name') or '').lower()
def _usable(engine,rows,f):
    wanted=[x for x in _names(engine,f) if x];counts={x:0 for x in wanted}
    for r in rows:
        h,a=engine._teams(r);ns={str(h.get('name') or '').lower(),str(a.get('name') or '').lower()}
        for n in counts:
            if n in ns:counts[n]+=1
    return bool(counts) and all(v>=MIN_TEAM_MATCHES for v in counts.values())
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
            o=json.loads(p.read_text(encoding='utf-8'));rows=[x for x in (o.get('matches') or []) if isinstance(x,dict) and engine._kickoff_ts(x)<cutoff];rows.sort(key=engine._kickoff_ts)
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
    # Scanner remains fail-fast: one page.
    rows=_fetch(engine,fd,f,1);base._HISTORY[key]=(now,rows);_write(disk,rows);return rows

def deep_history(engine,fd,f,max_pages=6):
    """For an explicitly selected match only. Fetch pages until both teams have history."""
    lid=base._league_id(f)
    if not lid:return []
    key=str(lid);now=time.time();c=base._HISTORY.get(key)
    if c and _usable(engine,c[1],f):return c[1]
    rows=_fetch(engine,fd,f,max_pages)
    if rows:base._HISTORY[key]=(now,rows);_write(CACHE/f'{key}.json',rows)
    return rows

base._history=fast_history
install=base.install
