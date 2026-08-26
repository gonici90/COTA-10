import csv
import math
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
CSV_CANDIDATES = [Path('premier_league_2025_26.csv'), Path('data/premier_league_2025_26.csv')]

def _f(row, key):
    try:
        v = row.get(key, '')
        return float(v) if v not in ('', None) else None
    except (TypeError, ValueError):
        return None

def _load():
    path = next((p for p in CSV_CANDIDATES if p.exists()), None)
    if not path:
        raise HTTPException(500, 'CSV offline nu a fost gasit')
    out = []
    with path.open(encoding='utf-8-sig', newline='') as fh:
        for r in csv.DictReader(fh):
            try:
                r['_date'] = datetime.strptime(r['Date'], '%d/%m/%Y').date()
                r['_hg'] = int(r['FTHG']); r['_ag'] = int(r['FTAG'])
            except Exception:
                continue
            out.append(r)
    out.sort(key=lambda r: (r['_date'], r.get('Time','')))
    return out

def _pois(k, lam):
    return math.exp(-lam) * lam**k / math.factorial(k)

def _probs(hl, al):
    grid = [[_pois(h,hl)*_pois(a,al) for a in range(11)] for h in range(11)]
    z = sum(map(sum,grid)) or 1
    h = sum(grid[i][j] for i in range(11) for j in range(11) if i>j)/z
    x = sum(grid[i][i] for i in range(11))/z
    a = sum(grid[i][j] for i in range(11) for j in range(11) if i<j)/z
    t = hl+al
    u25 = sum(_pois(k,t) for k in range(3))
    return {'1':h,'X':x,'2':a,'Over 2.5':1-u25,'Under 2.5':u25}

def _team_stats(history, team, venue, last=10):
    games=[]; venue_games=[]
    for r in reversed(history):
        if team not in (r['HomeTeam'],r['AwayTeam']): continue
        home = r['HomeTeam']==team
        gf,ga=(r['_hg'],r['_ag']) if home else (r['_ag'],r['_hg'])
        games.append((gf,ga))
        if (venue=='home' and home) or (venue=='away' and not home): venue_games.append((gf,ga))
        if len(games)>=last and len(venue_games)>=5: break
    games=games[:last]; venue_games=venue_games[:5]
    def avg(xs):
        if not xs:return None
        sw=gf=ga=0.0
        for i,(a,b) in enumerate(xs):
            w=.88**i; gf+=a*w; ga+=b*w; sw+=w
        return gf/sw,ga/sw,len(xs)
    o,v=avg(games),avg(venue_games)
    if not o:return None
    if v:return {'gf':.65*o[0]+.35*v[0],'ga':.65*o[1]+.35*v[1],'n':o[2],'vn':v[2]}
    return {'gf':o[0],'ga':o[1],'n':o[2],'vn':0}

def _odds(r):
    return {'1':_f(r,'B365H') or _f(r,'AvgH'),'X':_f(r,'B365D') or _f(r,'AvgD'),'2':_f(r,'B365A') or _f(r,'AvgA'),'Over 2.5':_f(r,'B365>2.5') or _f(r,'Avg>2.5'),'Under 2.5':_f(r,'B365<2.5') or _f(r,'Avg<2.5')}

def _won(m,h,a):
    if m=='1':return h>a
    if m=='X':return h==a
    if m=='2':return h<a
    if m=='Over 2.5':return h+a>2.5
    if m=='Under 2.5':return h+a<2.5
    return False

def run_backtest(days=90):
    rows=_load()
    if not rows:return {'tested':0,'wins':0,'losses':0,'hit_rate':0,'roi':0,'profit':0,'avg_odds':0}
    last_date=rows[-1]['_date']; cutoff=last_date-timedelta(days=days-1)
    picks=[]; history=[]; considered=0
    for r in rows:
        if r['_date']<cutoff:
            history.append(r); continue
        hs=_team_stats(history,r['HomeTeam'],'home'); aws=_team_stats(history,r['AwayTeam'],'away')
        if not hs or not aws or min(hs['n'],aws['n'])<5:
            history.append(r); continue
        considered+=1
        hl=max(.15,((hs['gf']+aws['ga'])/2)*1.07); al=max(.15,((aws['gf']+hs['ga'])/2)*.96)
        ps=_probs(hl,al); odds=_odds(r); candidates=[]
        for m,p in ps.items():
            o=odds.get(m)
            if not o or o<=1:continue
            imp=1/o
            calibrated=max(.05,min(.95,.65*p+.35*imp))
            ev=(calibrated*o-1)*100
            if calibrated>=.60 and abs(p-imp)<=.18 and ev<=45:
                score=calibrated*100+min(max(ev,0),20)*.25-max(0,o-3)*4
                candidates.append((score,calibrated,m,o,ev))
        if candidates:
            _,p,m,o,ev=max(candidates,key=lambda x:(x[0],x[1]))
            won=_won(m,r['_hg'],r['_ag']); profit=(o-1) if won else -1
            picks.append({'date':r['_date'].isoformat(),'match':r['HomeTeam']+' - '+r['AwayTeam'],'market':m,'probability':round(p*100,1),'odds':round(o,2),'ev':round(ev,1),'result':f"{r['_hg']}-{r['_ag']}",'won':won,'profit':round(profit,2)})
        history.append(r)
    wins=sum(p['won'] for p in picks); profit=sum(p['profit'] for p in picks); n=len(picks)
    avg_odds=round(sum(p['odds'] for p in picks)/n,2) if n else 0
    markets={}
    for p in picks:
        s=markets.setdefault(p['market'],{'n':0,'wins':0,'prob_sum':0.0,'odds_sum':0.0,'profit':0.0})
        s['n']+=1; s['wins']+=int(p['won']); s['prob_sum']+=p['probability']; s['odds_sum']+=p['odds']; s['profit']+=p['profit']
    for s in markets.values():
        s['hit_rate']=round(100*s['wins']/s['n'],1)
        s['avg_predicted']=round(s['prob_sum']/s['n'],1)
        s['calibration_gap']=round(s['avg_predicted']-s['hit_rate'],1)
        s['avg_odds']=round(s['odds_sum']/s['n'],2)
        s['profit']=round(s['profit'],2); s['roi']=round(100*s['profit']/s['n'],1)
        del s['prob_sum']; del s['odds_sum']
    bucket_defs=(('60-69',60,70,65),('70-79',70,80,75),('80-89',80,90,85),('90+',90,101,95))
    calibration={}
    for name,lo,hi,mid in bucket_defs:
        bp=[p for p in picks if lo <= p['probability'] < hi]
        bn=len(bp); bw=sum(int(p['won']) for p in bp); real=round(100*bw/bn,1) if bn else 0
        correction=round(real-mid,1) if bn else None
        calibration[name]={'n':bn,'wins':bw,'hit_rate':real,'expected_mid':mid,'correction':correction,'correction_pp':correction}
    return {'days':days,'dataset_end':last_date.isoformat(),'tested':n,'wins':wins,'losses':n-wins,'hit_rate':round(100*wins/n,1) if n else 0,'profit':round(profit,2),'roi':round(100*profit/n,1) if n else 0,'avg_odds':avg_odds,'stake_per_pick':1,'total_staked':n,'coverage':{'days_requested':days,'days_fetched':days,'days_with_matches':len({r['_date'] for r in rows if r['_date']>=cutoff}),'days_with_errors':0,'fixtures_found':sum(1 for r in rows if r['_date']>=cutoff),'finished_considered':considered,'fixtures_analyzed':considered,'analysis_errors':0,'rate_limit_days':0,'selection_rate':round(100*n/considered,1) if considered else 0},'markets':markets,'calibration':calibration,'recent':picks[-30:][::-1],'errors':[],'note':'Backtest OFFLINE pe CSV. Fara API/rate-limit. Cotele istorice B365/Avg sunt folosite la selectie si ROI; miza fixa 1 unitate/selectie.'}

@router.get('/api/backtest')
def offline_backtest(days:int=Query(7,ge=1,le=365),per_day:int=Query(20,ge=1,le=100)):
    return run_backtest(days)
