"""COTA-10 live multi-market engine backed by 5DollarFootballAPI.
Uses real Bet365 prices when the configured plan exposes /fixtures/{id}/odds.
"""
import math
from datetime import datetime, timezone, timedelta
from itertools import combinations

import auto_data as fd


def _pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _grid(lh, la, n=10):
    g = [[_pois(h, lh) * _pois(a, la) for a in range(n + 1)] for h in range(n + 1)]
    z = sum(map(sum, g)) or 1.0
    return [[v / z for v in row] for row in g]


def _score_probs(lh, la):
    g = _grid(lh, la)
    home = sum(g[h][a] for h in range(11) for a in range(11) if h > a)
    draw = sum(g[h][h] for h in range(11))
    away = 1.0 - home - draw
    return home, draw, away, g


def _ah_prob(g, line, home_side=True):
    # Approximate Asian-handicap win probability. Quarter lines split into adjacent half lines.
    if abs(line * 4 - round(line * 4)) < 1e-8 and int(round(abs(line) * 4)) % 2 == 1:
        lo = math.floor(line * 2) / 2
        hi = math.ceil(line * 2) / 2
        return (_ah_prob(g, lo, home_side) + _ah_prob(g, hi, home_side)) / 2
    p = 0.0
    for h in range(11):
        for a in range(11):
            margin = (h - a) if home_side else (a - h)
            x = margin + line
            if x > 0: p += g[h][a]
            elif abs(x) < 1e-9: p += .5 * g[h][a]
    return p


def _total_prob(lam, line, over=True):
    # Push is counted as half-success for ranking/calibration purposes.
    p = 0.0
    for k in range(25):
        pk = _pois(k, lam)
        if over:
            p += pk if k > line else (.5 * pk if abs(k-line) < 1e-9 else 0)
        else:
            p += pk if k < line else (.5 * pk if abs(k-line) < 1e-9 else 0)
    return p


def _stage(m):
    if not isinstance(m, dict): return None
    for k in ('closing', 'current', 'opening'):
        if isinstance(m.get(k), dict): return m[k]
    return None


def _num(v):
    try: return float(v)
    except Exception: return None


def _odds_payload(fid):
    raw = fd._get(f'/fixtures/{fid}/odds', {'bookmakers':'bet365'})
    if isinstance(raw, dict) and raw.get('bookmakers'):
        b = next((x for x in raw['bookmakers'] if x.get('slug') == 'bet365'), raw['bookmakers'][0])
        return b.get('odds') or {}
    return (raw or {}).get('odds', raw or {}) if isinstance(raw, dict) else {}


def _recent(team_id, before_ts, n=12):
    raw = fd._get(f'/teams/{team_id}/fixtures', {'status':'finished','end_time':before_ts,'per_page':min(50,max(20,n*2))})
    rows = raw.get('fixtures') or raw.get('data') or [] if isinstance(raw, dict) else raw or []
    rows = [x for x in rows if (x.get('goals') or {}).get('home') is not None]
    rows.sort(key=lambda x:x.get('kickoff_ts') or 0, reverse=True)
    return rows[:n]


def _team_rates(team_id, games):
    gf=ga=cf=ca=yf=ya=ws=0.0
    for i,m in enumerate(games):
        teams=m.get('teams') or {}; home=(teams.get('home') or {}).get('id')==team_id
        goals=m.get('goals') or {}; corners=m.get('corners') or {}; cards=m.get('cards') or {}
        hg,ag=_num(goals.get('home')),_num(goals.get('away'))
        hc,ac=_num(corners.get('home')),_num(corners.get('away'))
        hca,aca=cards.get('home') or {},cards.get('away') or {}
        hy=(_num(hca.get('yellow')) or 0)+2*(_num(hca.get('red')) or 0)
        ay=(_num(aca.get('yellow')) or 0)+2*(_num(aca.get('red')) or 0)
        if hg is None or ag is None: continue
        w=.90**i; ws+=w
        gf+=(hg if home else ag)*w; ga+=(ag if home else hg)*w
        if hc is not None and ac is not None:
            cf+=(hc if home else ac)*w; ca+=(ac if home else hc)*w
        yf+=(hy if home else ay)*w; ya+=(ay if home else hy)*w
    if not ws:return {'n':0,'gf':1.25,'ga':1.25,'cf':5.,'ca':5.,'cards_for':2.,'cards_against':2.}
    return {'n':len(games),'gf':gf/ws,'ga':ga/ws,'cf':cf/ws,'ca':ca/ws,'cards_for':yf/ws,'cards_against':ya/ws}


def _add(out, market, p, odd, source):
    odd=_num(odd)
    if odd is None or odd <= 1.01:return
    p=max(.02,min(.98,p)); implied=1/odd
    # Do not let the model wander unrealistically far from a liquid bookmaker price.
    calibrated=max(.03,min(.97,.62*p+.38*implied))
    ev=(calibrated*odd-1)*100
    out.append({'market':market,'probability':round(calibrated*100,1),'raw_probability':round(p*100,1),
                'bookmaker_odds':round(odd,2),'fair_odds':round(1/calibrated,2),'ev':round(ev,1),
                'safe':calibrated>=.64,'value':ev>=2.0,'suspicious':abs(p-implied)>.25,'source':source,
                'recommendation_score':round(calibrated*100+max(-5,min(10,ev))*.15,1)})


def analyze_fixture(f):
    teams=f.get('teams') or {}; h=teams.get('home') or {}; a=teams.get('away') or {}; fid=f.get('id')
    ts=f.get('kickoff_ts') or int(datetime.now(timezone.utc).timestamp())
    hr=_team_rates(h.get('id'),_recent(h.get('id'),ts)); ar=_team_rates(a.get('id'),_recent(a.get('id'),ts))
    lh=max(.15,(hr['gf']+ar['ga'])/2*1.06); la=max(.15,(ar['gf']+hr['ga'])/2*.96)
    hp,dp,ap,g=_score_probs(lh,la); odds=_odds_payload(fid); picks=[]
    m=_stage(odds.get('1x2'))
    if m:
        _add(picks,'1',hp,m.get('home'),'Bet365 1X2');_add(picks,'X',dp,m.get('draw'),'Bet365 1X2');_add(picks,'2',ap,m.get('away'),'Bet365 1X2')
    m=_stage(odds.get('asian_handicap'))
    if m:
        line=_num(m.get('line'))
        if line is not None:
            _add(picks,f'AH Home {line:+g}',_ah_prob(g,line,True),m.get('home'),'Bet365 Asian Handicap')
            _add(picks,f'AH Away {-line:+g}',_ah_prob(g,-line,False),m.get('away'),'Bet365 Asian Handicap')
    m=_stage(odds.get('goal_line'))
    if m:
        line=_num(m.get('line'))
        if line is not None:
            _add(picks,f'Over {line:g}',_total_prob(lh+la,line,True),m.get('over'),'Bet365 Goal Line')
            _add(picks,f'Under {line:g}',_total_prob(lh+la,line,False),m.get('under'),'Bet365 Goal Line')
    corner_lam=max(2.,(hr['cf']+hr['ca']+ar['cf']+ar['ca'])/2)
    m=_stage(odds.get('corner_line'))
    if m:
        line=_num(m.get('line'))
        if line is not None:
            _add(picks,f'Corners Over {line:g}',_total_prob(corner_lam,line,True),m.get('over'),'Bet365 Corners')
            _add(picks,f'Corners Under {line:g}',_total_prob(corner_lam,line,False),m.get('under'),'Bet365 Corners')
    card_lam=max(.8,(hr['cards_for']+hr['cards_against']+ar['cards_for']+ar['cards_against'])/2)
    m=_stage(odds.get('card_line'))
    if m:
        line=_num(m.get('line'))
        if line is not None:
            _add(picks,f'Cards Over {line:g}',_total_prob(card_lam,line,True),m.get('over'),'Bet365 Cards')
            _add(picks,f'Cards Under {line:g}',_total_prob(card_lam,line,False),m.get('under'),'Bet365 Cards')
    picks=[x for x in picks if not x['suspicious']]
    picks.sort(key=lambda x:(x['safe'],x['recommendation_score'],x['probability']),reverse=True)
    best=picks[0] if picks else None
    return {'fixture_id':fid,'league':(f.get('league') or {}).get('name',''),'country':'','home':h.get('name','?'),'away':a.get('name','?'),
            'home_xg':round(lh,2),'away_xg':round(la,2),'confidence':'ridicată' if min(hr['n'],ar['n'])>=8 else 'medie',
            'markets':picks,'best_market':best,'best_value':next((x for x in picks if x['value']),None),'home_last':hr['n'],'away_last':ar['n']}


def _day_fixtures(day):
    start=int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()); end=start+86400
    raw=fd._get('/fixtures',{'start_time':start,'end_time':end,'include':'odds','per_page':50})
    return raw.get('fixtures') or raw.get('data') or [] if isinstance(raw,dict) else raw or []


def build_combo(rows,target):
    pool=[]
    for r in rows:
        for p in r.get('markets',[]):
            if p['safe'] and p['bookmaker_odds']>=1.08:
                pool.append({**p,'home':r['home'],'away':r['away'],'match':r['home']+' - '+r['away']})
    pool=sorted(pool,key=lambda x:(x['probability'],x['recommendation_score']),reverse=True)[:30]
    best=None
    for z in range(1,min(5,len(pool))+1):
        for c in combinations(pool,z):
            if len({x['match'] for x in c})<len(c):continue
            odd=math.prod(x['bookmaker_odds'] for x in c)
            if not target*.94<=odd<=target*1.10:continue
            joint=math.prod(x['probability']/100 for x in c)
            score=joint-abs(math.log(odd/target))*.03
            if best is None or score>best[0]:best=(score,odd,c)
    if not best:return None
    return {'combined_odds':round(best[1],2),'matches':[{'home':x['home'],'away':x['away'],'selection':x['market'],'probability':x['probability'],'odds':x['bookmaker_odds'],'ev':x['ev'],'score':x['recommendation_score']} for x in best[2]]}


def analyze_day(day,target=10,limit=12):
    fs=[x for x in _day_fixtures(day) if str(x.get('status','')).lower() not in ('finished','cancelled','canceled','postponed')]
    rows=[];errors=[]
    for f in fs[:max(1,min(int(limit),20))]:
        try:
            r=analyze_fixture(f)
            if r['best_market']:rows.append(r)
        except Exception as e:errors.append({'fixture':f.get('id'),'error':str(e)[:180]})
    rows.sort(key=lambda x:x['best_market']['recommendation_score'],reverse=True)
    return {'date':day,'provider':'5DollarFootballAPI + Bet365','api_fixtures':len(fs),'eligible':len(fs),'analyzed':len(rows),
            'analysis_errors':errors[:10],'ranking':rows,'suggested_combo':build_combo(rows,float(target))}
