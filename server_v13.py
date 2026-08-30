"""Analiza Cota AI v10.4: Big-5 CSV walk-forward using real historical bookmaker odds only."""
from collections import defaultdict
from itertools import combinations
import math
import time

from fastapi.responses import HTMLResponse
import server_v7, offline_backtest, odds_sports, backtest_engine

app = server_v7.app
app.version = '10.4'


# Historical-price integrity rule:
# Football-Data CSVs in this repo expose real bookmaker/market prices for
# 1/X/2 and Over/Under 2.5. Other lines previously used derived/model prices,
# which are not suitable for an honest profitability backtest.
_REAL_ODDS_MARKETS = {'1', 'X', '2', 'Over 2.5', 'Under 2.5'}
_original_probs = offline_backtest._probs


def _real_market_probs(home_lambda, away_lambda):
    probs = _original_probs(home_lambda, away_lambda)
    return {m: p for m, p in probs.items() if m in _REAL_ODDS_MARKETS}


# This also prevents a synthetic market from winning the per-match candidate
# ranking before the ticket builder sees the match.
offline_backtest._probs = _real_market_probs


def _quality_ticket_for_day(day_picks, target):
    target = float(target)
    near_two = 1.70 <= target <= 2.30

    if near_two:
        min_p, max_o, max_legs = .64, 2.10, 3
        low_ticket, high_ticket = target * .95, target * 1.05
    elif target <= 1.5:
        min_p, max_o, max_legs = .70, 1.70, 2
        low_ticket, high_ticket = target * .94, target * 1.06
    elif target <= 5:
        min_p, max_o, max_legs = .64, 2.35, 5
        low_ticket, high_ticket = target * .93, target * 1.07
    else:
        min_p, max_o, max_legs = .61, 2.65, 7
        low_ticket, high_ticket = target * .92, target * 1.08

    pool = []
    for p in day_picks:
        if p.get('market') not in _REAL_ODDS_MARKETS:
            continue
        try:
            probability = float(p.get('probability') or 0) / 100.0
            odds = float(p.get('odds') or 0)
        except (TypeError, ValueError):
            continue
        if probability < min_p or not (1.12 <= odds <= max_o):
            continue
        if p.get('trail_sample', 0) >= 18 and p.get('trail_roi', 0) < -12:
            continue
        if p.get('band_sample', 0) >= 15 and (
            p.get('band_roi', 0) < -10 or p.get('band_score', 0) < -4.0
        ):
            continue
        pool.append(p)

    pool = sorted(
        pool,
        key=lambda p: (
            float(p.get('probability') or 0)
            + min(max(float(p.get('ev') or 0), 0), 15) * .30
            + max(-5, min(5, float(p.get('band_score') or 0))) * .35,
            float(p.get('odds') or 0),
        ),
        reverse=True,
    )[:28]

    best = None
    for z in range(1, min(max_legs, len(pool)) + 1):
        for c in combinations(pool, z):
            odd = math.prod(float(p['odds']) for p in c)
            if odd < low_ticket or odd > high_ticket:
                continue

            if near_two and z == 1:
                p0 = float(c[0].get('probability') or 0) / 100.0
                if not (target * .96 <= odd <= target * 1.04 and p0 >= .68):
                    continue

            avg_p = sum(float(p.get('probability') or 0) for p in c) / z
            avg_ev = sum(float(p.get('ev') or 0) for p in c) / z
            avg_band = sum(float(p.get('band_score') or 0) for p in c) / z
            leg_penalty = (abs(z - 2) * .006) if near_two else (z * .006)
            score = (
                abs(math.log(odd / target))
                - avg_p / 720
                - min(max(avg_ev, 0), 12) / 1100
                - avg_band / 6000
                + leg_penalty
            )
            if best is None or score < best[0]:
                best = (score, odd, c)

    if not best:
        return None

    _, odd, c = best
    won = all(bool(p.get('won')) for p in c)
    profit = odd - 1 if won else -1
    return {
        'date': c[0]['date'],
        'target': target,
        'odds': round(odd, 2),
        'legs': len(c),
        'won': won,
        'profit': round(profit, 2),
        'selections': [
            {
                'match': p['match'],
                'market': p['market'],
                'odds': p['odds'],
                'probability': p['probability'],
                'won': p['won'],
            }
            for p in c
        ],
    }


offline_backtest._ticket_for_day = _quality_ticket_for_day


def _summary(days, target, data, tb):
    tickets = int(tb.get('tickets') or 0)
    wins = int(tb.get('wins') or 0)
    losses = int(tb.get('losses') or 0)
    cov = data.get('coverage') or {}
    return {
        'days_requested': int(days),
        'ticket_odds_requested': round(float(target), 2),
        'tickets': tickets,
        'wins': wins,
        'losses': losses,
        'pushes': 0,
        'hit_rate': float(tb.get('hit_rate') or 0),
        'profit': round(float(tb.get('profit') or 0) * 100, 2),
        'roi': float(tb.get('roi') or 0),
        'average_actual_odds': float(tb.get('avg_odds') or 0) or None,
        'no_ticket_days': max(0, int(days) - tickets),
        'unsettled_days': 0,
        'fixtures_seen': int(cov.get('fixtures_found') or 0),
        'fixtures_analyzed': int(cov.get('fixtures_analyzed') or 0),
        'truncated_days': 0,
        'note': 'Big-5 CSV walk-forward; numai cote istorice reale 1/X/2 si Over/Under 2.5.'
    }


def _all_tickets(picks, target):
    by_day = defaultdict(list)
    for p in picks:
        by_day[p.get('date')].append(p)
    out = []
    for d in sorted(k for k in by_day if k):
        t = offline_backtest._ticket_for_day(by_day[d], float(target))
        if t:
            out.append(t)
    return out


def _run_csv_job(job_id, days, target):
    original_ticket_backtest = offline_backtest._ticket_backtest
    captured = {'picks': []}

    def capture_ticket_backtest(picks, t):
        captured['picks'] = list(picks)
        return original_ticket_backtest(picks, t)

    try:
        with backtest_engine.JOBS_LOCK:
            backtest_engine.JOBS[job_id]['current_day'] = 'Big 5 CSV / cote reale'
            backtest_engine.JOBS[job_id]['progress'] = 1

        offline_backtest._ticket_backtest = capture_ticket_backtest
        data = offline_backtest.run_backtest(max(20, int(days)))
        picks = captured.get('picks') or []
        tb = original_ticket_backtest(picks, float(target))
        tickets = _all_tickets(picks, float(target))

        rows = []
        for t in reversed(tickets):
            legs = [
                {
                    'match': q.get('match'),
                    'selection': q.get('market'),
                    'odds': q.get('odds'),
                    'score': 'OK' if q.get('won') else 'MISS'
                }
                for q in (t.get('selections') or [])
            ]
            rows.append({
                'date': t.get('date'),
                'requested_odds': round(float(target), 2),
                'actual_odds': t.get('odds'),
                'legs': t.get('legs', 0),
                'status': 'WIN' if t.get('won') else 'LOSE',
                'profit': round(float(t.get('profit') or 0) * 100, 2),
                'leg_results': legs
            })

        result = {'summary': _summary(days, target, data, tb), 'daily': rows}
        with backtest_engine.JOBS_LOCK:
            backtest_engine.JOBS[job_id].update({
                'status': 'done',
                'progress': int(days),
                'partial': result['summary'],
                'result': result,
                'finished_at': time.time(),
                'current_day': None
            })
    except Exception as exc:
        with backtest_engine.JOBS_LOCK:
            backtest_engine.JOBS[job_id].update({
                'status': 'error',
                'error': f'{type(exc).__name__}: {str(exc)[:500]}',
                'finished_at': time.time()
            })
    finally:
        offline_backtest._ticket_backtest = original_ticket_backtest


backtest_engine._run = _run_csv_job


def _drop(path):
    app.router.routes[:] = [
        r for r in app.router.routes
        if not (getattr(r, 'path', None) == path and 'GET' in (getattr(r, 'methods', None) or set()))
    ]


for p in ('/', '/health'):
    _drop(p)


@app.get('/health')
def health():
    return {
        'status': 'ok',
        'version': '10.4',
        'backtest': 'Big-5 CSV walk-forward, real historical odds only',
        'backtest_leagues': ['Premier League', 'La Liga', 'Serie A', 'Bundesliga', 'Ligue 1'],
        'backtest_cota2_band': '1.90-2.10',
        'backtest_markets': ['1', 'X', '2', 'Over 2.5', 'Under 2.5'],
        'synthetic_historical_odds': False,
        'multisport_provider': 'The Odds API',
        'the_odds_api_configured': odds_sports.configured(),
        'ui': 'pro-multisport-v10.4'
    }


@app.get('/', response_class=HTMLResponse)
def home():
    response = server_v7.home()
    html = response.body.decode('utf-8')
    html = html.replace('ENGINE v9.6', 'ENGINE v10.4').replace('engine v9.6', 'engine v10.4')
    html = html.replace(
        'Backtestul folosește cote pre-match/closing disponibile în Pro; tick-history complet este o facilitate separată.',
        'WALK-FORWARD v10.4: backtest Big 5 numai pe cote istorice reale disponibile în CSV: 1/X/2 și Over/Under 2.5. Șansele duble și liniile 1.5/3.5 nu mai sunt testate cu prețuri sintetice. Pentru țintă 2, biletul rămâne între 1.90 și 2.10 și nu este forțat.'
    )
    return HTMLResponse(html)
