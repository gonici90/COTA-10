import csv, math
from collections import defaultdict, deque
from datetime import datetime,timedelta
from pathlib import Path
from fastapi import APIRouter,HTTPException,Query
router=APIRouter()
LEAGUES={'premier_league_2025_26.csv':'Premier League','SP1.csv':'La Liga','I1.csv':'Serie A','D1.csv':'Bundesliga','F1.csv':'Ligue 1'}
def _f(r,k):
 try:return float(r.get(k)) if r.get(k) not in ('',None) else None
 except:return None
def _load():
 out=[]
 for fn,league in LEAGUES.items():
  p=Path(fn)
  if not p.exists():continue
  with p.open(encoding='utf-8-sig',newline='') as f:
   for r in csv.DictReader(f):
    try:r['_date']=datetime.strptime(r['Date'],'%d/%m/%Y').date();r['_hg']=int(r['FTHG']);r['_ag']=int(r['FTAG']);r['_league']=league
    except:continue
    out.append(r)
 if not out:raise HTTPException(500,'Niciun CSV Big 5 nu a fost gasit')
 out.sort(key=lambda r:(r['_date'],r['_league'],r.get('Time','')));return out
def _pois(k,l):return math.exp(-l)*l**k/math.factorial(k)
def _probs(hl,al):
 g=[[_pois(h,hl)*_pois(a,al) for a in range(11)] for h in range(11)];z=sum(map(sum,g)) or 1
 h=sum(g[i][j] for i in range(11) for j in range(11) if i>j)/z;x=sum(g[i][i] for i in range(11))/z;a=sum(g[i][j] for i in range(11) for j in range(11) if i<j)/z;t=hl+al
 under=lambda line:sum(_pois(k,t) for k in range(int(math.floor(line))+1));btts_no=math.exp(-hl)+math.exp(-al)-math.exp(-(hl+al))
 return {'1':h,'X':x,'2':a,'1X':h+x,'X2':x+a,'12':h+a,'Over 1.5':1-under(1.5),'Under 1.5':under(1.5),'Over 2.5':1-under(2.5),'Under 2.5':under(2.5),'Over 3.5':1-under(3.5),'Under 3.5':under(3.5),'BTTS Da':1-btts_no,'BTTS Nu':btts_no}
def _stats(hist,team,venue,last=12):
 gs=[];vg=[]
 for r in reversed(hist):
  if team not in (r['HomeTeam'],r['AwayTeam']):continue
  home=r['HomeTeam']==team;gf,ga=(r['_hg'],r['_ag']) if home else (r['_ag'],r['_hg']);gs.append((gf,ga))
  if (venue=='home' and home) or (venue=='away' and not home):vg.append((gf,ga))
  if len(gs)>=last and len(vg)>=6:break
 def av(xs):
  if not xs:return None
  sw=gf=ga=0.
  for i,(aa,b) in enumerate(xs):w=.90**i;sw+=w;gf+=aa*w;ga+=b*w
  return gf/sw,ga/sw,len(xs)
 o,v=av(gs[:last]),av(vg[:6])
 if not o:return None
 return {'gf':.65*o[0]+.35*v[0] if v else o[0],'ga':.65*o[1]+.35*v[1] if v else o[1],'n':o[2],'venue_n':v[2] if v else 0}
def _base_odds(r):return {'1':_f(r,'B365H') or _f(r,'AvgH'),'X':_f(r,'B365D') or _f(r,'AvgD'),'2':_f(r,'B365A') or _f(r,'AvgA'),'Over 2.5':_f(r,'B365>2.5') or _f(r,'Avg>2.5'),'Under 2.5':_f(r,'B365<2.5') or _f(r,'Avg<2.5')}
def _odds(r,probs):
 o=_base_odds(r);h,x,a=o.get('1'),o.get('X'),o.get('2')
 if h and x and a:
  qh,qx,qa=1/h,1/x,1/a;s=qh+qx+qa;qh,qx,qa=qh/s,qx/s,qa/s;o['1X']=1/(qh+qx);o['X2']=1/(qx+qa);o['12']=1/(qh+qa)
 for m in ('Over 1.5','Under 1.5','Over 3.5','Under 3.5','BTTS Da','BTTS Nu'):
  if probs.get(m,0)>0:o[m]=1/probs[m]
 return o
def _won(m,h,a):
 if m=='1':return h>a
 if m=='X':return h==a
 if m=='2':return h<a
 if m=='1X':return h>=a
 if m=='X2':return h<=a
 if m=='12':return h!=a
 if m.startswith('Over '):return h+a>float(m.split()[1])
 if m.startswith('Under '):return h+a<float(m.split()[1])
 if m=='BTTS Da':return h>0 and a>0
 if m=='BTTS Nu':return h==0 or a==0
 return False
def _summary(ps):
 n=len(ps);w=sum(int(p['won']) for p in ps);pr=sum(p['profit'] for p in ps)
 return {'n':n,'wins':w,'losses':n-w,'hit_rate':round(100*w/n,1) if n else 0,'profit':round(pr,2),'roi':round(100*pr/n,1) if n else 0,'avg_odds':round(sum(p['odds'] for p in ps)/n,2) if n else 0,'roi_sample':n}
def _rel(db,league,market,p):
 lm=list(db[(league,market)]);gm=list(db[('*',market)]);sample=lm if len(lm)>=12 else gm
 if len(sample)<10:return p,0,len(sample)
 wins=sum(x[0] for x in sample);avgp=sum(x[1] for x in sample)/len(sample);real=(wins+4)/(len(sample)+8);corr=max(-.12,min(.06,real-avgp));weight=min(.80,len(sample)/45);return max(.05,min(.95,p+corr*weight)),corr*weight,len(sample)
def run_backtest(days=90):
 rows=_load();last=max(r['_date'] for r in rows);cut=last-timedelta(days=days-1);hist={k:[] for k in LEAGUES.values()};perf=defaultdict(lambda:deque(maxlen=80));picks=[];considered=0
 for r in rows:
  lh=hist[r['_league']];hs=_stats(lh,r['HomeTeam'],'home');aws=_stats(lh,r['AwayTeam'],'away')
  if hs and aws and min(hs['n'],aws['n'])>=9 and min(hs['venue_n'],aws['venue_n'])>=4:
   hl=max(.15,((hs['gf']+aws['ga'])/2)*1.07);al=max(.15,((aws['gf']+hs['ga'])/2)*.96);raw=_probs(hl,al);odds=_odds(r,raw);cand=[];actual={'1','X','2','Over 2.5','Under 2.5'}
   for m,p0 in raw.items():
    o=odds.get(m)
    if not o or o<=1:continue
    p,adj,nrel=_rel(perf,r['_league'],m,p0)
    if m in actual:
     imp=1/o;cal=max(.05,min(.95,.72*p+.28*imp));ev=(cal*o-1)*100;edge=(p-imp)*100
     if cal>=.70 and 3.5<=edge<=13 and 3<=ev<=18 and 1.28<=o<=2.80:cand.append((cal*100+min(ev,10)*.25,cal,m,o,ev,'historical',adj,nrel))
    else:
     # Derived odds are only a simulation proxy. Keep them separate from historical bookmaker odds.
     cal=p;ev=(cal*o-1)*100;floor=.80 if m in ('1X','X2','12','Over 1.5','Under 3.5') else .76
     if floor<=cal<=.87 and 1.20<=o<=1.42:cand.append((cal*100,cal,m,o,ev,'derived-model',adj,nrel))
   if r['_date']>=cut:
    considered+=1
    if cand:
     cand.sort(reverse=True);best=cand[0]
     if (len(cand)==1 or best[0]-cand[1][0]>=4.0) and best[1]>=.70:
      _,p,m,o,ev,src,adj,nrel=best;won=_won(m,r['_hg'],r['_ag']);profit=o-1 if won else -1
      picks.append({'date':r['_date'].isoformat(),'league':r['_league'],'match':r['HomeTeam']+' - '+r['AwayTeam'],'market':m,'probability':round(p*100,1),'estimated':round(p*100,1),'gap':round(adj*100,1),'reliability_sample':nrel,'odds':round(o,2),'odds_source':src,'ev':round(ev,1),'result':f"{r['_hg']}-{r['_ag']}",'won':won,'profit':round(profit,2)})
   for m,p0 in raw.items():
    w=int(_won(m,r['_hg'],r['_ag']));perf[(r['_league'],m)].append((w,p0));perf[('*',m)].append((w,p0))
  lh.append(r)
 total=_summary(picks);by_league={l:_summary([p for p in picks if p['league']==l]) for l in LEAGUES.values()};markets={}
 for m in sorted({p['market'] for p in picks}):
  mp=[p for p in picks if p['market']==m];s=_summary(mp);s['estimated']=round(sum(p['probability'] for p in mp)/len(mp),1);s['gap']=round(s['estimated']-s['hit_rate'],1);markets[m]=s
 buckets={}
 for name,lo,hi in [('60-69',60,70),('70-79',70,80),('80-89',80,90),('90+',90,101)]:
  bp=[p for p in picks if lo<=p['probability']<hi];s=_summary(bp);est=round(sum(p['probability'] for p in bp)/len(bp),1) if bp else None;corr=round(s['hit_rate']-est,1) if bp else None;s['estimated']=est;s['correction_pp']=corr;s['correction']=corr;buckets[name]=s
 return {'days':days,'dataset_end':last.isoformat(),'leagues_loaded':list(LEAGUES.values()),'dataset_matches':len(rows),'tested':total['n'],'wins':total['wins'],'losses':total['losses'],'hit_rate':total['hit_rate'],'profit':total['profit'],'roi':total['roi'],'avg_odds':total['avg_odds'],'stake_per_pick':1,'coverage':{'days_requested':days,'days_fetched':days,'fixtures_found':sum(1 for r in rows if r['_date']>=cut),'finished_considered':considered,'fixtures_analyzed':considered,'selection_rate':round(100*total['n']/considered,1) if considered else 0,'no_bet':considered-total['n'],'days_with_errors':0,'analysis_errors':0,'rate_limit_days':0},'leagues':by_league,'markets':markets,'calibration':buckets,'recent':picks[-30:][::-1],'errors':[],'note':'Backtest OFFLINE Big 5 adaptiv, fara look-ahead. Calibrarea liga/piata foloseste numai meciurile anterioare. ROI/profit este simulat la miza fixa 1; pentru 1/X/2 si O/U2.5 foloseste cote istorice CSV, iar pentru pietele fara cote istorice foloseste cote derivate de model si trebuie tratat doar ca diagnostic, nu ROI bookmaker real.'}
@router.get('/api/backtest')
def offline_backtest(days:int=Query(90,ge=1,le=365),per_day:int=Query(100,ge=1,le=100)):return run_backtest(days)
