"""Analiza Cota AI v9.3: ticket-range driven UI with per-match odds limits."""
import math

from fastapi import HTTPException, Query
from fastapi.responses import HTMLResponse

import server_v3
import market_engine
import odds_sports
import ticket_constraints


app = server_v3.app
app.version = "9.3"


def _drop_get(path):
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and "GET" in (getattr(route, "methods", None) or set())
        )
    ]


for _path in ("/", "/api/analyze", "/health"):
    _drop_get(_path)


def _internal_target(ticket_odds_min, ticket_odds_max):
    lo = float(ticket_odds_min or 0)
    hi = float(ticket_odds_max or 0)
    if lo > 0 and hi > 0:
        return max(1.05, min(500.0, math.sqrt(lo * hi)))
    if lo > 0:
        return max(1.05, min(500.0, lo))
    if hi > 0:
        return max(1.05, min(500.0, hi))
    return 10.0


def _validate_ranges(ticket_odds_min, ticket_odds_max, min_legs, max_legs, leg_odds_min, leg_odds_max):
    if ticket_odds_min and ticket_odds_max and ticket_odds_min > ticket_odds_max:
        raise HTTPException(400, "Cota minimă a biletului nu poate fi mai mare decât cota maximă")
    if min_legs and max_legs and min_legs > max_legs:
        raise HTTPException(400, "Numărul minim de meciuri nu poate fi mai mare decât maximul")
    if leg_odds_min and leg_odds_max and leg_odds_min > leg_odds_max:
        raise HTTPException(400, "Cota minimă per meci nu poate fi mai mare decât cota maximă")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "9.3",
        "football_provider": "5DollarFootballAPI Pro + Bet365",
        "multisport_provider": "The Odds API",
        "the_odds_api_configured": odds_sports.configured(),
        "optimizer": "safety-first-global + ticket/per-match constraints",
        "ui": "pro-multisport-v3",
    }


@app.get("/api/analyze")
def analyze(
    day: str,
    sport: str = Query("football"),
    period_days: int = Query(1, ge=1, le=7),
    ticket_odds_min: float = Query(0.0, ge=0.0, le=500.0),
    ticket_odds_max: float = Query(0.0, ge=0.0, le=500.0),
    min_legs: int = Query(0, ge=0, le=30),
    max_legs: int = Query(0, ge=0, le=30),
    leg_odds_min: float = Query(0.0, ge=0.0, le=100.0),
    leg_odds_max: float = Query(0.0, ge=0.0, le=100.0),
):
    sport = sport.lower().strip()
    _validate_ranges(
        ticket_odds_min,
        ticket_odds_max,
        min_legs,
        max_legs,
        leg_odds_min,
        leg_odds_max,
    )
    target = _internal_target(ticket_odds_min, ticket_odds_max)
    days = period_days

    if sport == "football":
        result = market_engine.analyze_period(day, target, days, 200)
        result["sport"] = "football"
    elif sport == "soccer_extra":
        result = odds_sports.analyze_group("soccer", day, target, days)
    elif sport == "tennis":
        result = odds_sports.analyze_group("tennis", day, target, days)
    elif sport == "basketball":
        result = odds_sports.analyze_group("basketball", day, target, days)
    elif sport == "mix":
        result = odds_sports.analyze_mix(day, target, days)
    else:
        raise HTTPException(400, "Sport necunoscut")

    try:
        result = ticket_constraints.apply(
            result,
            target,
            odds_min=ticket_odds_min,
            odds_max=ticket_odds_max,
            min_legs=min_legs,
            max_legs=max_legs,
            leg_odds_min=leg_odds_min,
            leg_odds_max=leg_odds_max,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    result["period_days_selected"] = days
    result["period_days_is_custom"] = True
    result["internal_target"] = round(target, 3)
    return result


EXTRA_CSS = r'''
.analyze-wrap{display:flex;align-items:end;height:100%}
.analyze-main{
 width:100%;min-height:47px;border:0;border-radius:13px;padding:12px 16px;
 background:var(--accent);color:#052112;font-weight:1000;letter-spacing:.2px
}
.analyze-main:active{transform:translateY(1px)}
@media(min-width:720px){.filter-grid{grid-template-columns:repeat(7,1fr)}}
'''

TARGET_BLOCK = r'''  <div>
   <div class="label">Cotă țintă</div>
   <div class="targets">
    <button class="target" onclick="go(1.5,this)"><div class="big">1.50</div><div class="small">1 zi</div></button>
    <button class="target" onclick="go(5,this)"><div class="big">5</div><div class="small">max. 2 zile</div></button>
    <button class="target active" onclick="go(10,this)"><div class="big">10</div><div class="small">max. 4 zile</div></button>
    <button class="target" onclick="go(100,this)"><div class="big">100</div><div class="small">max. 7 zile</div></button>
   </div>
   <div class="custom"><input id="customTarget" inputmode="decimal" type="number" min="1.05" max="500" step="0.1" placeholder="Altă cotă, ex. 50"><button onclick="goCustom()">ANALIZEAZĂ</button></div>
  </div>'''

ANALYZE_BLOCK = r'''  <div class="analyze-wrap">
   <button class="analyze-main" onclick="go(10,null)">ANALIZEAZĂ BILETUL</button>
  </div>'''


@app.get("/", response_class=HTMLResponse)
def home():
    response = server_v3.home()
    html = response.body.decode("utf-8")

    html = html.replace("ENGINE v9.2", "ENGINE v9.3")
    html = html.replace("engine v9.2", "engine v9.3")
    html = html.replace("</style>", EXTRA_CSS + "\n</style>", 1)
    html = html.replace(TARGET_BLOCK, ANALYZE_BLOCK, 1)

    # Add per-match odds beside the existing ticket filters.
    period_field = r'''   <div class="filter-field wide"><label>Perioadă meciuri</label><select id="periodDays"><option value="0">Auto după cotă</option><option value="1">1 zi</option><option value="2">2 zile</option><option value="3">3 zile</option><option value="4">4 zile</option><option value="5">5 zile</option><option value="6">6 zile</option><option value="7">7 zile</option></select></div>'''
    replacement = r'''   <div class="filter-field"><label>Cotă/meci MIN</label><input id="legOddsMin" type="number" inputmode="decimal" min="1.01" max="100" step="0.01" placeholder="Auto"></div>
   <div class="filter-field"><label>Cotă/meci MAX</label><input id="legOddsMax" type="number" inputmode="decimal" min="1.01" max="100" step="0.01" placeholder="Auto"></div>
   <div class="filter-field wide"><label>Perioadă meciuri</label><select id="periodDays"><option value="1" selected>1 zi</option><option value="2">2 zile</option><option value="3">3 zile</option><option value="4">4 zile</option><option value="5">5 zile</option><option value="6">6 zile</option><option value="7">7 zile</option></select></div>'''
    html = html.replace(period_field, replacement, 1)
    html = html.replace(
        "Limitele se aplică biletului final, nu cotelor individuale.",
        "Cota biletului și numărul de meciuri se aplică biletului final; Cotă/meci MIN–MAX filtrează fiecare selecție individuală.",
        1,
    )

    # Wire the two new inputs.
    html = html.replace(
        "const periodDays=document.getElementById('periodDays');",
        "const periodDays=document.getElementById('periodDays');\n"
        "const legOddsMin=document.getElementById('legOddsMin');\n"
        "const legOddsMax=document.getElementById('legOddsMax');",
        1,
    )

    old_url = r'''const params=new URLSearchParams({day:d.value,target:String(t),sport:currentSport});
  const omin=Number(ticketOddsMin.value||0),omax=Number(ticketOddsMax.value||0);
  const lmin=Number(minLegs.value||0),lmax=Number(maxLegs.value||0),pd=Number(periodDays.value||0);
  if(omin>0)params.set('ticket_odds_min',String(omin));
  if(omax>0)params.set('ticket_odds_max',String(omax));
  if(lmin>0)params.set('min_legs',String(lmin));
  if(lmax>0)params.set('max_legs',String(lmax));
  if(pd>0)params.set('period_days',String(pd));
  const url='/api/analyze?'+params.toString();'''
    new_url = r'''const params=new URLSearchParams({day:d.value,sport:currentSport});
  const omin=Number(ticketOddsMin.value||0),omax=Number(ticketOddsMax.value||0);
  const lmin=Number(minLegs.value||0),lmax=Number(maxLegs.value||0),pd=Number(periodDays.value||1);
  const legmin=Number(legOddsMin.value||0),legmax=Number(legOddsMax.value||0);
  if(omin>0)params.set('ticket_odds_min',String(omin));
  if(omax>0)params.set('ticket_odds_max',String(omax));
  if(lmin>0)params.set('min_legs',String(lmin));
  if(lmax>0)params.set('max_legs',String(lmax));
  if(legmin>0)params.set('leg_odds_min',String(legmin));
  if(legmax>0)params.set('leg_odds_max',String(legmax));
  params.set('period_days',String(pd));
  const url='/api/analyze?'+params.toString();'''
    html = html.replace(old_url, new_url, 1)

    html = html.replace(
        "Caut global varianta pentru COTA '+esc(t)+'",
        "Caut cea mai sigură combinație în limitele alese",
        1,
    )
    html = html.replace(
        "'+esc(sportName)+' • COTA '+esc(t)+' • BEST BUILD",
        "'+esc(sportName)+' • BEST BUILD",
        1,
    )
    html = html.replace(
        "Nu există momentan o combinație suficient de apropiată de COTA '+esc(t)+'.",
        "Nu există momentan o combinație care să respecte toate limitele alese.",
        1,
    )

    # Extend the active-filter summary with the per-match odds interval.
    old_summary = "if(tc.min_legs||tc.max_legs)bits.push('Meciuri '+esc(tc.min_legs??'—')+'–'+esc(tc.max_legs??'—'));"
    new_summary = old_summary + "\n   if(tc.leg_odds_min||tc.leg_odds_max)bits.push('Cotă/meci '+esc(tc.leg_odds_min??'—')+'–'+esc(tc.leg_odds_max??'—'));"
    html = html.replace(old_summary, new_summary, 1)

    return HTMLResponse(html)
