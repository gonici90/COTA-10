"""Fast historical backtest: only 1X2, double chance and goals; bulk odds only."""
import math


def install(bt, engine):
    original_summary = bt._summary

    def kind(name):
        n = str(name or '').strip()
        if n in {'1','X','2'}: return '1X2'
        if n in {'1X','X2','12'}: return 'DOUBLE_CHANCE'
        if n.startswith('Over ') or n.startswith('Under '): return 'GOALS'
        return None

    def synth_double_chance(markets):
        by = {str(x.get('market')): x for x in markets if str(x.get('market')) in {'1','X','2'}}
        out = []
        for label, pair in [('1X',('1','X')),('X2',('X','2')),('12',('1','2'))]:
            if not all(x in by for x in pair): continue
            try:
                p = min(.97, sum(float(by[x].get('ticket_probability') or by[x].get('probability') or 0) for x in pair)/100.0)
                # fair-ish synthetic price from model probability, conservatively shaded
                odd = max(1.05, round((1.0/p)*0.95, 2))
            except Exception: continue
            out.append({'market':label,'bookmaker_odds':odd,'probability':round(p*100,2),'ticket_probability':round(p*100,2),'recommendation_score':round(p*100,2),'suspicious':False,'synthetic_price':True})
        return out

    def settle(sel, odd, fixture):
        n=str(sel or '')
        if n not in {'1X','X2','12'}: return bt._settle_selection(n, odd, fixture)
        h,a=bt._score_values(fixture)
        if h is None: return None
        win=(n=='1X' and h>=a) or (n=='X2' and a>=h) or (n=='12' and h!=a)
        return float(odd) if win else 0.0

    def analyze_day(day,target):
        payload=bt._fetch_finished_day(day); fixtures=payload['matches']; rows=[]; lookup={}
        for f in fixtures:
            if bt._score_values(f)[0] is None: continue
            ef=dict(f); ef['_pro_bulk_odds']=True
            parsed=bt._prematch_only(bt._extract_inline_odds(f))
            if parsed: ef['odds']=parsed
            try: row=engine.analyze_fixture(ef)
            except Exception: continue
            markets=[m for m in (row.get('markets') or []) if kind(m.get('market')) in {'1X2','GOALS'} and bt._market_data_available(m.get('market'),f)]
            markets += synth_double_chance(markets)
            if not markets: continue
            row['markets']=markets
            row['best_market']=max(markets,key=lambda x:(not bool(x.get('suspicious')),float(x.get('ticket_probability') or x.get('probability') or 0)))
            rows.append(row); lookup[bt._fixture_key_from_row(row)]=f
        rows.sort(key=lambda r:float((r.get('best_market') or {}).get('ticket_probability') or 0),reverse=True)
        # Reuse global optimizer, but only after restricting markets to the three requested families.
        combo,diag=engine.build_combo(rows,float(target))
        base={'date':day,'requested_odds':round(float(target),2),'fixtures':len(fixtures),'analyzed':len(rows),'truncated':bool(payload.get('truncated'))}
        if not combo: return {**base,'status':'NO_TICKET','actual_odds':None,'legs':0,'profit':0.0,'return_factor':None,'diagnostics':diag,'market_mix':{}}
        factor=1.0; details=[]; mix={}; unresolved=False
        for leg in combo.get('matches') or []:
            f=lookup.get((str(leg.get('home')),str(leg.get('away')),str(leg.get('kickoff'))))
            if f is None: unresolved=True; break
            lf=settle(leg.get('selection'),leg.get('odds'),f)
            if lf is None: unresolved=True; break
            factor*=lf; h,a=bt._score_values(f); k=kind(leg.get('selection')) or 'OTHER'; mix[k]=mix.get(k,0)+1
            details.append({'match':f"{leg.get('home')} - {leg.get('away')}",'selection':leg.get('selection'),'odds':leg.get('odds'),'score':f'{h}-{a}','kind':k,'return_factor':round(lf,3)})
        if unresolved: return {**base,'status':'UNSETTLED','actual_odds':combo.get('combined_odds'),'legs':len(combo.get('matches') or []),'profit':0.0,'return_factor':None,'leg_results':details,'market_mix':mix}
        profit=bt.STAKE*(factor-1.0); status='WIN' if profit>.005 else 'LOSE' if profit<-.005 else 'PUSH'
        return {**base,'status':status,'actual_odds':combo.get('combined_odds'),'legs':len(combo.get('matches') or []),'estimated_probability':combo.get('estimated_joint_probability'),'return_factor':round(factor,4),'profit':round(profit,2),'leg_results':details,'market_mix':mix}

    def summary(days,target,daily):
        out=original_summary(days,target,daily); mix={}
        for r in daily:
            for k,n in (r.get('market_mix') or {}).items(): mix[k]=mix.get(k,0)+int(n or 0)
        out.update({'market_mix':mix,'mode':'FAST 1X2 + DOUBLE CHANCE + GOALS','note':'Fast bulk-only backtest. Double-chance prices are derived estimates when the bulk feed does not expose a direct DC price; results are therefore indicative, not bookmaker-exact.'})
        return out

    bt._analyze_day=analyze_day; bt._summary=summary
