import math
import os
import json
from pathlib import Path
from datetime import date, timedelta
from itertools import combinations
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

app=FastAPI(title='COTA 10 Football Analyzer',version='5.3')
API_KEY=os.getenv('FOOTBALLDATA_API_KEY') or os.getenv('API_FOOTBALL_KEY','');API_BASE='https://footballdata.io/api/v1'
MIN_COMBO_PROBABILITY=60.;MIN_RECOMMENDATION_PROBABILITY=60.;MIN_VALUE_EV=3.;MAX_MODEL_BOOK_GAP=.18;MAX_ACCEPTED_EV=45.
CACHE_DIR=Path(os.getenv('COTA_CACHE_DIR','/tmp/cota10-cache'));CACHE_DIR.mkdir(parents=True,exist_ok=True)
def pois(k,l):return math.exp(-l)*l**k/math.factorial(k)
def market_probabilities(hl,al):
 g=[[pois(h,hl)*pois(a,al) for a in range(11)] for h in range(11)];z=sum(map(sum,g)) or 1;home=sum(g[h][a] for h in range(11) for a in range(11) if h>a)/z;draw=sum(g[h][h] for h in range(11))/z;away=sum(g[h][a] for h in range(11) for a in range(11) if h<a)/z;b=(1-math.exp(-hl))*(1-math.exp(-al));t=hl+al;m={'1':home,'X':draw,'2':away,'1X':home+draw,'X2':away+draw,'12':home+away,'GG':b,'NG':1-b}
 for n in (1,2,3):u=sum(pois(k,t) for k in range(n+1));m[f'Over {n}.5']=1-u;m[f'Under {n}.5']=u
 return m
def _cache_file(path,params):
 key=json.dumps([path,sorted((params or {}).items())],ensure_ascii=True,separators=(',',':'));import hashlib;return CACHE_DIR/(hashlib.sha256(key.encode()).hexdigest()+'.json')
def _cacheable(path):return path.startswith('/matches/date/') or path=='/matches'
async def api_get(path,params=None):
 if not API_KEY:raise HTTPException(500,'FOOTBALLDATA_API_KEY is not configured on Render')
 params=params or {};cf=_cache_file(path,params) if _cacheable(path) else None
 if cf and cf.exists():
  try:return json.loads(cf.read_text(encoding='utf-8'))
  except Exception:pass
 async with httpx.AsyncClient(timeout=25) as c:r=await c.get(API_BASE+path,params=params,headers={'Authorization':f'Bearer {API_KEY}'})
 if r.status_code==429:raise HTTPException(429,f'Footballdata.io rate limit exceeded: {r.text[:300]}')
 if r.status_code!=200:raise HTTPException(502,f'Footballdata.io returned HTTP {r.status_code}: {r.text[:300]}')
 d=r.json()
 if d.get('success') is False:raise HTTPException(502,f"Footballdata.io error: {d.get('error',d)}")
 if cf:
  try:cf.write_text(json.dumps(d,ensure_ascii=False),encoding='utf-8')
  except Exception:pass
 return d
def extract_matches(d):
 p=d.get('data',[])
 if isinstance(p,list):return p
 if isinstance(p,dict):
  for k in ('matches','fixtures','results'):
   if isinstance(p.get(k),list):return p[k]
 return []
def team_info(m,s):
 o=m.get(f'{s}_team') or m.get(s) or {};return {'id':o.get('team_id') or o.get('id'),'name':o.get('team_name') or o.get('name') or '?'}
def score_values(m):
 s=m.get('score') or m.get('scores') or {};h=s.get('home') if isinstance(s,dict) else None;a=s.get('away') if isinstance(s,dict) else None
 if h is None:h=m.get('home_score')
 if a is None:a=m.get('away_score')
 try:h=int(h) if h is not None else None
 except:h=None
 try:a=int(a) if a is not None else None
 except:a=None
 return h,a
async def recent_team_stats(tid,before,venue,last=10):
 d=await api_get('/matches',{'team_id':tid,'to':before,'limit':40});gs=[x for x in extract_matches(d) if None not in score_values(x)];gs.sort(key=lambda x:str(x.get('date') or x.get('match_date') or x.get('kickoff') or ''),reverse=True);overall=gs[:last];vg=[]
 for x in gs:
  ih=team_info(x,'home')['id']==tid
  if (venue=='home' and ih) or (venue=='away' and not ih):vg.append(x)
  if len(vg)>=5:break
 def calc(ss):
  if not ss:return None
  gf=ga=ws=0.
  for i,x in enumerate(ss):
   h,a=score_values(x);ih=team_info(x,'home')['id']==tid;sc,co=(h,a) if ih else (a,h);w=.88**i;gf+=sc*w;ga+=co*w;ws+=w
  return {'gf':gf/ws,'ga':ga/ws,'n':len(ss)}
 o,v=calc(overall),calc(vg)
 if not o:return {'played':0,'gf':1.2,'ga':1.2,'venue_n':0}
 return {'played':o['n'],'gf':.65*o['gf']+.35*v['gf'] if v else o['gf'],'ga':.65*o['ga']+.35*v['ga'] if v else o['ga'],'venue_n':v['n'] if v else 0}
def odds_map(d):
 p=d.get('data') or {};o=p.get('odds') if isinstance(p,dict) else {};out={}
 if not isinstance(o,dict):return out
 def add(n,v):
  try:
   v=float(v)
   if v>1:out[n]=v
  except:pass
 mw=o.get('match_winner') or {};add('1',mw.get('home'));add('X',mw.get('draw'));add('2',mw.get('away'));dc=o.get('double_chance') or {};add('1X',dc.get('home_or_draw'));add('12',dc.get('home_or_away'));add('X2',dc.get('draw_or_away'));bt=o.get('both_teams_to_score') or {};add('GG',bt.get('yes'));add('NG',bt.get('no'));tg=o.get('total_goals') or {}
 for n in (1,2,3):add(f'Over {n}.5',tg.get(f'over_{n}_5'));add(f'Under {n}.5',tg.get(f'under_{n}_5'))
 return out
def sanity_market(p,b):
 if not b:return p,None,False,None
 imp=1/b;s=abs(p-imp)>MAX_MODEL_BOOK_GAP;c=max(.05,min(.95,.65*p+.35*imp));ev=(c*b-1)*100
 return (c,ev,True,'model/bookmaker divergence') if s or ev>MAX_ACCEPTED_EV else (c,ev,False,None)
def recommendation_score(m,c):
 p=m['probability'];ev=m['ev'] or 0;cb=5 if c=='ridicată' else 2 if c=='medie' else -10;o=m['bookmaker_odds'] or m['fair_odds'];return p+min(max(ev,0),20)*.25+cb-max(0,o-3)*4
async def analyze_fixture(f,day,include_odds=True):
 home,away=team_info(f,'home'),team_info(f,'away');fid=f.get('match_id') or f.get('id')
 if not home['id'] or not away['id']:raise ValueError('team_id lipsă')
 hs=await recent_team_stats(home['id'],day,'home');aws=await recent_team_stats(away['id'],day,'away');hl=max(.15,((hs['gf']+aws['ga'])/2)*1.07);al=max(.15,((aws['gf']+hs['ga'])/2)*.96);sample=min(hs['played'],aws['played']);conf='ridicată' if sample>=8 and hs['venue_n']>=3 and aws['venue_n']>=3 else 'medie' if sample>=5 else 'scăzută';book={}
 if include_odds and fid:
  try:book=odds_map(await api_get(f'/matches/{fid}/odds'))
  except:book={}
 ms=[]
 for name,p in market_probabilities(hl,al).items():
  if sample<5:p=.75*p+.25*.5
  p=max(.05,min(.95,p));b=book.get(name);raw=p;p,ev,susp,reason=sanity_market(p,b);prob=round(p*100,1);it={'market':name,'probability':prob,'raw_probability':round(raw*100,1),'fair_odds':round(1/p,2),'bookmaker_odds':round(b,2) if b else None,'ev':round(ev,1) if ev is not None else None,'value':bool(ev is not None and ev>=MIN_VALUE_EV and not susp and conf!='scăzută'),'safe':bool(prob>=MIN_RECOMMENDATION_PROBABILITY and not susp and conf!='scăzută'),'suspicious':susp,'warning':reason};it['recommendation_score']=round(recommendation_score(it,conf),1);ms.append(it)
 ms.sort(key=lambda x:(x['safe'],x['recommendation_score'],x['probability']),reverse=True);safe=[x for x in ms if x['safe']];best=max(safe,key=lambda x:x['recommendation_score']) if safe else max([x for x in ms if not x['suspicious']] or ms,key=lambda x:x['probability']);vals=[x for x in ms if x['value']];bv=max(vals,key=lambda x:(x['ev'],x['probability'])) if vals else None;l=f.get('league') or {}
 return {'fixture_id':fid,'league':l.get('name') or f.get('league_name') or '','country':l.get('country') or f.get('country') or '','home':home['name'],'away':away['name'],'home_xg':round(hl,2),'away_xg':round(al,2),'confidence':conf,'markets':ms,'best_market':best,'best_value':bv,'home_last':hs['played'],'away_last':aws['played']}
def build_target_combo(ms,target=10.):
 cs=[]
 for m in ms:
  ch=[x for x in m['markets'] if x['safe'] and not x['suspicious'] and x['probability']>=MIN_COMBO_PROBABILITY and x['bookmaker_odds']]
  if ch:
   b=max(ch,key=lambda x:(x['recommendation_score'],x['ev'] if x['ev'] is not None else -999));cs.append({'home':m['home'],'away':m['away'],'selection':b['market'],'probability':b['probability'],'odds':b['bookmaker_odds'],'ev':b['ev'],'score':b['recommendation_score']})
 cs=sorted(cs,key=lambda x:(x['score'],x['probability']),reverse=True)[:16];best=None
 for z in range(2,min(8,len(cs))+1):
  for c in combinations(cs,z):
   pr=math.prod(x['odds'] for x in c)
   if pr<target*.7:continue
   score=abs(math.log(max(pr,.01)/target))-sum(x['probability'] for x in c)/z/600
   if best is None or score<best[0]:best=(score,pr,c)
 return {'combined_odds':round(best[1],2),'matches':list(best[2])} if best else None
@app.get('/health')
def health():return {'status':'ok','provider':'footballdata.io','api_key_configured':bool(API_KEY),'version':'5.3'}
@app.get('/api/analyze')
async def analyze(day:str=Query(default_factory=lambda:date.today().isoformat()),limit:int=8,target:float=10.):
 fs=extract_matches(await api_get(f'/matches/date/{day}',{'limit':100}));up=[f for f in fs if str(f.get('status','')).lower() not in {'complete','finished','ft','cancelled','canceled','postponed'}][:max(1,min(limit,8))];rs=[];errs=[]
 for f in up:
  try:rs.append(await analyze_fixture(f,day))
  except Exception as e:errs.append({'fixture':team_info(f,'home')['name']+' - '+team_info(f,'away')['name'],'error':str(e)[:250]})
 rs.sort(key=lambda x:(x['best_market']['recommendation_score'],x['best_market']['probability']),reverse=True);return {'date':day,'api_fixtures':len(fs),'eligible':len(up),'analyzed':len(rs),'analysis_errors':errs[:10],'ranking':rs,'suggested_combo':build_target_combo(rs,target)}
@app.get('/',response_class=HTMLResponse)
def home():return '''<!doctype html><html lang="ro"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>COTA 10</title><style>body{font-family:system-ui;background:#0b0f14;color:#fff;margin:0;padding:24px}main{max-width:900px;margin:auto}.card{background:#151b23;border:1px solid #29313d;border-radius:16px;padding:18px;margin:14px 0}button{background:#35c46a;border:0;border-radius:10px;padding:13px 18px;font-weight:800;margin:3px}input{padding:12px;border-radius:9px;border:1px solid #394453;background:#0b0f14;color:white}small{color:#9ba8b7}.prob{font-size:24px;font-weight:800}.warn{color:#ffcc66}.good{color:#55df8b}.bad{color:#ff7b7b}.tag{display:inline-block;padding:4px 8px;border-radius:8px;background:#263241;margin:4px}.value{background:#174a32;color:#76f0aa}</style></head><body><main><h1>COTA 10</h1><p>Recomandarea principală prioritizează probabilitatea și siguranța. VALUE este separat.</p><div class="card"><input id="d" type="date"><input id="t" type="number" value="10"><button onclick="go(5)">COTA 5</button><button onclick="go(10)">COTA 10</button><button onclick="bt(7)">Backtest 7 zile</button><button onclick="bt(30)">Backtest 30 zile</button><button onclick="bt(60)">Backtest 60 zile</button><button onclick="bt(90)">Backtest 90 zile</button></div><div id="out"></div></main><script>const val=(v,f='—')=>(v===undefined||v===null||Number.isNaN(v))?f:v;const pp=v=>v===undefined||v===null?'—':((v>=0?'+':'')+v+'pp');document.getElementById('d').value=new Date().toISOString().slice(0,10);async function go(target){t.value=target;let o=out;o.innerHTML='<p>Analizez COTA '+target+'...</p>';try{let r=await fetch('/api/analyze?day='+d.value+'&target='+target),x=await r.json();if(!r.ok)throw Error(x.detail||'Eroare');let h='<div class="card"><b>COTA '+target+' · '+x.analyzed+' meciuri analizate</b></div>';if(x.suggested_combo)h+='<h2>Bilet inteligent COTA '+target+'</h2><div class="card"><b>Cotă combinată '+x.suggested_combo.combined_odds+'</b><br>'+x.suggested_combo.matches.map(m=>m.home+' – '+m.away+': '+m.selection+' '+m.probability+'% @'+m.odds).join('<br>')+'</div>';else h+='<div class="card warn"><b>COTA '+target+' indisponibilă:</b> nu sunt suficiente selecții sigure. Nu forțez pronosticuri slabe doar pentru atingerea cotei.</div>';h+='<h2>Analiza multi-piață</h2>';for(let m of x.ranking){let b=m.best_market;h+='<div class="card"><small>'+m.country+' · '+m.league+'</small><h3>'+m.home+' – '+m.away+'</h3><div class="prob">Recomandare: '+b.market+' · '+b.probability+'%</div><small>xG '+m.home_xg+' - '+m.away_xg+' · încredere '+m.confidence+'</small><div>'+m.markets.map(q=>'<span class="tag '+(q.value?'value':'')+'">'+q.market+' '+q.probability+'%'+(q.bookmaker_odds?' @'+q.bookmaker_odds:'')+'</span>').join('')+'</div></div>'}o.innerHTML=h}catch(e){o.innerHTML='<div class="card warn">'+e.message+'</div>'}}async function bt(days){let o=out;o.innerHTML='<p>Rulez backtest '+days+' zile...</p>';try{let r=await fetch('/api/backtest?days='+days+'&per_day=100'),x=await r.json();if(!r.ok)throw Error(x.detail||'Eroare');let c=x.coverage||{};let h='<h2>Backtest '+days+' zile</h2><div class="card"><div class="prob">'+val(x.hit_rate,0)+'% hit-rate</div><b>'+val(x.wins,0)+' câștigate / '+val(x.tested,0)+' selecții</b><br><small>'+val(x.note,'')+'</small></div><h2>Acoperire backtest</h2><div class="card"><b>Zile:</b> '+val(c.days_fetched,0)+'/'+val(c.days_requested,days)+' preluate<br><b>Meciuri:</b> '+val(c.fixtures_found,0)+' găsite · '+val(c.finished_considered,0)+' terminate luate în calcul · '+val(c.fixtures_analyzed,0)+' analizate<br><b>NO BET:</b> '+val(c.no_bet,0)+' · <b>Selecții:</b> '+val(x.tested,0)+' · '+val(c.selection_rate,0)+'%</div><h2>Calibrare automată - diagnostic</h2><div class="card">'+Object.entries(x.calibration||{}).map(([k,v])=>'<b>'+k+'%:</b> '+val(v.hit_rate,0)+'% real ('+val(v.wins,0)+'/'+val(v.n,0)+')'+((v.correction_pp??v.correction)!==null&& (v.correction_pp??v.correction)!==undefined?' · corecție '+pp(v.correction_pp??v.correction):'')).join('<br>')+'</div><h2>Pe piețe</h2><div class="card">'+Object.entries(x.markets||{}).sort((a,b)=>b[1].n-a[1].n).map(([k,v])=>{let est=v.estimated??v.avg_predicted,gap=v.gap??v.calibration_gap;return '<b>'+k+':</b> '+val(v.hit_rate,0)+'% real · estimat '+val(est)+'% · gap '+pp(gap)+' · n='+val(v.n,0)+' · cotă medie '+val(v.avg_odds)+' · ROI '+val(v.roi,0)+'% · profit '+val(v.profit,0)}).join('<br>')+'</div>';if(x.leagues){h+='<h2>Pe ligi</h2><div class="card">'+Object.entries(x.leagues).map(([k,v])=>'<b>'+k+':</b> '+val(v.hit_rate,0)+'% · '+val(v.wins,0)+'/'+val(v.n,0)+' · cotă medie '+val(v.avg_odds)+' · ROI '+val(v.roi,0)+'% · profit '+val(v.profit,0)).join('<br>')+'</div>'}h+='<h2>Ultimele selecții</h2>'+((x.recent||[]).map(p=>'<div class="card"><small>'+val(p.league,'')+' · '+val(p.date,'')+'</small><br><b>'+p.match+'</b><br>'+p.market+' · '+p.probability+'%'+(p.odds?' @'+p.odds:'')+' · '+p.result+' · <span class="'+(p.won?'good':'bad')+'">'+(p.won?'CÂȘTIGAT':'PIERDUT')+'</span></div>').join(''));if((x.errors||[]).length)h+='<div class="card warn">'+x.errors.map(e=>e.date+': '+e.error).join('<br>')+'</div>';o.innerHTML=h}catch(e){o.innerHTML='<div class="card warn">Eroare backtest: '+e.message+'</div>'}}</script></body></html>'''