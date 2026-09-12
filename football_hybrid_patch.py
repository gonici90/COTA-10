"""Fast live football analysis.

The user-facing request performs no per-fixture deep-odds network calls. It uses
bulk fixture odds plus any full odds already present in cache. This guarantees
that multimarket enrichment cannot hold the HTTP request behind provider pacing.
"""
import time
from datetime import datetime, timedelta


def install(engine,fd):
    def key(f):
        fid=f.get('id') or f.get('fixture_id'); h,a=engine._teams(f)
        return fid or (h.get('name'),a.get('name'),engine._kickoff_ts(f))

    def analyze_period(day,target=10,days=1,limit=200):
        days=max(1,min(int(days),7)); start=datetime.fromisoformat(day).date(); fs=[]; by_day={}; seen=set()
        for i in range(days):
            d=(start+timedelta(days=i)).isoformat(); got=engine._day_fixtures(d); by_day[d]=len(got)
            for f in got:
                if isinstance(f,dict) and key(f) not in seen:seen.add(key(f)); fs.append(f)
        # Bound live work. More fixtures do not justify an unbounded synchronous request.
        attempt=min(len(fs),max(1,min(int(limit),80))); rows=[]; no=[]; errors=[]; market_counts={}
        for f in fs[:attempt]:
            try:
                fid=f.get('id') or f.get('fixture_id'); ef=f
                c=engine._ODDS_CACHE.get(fid) if fid else None
                if c and time.time()-c[0]<engine.ODDS_CACHE_TTL and engine._has_prices(c[1]):
                    ef=dict(f); ef.pop('_pro_bulk_odds',None); ef['odds']=c[1]
                r=engine.analyze_fixture(ef)
                if r.get('best_market'):
                    rows.append(r)
                    for p in r.get('markets') or []:
                        m=str(p.get('market') or ''); typ='1X2' if m in {'1','X','2'} else 'GOALS' if m.startswith(('Over ','Under ')) else 'BTTS' if m in {'GG','NG'} else 'AH' if m.startswith('AH ') else 'OTHER'; market_counts[typ]=market_counts.get(typ,0)+1
                else:no.append(r)
            except Exception as e:
                h,a=engine._teams(f); errors.append({'fixture':f.get('id') or f.get('fixture_id'),'match':h.get('name','?')+' - '+a.get('name','?'),'error':type(e).__name__+': '+str(e)[:180]})
        rows.sort(key=lambda x:(x.get('best_market') or {}).get('recommendation_score',0),reverse=True)
        combo,diag=engine.build_combo(rows,target); diag['market_inventory']=market_counts
        return {'date':day,'days':days,'period_end':(start+timedelta(days=days-1)).isoformat(),'provider':'5DollarFootballAPI Pro FAST bulk','fixtures_by_day':by_day,'api_fixtures':len(fs),'eligible':len(fs),'attempted':attempt,'analyzed':len(rows),'without_usable_odds':max(0,attempt-len(rows)),'no_odds_examples':[{'fixture':x.get('fixture_id'),'match':str(x.get('home','?'))+' - '+str(x.get('away','?'))} for x in no[:20]],'analysis_errors':errors,'ranking':rows,'suggested_combo':combo,'combo_diagnostics':diag,'hybrid':{'mode':'FAST_NO_DEEP_NETWORK','market_inventory':market_counts}}
    engine.analyze_period=analyze_period
    engine.analyze_day=lambda day,target=10,limit=12:analyze_period(day,target,1,limit)
