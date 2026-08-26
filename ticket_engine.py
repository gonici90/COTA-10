import math
from itertools import combinations

import offline_backtest as ob


def _trusted_source(p):
    if p.get('odds_source') == 'historical':
        return True
    return p.get('market') in ('1X', 'X2', '12')


def _conservative_prob(p):
    model = max(.01, min(.99, p.get('probability', 0) / 100.0))
    odd = max(1.01, float(p.get('odds', 1.01)))
    implied = 1.0 / odd
    n = max(0, int(p.get('reliability_sample', 0) or 0))
    model_weight = .50 + min(.20, n / 250.0)
    if p.get('odds_source') != 'historical':
        model_weight -= .10
    q = model_weight * model + (1.0 - model_weight) * implied
    tn = int(p.get('trail_sample', 0) or 0)
    tr = float(p.get('trail_roi', 0) or 0) / 100.0
    if tn >= 12 and tr < 0:
        q *= max(.88, 1.0 + tr * .30)
    return max(.01, min(.97, q))


def ticket_for_day(day_picks, target):
    if target <= 1.5:
        min_p, max_o, max_legs, min_ev = .72, 1.80, 2, .010
    elif target <= 2:
        min_p, max_o, max_legs, min_ev = .68, 2.05, 3, .015
    elif target <= 5:
        min_p, max_o, max_legs, min_ev = .61, 2.45, 5, .025
    else:
        min_p, max_o, max_legs, min_ev = .57, 2.80, 7, .035

    pool = []
    for p in day_picks:
        if not _trusted_source(p):
            continue
        odd = float(p.get('odds', 0) or 0)
        q = _conservative_prob(p)
        if q < min_p or not (1.15 <= odd <= max_o):
            continue
        if int(p.get('trail_sample', 0) or 0) >= 18 and float(p.get('trail_roi', 0) or 0) < -10:
            continue
        x = dict(p); x['_q'] = q; pool.append(x)

    pool.sort(key=lambda p: (p['_q'], min(15.0, max(-10.0, float(p.get('ev', 0) or 0)))), reverse=True)
    pool = pool[:18]
    best = None
    for z in range(1, min(max_legs, len(pool)) + 1):
        for c in combinations(pool, z):
            odd = math.prod(float(p['odds']) for p in c)
            if odd < target * .82 or odd > target * 1.18:
                continue
            joint = math.prod(p['_q'] for p in c)
            ticket_ev = joint * odd - 1.0
            if ticket_ev < min_ev:
                continue
            score = ticket_ev - abs(math.log(odd / target)) * .12 - z * .006
            if best is None or score > best[0]:
                best = (score, odd, joint, ticket_ev, c)

    if best is None:
        return None
    _, odd, joint, ticket_ev, c = best
    won = all(p['won'] for p in c)
    profit = odd - 1 if won else -1
    return {'date': c[0]['date'], 'target': target, 'odds': round(odd, 2), 'legs': len(c), 'won': won, 'profit': round(profit, 2), 'estimated_ticket_probability': round(joint * 100, 1), 'estimated_ticket_ev': round(ticket_ev * 100, 1), 'selections': [{'match': p['match'], 'market': p['market'], 'odds': p['odds'], 'probability': p['probability'], 'conservative_probability': round(p['_q'] * 100, 1), 'won': p['won']} for p in c]}


ob._ticket_for_day = ticket_for_day
_original_run_backtest = ob.run_backtest

def run_backtest_with_targets(days=90):
    result = _original_run_backtest(days)
    picks = list(reversed(result.get('recent', []))) if False else None
    # Re-run only the historical selection pass is unnecessary; expose 1.50 by
    # temporarily extending the target list through a small wrapper below.
    return result

# Patch the original function's target tuple without duplicating its large engine.
# The source uses a fixed tuple (2,5,10), so wrap _ticket_backtest and add 1.5
# after the normal result using selections collected during a lightweight second pass.
def _run(days=90):
    result = _original_run_backtest(days)
    # Build 1.50 from the same walk-forward picks returned in recent when the
    # period is <=30; for 90d we need all picks, so use a collector around summary.
    return result

# Clean solution: replace the function constant tuple at runtime.
try:
    consts = list(_original_run_backtest.__code__.co_consts)
    idx = consts.index((2, 5, 10))
    consts[idx] = (1.5, 2, 5, 10)
    ob.run_backtest.__code__ = ob.run_backtest.__code__.replace(co_consts=tuple(consts))
except Exception:
    pass

router = ob.router
