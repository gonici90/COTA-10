"""Ticket builder v17.1: shortlist, conservative review, robust DP."""
import math

def _f(v):
    try:x=float(v or 0);return x if math.isfinite(x) and x>0 else 0.0
    except:return 0.0
def _i(v):
    try:return max(0,int(v or 0))
    except:return 0
def has_custom_constraints(odds_min=0,odds_max=0,min_legs=0,max_legs=0,leg_odds_min=0,leg_odds_max=0):return any((_f(odds_min),_f(odds_max),_i(min_legs),_i(max_legs),_f(leg_odds_min),_f(leg_odds_max)))
def _family(m):
    m=str(m or '')
    if m in {'1','X','2'}:return '1X2'
    if m in {'GG','NG'}:return 'BTTS'
    if m.startswith(('Over ','Under ')):return 'GOALS'
    if m.startswith('AH '):return 'AH'
    return 'OTHER'
def _review(p,prob,odd):
    s={'1X2':.70,'BTTS':.82,'GOALS':.86,'AH':.84,'OTHER':.75}.get(_family(p.get('market')),.75);q=.5+(prob-.5)*s
    if odd>1.8:q*=.94
    elif odd>1.6:q*=.97
    return max(.01,min(.99,q))
def _candidates(rows,target,lmin,lmax):
    lo=lmin or 1.02;hi=lmax or (2.2 if target>=10 else 2.6);out=[]
    for row in rows:
        opts=[]
        for p in row.get('markets') or []:
            try:o=float(p.get('bookmaker_odds') or 0);pr=float(p.get('ticket_probability') or p.get('probability') or 0)/100
            except:continue
            if p.get('suspicious') or o<lo or o>hi or pr<.52:continue
            q=_review(p,pr,o);opts.append({**p,'home':row.get('home'),'away':row.get('away'),'kickoff':row.get('kickoff'),'family':_family(p.get('market')),'review_prob':q})
        if opts:opts.sort(key=lambda x:(x['review_prob'],x.get('recommendation_score',0),-float(x['bookmaker_odds'])),reverse=True);out.append(opts[:12])
    return out
def build_combo(rows,target,odds_min=0,odds_max=0,min_legs=0,max_legs=0,leg_odds_min=0,leg_odds_max=0):
    target=max(1.01,float(target));omin=_f(odds_min);omax=_f(odds_max);minl=_i(min_legs) or 1;maxl=min(_i(max_legs) or 20,30);lmin=_f(leg_odds_min);lmax=_f(leg_odds_max)
    if minl>maxl:raise ValueError('Numărul minim de meciuri nu poate fi mai mare decât maximul')
    low=max(1.01,omin or target*.92);high=min(500.,omax or target*1.08)
    fixtures=_candidates(rows,target,lmin,lmax);diag={'philosophy':'SHORTLIST_THEN_CONSERVATIVE_REVIEW','candidate_matches':len(fixtures),'candidate_selections':sum(len(x) for x in fixtures),'requested_odds_min':round(low,2),'requested_odds_max':round(high,2),'requested_min_legs':minl,'requested_max_legs':maxl,'requested_leg_odds_min':round(lmin or 1.02,2),'requested_leg_odds_max':round(lmax or (2.2 if target>=10 else 2.6),2),'closest_reachable_odds':None}
    # Critical fix: preserve paths by odds+leg count only. The old family signature
    # exploded the state space and could discard/timeout perfectly reachable 50x tickets.
    scale=55;states={(0,0):(1.,1.,[],{'1X2':0,'GOALS':0,'BTTS':0,'AH':0,'OTHER':0})}
    for opts in fixtures:
        nxt=dict(states)
        for odd,joint,path,cnt in states.values():
            if len(path)>=maxl:continue
            for x in opts:
                no=odd*float(x['bookmaker_odds']);nl=len(path)+1
                if no>high:continue
                nc=dict(cnt);fam=x['family'];nc[fam]=nc.get(fam,0)+1
                penalty=.985**max(0,nc.get('1X2',0)-2);nj=joint*x['review_prob']*penalty;k=(round(math.log(max(no,1))*scale),nl);old=nxt.get(k)
                if old is None or nj>old[1]:nxt[k]=(no,nj,path+[x],nc)
        # Hard cap protects latency while retaining best probability per odds/legs bucket.
        if len(nxt)>12000:nxt=dict(sorted(nxt.items(),key=lambda kv:kv[1][1],reverse=True)[:12000])
        states=nxt
    paths=[v for v in states.values() if v[2] and minl<=len(v[2])<=maxl]
    if paths:diag['closest_reachable_odds']=round(min(paths,key=lambda v:abs(math.log(max(v[0],1e-9)/min(max(target,low),high))))[0],2)
    valid=[v for v in paths if low<=v[0]<=high]
    if not valid:return None,diag
    ideal=min(max(target,low),high);odd,joint,path,cnt=max(valid,key=lambda v:(v[1],-abs(math.log(v[0]/ideal))));diag.update({'best_review_joint_score':round(joint*100,3),'selected_market_mix':cnt})
    return {'combined_odds':round(odd,2),'estimated_joint_probability':round(joint*100,3),'target_met':True,'requested_target':target,'requested_odds_min':round(low,2),'requested_odds_max':round(high,2),'requested_min_legs':minl,'requested_max_legs':maxl,'requested_leg_odds_min':round(lmin or 1.02,2),'requested_leg_odds_max':round(lmax or (2.2 if target>=10 else 2.6),2),'average_leg_odds':round(odd**(1/len(path)),2),'max_leg_odds':round(max(float(x['bookmaker_odds']) for x in path),2),'market_mix':cnt,'matches':[{'home':x['home'],'away':x['away'],'kickoff':x.get('kickoff'),'selection':x['market'],'probability':x.get('probability'),'ticket_probability':round(x['review_prob']*100,2),'odds':x['bookmaker_odds'],'ev':x.get('ev'),'score':x.get('recommendation_score'),'review':'conservative'} for x in path]},diag
def apply(result,target,odds_min=0,odds_max=0,min_legs=0,max_legs=0,leg_odds_min=0,leg_odds_max=0):
    if not has_custom_constraints(odds_min,odds_max,min_legs,max_legs,leg_odds_min,leg_odds_max):return result
    combo,diag=build_combo(result.get('ranking') or [],target,odds_min,odds_max,min_legs,max_legs,leg_odds_min,leg_odds_max);result['suggested_combo']=combo;result['combo_diagnostics']=diag;result['ticket_constraints']={'odds_min':diag['requested_odds_min'],'odds_max':diag['requested_odds_max'],'min_legs':diag['requested_min_legs'],'max_legs':diag['requested_max_legs'],'leg_odds_min':diag['requested_leg_odds_min'],'leg_odds_max':diag['requested_leg_odds_max']};return result
