"""COTA-10 resilient live multi-market engine backed by 5DollarFootballAPI."""
import math
from datetime import datetime, timezone, timedelta
import auto_data as fd

_ODDS_CACHE={}
def _num(v):
    try:return float(v)
    except:return None
def _pois(k,l):return math.exp(-l)*l**k/math.factorial(k)
def _teams(m):
    t=m.get('teams') or {}
    if isinstance(t,dict) and (t.get('home') or t.get('away')):h,a=t.get('home') or {},t.get('away') or {}
    else:h,a=m.get('home_team') or m.get('home') or {},m.get('away_team') or m.get('away') or {}
    if not isinstance(h,dict):h={'name':str(h)}
    if not isinstance(a,dict):a={'name':str(a)}
    return h,a
def _kickoff_ts(m):
    v=m.get('kickoff_ts') or m.get('timestamp') or m.get('start_time') or m.get('kickoff') or m.get('date')
    if isinstance(v,(int,float)):return int(v)
    if isinstance(v,str):
        try:return int(float(v))
        except:pass
        try:return int(datetime.fromisoformat(v.replace('Z','+00:00')).timestamp())
        except:pass
    return int(datetime.now(timezone.utc).timestamp())
def _stage(m):
    if not isinstance(m,dict):return None
    for k in ('closing','current','opening','inplay'):
        if isinstance(m.get(k),dict):return m[k]
    return m
def _normalize_odds(data):
    if not isinstance(data,dict):return {}
    for k in ('data','response','result'):
        if isinstance(data.get(k),dict):data=data[k]
    books=data.get('bookmakers') or data.get('books') or []
    if isinstance(books,dict):books=list(books.values())
    if books:
        b=next((x for x in books if isinstance(x,dict) and '365' in str(x.get('slug') or x.get('name') or '').lower()),books[0])
        return b.get('odds') or b.get('markets') or b if isinstance(b,dict) else {}
    return data.get('odds') or data.get('markets') or data
def _odds_payload(f):
    fid=f.get('id') or f.get('fixture_id')
    inline=f.get('odds') or f.get('markets')
    if inline:return _normalize_odds({'odds':inline})
    if not fid:return {}
    if fid in _ODDS_CACHE:return _ODDS_CACHE[fid]
    try:o=_normalize_odds(fd._get(f'/fixtures/{fid}/odds'))
    except Exception:o={}
    _ODDS_CACHE[fid]=o;return o
def _grid(lh,la):
    g=[[_pois(h,lh)*_pois(a,la) for a in range(11)] for h in range(11)];z=sum(map(sum,g)) or 1
    return [[x/z for x in r] for r in g]
def _ah(g,line,home):
    p=0
    for h in range(11):
      for a in range(11):
        x=((h-a) if home else (a-h))+line;p+=g[h][a] if x>0 else .5*g[h][a] if abs(x)<1e-9 else 0
    return p
def _total(lam,line,over):
    return sum((_pois(k,lam) if ((k>line) if over else (k<line)) else .5*_pois(k,lam) if abs(k-line)<1e-9 else 0) for k in range(25))
def _add(out,name,p,odd,src):
    odd=_num(odd)
    if not odd or odd<=1.01:return
    p=max(.02,min(.98,p));imp=1/odd;cal=max(.03,min(.97,.68*p+.32*imp));ev=(cal*odd-1)*100
    out.append({'market':name,'probability':round(cal*100,1),'raw_probability':round(p*100,1),'bookmaker_odds':round(odd,2),'fair_odds':round(1/cal,2),'ev':round(ev,1),'safe':cal>=.58,'value':ev>=2,'suspicious':abs(p-imp)>.35,'source':src,'recommendation_score':round(cal*100+max(-5,min(10,ev))*.12,1)})
def analyze_fixture(f):
    h,a=_teams(f);ts=_kickoff_ts(f);odds=_odds_payload(f);picks=[]
    # Robust baseline: bookmaker probabilities de-vigged. This avoids two extra API calls per team.
    m=_stage(odds.get('1x2') or odds.get('match_winner'))
    if m:
        vals=[_num(m.get(k)) for k in ('home','draw','away')]; inv=[1/x if x and x>1 else 0 for x in vals];z=sum(inv) or 1
        for n,o,p in zip(('1','X','2'),vals,[x/z for x in inv]):_add(picks,n,p,o,'Bet365 1X2')
    # Estimate goal intensity conservatively from the goal line itself when available.
    gm=_stage(odds.get('goal_line') or odds.get('goalline') or odds.get('goals') or odds.get('total_goals'))
    gl=_num(gm.get('line')) if gm else None; lam=max(.8,gl if gl else 2.55)
    if gm and gl is not None:
        _add(picks,f'Over {gl:g}',_total(lam,gl,True),gm.get('over'),'Bet365 Goals');_add(picks,f'Under {gl:g}',_total(lam,gl,False),gm.get('under'),'Bet365 Goals')
    am=_stage(odds.get('asian_handicap') or odds.get('asian'))
    if am:
        line=_num(am.get('line'))
        if line is not None:
            # infer modest strength difference from 1X2 when present
            hp=next((x['raw_probability']/100 for x in picks if x['market']=='1'),.40);ap=next((x['raw_probability']/100 for x in picks if x['market']=='2'),.35);diff=max(-1.2,min(1.2,(hp-ap)*2.4));lh=max(.25,lam/2+diff/2);la=max(.25,lam/2-diff/2);g=_grid(lh,la)
            _add(picks,f'AH Home {line:+g}',_ah(g,line,True),am.get('home'),'Bet365 AH');_add(picks,f'AH Away {-line:+g}',_ah(g,-line,False),am.get('away'),'Bet365 AH')
    for keys,label,default in [(("corner_line","corner","corners"),'Corners',10.0),(("card_line","cards"),'Cards',4.2)]:
        mm=_stage(next((odds.get(k) for k in keys if odds.get(k)),None));line=_num(mm.get('line')) if mm else None
        if mm and line is not None:_add(picks,f'{label} Over {line:g}',_total(default,line,True),mm.get('over'),'Bet365 '+label);_add(picks,f'{label} Under {line:g}',_total(default,line,False),mm.get('under'),'Bet365 '+label)
    picks.sort(key=lambda x:(not x['suspicious'],x['probability'],x['recommendation_score']),reverse=True);usable=[x for x in picks if not x['suspicious']];best=usable[0] if usable else (picks[0] if picks else None);league=f.get('league') or {};league=league if isinstance(league,dict) else {'name':str(league)}
    return {'fixture_id':f.get('id') or f.get('fixture_id'),'kickoff':datetime.fromtimestamp(ts,timezone.utc).isoformat(),'league':league.get('name',''),'country':'','home':h.get('name','?'),'away':a.get('name','?'),'home_xg':round(lam/2,2),'away_xg':round(lam/2,2),'confidence':'medie','markets':picks,'best_market':best,'best_value':next((x for x in usable if x['value']),None),'odds_markets':list(odds) if isinstance(odds,dict) else []}
def _day_fixtures(day):
    start=int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp());out=[];seen=set()
    for page in range(1,21):
        raw=fd._get('/fixtures',{'start_time':start,'end_time':start+86400-1,'per_page':50,'page':page});data=raw.get('data',raw) if isinstance(raw,dict) else raw
        if isinstance(data,dict):rows=data.get('fixtures') or data.get('data') or [];pag=data.get('pagination') or {}
        else:rows=data or [];pag={}
        for x in rows:
            if not isinstance(x,dict):continue
            fid=x.get('id') or x.get('fixture_id');key=fid or (_teams(x)[0].get('name'),_teams(x)[1].get('name'),_kickoff_ts(x))
            if key not in seen:seen.add(key);out.append(x)
        if not rows or (not pag.get('has_more') and len(rows)<50):break
    return out
def build_combo(rows,target):
    target=float(target);c=[]
    for r in rows:
        good=[]
        for p in r.get('markets',[]):
            o=p.get('bookmaker_odds');pr=p.get('probability',0)/100
            if o and 1.04<=o<=3.5 and pr>=.54 and not p.get('suspicious'):good.append({**p,'home':r['home'],'away':r['away'],'kickoff':r.get('kickoff'),'combo_prob':pr})
        if good:c.append(max(good,key=lambda x:(x['combo_prob'],-x['bookmaker_odds'])))
    # Dynamic programming, one pick per match, optimized for joint probability near target.
    maxlegs=20 if target>=50 else 12;states={(0,0):(1.,1.,[])}
    for x in c:
        nxt=dict(states)
        for (n,_),(o,j,path) in states.items():
            if n>=maxlegs:continue
            no=o*x['bookmaker_odds']
            if no>target*1.18:continue
            nj=j*x['combo_prob'];key=(n+1,round(math.log(max(no,1))*100));old=nxt.get(key)
            if old is None or nj>old[1]:nxt[key]=(no,nj,path+[x])
        states=nxt
    valid=[v for (n,_),v in states.items() if v[2] and target*.86<=v[0]<=target*1.15 and (n>=8 if target>=50 else True)]
    if not valid:return None
    o,j,path=max(valid,key=lambda v:(v[1],-abs(math.log(v[0]/target))))
    return {'combined_odds':round(o,2),'estimated_joint_probability':round(j*100,1),'matches':[{'home':x['home'],'away':x['away'],'kickoff':x.get('kickoff'),'selection':x['market'],'probability':x['probability'],'odds':x['bookmaker_odds'],'ev':x['ev'],'score':x['recommendation_score']} for x in path]}
def analyze_period(day,target=10,days=1,limit=200):
    days=max(1,min(int(days),7));start=datetime.fromisoformat(day).date();fs=[];by_day={}
    for i in range(days):
        d=(start+timedelta(days=i)).isoformat();got=[x for x in _day_fixtures(d) if str(x.get('status','')).lower() not in ('finished','complete','ft','cancelled','canceled','postponed')];by_day[d]=len(got);fs.extend(got)
    rows=[];errors=[];no=[];attempt=min(len(fs),max(1,min(int(limit),200)))
    for f in fs[:attempt]:
        try:
            r=analyze_fixture(f)
            (rows if r['best_market'] else no).append(r)
        except Exception as e:
            h,a=_teams(f);errors.append({'fixture':f.get('id') or f.get('fixture_id'),'match':h.get('name','?')+' - '+a.get('name','?'),'error':type(e).__name__+': '+str(e)[:180]})
    rows.sort(key=lambda x:x['best_market']['recommendation_score'],reverse=True)
    return {'date':day,'days':days,'period_end':(start+timedelta(days=days-1)).isoformat(),'provider':'5DollarFootballAPI + Bet365','fixtures_by_day':by_day,'api_fixtures':len(fs),'eligible':len(fs),'attempted':attempt,'analyzed':len(rows),'without_usable_odds':len(no),'no_odds_examples':[{'fixture':x['fixture_id'],'match':x['home']+' - '+x['away']} for x in no[:20]],'analysis_errors':errors[:20],'ranking':rows,'suggested_combo':build_combo(rows,target)}
def analyze_day(day,target=10,limit=12):return analyze_period(day,target,1,limit)
