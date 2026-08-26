import math
from itertools import combinations

import offline_backtest as ob

# Previous Big 5 season is history/warm-up for the current season.
ob.LEAGUES.update({
    'E0 3.csv': 'Premier League',
    'SP1 2.csv': 'La Liga',
    'I1 2.csv': 'Serie A',
    'D1 2.csv': 'Bundesliga',
    'F1 2.csv': 'Ligue 1',
})

# IMPORTANT: a backtest must never bet at odds manufactured from the same
# model probability. The old engine did that for 1X/X2/12/O1.5/U3.5/BTTS.
# Keep those probabilities for diagnostics, but tickets use only markets for
# which the CSV contains an actual historical bookmaker price.
def _real_odds_only(r, probs):
    return ob._base_odds(r)

ob._odds = _real_odds_only


def _conservative_prob(p):
    model = max(.01, min(.99, float(p.get('probability', 0) or 0) / 100.0))
    odd = max(1.01, float(p.get('odds', 1.01) or 1.01))
    implied = 1.0 / odd
    n = max(0, int(p.get('reliability_sample', 0) or 0))
    # Shrink model toward the market. More history permits a little more model
    # weight, but never lets a short sample dominate the bookmaker baseline.
    model_weight = min(.62, .38 + n / 300.0)
    q = model_weight * model + (1.0 - model_weight) * implied
    tn = int(p.get('trail_sample', 0) or 0)
    tr = float(p.get('trail_roi', 0) or 0) / 100.0
    if tn >= 20 and tr < 0:
        q *= max(.90, 1.0 + tr * .25)
    return max(.01, min(.97, q))


def ticket_for_day(day_picks, target):
    if target <= 1.5:
        min_q, max_o, max_legs = .66, 1.72, 2
    elif target <= 2:
        min_q, max_o, max_legs = .60, 2.10, 3
    elif target <= 5:
        min_q, max_o, max_legs = .53, 2.50, 5
    else:
        min_q, max_o, max_legs = .48, 2.90, 7

    pool = []
    for p in day_picks:
        # odds_source=historical means an actual CSV bookmaker/average price.
        if p.get('odds_source') != 'historical':
            continue
        odd = float(p.get('odds', 0) or 0)
        if not (1.15 <= odd <= max_o):
            continue
        q = _conservative_prob(p)
        if q < min_q:
            continue
        tn = int(p.get('trail_sample', 0) or 0)
        tr = float(p.get('trail_roi', 0) or 0)
        if tn >= 25 and tr < -12:
            continue
        x = dict(p)
        x['_q'] = q
        pool.append(x)

    # Do not require a positive model EV here: that was a major sample killer.
    # Rank by conservative probability, calibration history and closeness later.
    pool.sort(key=lambda p: (p['_q'], float(p.get('band_score', 0) or 0)), reverse=True)
    pool = pool[:24]
    best = None
    for z in range(1, min(max_legs, len(pool)) + 1):
        for c in combinations(pool, z):
            # Never put two selections from the same match on one accumulator.
            if len({p['match'] for p in c}) != z:
                continue
            odd = math.prod(float(p['odds']) for p in c)
            if odd < target * .78 or odd > target * 1.22:
                continue
            joint = math.prod(p['_q'] for p in c)
            score = joint - abs(math.log(odd / target)) * .08 - z * .004
            if best is None or score > best[0]:
                best = (score, odd, joint, c)

    if best is None:
        return None
    _, odd, joint, c = best
    won = all(p['won'] for p in c)
    profit = odd - 1 if won else -1
    return {
        'date': c[0]['date'], 'target': target, 'odds': round(odd, 2),
        'legs': len(c), 'won': won, 'profit': round(profit, 2),
        'estimated_ticket_probability': round(joint * 100, 1),
        'selections': [
            {'match': p['match'], 'market': p['market'], 'odds': p['odds'],
             'probability': p['probability'],
             'conservative_probability': round(p['_q'] * 100, 1),
             'won': p['won']} for p in c
        ]
    }


ob._ticket_for_day = ticket_for_day
_original_run_backtest = ob.run_backtest

# Add COTA 1.50 to the original target tuple without duplicating the large
# walk-forward engine.
try:
    consts = list(_original_run_backtest.__code__.co_consts)
    idx = consts.index((2, 5, 10))
    consts[idx] = (1.5, 2, 5, 10)
    ob.run_backtest.__code__ = ob.run_backtest.__code__.replace(co_consts=tuple(consts))
except Exception:
    pass

router = ob.router
