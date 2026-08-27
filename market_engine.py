"""COTA-10 live multi-market engine backed by 5DollarFootballAPI."""
import math
from datetime import datetime, timezone, timedelta
from itertools import combinations
import auto_data as fd

def _pois(k,l): return math.exp(-l)*l**k/math.factorial(k)
def _num(v):
    try:return float(v)
    except:return None
def _grid(lh,la,n=10):
    g=[[_pois(h,lh)*_pois(a,la) for a in range(n+1)] for h in range(n+1)];z=sum(map(sum,g)) or 1
    return [[v/z for v in r] for r in g]
def _score_probs(lh,la):
    g=_grid(lh,la);hp=sum(g[h][a] for h in range(11) for a in range(11) if h>a);dp=sum(g[h][h] for h in range(11));return hp,dp,1-hp-dp,g
def _ah_prob(g,line,home=True):
    if abs(line*4-round(line*4))<1e-8 and int(round(abs(line)*4))%2:
        return (_ah_prob(g,math.floor(line*2)/2,home)+_ah_prob(g,math.ceil(line*2)/2,home))/2
    p=0
    for h in range(11):
        for a in range(11):
            x=((h-a) if home else (a-h))+line;p+=g[h][a] if x>0 else .5*g[h][a] if abs(x)<1e-9 else 0
    return p
def _total_prob(lam,line,over=True):
    p=0
    for k in range(25):
        pk=_pois(k,lam);p+=pk if ((k>line) if over else (k<line)) else (.5*pk if abs(k-line)<1e-9 else 0)
    return p
def _stage(m):
    if not isinstance(m,dict):return None
    # 5Dollar currently uses opening / closing / inplay. Closing is latest pre-match.
    for k in ('closing','current','opening','inplay'):
        if isinstance(m.get(k),dict):return m[k]
    return m if any(k in m for k in ('home','away','over','under','draw','yes','no')) else None
def _odds_payload(fid):
    raw=fd._get(f'/fixtures/{fid}/odds',{'bookmakers':'bet365'})
    # Official API envelope: {success:1,data:{fixture_id,bookmakers:[...]}}
    data=raw.get('data',raw) if isinstance(raw,dict) else {}
    if not isinstance(data,dict):return {}
    books=data.get('bookmakers') or []
    if books:
        b=next((x for x in books if str(x.get('slug','')).lower()=='bet365'),books[0])
        return b.get('odds') or b.get('markets') or {}
    return data.get('odds') or data.get('markets') or {}
def _recent(team_id,before,n=12):
    if not team_id:return []
    raw=fd._get(f'/teams/{team_id}/fixtures',{'status':'finished','end_time':before,'per_page':min(50,max(20,n*2))})
    data=raw.get('data',raw) if isinstance(raw,dict) else raw
    if isinstance(data,dict):rows=data.get('fixtures') or data.get('data') or []
    else:rows=data or []
    rows=[x for x in rows if (x.get('goals') or {}).get('home') is not None];rows.sort(key=lambda x:x.get('kickoff_ts') or 0,reverse=True);return rows[:n]
def _team_rates(tid,games):
    gf=ga=cf=ca=yf=ya=ws=0
    for i,m in enumerate(games):
        teams=m.get('teams') or {};home=(teams.get('home') or {}).get('id')==tid;goals=m.get('goals') or {};corn=m.get('corners') or {};cards=m.get('cards') or {};hg,ag=_num(goals.get('home')),_num(goals.get('away'));hc,ac=_num(corn.get('home')),_num(corn.get('away'));hca,aca=cards.get('home') or {},cards.get('away') or {}
        if hg is None or ag is None:continue
        hy=(_num(hca.get('yellow')) or 0)+2*(_num(hca.get('red')) or 0);ay=(_num(aca.get('yellow')) or 0)+2*(_num(aca.get('red')) or 0);w=.9**i;ws+=w;gf+=(hg if home else ag)*w;ga+=(ag if home else hg)*w
        if hc is not None and ac is not None:cf+=(hc if home else ac)*w;ca+=(ac if home else hc)*w
        yf+=(hy if home else ay)*w;ya+=(ay if home else hy)*w
    if not ws:return {'n':0,'gf':1.25,'ga':1.25,'cf':5,'ca':5,'cards_for':2,'cards_against':2}
    return {'n':len(games),'gf':gf/ws,'ga':ga/ws,'cf':cf/ws,'ca':ca/ws,'cards_for':yf/ws,'cards_against':ya/ws}
def _add(out,name,p,odd,src):
    odd=_num(odd)
    if odd is None or odd<=1.01:return
    p=max(.02,min(.98,p));imp=1/odd;cal=max(.03,min(.97,.62*p+.38*imp));ev=(cal*odd-1)*100
    out.append({'market':name,'probability':round(cal*100,1),'raw_probability':round(p*100,1),'bookmaker_odds':round(odd,2),'fair_odds':round(1/cal,2),'ev':round(ev,1),'safe':cal>=.64,'value':ev>=2,'suspicious':abs(p-imp)>.25,'source':src,'recommendation_score':round(cal*100+max(-5,min(10,ev))*.15,1)})
def analyze_fixture(f):
    teams=f.get('teams') or {};h=teams.get('home') or {};a=teams.get('away') or {};fid=f.get('id');ts=f.get('kickoff_ts') or int(datetime.now(timezone.utc).timestamp());hr=_team_rates(h.get('id'),_recent(h.get('id'),ts));ar=_team_rates(a.get('id'),_recent(a.get('id'),ts));lh=max(.15,(hr['gf']+ar['ga'])/2*1.06);la=max(.15,(ar['gf']+hr['ga'])/2*.96);hp,dp,ap,g=_score_probs(lh,la);odds=_odds_payload(fid);picks=[]
    m=_stage(odds.get('1x2'))
    if m:_add(picks,'1',hp,m.get('home'),'Bet365 1X2');_add(picks,'X',dp,m.get('draw'),'Bet365 1X2');_add(picks,'2',ap,m.get('away'),'Bet365 1X2')
    m=_stage(odds.get('asian_handicap') or odds.get('asian'))
    if m:
        line=_num(m.get('line'))
        if line is not None:_add(picks,f'AH Home {line:+g}',_ah_prob(g,line,True),m.get('home'),'Bet365 Asian Handicap');_add(picks,f'AH Away {-line:+g}',_ah_prob(g,-line,False),m.get('away'),'Bet365 Asian Handicap')
    specs=[(('goal_line','goalline','goals'),'Goals',lh+la),(('corner_line','corner','corners'),'Corners',max(2,(hr['cf']+hr['ca']+ar['cf']+ar['ca'])/2)),(('card_line','cards'),'Cards',max(.8,(hr['cards_for']+hr['cards_against']+ar['cards_for']+ar['cards_against'])/2))]
    for keys,label,lam in specs:
        market=next((odds.get(k) for k in keys if odds.get(k)),None);m=_stage(market);line=_num(m.get('line')) if m else None
        if line is not None:_add(picks,('Over ' if label=='Goals' else label+' Over ')+f'{line:g}',_total_prob(lam,line,True),m.get('over'),'Bet365 '+label);_add(picks,('Under ' if label=='Goals' else label+' Under ')+f'{line:g}',_total_prob(lam,line,False),m.get('under'),'Bet365 '+label)
    # Keep valid priced markets; suspicious selections stay visible but cannot enter combo.
    picks.sort(key=lambda x:(not x['suspicious'],x['safe'],x['recommendation_score']),reverse=True);usable=[x for x in picks if not x['suspicious']];best=usable[0] if usable else (picks[0] if picks else None)
    return {'fixture_id':fid,'kickoff':datetime.fromtimestamp(ts,timezone.utc).isoformat(),'league':(f.get('league') or {}).get('name',''),'country':'','home':h.get('name','?'),'away':a.get('name','?'),'home_xg':round(lh,2),'away_xg':round(la,2),'confidence':'ridicată' if min(hr['n'],ar['n'])>=8 else 'medie','markets':picks,'best_market':best,'best_value':next((x for x in usable if x['value']),None),'home_last':hr['n'],'away_last':ar['n'],'odds_markets':list(odds.keys())}
def _day_fixtures(day):
    start=int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp());raw=fd._get('/fixtures',{'start_time':start,'end_time':start+86400,'per_page':100});data=raw.get('data',raw) if isinstance(raw,dict) else raw
    if isinstance(data,dict):return data.get('fixtures') or data.get('data') or []
    return data or []
def build_combo(rows,target):
    cs=[]
    for r in rows:
        match=r['home']+' - '+r['away']
        for p in r.get('markets',[]):
            odd=p['bookmaker_odds'];prob=p['probability']/100;mp=.78 if odd<1.20 else .72 if odd<1.35 else .67 if odd<1.60 else .64
            if 1.07<=odd<=3.25 and prob>=mp and not p.get('suspicious'):cs.append({**p,'home':r['home'],'away':r['away'],'kickoff':r.get('kickoff'),'match':match,'combo_prob':prob,'q':prob-(1/odd)*.1})
    pm={}
    for x in sorted(cs,key=lambda x:(x['q'],x['probability']),reverse=True):
        pm.setdefault(x['match'],[])
        if len(pm[x['match']])<2:pm[x['match']].append(x)
    pool=sorted([x for xs in pm.values() for x in xs],key=lambda x:(x['q'],x['probability']),reverse=True)[:40];best=None
    for z in range(1,min(12,len(pm))+1):
        for c in combinations(pool,z):
            if len({x['match'] for x in c})<len(c):continue
            odd=math.prod(x['bookmaker_odds'] for x in c)
            if not target*.90<=odd<=target*1.12:continue
            joint=math.prod(x['combo_prob'] for x in c);score=joint-abs(math.log(odd/target))*.015
            if best is None or score>best[0]:best=(score,odd,c,joint)
    if not best:return None
    return {'combined_odds':round(best[1],2),'estimated_joint_probability':round(best[3]*100,1),'matches':[{'home':x['home'],'away':x['away'],'kickoff':x.get('kickoff'),'selection':x['market'],'probability':x['probability'],'odds':x['bookmaker_odds'],'ev':x['ev'],'score':x['recommendation_score']} for x in best[2]]}
def analyze_period(day,target=10,days=1,limit=80):
    days=max(1,min(int(days),7));start=datetime.fromisoformat(day).date();fs=[];by_day={}
    for i in range(days):
        d=(start+timedelta(days=i)).isoformat();got=[x for x in _day_fixtures(d) if str(x.get('status','')).lower() not in ('finished','cancelled','canceled','postponed')];by_day[d]=len(got);fs.extend(got)
    rows=[];errors=[];no_odds=[]
    for f in fs[:max(1,min(int(limit),80))]:
        try:
            r=analyze_fixture(f)
            if r['best_market']:rows.append(r)
            else:no_odds.append({'fixture':f.get('id'),'match':(f.get('teams',{}).get('home',{}).get('name','?')+' - '+f.get('teams',{}).get('away',{}).get('name','?')),'odds_markets':r.get('odds_markets',[])})
        except Exception as e:errors.append({'fixture':f.get('id'),'error':str(e)[:180]})
    rows.sort(key=lambda x:x['best_market']['recommendation_score'],reverse=True)
    return {'date':day,'days':days,'period_end':(start+timedelta(days=days-1)).isoformat(),'provider':'5DollarFootballAPI + Bet365','fixtures_by_day':by_day,'api_fixtures':len(fs),'eligible':len(fs),'attempted':min(len(fs),80),'analyzed':len(rows),'without_usable_odds':len(no_odds),'no_odds_examples':no_odds[:10],'analysis_errors':errors[:10],'ranking':rows,'suggested_combo':build_combo(rows,float(target))}
def analyze_day(day,target=10,limit=12):return analyze_period(day,target,1,limit)
