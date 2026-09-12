"""Football live v17.2: scan the useful pool, not the first arbitrary fixtures.

5Dollar bulk is the independent-model source. The Odds API supplements major
soccer leagues when configured. No per-fixture deep odds calls are allowed here.
"""
import time
from datetime import datetime, timedelta


def install(engine,fd):
    def key(f):
        fid=f.get('id') or f.get('fixture_id');h,a=engine._teams(f);return fid or (h.get('name'),a.get('name'),engine._kickoff_ts(f))
    def inline_odds(f):
        raw=f.get('odds') or f.get('markets') or f.get('_pro_bulk_odds')
        if raw:
            try:
                o=engine._normalize_odds({'odds':raw})
                if engine._has_prices(o):return o
            except Exception:pass
        fid=f.get('id') or f.get('fixture_id');c=engine._ODDS_CACHE.get(fid) if fid else None
        if c and time.time()-c[0]<engine.ODDS_CACHE_TTL and engine._has_prices(c[1]):return c[1]
        return None
    def norm(s):return ''.join(ch for ch in str(s or '').lower() if ch.isalnum())
    def analyze_period(day,target=10,days=1,limit=200):
        days=max(1,min(int(days),7));start=datetime.fromisoformat(day).date();fs=[];by_day={};seen=set()
        for i in range(days):
            d=(start+timedelta(days=i)).isoformat();got=engine._day_fixtures(d);by_day[d]=len(got)
            for f in got:
                if isinstance(f,dict) and key(f) not in seen:seen.add(key(f));fs.append(f)
        # ROOT FIX: inspect all fixtures cheaply, then analyze those that ALREADY have
        # usable bulk/cached odds. Never waste the 80-slot budget on no-odds fixtures.
        priced=[]
        for f in fs:
            o=inline_odds(f)
            if o:
                ef=dict(f);ef.pop('_pro_bulk_odds',None);ef['odds']=o;priced.append(ef)
        priced=priced[:min(120,max(1,int(limit)))]
        rows=[];errors=[];market_counts={}
        for f in priced:
            try:
                r=engine.analyze_fixture(f)
                if r.get('best_market'):
                    rows.append(r)
                    for p in r.get('markets') or []:
                        m=str(p.get('market') or '');typ='1X2' if m in {'1','X','2'} else 'GOALS' if m.startswith(('Over ','Under ')) else 'BTTS' if m in {'GG','NG'} else 'AH' if m.startswith('AH ') else 'OTHER';market_counts[typ]=market_counts.get(typ,0)+1
            except Exception as e:errors.append({'fixture':f.get('id') or f.get('fixture_id'),'error':type(e).__name__+': '+str(e)[:160]})
        # Major-league fallback. It is intentionally a second source: broader coverage,
        # consensus probabilities, and h2h/spreads/totals. Failure never breaks football.
        extra=[];extra_err=None
        try:
            import odds_sports
            if odds_sports.configured():
                ex=odds_sports.analyze_group('soccer',day,target,days,league_limit=10);extra=ex.get('ranking') or []
        except Exception as e:extra_err=type(e).__name__+': '+str(e)[:160]
        existing={(norm(r.get('home')),norm(r.get('away'))) for r in rows}
        for r in extra:
            k=(norm(r.get('home')),norm(r.get('away')))
            if k not in existing:rows.append(r);existing.add(k)
        rows.sort(key=lambda x:(x.get('best_market') or {}).get('recommendation_score',0),reverse=True)
        combo,diag=engine.build_combo(rows,target);diag['market_inventory']=market_counts;diag['major_soccer_added']=len(extra)
        return {'date':day,'days':days,'period_end':(start+timedelta(days=days-1)).isoformat(),'provider':'5Dollar independent + The Odds API major soccer','fixtures_by_day':by_day,'api_fixtures':len(fs),'eligible':len(fs),'attempted':len(priced),'analyzed':len(rows),'without_usable_odds':max(0,len(fs)-len(priced)),'analysis_errors':errors,'ranking':rows,'suggested_combo':combo,'combo_diagnostics':diag,'hybrid':{'mode':'SCAN_ALL_PRICED_PLUS_MAJOR_SOCCER','priced_5dollar':len(priced),'major_soccer_added':len(extra),'major_soccer_error':extra_err,'market_inventory':market_counts}}
    engine.analyze_period=analyze_period
    engine.analyze_day=lambda day,target=10,limit=12:analyze_period(day,target,1,limit)
