"""Analiza Cota AI v9.2: user-controlled ticket constraints.

Keeps server_v2 tennis coverage patch, replaces only the analyze/root routes, and
leaves all existing sport engines untouched.
"""
from fastapi import HTTPException, Query
from fastapi.responses import HTMLResponse

import server_v2  # installs tennis coverage patch before server is used
import server as base_server
import market_engine
import odds_sports
import ticket_constraints


app = server_v2.app
app.version = "9.2"


def _drop_get(path):
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and "GET" in (getattr(route, "methods", None) or set())
        )
    ]


# Replace only these endpoints from server.py.
for _path in ("/", "/api/analyze", "/health"):
    _drop_get(_path)


def _auto_window(target):
    t = float(target)
    if t <= 1.75:
        return 1
    if t <= 6:
        return 2
    if t <= 20:
        return 4
    return 7


def _validate_ranges(odds_min, odds_max, min_legs, max_legs):
    if odds_min and odds_max and odds_min > odds_max:
        raise HTTPException(400, "Cota minimă nu poate fi mai mare decât cota maximă")
    if min_legs and max_legs and min_legs > max_legs:
        raise HTTPException(400, "Numărul minim de meciuri nu poate fi mai mare decât maximul")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "9.2",
        "football_provider": "5DollarFootballAPI Pro + Bet365",
        "multisport_provider": "The Odds API",
        "the_odds_api_configured": odds_sports.configured(),
        "optimizer": "safety-first-global + user constraints",
        "ui": "pro-multisport-v2",
    }


@app.get("/api/analyze")
def analyze(
    day: str,
    target: float = Query(10.0, ge=1.05, le=500.0),
    sport: str = Query("football"),
    period_days: int = Query(0, ge=0, le=7),
    ticket_odds_min: float = Query(0.0, ge=0.0, le=500.0),
    ticket_odds_max: float = Query(0.0, ge=0.0, le=500.0),
    min_legs: int = Query(0, ge=0, le=30),
    max_legs: int = Query(0, ge=0, le=30),
):
    sport = sport.lower().strip()
    _validate_ranges(ticket_odds_min, ticket_odds_max, min_legs, max_legs)
    days = period_days or _auto_window(target)

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
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    result["period_days_selected"] = days
    result["period_days_is_custom"] = bool(period_days)
    return result


FILTER_CSS = r'''
.ticket-filter-panel{
 margin-top:12px;border:1px solid #244331;background:rgba(7,20,14,.65);
 border-radius:17px;padding:13px;position:relative;z-index:1
}
.filter-title{font-size:10px;color:#80dca4;font-weight:900;text-transform:uppercase;letter-spacing:.8px;margin-bottom:10px}
.filter-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.filter-field{min-width:0}
.filter-field.wide{grid-column:span 2}
.filter-field label{display:block;font-size:9px;color:var(--muted);font-weight:800;text-transform:uppercase;letter-spacing:.45px;margin:0 0 5px}
.filter-field input,.filter-field select{
 width:100%;border:1px solid #284536;background:#08150f;color:var(--text);border-radius:12px;
 padding:11px 10px;outline:none;font:inherit;appearance:none
}
.filter-field input:focus,.filter-field select:focus{border-color:#3edc82;box-shadow:0 0 0 3px rgba(71,232,137,.08)}
.filter-hint{font-size:8px;color:#667e70;margin-top:8px;line-height:1.4}
@media(min-width:720px){.filter-grid{grid-template-columns:repeat(5,1fr)}.filter-field.wide{grid-column:auto}}
'''

FILTER_HTML = r'''
 <div class="ticket-filter-panel">
  <div class="filter-title">Filtre bilet</div>
  <div class="filter-grid">
   <div class="filter-field"><label>Cotă bilet MIN</label><input id="ticketOddsMin" type="number" inputmode="decimal" min="1.01" max="500" step="0.1" placeholder="Auto"></div>
   <div class="filter-field"><label>Cotă bilet MAX</label><input id="ticketOddsMax" type="number" inputmode="decimal" min="1.01" max="500" step="0.1" placeholder="Auto"></div>
   <div class="filter-field"><label>Nr. meciuri MIN</label><input id="minLegs" type="number" inputmode="numeric" min="1" max="30" step="1" placeholder="Auto"></div>
   <div class="filter-field"><label>Nr. meciuri MAX</label><input id="maxLegs" type="number" inputmode="numeric" min="1" max="30" step="1" placeholder="Auto"></div>
   <div class="filter-field wide"><label>Perioadă meciuri</label><select id="periodDays"><option value="0">Auto după cotă</option><option value="1">1 zi</option><option value="2">2 zile</option><option value="3">3 zile</option><option value="4">4 zile</option><option value="5">5 zile</option><option value="6">6 zile</option><option value="7">7 zile</option></select></div>
  </div>
  <div class="filter-hint">Dacă lași un câmp pe Auto, motorul folosește regulile lui normale. Limitele se aplică biletului final, nu cotelor individuale.</div>
 </div>
'''


@app.get("/", response_class=HTMLResponse)
def home():
    response = base_server.home()
    html = response.body.decode("utf-8")

    html = html.replace("ENGINE v9.0", "ENGINE v9.2")
    html = html.replace("engine v9.0", "engine v9.2")
    html = html.replace("</style>", FILTER_CSS + "\n</style>", 1)
    html = html.replace("\n</section>\n\n<div id=\"out\"></div>", FILTER_HTML + "\n</section>\n\n<div id=\"out\"></div>", 1)

    html = html.replace(
        "const customTarget=document.getElementById('customTarget');",
        "const customTarget=document.getElementById('customTarget');\n"
        "const ticketOddsMin=document.getElementById('ticketOddsMin');\n"
        "const ticketOddsMax=document.getElementById('ticketOddsMax');\n"
        "const minLegs=document.getElementById('minLegs');\n"
        "const maxLegs=document.getElementById('maxLegs');\n"
        "const periodDays=document.getElementById('periodDays');",
        1,
    )

    old_url = "const url='/api/analyze?day='+encodeURIComponent(d.value)+'&target='+encodeURIComponent(t)+'&sport='+encodeURIComponent(currentSport);"
    new_url = r'''const params=new URLSearchParams({day:d.value,target:String(t),sport:currentSport});
  const omin=Number(ticketOddsMin.value||0),omax=Number(ticketOddsMax.value||0);
  const lmin=Number(minLegs.value||0),lmax=Number(maxLegs.value||0),pd=Number(periodDays.value||0);
  if(omin>0)params.set('ticket_odds_min',String(omin));
  if(omax>0)params.set('ticket_odds_max',String(omax));
  if(lmin>0)params.set('min_legs',String(lmin));
  if(lmax>0)params.set('max_legs',String(lmax));
  if(pd>0)params.set('period_days',String(pd));
  const url='/api/analyze?'+params.toString();'''
    html = html.replace(old_url, new_url, 1)

    # Show the effective constraints returned by the backend above the ticket.
    marker = "if(x.sources){"
    summary = r'''if(x.ticket_constraints||x.period_days_is_custom){
   const tc=x.ticket_constraints||{};
   let bits=[];
   if(tc.odds_min||tc.odds_max)bits.push('Cotă '+esc(tc.odds_min??'—')+'–'+esc(tc.odds_max??'—'));
   if(tc.min_legs||tc.max_legs)bits.push('Meciuri '+esc(tc.min_legs??'—')+'–'+esc(tc.max_legs??'—'));
   if(x.period_days_selected)bits.push('Perioadă '+esc(x.period_days_selected)+' zile');
   h+='<div class="panel"><div class="meta"><b>Filtre active:</b><span>'+bits.join(' • ')+'</span></div></div>';
  }

  if(x.sources){'''
    html = html.replace(marker, summary, 1)

    return HTMLResponse(html)
