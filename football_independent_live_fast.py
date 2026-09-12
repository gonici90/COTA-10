"""Fail-fast history loader for live football.

Live HTTP requests must never spend minutes paging historical league data. Use
memory/disk/backtest caches; if history is not already available, make at most
ONE provider request for that league. This trades a few unanalyzed fixtures on
a cold cache for predictable response time and no hour-long failed requests.
"""
import json, os, time
from pathlib import Path
import football_independent_live as base

CACHE=Path(os.getenv('COTA_CACHE_DIR','/tmp/cota10-cache'))/'live-independent-history'; CACHE.mkdir(parents=True,exist_ok=True)
DISK_TTL=24*3600
MAX_PAGES=1
MIN_TEAM_MATCHES=3


def _rows(raw):
    if isinstance(raw,dict): rows=raw.get('fixtures') or raw.get('data') or []; pag=raw.get('pagination') or {}
    else: rows,pag=raw or [],{}
    if isinstance(rows,dict): rows=rows.get('fixtures') or rows.get('data') or []
    return [x for x in rows if isinstance(x,dict)],pag


def _names(engine,f):
    h,a=engine._teams(f); return str(h.get('name') or '').lower(),str(a.get('name') or '').lower()


def _usable(engine,rows,f):
    wanted=[x for x in _names(engine,f) if x]; counts={x:0 for x in wanted}
    for r in rows:
        h,a=engine._teams(r); ns={str(h.get('name') or '').lower(),str(a.get('name') or '').lower()}
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
    root=Path(os.getenv('COTA_CACHE_DIR','/tmp/cota10-cache'))/'backtest-pro-wf-v12'/'ranges'; cutoff=engine._kickoff_ts(f)
    try: ps=sorted(root.glob(f'*-{lid}.json'),key=lambda p:p.stat().st_mtime,reverse=True)
    except Exception:return None
    for p in ps[:4]:
        try:
            o=json.loads(p.read_text(encoding='utf-8')); rows=[x for x in (o.get('matches') or []) if isinstance(x,dict) and engine._kickoff_ts(x)<cutoff]; rows.sort(key=engine._kickoff_ts)
            if _usable(engine,rows,f):return rows
        except Exception:continue


def fast_history(engine,fd,f):
    lid=base._league_id(f)
    if not lid:return []
    key=str(lid); now=time.time(); c=base._HISTORY.get(key)
    if c and now-c[0]<base.TTL:return c[1]
    disk=CACHE/f'{key}.json'; rows=_read(disk)
    if rows is not None and _usable(engine,rows,f):base._HISTORY[key]=(now,rows); return rows
    rows=_backtest(engine,lid,f)
    if rows:base._HISTORY[key]=(now,rows); _write(disk,rows); return rows
    # Cold-cache safety: ONE history call only. Never paginate inside a live request.
    ko=engine._kickoff_ts(f); end=max(1,ko-1); start=end-base.HISTORY_DAYS*86400
    try: raw=fd._get(f'/leagues/{lid}/fixtures',{'start_time':start,'end_time':end,'status':'finished','per_page':50,'page':1,'lang':'en'}); rows,_=_rows(raw)
    except Exception: rows=[]
    rows.sort(key=engine._kickoff_ts)
    base._HISTORY[key]=(now,rows); _write(disk,rows)
    return rows

base._history=fast_history
install=base.install
