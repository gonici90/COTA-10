import csv
import math
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
CSV_CANDIDATES = [Path('premier_league_2025_26.csv'), Path('data/premier_league_2025_26.csv')]
MIN_PROB=.58
MIN_MARKET_HISTORY=5

def _f(row,key):
    try:
        v=row.get(key,''); return float(v) if v not in ('',None) else None
    except (TypeError,ValueError): return None

def _load():
    path=next((p for p in CSV_CANDIDATES if p.exists()),None)
    if not path: raise HTTPException(500,'CSV offline nu a fost gasit')
    out=[]
    with path.open(encoding='utf-8-sig',newline='') as fh:
        for r in csv.DictReader(fh):
            try:
                r['_date']=datetime.strptime(r['Date'],'%d/%m/%Y').date(); r['_hg']=int(r['FTHG']); r['_ag']=int(r['FTAG'])
            except Exception: continue
            out.append(r)
    out.sort(key=lambda r:(r['_date'],r.get('Time',''))); return out

def _pois(k,lam): return math.exp(-lam)*lam**k/math.factorial(k)
def _probs(hl,al):
    g=[[_pois(h,hl)*_pois(a,al) for a in range(11)] for h in range(11)]; z=sum(map(sum,g)) or 1
    home=sum(g[h][a] for h in range(11) for a in range(11) if h>a)/z; draw=sum(g[h][h] for h in range(11))/z; away=sum(g[h][a] for h in range(11) for a in range(11) if h<a)/z
    b=(1-math.exp(-hl))*(1-math.exp(-al)); t=hl+al
    m={'1':home,'X':draw,'2':away,'1X':home+draw,'X2':away+draw,'12':home+away,'GG':b,'NG':1-b}
    for n in (1,2,3):
        u=sum(_pois(k,t) for k in range(n+1)); m[f'Over {n}.5']=1-u; m[f'Under {n}.5']=u
    return m

def _team_stats(history,team,venue,last=10):
    games=[]; vg=[]
    for r in reversed(history):
        if team not in (r['HomeTeam'],r['AwayTeam']): continue
        home=r['HomeTeam']==team; gf,ga=(r['_hg'],r['_ag']) if home else (r['_ag'],r['_hg']); games.append((gf,ga))
        if (venue=='home' and home) or (venue=='away' and not home): vg.append((gf,ga))
        if len(games)>=last and len(vg)>=5: break
    games=games[:last]; vg=vg[:5]
    def avg(xs):
        if not xs:return None
        sw=gf=ga=0.
        for i,(a,b) in enumerate(xs): w=.88**i; gf+=a*w; ga+=b*w; sw+=w
        return gf/sw,ga/sw,len(xs)
    o,v=avg(games),avg(vg)
    if not o:return None
    return {'gf':.65*o[0]+.35*v[0] if v else o[0],'ga':.65*o[1]+.35*v[1] if v else o[1],'n':o[2],'vn':v[2] if v else 0}

def _base_odds(r):
    return {'1':_f(r,'B365H') or _f(r,'AvgH'),'X':_f(r,'B365D') or _f(r,'AvgD'),'2':_f(r,'B365A') or _f(r,'AvgA'),'Over 2.5':_f(r,'B365>2.5') or _f(r,'Avg>2.5'),'Under 2.5':_f(r,'B365<2.5') or _f(r,'Avg<2.5')}
def _odds(r):
    o=_base_odds(r); h,x,a=o.get('1'),o.get('X'),o.get('2')
    # Synthetic fair double-chance prices from historical 1X2 when CSV has no direct DC column.
    if h and x and a:
        ih,ix,ia=1/h,1/x,1/a; z=ih+ix+ia
        ph,px,pa=ih/z,ix/z,ia/z
        for n,p in [('1X',ph+px),('X2',px+pa),('12',ph+pa)]: o[n]=round(1/p,3) if p else None
    return o

def _won(m,h,a):
    t=h+a
    if m=='1':return h>a
    if m=='X':return h==a
    if m=='2':return h<a
    if m=='1X':return h>=a
    if m=='X2':return h<=a
    if m=='12':return h!=a
    if m=='GG':return h>0 and a>0
    if m=='NG':return h==0 or a==0
    if m.startswith('Over '):return t>float(m.split()[1])
    if m.startswith('Under '):return t<float(m.split()[1])
    return False

def _history_adjust(m,p,learn):
    s=learn.get(m)
    if not s or s['n']<MIN_MARKET_HISTORY:return p,0.,0
    real=s['wins']/s['n']; pred=s['prob_sum']/s['n']; gap=real-pred
    # Shrink correction until enough observations exist; prevents tiny samples dominating selection.
    weight=min(.65,s['n']/30*.65); adj=max(.05,min(.95,p+gap*weight))
    return adj,gap*100,s['n']

def run_backtest(days=90):
    rows=_load()
    if not rows:return {'tested':0,'wins':0,'losses':0,'hit_rate':0,'roi':0,'profit':0,'avg_odds':0}
    last_date=rows[-1]['_date']; cutoff=last_date-timedelta(days=days-1); picks=[]; history=[]; considered=0; learn={}
    for r in rows:
        if r['_date']<cutoff: history.append(r); continue
        hs=_team_stats(history,r['HomeTeam'],'home'); aws=_team_stats(history,r['AwayTeam'],'away')
        if not hs or not aws or min(hs['n'],aws['n'])<5: history.append(r); continue
        considered+=1; hl=max(.15,((hs['gf']+aws['ga'])/2)*1.07); al=max(.15,((aws['gf']+hs['ga'])/2)*.96); ps=_probs(hl,al); odds=_odds(r); candidates=[]
        for m,raw in ps.items():
            o=odds.get(m)
            if not o or o<=1: continue
            imp=1/o; blended=max(.05,min(.95,.72*raw+.28*imp)); calibrated,gap,n_hist=_history_adjust(m,blended,learn); ev=(calibrated*o-1)*100
            divergence=abs(raw-imp)
            if calibrated>=MIN_PROB and divergence<=.20 and ev<=35:
                reliability=min(n_hist,30)/30
                score=calibrated*100 + min(max(ev,0),15)*.20 - max(0,o-3)*4 - max(0,divergence-.10)*40 + reliability*2
                candidates.append((score,calibrated,m,o,ev,raw,gap,n_hist))
        if candidates:
            _,p,m,o,ev,raw,gap,n_hist=max(candidates,key=lambda x:(x[0],x[1])); won=_won(m,r['_hg'],r['_ag']); profit=(o-1) if won else -1
            picks.append({'date':r['_date'].isoformat(),'match':r['HomeTeam']+' - '+r['AwayTeam'],'market':m,'probability':round(p*100,1),'raw_probability':round(raw*100,1),'odds':round(o,2),'ev':round(ev,1),'history_correction':round(gap,1),'market_history_n':n_hist,'result':f"{r['_hg']}-{r['_ag']}",'won':won,'profit':round(profit,2)})
        # Learn every available market outcome after the match, not only the chosen pick.
        for m,raw in ps.items():
            s=learn.setdefault(m,{'n':0,'wins':0,'prob_sum':0.}); s['n']+=1; s['wins']+=int(_won(m,r['_hg'],r['_ag'])); s['prob_sum']+=raw
        history.append(r)
    wins=sum(p['won'] for p in picks); profit=sum(p['profit'] for p in picks); n=len(picks); avg_odds=round(sum(p['odds'] for p in picks)/n,2) if n else 0; markets={}
    for p in picks:
        s=markets.setdefault(p['market'],{'n':0,'wins':0,'prob_sum':0.,'odds_sum':0.,'profit':0.}); s['n']+=1;s['wins']+=int(p['won']);s['prob_sum']+=p['probability'];s['odds_sum']+=p['odds'];s['profit']+=p['profit']
    for s in markets.values():
        s['hit_rate']=round(100*s['wins']/s['n'],1);s['avg_predicted']=round(s['prob_sum']/s['n'],1);s['calibration_gap']=round(s['avg_predicted']-s['hit_rate'],1);s['avg_odds']=round(s['odds_sum']/s['n'],2);s['profit']=round(s['profit'],2);s['roi']=round(100*s['profit']/s['n'],1);del s['prob_sum'];del s['odds_sum']
    calibration={}
    for name,lo,hi,mid in (('60-69',60,70,65),('70-79',70,80,75),('80-89',80,90,85),('90+',90,101,95)):
        bp=[p for p in picks if lo<=p['probability']<hi];bn=len(bp);bw=sum(int(p['won']) for p in bp);real=round(100*bw/bn,1) if bn else 0;cor=round(real-mid,1) if bn else None;calibration[name]={'n':bn,'wins':bw,'hit_rate':real,'expected_mid':mid,'correction':cor,'correction_pp':cor}
    learned={m:{'n':s['n'],'hit_rate':round(100*s['wins']/s['n'],1),'avg_model':round(100*s['prob_sum']/s['n'],1),'gap':round(100*(s['wins']/s['n']-s['prob_sum']/s['n']),1)} for m,s in learn.items() if s['n']}
    return {'days':days,'dataset_end':last_date.isoformat(),'tested':n,'wins':wins,'losses':n-wins,'hit_rate':round(100*wins/n,1) if n else 0,'profit':round(profit,2),'roi':round(100*profit/n,1) if n else 0,'avg_odds':avg_odds,'stake_per_pick':1,'total_staked':n,'coverage':{'days_requested':days,'days_fetched':days,'days_with_matches':len({r['_date'] for r in rows if r['_date']>=cutoff}),'days_with_errors':0,'fixtures_found':sum(1 for r in rows if r['_date']>=cutoff),'finished_considered':considered,'fixtures_analyzed':considered,'analysis_errors':0,'rate_limit_days':0,'selection_rate':round(100*n/considered,1) if considered else 0},'markets':markets,'market_learning':learned,'calibration':calibration,'recent':picks[-30:][::-1],'errors':[],'note':'Backtest OFFLINE multi-piata. Algoritmul compara pietele disponibile si alege dinamic pronosticul cu cel mai bun scor calibrat; istoricul pietei corecteaza gradual probabilitatea. Cotele istorice B365/Avg sunt folosite la selectie si ROI.'}

@router.get('/api/backtest')
def offline_backtest(days:int=Query(7,ge=1,le=365),per_day:int=Query(20,ge=1,le=100)):
    return run_backtest(days)
