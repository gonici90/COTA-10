"""LIVE football model independent from bookmaker odds.

Team probabilities are produced ONLY from historical results available before kickoff:
recent form, home/away scoring, league scoring environment, Elo and a small H2H adjustment.
Bet365 prices are read only after prediction. They NEVER create the prediction, but are
used as a sanity/reliability check so extreme model-vs-market disagreement is not labelled SOLID.
"""
import math, time
from datetime import datetime, timezone

import backtest_pro_walkforward as wf

_HISTORY = {}
TTL = 6 * 3600
HISTORY_DAYS = 240


def _league_id(f):
    l=f.get('league') or {}
    if isinstance(l,dict): return l.get('id') or l.get('league_id')
    return f.get('league_id')


def _history(engine, fd, f):
    lid=_league_id(f)
    if not lid: return []
    now=time.time(); cached=_HISTORY.get(str(lid))
    if cached and now-cached[0] < TTL: return cached[1]
    ko=engine._kickoff_ts(f); end=max(1,ko-1); start=end-HISTORY_DAYS*86400; out=[]
    for page in range(1,10):
        raw=fd._get(f'/leagues/{lid}/fixtures',{'start_time':start,'end_time':end,'status':'finished','per_page':50,'page':page,'lang':'en'})
        if isinstance(raw,dict): rows=raw.get('fixtures') or raw.get('data') or []; pag=raw.get('pagination') or {}
        else: rows,pag=raw or [],{}
        out.extend(x for x in rows if isinstance(x,dict))
        if not pag.get('has_more'): break
    out.sort(key=engine._kickoff_ts); _HISTORY[str(lid)]=(now,out); return out


def _h2h_adjust(engine,hist,home,away):
    rows=[]
    for x in hist:
        h,a=engine._teams(x); hn=str(h.get('name') or ''); an=str(a.get('name') or '')
        if {hn.lower(),an.lower()} != {home.lower(),away.lower()}: continue
        try:
            sh,sa=wf._score(x)
            if sh is None: continue
        except Exception: continue
        rows.append(float((sh-sa) if hn.lower()==home.lower() else (sa-sh)))
    rows=rows[-6:]
    if not rows:return 0.0,0
    return max(-.18,min(.18,(sum(rows)/len(rows))*.055)),len(rows)


def _independent_model(engine,fd,f):
    hist=_history(engine,fd,f); state=wf.State()
    for x in hist: state.update(x)
    m=state.predict(f)
    if not m:return None,{'history_matches':len(hist),'reason':'insufficient_team_history'}
    h,a=engine._teams(f); home=str(h.get('name') or ''); away=str(a.get('name') or '')
    adj,h2hn=_h2h_adjust(engine,hist,home,away)
    lh=max(.20,min(4.0,float(m['lh'])+adj)); la=max(.20,min(4.0,float(m['la'])-adj)); g=engine._grid(lh,la)
    ph=sum(g[i][j] for i in range(len(g)) for j in range(len(g)) if i>j); pd=sum(g[i][i] for i in range(len(g))); pa=max(.001,1-ph-pd); z=ph+pd+pa
    return {'1':ph/z,'X':pd/z,'2':pa/z,'lh':lh,'la':la},{'history_matches':len(hist),'h2h_matches':h2hn,'h2h_goal_adjustment':round(adj,3)}


def _append(out,name,p,odd,bookp,source):
    try:p=float(p); odd=float(odd); bookp=float(bookp)
    except Exception:return
    if odd<=1.01:return
    p=max(.01,min(.99,p)); edge=p-bookp; ev=p*odd-1
    # Calibration: preserve the independent model as the displayed raw probability,
    # but shrink ticket confidence when it strongly disagrees with a liquid market.
    # A 64% model call at odds 2.25 must not be treated like a normal 64% favourite.
    disagreement=abs(edge)
    shrink=min(.45, .35 + disagreement) if disagreement>.08 else .35
    calibrated=max(.02,min(.98,p + shrink*(bookp-p)))
    reliability=max(.72,1.0-disagreement*.75)
    ticket_p=calibrated*reliability
    suspicious=disagreement>.22 or (ev>.30 and odd>=1.80)
    solid=(ticket_p>=.60 and disagreement<=.12 and not suspicious)
    out.append({'market':name,'probability':round(calibrated*100,1),'ticket_probability':round(ticket_p*100,2),'raw_probability':round(p*100,1),'book_probability':round(bookp*100,1),'model_market_gap':round(edge*100,1),'bookmaker_odds':round(odd,2),'fair_odds':round(1/calibrated,2),'ev':round((calibrated*odd-1)*100,1),'safe':solid,'value':calibrated*odd-1>=.02 and not suspicious,'suspicious':suspicious,'source':source,'recommendation_score':round(ticket_p*100+max(-8,min(8,(calibrated*odd-1)*100))*.06,1)})


def install(engine,fd):
    def analyze_fixture(f):
        h,a=engine._teams(f); ts=engine._kickoff_ts(f); odds=engine._odds_payload(f); picks=[]; model,diag=_independent_model(engine,fd,f)
        league=f.get('league') or {}; league=league if isinstance(league,dict) else {'name':str(league)}
        base={'fixture_id':f.get('id') or f.get('fixture_id'),'kickoff':datetime.fromtimestamp(ts,timezone.utc).isoformat(),'league':league.get('name',''),'country':league.get('country',''),'home':h.get('name','?'),'away':a.get('name','?'),'confidence':'scazuta','markets':[],'best_market':None,'best_value':None,'odds_markets':list(odds) if isinstance(odds,dict) else [],'analysis_basis':'TEAM DATA prediction + market sanity calibration; odds do not create the prediction','model_diagnostics':diag}
        if not model:return base
        mx=engine._stage(odds.get('1x2') or odds.get('match_winner'))
        if mx:
            vals=[engine._num(mx.get(k)) for k in ('home','draw','away')]; bp=engine._devig_three(*vals)
            if bp:
                for n,p,o,b in zip(('1','X','2'),(model['1'],model['X'],model['2']),vals,bp):_append(picks,n,p,o,b,'Independent team model + market calibration')
        gm=engine._stage(odds.get('goal_line') or odds.get('goalline') or odds.get('goals') or odds.get('total_goals'))
        if gm:
            line=engine._num(gm.get('line')); over=engine._num(gm.get('over')); under=engine._num(gm.get('under')); bp=engine._devig_pair(over,under)
            if line is not None and bp:
                lam=model['lh']+model['la']; _append(picks,f'Over {line:g}',engine._total(lam,line,True),over,bp[0],'Independent goals model + market calibration'); _append(picks,f'Under {line:g}',engine._total(lam,line,False),under,bp[1],'Independent goals model + market calibration')
        bm=engine._stage(odds.get('btts') or odds.get('both_teams_to_score'))
        if bm:
            yes,no=engine._num(bm.get('yes')),engine._num(bm.get('no')); bp=engine._devig_pair(yes,no)
            if bp:
                py=(1-math.exp(-model['lh']))*(1-math.exp(-model['la'])); _append(picks,'GG',py,yes,bp[0],'Independent goals model + market calibration'); _append(picks,'NG',1-py,no,bp[1],'Independent goals model + market calibration')
        am=engine._stage(odds.get('asian_handicap') or odds.get('asian'))
        if am:
            line=engine._num(am.get('line')); oh=engine._num(am.get('home')); oa=engine._num(am.get('away')); bp=engine._devig_pair(oh,oa)
            if line is not None and bp:
                g=engine._grid(model['lh'],model['la']); _append(picks,f'AH Home {line:+g}',engine._ah(g,line,True),oh,bp[0],'Independent team model + market calibration'); _append(picks,f'AH Away {-line:+g}',engine._ah(g,-line,False),oa,bp[1],'Independent team model + market calibration')
        picks.sort(key=lambda x:(x['safe'],not x['suspicious'],x['ticket_probability'],x['recommendation_score']),reverse=True); usable=[x for x in picks if not x['suspicious']]
        best=usable[0] if usable else (picks[0] if picks else None)
        base.update({'home_xg':round(model['lh'],2),'away_xg':round(model['la'],2),'confidence':'ridicata' if diag.get('history_matches',0)>=80 else 'medie','markets':picks,'best_market':best,'best_value':next((x for x in usable if x['value']),None)})
        return base
    engine.analyze_fixture=analyze_fixture
