"""COTA 10 v20.2: sportsbook browser + deep on-demand analysis."""
from collections import defaultdict
from datetime import datetime,timedelta
from fastapi import HTTPException,Query
from fastapi.responses import HTMLResponse
import market_engine,auto_data as fd
import football_independent_live as independent
import football_independent_live_fast as history_loader

MAJOR=['champions league','europa league','conference league','premier league','la liga','serie a','bundesliga','ligue 1','liga 1','eredivisie','primeira liga','super lig','championship']
COUNTRY={'anglia':0,'spania':1,'italia':2,'germania':3,'franta':4,'romania':5,'olanda':6,'portugalia':7,'turcia':8}
def _priority(country,comp):
    s=comp.lower();
    for i,n in enumerate(MAJOR):
        if n in s:return (0,i,COUNTRY.get(country.lower(),99),s)
    return (1,COUNTRY.get(country.lower(),99),country.lower(),s)
def _league_meta(f):
    l=f.get('league');country='';comp=''
    if isinstance(l,dict):country=str(l.get('country') or l.get('country_name') or '');comp=str(l.get('name') or l.get('league_name') or '')
    elif l:comp=str(l)
    country=country or str(f.get('country') or f.get('country_name') or '');comp=comp or str(f.get('league_name') or f.get('competition') or 'Competiție')
    if not country and ' - ' in comp:
        a,b=comp.split(' - ',1)
        if a and b:country,comp=a.strip(),b.strip()
    return country or 'Altele',comp
def _find(day,fid):
    try:rows=fd._fixtures(day,True)
    except Exception:rows=market_engine._day_fixtures(day)
    return next((f for f in rows if isinstance(f,dict) and str(f.get('id') or f.get('fixture_id'))==str(fid)),None)

def install(app):
 @app.get('/api/fixtures')
 def fixtures(day:str,days:int=Query(1,ge=1,le=7)):
    start=datetime.fromisoformat(day).date();groups=defaultdict(list);seen=set()
    for i in range(days):
      d=(start+timedelta(days=i)).isoformat()
      try:got=fd._fixtures(d,False)
      except Exception:got=market_engine._day_fixtures(d)
      for f in got:
       if not isinstance(f,dict):continue
       fid=f.get('id') or f.get('fixture_id');h,a=market_engine._teams(f);k=fid or(h.get('name'),a.get('name'),market_engine._kickoff_ts(f))
       if k in seen:continue
       seen.add(k);country,comp=_league_meta(f);groups[(country,comp)].append({'id':fid,'home':h.get('name','?'),'away':a.get('name','?'),'kickoff':market_engine._kickoff_ts(f),'date':d})
    comps=[]
    for k,v in sorted(groups.items(),key=lambda x:_priority(x[0][0],x[0][1])):
      v.sort(key=lambda m:m['kickoff']);comps.append({'country':k[0],'competition':k[1],'major':_priority(*k)[0]==0,'matches':v})
    return {'day':day,'count':sum(len(x['matches']) for x in comps),'competitions':comps}
 @app.get('/api/match-analysis')
 def match_analysis(fixture_id:str,day:str):
    found=_find(day,fixture_id)
    if found is None:raise HTTPException(404,'Meciul nu a fost găsit')
    fid=found.get('id') or found.get('fixture_id');odds={};source='bulk';inline=found.get('odds') or found.get('markets')
    if inline:odds=market_engine._normalize_odds({'odds':inline})
    if not market_engine._has_prices(odds) and fid:
      try:odds=market_engine._normalize_odds(fd._get(f'/fixtures/{fid}/odds',{'bookmakers':'bet365'}));source='fixture-detail'
      except Exception:odds={}
    enriched=dict(found)
    if market_engine._has_prices(odds):enriched['odds']=odds;market_engine._ODDS_CACHE[fid]=(datetime.now().timestamp(),odds)
    # User explicitly selected this match: permit deeper history pagination here only.
    old=independent._history
    try:
      independent._history=lambda engine,provider,f: history_loader.deep_history(engine,provider,f,6)
      r=market_engine.analyze_fixture(enriched)
    except Exception as e:raise HTTPException(502,'Analiza meciului a eșuat: '+str(e)[:160])
    finally:independent._history=old
    if not r.get('best_market'):
      reason=(r.get('model_diagnostics') or {}).get('reason')
      if reason=='insufficient_team_history':raise HTTPException(422,'Istoricul disponibil nu conține suficiente meciuri ale ambelor echipe nici după căutarea extinsă.')
      if not market_engine._has_prices(odds):raise HTTPException(422,'Providerul nu oferă momentan cote Bet365 utilizabile pentru acest meci.')
      raise HTTPException(422,'Modelul nu a găsit o piață utilizabilă.')
    r['live_odds_source']=source;return r
 @app.get('/sportsbook',response_class=HTMLResponse)
 def sportsbook():return HTMLResponse(PAGE)

PAGE=r'''<!doctype html><html lang="ro"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>COTA 10</title><style>:root{--b:#03130d;--c:#092319;--l:#1d5b3d;--g:#52e995;--t:#eef9f2;--m:#8fa99a}*{box-sizing:border-box}body{margin:0;background:var(--b);color:var(--t);font-family:system-ui}.wrap{max-width:900px;margin:auto;padding:18px}.top{position:sticky;top:0;background:#03130df2;padding:12px 0;z-index:4}.brand{font-size:28px;font-weight:900}.sub,.time{color:var(--m)}.controls{display:flex;gap:9px;margin:12px 0}input,button{border:1px solid var(--l);border-radius:12px;background:var(--c);color:var(--t);padding:12px;font-size:16px}button{font-weight:800}.go{background:var(--g);color:#052014}.comp{margin:14px 0;border:1px solid var(--l);border-radius:17px;overflow:hidden}.comp.major{border-width:2px}.chead{padding:14px 16px;background:#0b291d;font-weight:900}.country{color:var(--g);font-size:12px;letter-spacing:1px}.match{display:grid;grid-template-columns:1fr auto;gap:10px;padding:14px 16px;border-top:1px solid #16432f}.teams{font-weight:800}.an{padding:9px 12px}.result{margin-top:10px;padding:14px;border-radius:13px;background:#061a12;border:1px solid var(--l);grid-column:1/-1}.best{font-size:21px;font-weight:900;color:var(--g)}.market{display:flex;justify-content:space-between;margin-top:7px}.err{color:#ffabab}@media(max-width:600px){.match{grid-template-columns:1fr}.an{width:100%}.controls{flex-direction:column}}</style></head><body><div class="wrap"><div class="top"><div class="brand">COTA 10 • LIVE ANALYST</div><div class="sub">Ligile importante sunt primele. Analiza profundă pornește doar pentru meciul ales.</div><div class="controls"><input id="day" type="date"><button class="go" onclick="load()">ARATĂ MECIURILE</button></div></div><div id="out"></div></div><script>const out=document.getElementById('out'),day=document.getElementById('day');day.value=new Date().toISOString().slice(0,10);const e=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));async function load(){out.innerHTML='Încarc…';try{let r=await fetch('/api/fixtures?day='+day.value),j=await r.json();if(!r.ok)throw Error(j.detail||'Eroare');out.innerHTML='<div class="sub">'+j.count+' meciuri • '+j.competitions.length+' competiții</div>'+j.competitions.map((c,i)=>'<section class="comp '+(c.major?'major':'')+'"><div class="chead"><div class="country">'+e(c.country).toUpperCase()+'</div>'+e(c.competition)+'</div>'+c.matches.map((m,n)=>'<div class="match"><div><div class="teams">'+e(m.home)+' — '+e(m.away)+'</div><div class="time">'+new Date(m.kickoff*1000).toLocaleString('ro-RO')+'</div></div><button class="an" onclick="an(this,\''+e(m.id)+'\',\''+e(m.date)+'\')">ANALIZEAZĂ</button><div id="r'+i+'_'+n+'"></div></div>').join('')+'</section>').join('')}catch(x){out.innerHTML='<div class="err">'+e(x.message)+'</div>'}}async function an(b,id,d){let x=b.parentElement.querySelector('div[id^=r]');b.disabled=true;b.textContent='ANALIZEZ…';x.className='result';x.innerHTML='Caut istoricul extins + cotele acestui meci…';try{let r=await fetch('/api/match-analysis?fixture_id='+encodeURIComponent(id)+'&day='+d),j=await r.json();if(!r.ok)throw Error(j.detail||'Eroare');let q=j.best_market||{};x.innerHTML='<div class="best">'+e(q.market)+' @'+e(q.bookmaker_odds)+' • '+e(q.ticket_probability||q.probability)+'%</div><div class="time">xG '+e(j.home_xg)+' – '+e(j.away_xg)+' • '+e(j.confidence)+'</div>'+(j.markets||[]).slice(0,12).map(z=>'<div class="market"><span>'+e(z.market)+'</span><span>@'+e(z.bookmaker_odds)+' • '+e(z.ticket_probability||z.probability)+'%</span></div>').join('')}catch(y){x.innerHTML='<span class="err">'+e(y.message)+'</span>'}finally{b.disabled=false;b.textContent='REANALIZEAZĂ'}}load();</script></body></html>'''
