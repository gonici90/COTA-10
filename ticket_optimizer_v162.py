"""COTA v16.2 ticket optimizer.

Builds the requested ticket from ALL usable markets produced by the independent
live model. Objective: maximise joint model probability while reaching the target
odds, with at most one selection per fixture. No market-type quota is imposed.
"""
import math


def _num(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def _candidates(rows, target):
    # For COTA 10 we prefer individually safer legs; the optimiser can combine
    # more of them instead of leaning on a handful of 1X2 favourites.
    min_prob = .60 if target >= 10 else .58
    max_odd = 2.60 if target >= 10 else 3.20
    fixtures=[]
    for row in rows:
        opts=[]
        for p in row.get('markets') or []:
            odd=_num(p.get('bookmaker_odds'))
            prob=_num(p.get('ticket_probability') or p.get('probability'))
            if odd is None or prob is None or p.get('suspicious'): continue
            prob/=100.0
            if odd < 1.02 or odd > max_odd or prob < min_prob: continue
            opts.append({**p,'home':row.get('home','?'),'away':row.get('away','?'),'kickoff':row.get('kickoff'),'combo_prob':prob})
        if opts:
            # Keep several alternatives from each fixture. This is essential:
            # Over/Under, BTTS and AH must compete directly with 1X2.
            opts.sort(key=lambda x:(x['combo_prob'],x.get('recommendation_score',0),-x['bookmaker_odds']),reverse=True)
            fixtures.append(opts[:12])
    return fixtures


def build_combo(rows,target):
    target=max(1.05,float(target)); fixtures=_candidates(rows,target)
    lo,hi=(target*.92,target*1.08) if target>=10 else (target*.93,target*1.08)
    diag={'optimizer':'v16.2 all-markets max-joint-probability','candidate_matches':len(fixtures),'candidate_selections':sum(map(len,fixtures)),'target_low':round(lo,2),'target_high':round(hi,2),'closest_reachable_odds':None,'best_joint_probability':None}
    if not fixtures:return None,diag

    # State is (odds bucket, legs). Keeping leg count prevents a high-priced
    # short ticket from erasing a safer many-leg route at similar total odds.
    scale=180
    states={(0,0):(1.0,1.0,[])}
    for opts in fixtures:
        nxt=dict(states)
        for odd,joint,path in list(states.values()):
            if len(path)>=12:continue
            for x in opts:
                no=odd*x['bookmaker_odds']
                if no>hi:continue
                nj=joint*x['combo_prob']; legs=len(path)+1
                key=(round(math.log(max(no,1))*scale),legs)
                old=nxt.get(key)
                if old is None or nj>old[1]+1e-12 or (abs(nj-old[1])<=1e-12 and abs(no-target)<abs(old[0]-target)):
                    nxt[key]=(no,nj,path+[x])
        states=nxt

    paths=[v for v in states.values() if v[2]]
    if paths:diag['closest_reachable_odds']=round(min(paths,key=lambda v:abs(math.log(v[0]/target)))[0],2)
    valid=[v for v in paths if lo<=v[0]<=hi]
    if not valid:return None,diag

    # Primary objective = chance that the whole ticket lands. Target closeness is
    # only the tie breaker. This naturally selects whichever markets are safest.
    odd,joint,path=max(valid,key=lambda v:(v[1],-abs(math.log(v[0]/target)),len(v[2])))
    diag['best_joint_probability']=round(joint*100,3)
    counts={}
    for x in path:
        m=str(x.get('market') or '')
        typ='1X2' if m in {'1','X','2'} else 'GOALS' if m.startswith(('Over ','Under ')) else 'BTTS' if m in {'GG','NG'} else 'AH' if m.startswith('AH ') else 'OTHER'
        counts[typ]=counts.get(typ,0)+1
    diag['selected_market_mix']=counts
    return {'combined_odds':round(odd,2),'estimated_joint_probability':round(joint*100,3),'target_met':True,'requested_target':target,'average_leg_odds':round(odd**(1/len(path)),2),'max_leg_odds':round(max(x['bookmaker_odds'] for x in path),2),'market_mix':counts,'matches':[{'home':x['home'],'away':x['away'],'kickoff':x.get('kickoff'),'selection':x['market'],'probability':x['probability'],'ticket_probability':x.get('ticket_probability'),'odds':x['bookmaker_odds'],'ev':x.get('ev'),'score':x.get('recommendation_score')} for x in path]},diag


def install(engine):
    engine.build_combo=build_combo
