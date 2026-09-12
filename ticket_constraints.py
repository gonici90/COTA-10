"""Ticket builder v17: shortlist first, then conservative review.

The scanner supplies candidates; this layer behaves like a second analyst: it
penalizes fragile/high-priced picks and market concentration, then builds the
highest-quality ticket inside the user's constraints. One pick per match.
"""
import math


def _f(v):
    try:
        x=float(v or 0); return x if math.isfinite(x) and x>0 else 0.0
    except Exception:return 0.0

def _i(v):
    try:return max(0,int(v or 0))
    except Exception:return 0

def has_custom_constraints(odds_min=0,odds_max=0,min_legs=0,max_legs=0,leg_odds_min=0,leg_odds_max=0):
    return any((_f(odds_min),_f(odds_max),_i(min_legs),_i(max_legs),_f(leg_odds_min),_f(leg_odds_max)))

def _family(m):
    m=str(m or '')
    if m in {'1','X','2'}:return '1X2'
    if m in {'GG','NG'}:return 'BTTS'
    if m.startswith(('Over ','Under ')):return 'GOALS'
    if m.startswith('AH '):return 'AH'
    return 'OTHER'

def _review_score(p,prob,odd):
    """Conservative second-pass probability, not a claim of true probability."""
    fam=_family(p.get('market'))
    # Shrink model confidence toward 50%; stronger shrinkage for straight results.
    shrink={'1X2':0.70,'BTTS':0.82,'GOALS':0.86,'AH':0.84,'OTHER':0.75}.get(fam,0.75)
    q=0.50+(prob-0.50)*shrink
    # High prices are intrinsically more fragile for accumulator construction.
    if odd>1.80:q*=0.94
    elif odd>1.60:q*=0.97
    return max(0.01,min(0.99,q))

def _candidates(rows,target,leg_min,leg_max):
    lo=leg_min or 1.02; hi=leg_max or (2.20 if target>=10 else 2.60); out=[]
    for row in rows:
        opts=[]
        for p in row.get('markets') or []:
            try:odd=float(p.get('bookmaker_odds') or 0); prob=float(p.get('ticket_probability') or p.get('probability') or 0)/100
            except Exception:continue
            if p.get('suspicious') or odd<lo or odd>hi or prob<0.52:continue
            rq=_review_score(p,prob,odd)
            opts.append({**p,'home':row.get('home'),'away':row.get('away'),'kickoff':row.get('kickoff'),'family':_family(p.get('market')),'review_prob':rq,'combo_prob':rq})
        if opts:
            opts.sort(key=lambda x:(x['review_prob'],x.get('recommendation_score',0),-float(x['bookmaker_odds'])),reverse=True)
            out.append(opts[:12])
    return out

def build_combo(rows,target,odds_min=0,odds_max=0,min_legs=0,max_legs=0,leg_odds_min=0,leg_odds_max=0):
    target=max(1.01,float(target)); omin=_f(odds_min); omax=_f(odds_max); minl=_i(min_legs) or 1; maxl=min(_i(max_legs) or 20,30); lmin=_f(leg_odds_min); lmax=_f(leg_odds_max)
    if minl>maxl:raise ValueError('Numărul minim de meciuri nu poate fi mai mare decât maximul')
    low=max(1.01,omin or target*.92); high=min(500.0,omax or target*1.08)
    if low>high:raise ValueError('Intervalul de cotă al biletului este invalid')
    fixtures=_candidates(rows,target,lmin,lmax); diag={'philosophy':'SHORTLIST_THEN_CONSERVATIVE_REVIEW','candidate_matches':len(fixtures),'candidate_selections':sum(map(len,fixtures)),'requested_odds_min':round(low,2),'requested_odds_max':round(high,2),'requested_min_legs':minl,'requested_max_legs':maxl,'requested_leg_odds_min':round(lmin or 1.02,2),'requested_leg_odds_max':round(lmax or (2.20 if target>=10 else 2.60),2)}
    # State keeps market-family counts. Soft concentration penalty makes ten straight
    # 1X2 picks lose to a similarly safe diversified ticket, without banning 1X2.
    scale=100; states={(0,0,(0,0,0,0)):(1.0,1.0,[],{'1X2':0,'GOALS':0,'BTTS':0,'AH':0,'OTHER':0})}
    for opts in fixtures:
        nxt=dict(states)
        for odd,joint,path,cnt in list(states.values()):
            if len(path)>=maxl:continue
            for x in opts:
                no=odd*float(x['bookmaker_odds'])
                if no>high:continue
                nc=dict(cnt); fam=x['family']; nc[fam]=nc.get(fam,0)+1
                concentration=max(0,nc.get('1X2',0)-2)
                quality=float(x['review_prob'])*(0.985**concentration)
                nj=joint*quality; nl=len(path)+1; sig=(min(nc.get('1X2',0),4),min(nc.get('GOALS',0),3),min(nc.get('BTTS',0),3),min(nc.get('AH',0),3)); k=(round(math.log(max(no,1))*scale),nl,sig)
                old=nxt.get(k)
                if old is None or nj>old[1]:nxt[k]=(no,nj,path+[x],nc)
        states=nxt
    valid=[v for v in states.values() if v[2] and minl<=len(v[2])<=maxl and low<=v[0]<=high]
    if not valid:return None,diag
    ideal=min(max(target,low),high); best=max(valid,key=lambda v:(v[1],-abs(math.log(v[0]/ideal))))
    odd,joint,path,cnt=best; diag.update({'best_review_joint_score':round(joint*100,3),'selected_market_mix':cnt})
    return {'combined_odds':round(odd,2),'estimated_joint_probability':round(joint*100,3),'target_met':True,'requested_target':target,'requested_odds_min':round(low,2),'requested_odds_max':round(high,2),'requested_min_legs':minl,'requested_max_legs':maxl,'requested_leg_odds_min':round(lmin or 1.02,2),'requested_leg_odds_max':round(lmax or (2.20 if target>=10 else 2.60),2),'average_leg_odds':round(odd**(1/len(path)),2),'max_leg_odds':round(max(float(x['bookmaker_odds']) for x in path),2),'market_mix':cnt,'matches':[{'home':x['home'],'away':x['away'],'kickoff':x.get('kickoff'),'selection':x['market'],'probability':x.get('probability'),'ticket_probability':round(x['review_prob']*100,2),'odds':x['bookmaker_odds'],'ev':x.get('ev'),'score':x.get('recommendation_score'),'review':'conservative'} for x in path]},diag

def apply(result,target,odds_min=0,odds_max=0,min_legs=0,max_legs=0,leg_odds_min=0,leg_odds_max=0):
    if not has_custom_constraints(odds_min,odds_max,min_legs,max_legs,leg_odds_min,leg_odds_max):return result
    combo,diag=build_combo(result.get('ranking') or [],target,odds_min,odds_max,min_legs,max_legs,leg_odds_min,leg_odds_max); result['suggested_combo']=combo; result['combo_diagnostics']=diag; result['ticket_constraints']={'odds_min':diag['requested_odds_min'],'odds_max':diag['requested_odds_max'],'min_legs':diag['requested_min_legs'],'max_legs':diag['requested_max_legs'],'leg_odds_min':diag['requested_leg_odds_min'],'leg_odds_max':diag['requested_leg_odds_max']}; return result
