"""Hybrid football enrichment for 5DollarFootballAPI Pro.

Bulk scan remains fast. Deep Bet365 odds are fetched for enough competitive
fixtures to give the ticket optimiser real Goals/AH/BTTS alternatives instead
of effectively feeding it 1X2-only rows.
"""
import time
from datetime import datetime, timedelta

# Four deep rows was the reason COTA tickets still became almost all 1X2.
# Eight is a deliberate compromise with the provider's 10 req/min pacing.
DETAIL_LIMIT_SHORT = 8
DETAIL_LIMIT_LONG = 6


def install(engine, fd):
    def _fixture_key(f):
        fid=f.get('id') or f.get('fixture_id'); h,a=engine._teams(f)
        return fid or (h.get('name'),a.get('name'),engine._kickoff_ts(f))

    def _full_odds(f):
        fid=f.get('id') or f.get('fixture_id')
        if not fid:return None
        cached=engine._ODDS_CACHE.get(fid)
        if cached and time.time()-cached[0] < engine.ODDS_CACHE_TTL and engine._has_prices(cached[1]):return cached[1]
        try: odds=engine._normalize_odds(fd._get(f'/fixtures/{fid}/odds',{'bookmakers':'bet365'}))
        except Exception:return None
        if not engine._has_prices(odds):return None
        engine._ODDS_CACHE[fid]=(time.time(),odds); return odds

    def analyze_period(day,target=10,days=1,limit=200):
        days=max(1,min(int(days),7)); start=datetime.fromisoformat(day).date(); fs=[]; by_day={}; seen=set()
        for i in range(days):
            d=(start+timedelta(days=i)).isoformat(); got=engine._day_fixtures(d); by_day[d]=len(got)
            for f in got:
                if not isinstance(f,dict):continue
                k=_fixture_key(f)
                if k not in seen:seen.add(k); fs.append(f)
        attempt=min(len(fs),max(1,min(int(limit),200))); rows=[]; no=[]; errors=[]; fixtures={}
        for f in fs[:attempt]:
            fixtures[_fixture_key(f)]=f
            try:
                r=engine.analyze_fixture(f); (rows if r.get('best_market') else no).append(r)
            except Exception as e:
                h,a=engine._teams(f); errors.append({'fixture':f.get('id') or f.get('fixture_id'),'match':h.get('name','?')+' - '+a.get('name','?'),'error':type(e).__name__+': '+str(e)[:180]})

        # Prioritise strong rows that have only the bulk 1X2 trio. Those are precisely
        # the fixtures where a deep call can unlock Goals, BTTS and Asian Handicap.
        def score(r):
            b=r.get('best_market') or {}; p=float(b.get('ticket_probability') or b.get('probability') or 0)
            only_1x2=all(str(x.get('market')) in {'1','X','2'} for x in (r.get('markets') or []))
            return (only_1x2,p,float(b.get('recommendation_score') or 0))
        rows.sort(key=score,reverse=True)
        detail_limit=DETAIL_LIMIT_SHORT if days<=3 else DETAIL_LIMIT_LONG
        shortlist=[]
        for r in rows:
            if len(shortlist)>=detail_limit:break
            fid=r.get('fixture_id')
            f=next((x for x in fs[:attempt] if (x.get('id') or x.get('fixture_id'))==fid),None)
            if f is not None:shortlist.append(f)

        replacements={}; enriched=0; market_counts={}
        for f in shortlist:
            odds=_full_odds(f)
            if not odds:continue
            ef=dict(f); ef.pop('_pro_bulk_odds',None); ef['odds']=odds
            try:r=engine.analyze_fixture(ef)
            except Exception:continue
            if r.get('best_market'):
                replacements[r.get('fixture_id') or _fixture_key(f)]=r; enriched+=1
                for p in r.get('markets') or []:
                    m=str(p.get('market') or ''); typ='1X2' if m in {'1','X','2'} else 'GOALS' if m.startswith(('Over ','Under ')) else 'BTTS' if m in {'GG','NG'} else 'AH' if m.startswith('AH ') else 'OTHER'; market_counts[typ]=market_counts.get(typ,0)+1

        final=[]
        for r in rows:
            k=r.get('fixture_id') or (r.get('home'),r.get('away'),r.get('kickoff')); final.append(replacements.get(k,r))
        final.sort(key=lambda x:(x.get('best_market') or {}).get('recommendation_score',0),reverse=True)
        combo,diag=engine.build_combo(final,target)
        diag['deep_market_inventory']=market_counts
        return {'date':day,'days':days,'period_end':(start+timedelta(days=days-1)).isoformat(),'provider':'5DollarFootballAPI Pro hybrid + Bet365','fixtures_by_day':by_day,'api_fixtures':len(fs),'eligible':len(fs),'attempted':attempt,'analyzed':len(final),'without_usable_odds':max(0,attempt-len(final)),'no_odds_examples':[{'fixture':x.get('fixture_id'),'match':str(x.get('home','?'))+' - '+str(x.get('away','?'))} for x in no[:20]],'analysis_errors':errors,'ranking':final,'suggested_combo':combo,'combo_diagnostics':diag,'hybrid':{'bulk_scan':attempt,'deep_odds_requested':len(shortlist),'deep_odds_enriched':enriched,'deep_limit':detail_limit,'deep_market_inventory':market_counts}}

    engine.analyze_period=analyze_period
    engine.analyze_day=lambda day,target=10,limit=12:analyze_period(day,target,1,limit)
