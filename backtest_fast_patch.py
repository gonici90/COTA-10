"""Fast quality-first historical backtest: 1X2, double chance, goals; bulk only."""


def install(bt, engine):
    original_summary = bt._summary

    def kind(name):
        n=str(name or '').strip()
        if n in {'1','X','2'}: return '1X2'
        if n in {'1X','X2','12'}: return 'DOUBLE_CHANCE'
        if n.startswith('Over ') or n.startswith('Under '): return 'GOALS'
        return None

    def prob(m):
        try: return float(m.get('ticket_probability') or m.get('probability') or 0)/100.0
        except Exception: return 0.0

    def odd(m):
        try: return float(m.get('bookmaker_odds') or m.get('odds') or 0)
        except Exception: return 0.0

    def synth_double_chance(markets):
        by={str(x.get('market')):x for x in markets if str(x.get('market')) in {'1','X','2'}}; out=[]
        for label,pair in [('1X',('1','X')),('X2',('X','2')),('12',('1','2'))]:
            if not all(x in by for x in pair): continue
            p=min(.97,prob(by[pair[0]])+prob(by[pair[1]]))
            if p<=0: continue
            o=max(1.05,round((1/p)*.95,2))
            out.append({'market':label,'bookmaker_odds':o,'probability':round(p*100,2),'ticket_probability':round(p*100,2),'recommendation_score':round(p*100,2),'suspicious':False,'synthetic_price':True})
        return out

    def qualifies(m):
        k=kind(m.get('market')); p=prob(m); o=odd(m)
        if not k or o<1.12 or o>2.20 or m.get('suspicious'): return False
        # Quality gate: no longer force a ticket every day.
        minp={'DOUBLE_CHANCE':.67,'GOALS':.61,'1X2':.60}[k]
        if p<minp: return False
        # Require a small probability cushion versus break-even price.
        return p-(1/o)>=.025

    def settle(sel,o,f):
        n=str(sel or '')
        if n not in {'1X','X2','12'}: return bt._settle_selection(n,o,f)
        h,a=bt._score_values(f)
        if h is None:return None
        win=(n=='1X' and h>=a) or (n=='X2' and a>=h) or (n=='12' and h!=a)
        return float(o) if win else 0.0

    def analyze_day(day,target):
        payload=bt._fetch_finished_day(day); fixtures=payload['matches']; rows=[]; lookup={}
        for f in fixtures:
            if bt._score_values(f)[0] is None: continue
            ef=dict(f); ef['_pro_bulk_odds']=True
            parsed=bt._prematch_only(bt._extract_inline_odds(f))
            if parsed: ef['odds']=parsed
            try: row=engine.analyze_fixture(ef)
            except Exception: continue
            base=[m for m in (row.get('markets') or []) if kind(m.get('market')) in {'1X2','GOALS'} and bt._market_data_available(m.get('market'),f)]
            markets=[m for m in (base+synth_double_chance(base)) if qualifies(m)]
            if not markets: continue
            # One candidate per fixture: safest qualified market, then value cushion.
            best=max(markets,key=lambda m:(prob(m),prob(m)-(1/odd(m))))
            row['markets']=[best]; row['best_market']=best
            rows.append(row); lookup[bt._fixture_key_from_row(row)]=f
        rows.sort(key=lambda r:prob(r['best_market']),reverse=True)
        combo,diag=engine.build_combo(rows,float(target))
        base={'date':day,'requested_odds':round(float(target),2),'fixtures':len(fixtures),'analyzed':len(rows),'truncated':bool(payload.get('truncated'))}
        # Reject optimizer compromises too far from requested ticket price.
        if combo:
            actual=float(combo.get('combined_odds') or 0)
            if actual < float(target)*.92 or actual > float(target)*1.08: combo=None
        if not combo:return {**base,'status':'NO_TICKET','actual_odds':None,'legs':0,'profit':0.0,'return_factor':None,'diagnostics':diag,'market_mix':{}}
        factor=1.0; details=[]; mix={}; unresolved=False
        for leg in combo.get('matches') or []:
            f=lookup.get((str(leg.get('home')),str(leg.get('away')),str(leg.get('kickoff'))))
            if f is None: unresolved=True; break
            lf=settle(leg.get('selection'),leg.get('odds'),f)
            if lf is None: unresolved=True; break
            factor*=lf; h,a=bt._score_values(f); k=kind(leg.get('selection')) or 'OTHER'; mix[k]=mix.get(k,0)+1
            details.append({'match':f"{leg.get('home')} - {leg.get('away')}",'selection':leg.get('selection'),'odds':leg.get('odds'),'score':f'{h}-{a}','kind':k,'return_factor':round(lf,3)})
        if unresolved:return {**base,'status':'UNSETTLED','actual_odds':combo.get('combined_odds'),'legs':len(combo.get('matches') or []),'profit':0.0,'return_factor':None,'leg_results':details,'market_mix':mix}
        profit=bt.STAKE*(factor-1); status='WIN' if profit>.005 else 'LOSE' if profit<-.005 else 'PUSH'
        return {**base,'status':status,'actual_odds':combo.get('combined_odds'),'legs':len(combo.get('matches') or []),'estimated_probability':combo.get('estimated_joint_probability'),'return_factor':round(factor,4),'profit':round(profit,2),'leg_results':details,'market_mix':mix}

    def summary(days,target,daily):
        out=original_summary(days,target,daily); mix={}
        for r in daily:
            for k,n in (r.get('market_mix') or {}).items():mix[k]=mix.get(k,0)+int(n or 0)
        out.update({'market_mix':mix,'mode':'QUALITY FAST: 1X2 + DOUBLE CHANCE + GOALS','note':'Nu forteaza bilet zilnic. Selectiile trec praguri minime de probabilitate si avantaj fata de break-even; bulk + cache, fara deep requests. Cotele DC sintetice raman orientative.'})
        return out

    bt._analyze_day=analyze_day; bt._summary=summary
