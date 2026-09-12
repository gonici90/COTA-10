"""COTA 10 v20.1 sportsbook UX: browse fast, analyze one match deeply on demand."""
from collections import defaultdict
from datetime import datetime, timedelta
from fastapi import HTTPException, Query
from fastapi.responses import HTMLResponse
import market_engine
import auto_data as fd


def _league_meta(f):
    """Normalize provider league/country shapes, including flat translated labels."""
    league=f.get('league')
    country=''; comp=''
    if isinstance(league,dict):
        country=str(league.get('country') or league.get('country_name') or '')
        comp=str(league.get('name') or league.get('league_name') or '')
    elif league: comp=str(league)
    country=country or str(f.get('country') or f.get('country_name') or '')
    comp=comp or str(f.get('league_name') or f.get('competition') or 'Competiție')
    # Some payloads expose "Anglia - Premier League" as the competition itself.
    if not country and ' - ' in comp:
        left,right=comp.split(' - ',1)
        if left and right: country,comp=left.strip(),right.strip()
    return country or 'Altele',comp


def _find_fixture(day,fixture_id):
    # Prefer the bulk include=odds payload: one call can already contain usable prices.
    try: rows=fd._fixtures(day,True)
    except Exception: rows=market_engine._day_fixtures(day)
    for f in rows:
        if isinstance(f,dict) and str(f.get('id') or f.get('fixture_id'))==str(fixture_id): return f
    return None


def install(app):
    @app.get('/api/fixtures')
    def fixtures(day:str,days:int=Query(1,ge=1,le=7)):
        start=datetime.fromisoformat(day).date();groups=defaultdict(list);seen=set()
        for i in range(days):
            d=(start+timedelta(days=i)).isoformat()
            # Listing needs fixtures, not expensive analysis. Reuse cached/bulk data when possible.
            try: got=fd._fixtures(d,False)
            except Exception: got=market_engine._day_fixtures(d)
            for f in got:
                if not isinstance(f,dict):continue
                fid=f.get('id') or f.get('fixture_id');h,a=market_engine._teams(f);k=fid or (h.get('name'),a.get('name'),market_engine._kickoff_ts(f))
                if k in seen:continue
                seen.add(k);country,comp=_league_meta(f)
                groups[(country,comp)].append({'id':fid,'home':h.get('name','?'),'away':a.get('name','?'),'kickoff':market_engine._kickoff_ts(f),'date':d})
        comps=[]
        for k,v in sorted(groups.items(),key=lambda x:(x[0][0].lower(),x[0][1].lower())):
            v.sort(key=lambda m:m['kickoff']);comps.append({'country':k[0],'competition':k[1],'matches':v})
        return {'day':day,'count':sum(len(x['matches']) for x in comps),'competitions':comps}

    @app.get('/api/match-analysis')
    def match_analysis(fixture_id:str,day:str):
        found=_find_fixture(day,fixture_id)
        if found is None:raise HTTPException(404,'Meciul nu a fost găsit')
        # Dedicated single-match enrichment. Unlike the browse page, this is exactly
        # where one per-fixture odds request is worth paying for.
        fid=found.get('id') or found.get('fixture_id'); odds={}; odds_source='bulk'
        inline=found.get('odds') or found.get('markets')
        if inline:
            odds=market_engine._normalize_odds({'odds':inline})
        if not market_engine._has_prices(odds) and fid:
            try:
                raw=fd._get(f'/fixtures/{fid}/odds',{'bookmakers':'bet365'})
                odds=market_engine._normalize_odds(raw);odds_source='fixture-detail'
            except Exception:odds={}
        if market_engine._has_prices(odds):
            enriched=dict(found);enriched.pop('_pro_bulk_odds',None);enriched['odds']=odds
            market_engine._ODDS_CACHE[fid]=(datetime.now().timestamp(),odds)
        else:enriched=found
        try:r=market_engine.analyze_fixture(enriched)
        except Exception as e:raise HTTPException(502,'Analiza meciului a eșuat: '+str(e)[:160])
        if not r.get('best_market'):
            reason=((r.get('model_diagnostics') or {}).get('reason'))
            if reason=='insufficient_team_history':raise HTTPException(422,'Nu există suficient istoric al echipelor pentru modelul independent.')
            if not market_engine._has_prices(odds):raise HTTPException(422,'Providerul nu oferă momentan cote Bet365 utilizabile pentru acest meci.')
            raise HTTPException(422,'Modelul nu a găsit o piață utilizabilă pentru acest meci.')
        r['live_odds_source']=odds_source;return r

    @app.get('/sportsbook',response_class=HTMLResponse)
    def sportsbook():return HTMLResponse(PAGE)

PAGE=r'''<!doctype html><html lang="ro"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>COTA 10 • Meciuri</title><style>
:root{--bg:#03130d;--card:#092319;--line:#1d5b3d;--green:#52e995;--text:#eef9f2;--muted:#8fa99a}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,sans-serif}.wrap{max-width:900px;margin:auto;padding:18px}.top{position:sticky;top:0;background:#03130df2;padding:12px 0;z-index:4}.brand{font-size:28px;font-weight:900}.sub{color:var(--muted);margin:4px 0 14px}.controls{display:flex;gap:9px}input,button{border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--text);padding:12px;font-size:16px}button{font-weight:800;cursor:pointer}.go{background:var(--green);color:#052014}.comp{margin:14px 0;border:1px solid var(--line);border-radius:17px;overflow:hidden}.chead{padding:14px 16px;background:#0b291d;font-weight:900}.country{color:var(--green);font-size:12px;text-transform:uppercase;letter-spacing:1px}.match{display:grid;grid-template-columns:1fr auto;gap:10px;padding:14px 16px;border-top:1px solid #16432f}.teams{font-weight:800}.time{font-size:13px;color:var(--muted);margin-top:5px}.an{padding:9px 12px}.result{margin-top:10px;padding:14px;border-radius:13px;background:#061a12;border:1px solid var(--line);grid-column:1/-1}.best{font-size:21px;font-weight:900;color:var(--green)}.markets{margin-top:10px;display:grid;gap:6px}.market{display:flex;justify-content:space-between;color:#cfe2d7}.loading{color:var(--muted);padding:20px}.err{color:#ffb0b0}@media(max-width:600px){.match{grid-template-columns:1fr}.an{width:100%}.controls{flex-direction:column}}
</style></head><body><div class="wrap"><div class="top"><div class="brand">COTA 10 • LIVE ANALYST</div><div class="sub">Competiții și meciuri întâi. Analiza completă pornește numai pentru meciul ales.</div><div class="controls"><input id="day" type="date"><button class="go" onclick="load()">ARATĂ MECIURILE</button></div></div><div id="out"></div></div><script>
const out=document.getElementById('out'),day=document.getElementById('day');day.value=new Date().toISOString().slice(0,10);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function load(){out.innerHTML='<div class="loading">Încarc programul…</div>';try{let r=await fetch('/api/fixtures?day='+day.value);let j=await r.json();if(!r.ok)throw Error(j.detail||'Eroare');out.innerHTML='<div class="sub">'+j.count+' meciuri • '+j.competitions.length+' competiții</div>'+j.competitions.map((c,ci)=>'<section class="comp"><div class="chead"><div class="country">'+esc(c.country)+'</div>'+esc(c.competition)+'</div>'+c.matches.map((m,mi)=>'<div class="match"><div><div class="teams">'+esc(m.home)+' — '+esc(m.away)+'</div><div class="time">'+new Date(m.kickoff*1000).toLocaleString('ro-RO')+'</div></div><button class="an" onclick="analyze(this,\''+esc(m.id)+'\',\''+esc(m.date)+'\')">ANALIZEAZĂ</button><div id="r'+ci+'_'+mi+'"></div></div>').join('')+'</section>').join('')}catch(e){out.innerHTML='<div class="err">'+esc(e.message)+'</div>'}}
async function analyze(btn,id,d){let box=btn.parentElement.querySelector('div[id^=r]');btn.disabled=true;btn.textContent='ANALIZEZ…';box.className='result';box.innerHTML='Citesc istoricul și cotele acestui meci…';try{let r=await fetch('/api/match-analysis?fixture_id='+encodeURIComponent(id)+'&day='+d);let j=await r.json();if(!r.ok)throw Error(j.detail||'Eroare');let b=j.best_market||{};box.innerHTML='<div class="best">'+esc(b.market)+' @'+esc(b.bookmaker_odds)+' • '+esc(b.ticket_probability||b.probability)+'%</div><div class="time">xG '+esc(j.home_xg)+' – '+esc(j.away_xg)+' • '+esc(j.confidence)+' • '+esc(j.live_odds_source||'')+'</div><div class="markets">'+(j.markets||[]).slice(0,12).map(x=>'<div class="market"><span>'+esc(x.market)+'</span><span>@'+esc(x.bookmaker_odds)+' • '+esc(x.ticket_probability||x.probability)+'%</span></div>').join('')+'</div>'}catch(e){box.innerHTML='<span class="err">'+esc(e.message)+'</span>'}finally{btn.disabled=false;btn.textContent='REANALIZEAZĂ'}}load();
</script></body></html>'''
