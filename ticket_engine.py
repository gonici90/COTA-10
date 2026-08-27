import math
from itertools import combinations

import offline_backtest as ob

ob.LEAGUES.update({
    'E0 3.csv': 'Premier League',
    'SP1 2.csv': 'La Liga',
    'I1 2.csv': 'Serie A',
    'D1 2.csv': 'Bundesliga',
    'F1 2.csv': 'Ligue 1',
})


def _is_historical(p):
    return str(p.get('odds_source') or '').startswith('historical')


def _trusted_source(p):
    if _is_historical(p):
        return True
    return p.get('market') in ('1X', 'X2', '12', 'Over 1.5', 'Under 3.5')


def _conservative_prob(p):
    model = max(.01, min(.99, p.get('probability', 0) / 100.0))
    odd = max(1.01, float(p.get('odds', 1.01)))
    implied = 1.0 / odd
    n = max(0, int(p.get('reliability_sample', 0) or 0))
    maturity = max(0., min(1., float(p.get('ml_maturity', 0) or 0) / 100.0))
    model_weight = .48 + min(.17, n / 300.0) + .08 * maturity
    if not _is_historical(p):
        model_weight -= .10
    model_weight = max(.38, min(.73, model_weight))
    q = model_weight * model + (1.0 - model_weight) * implied
    tn = int(p.get('trail_sample', 0) or 0)
    tr = float(p.get('trail_roi', 0) or 0) / 100.0
    if tn >= 12 and tr < 0:
        q *= max(.88, 1.0 + tr * .30)
    return max(.01, min(.97, q))


def ticket_for_day(day_picks, target):
    # COTA 1.50 is deliberately built from safer legs whenever possible.
    # A lone 1.30-1.35 selection is no longer accepted as a 1.50 ticket.
    if target <= 1.5:
        min_p, max_o, max_legs, min_ev = .69, 1.58, 3, -.035
        min_target, max_target = target * .96, target * 1.08
    elif target <= 2:
        min_p, max_o, max_legs, min_ev = .67, 2.05, 3, .010
        min_target, max_target = target * .94, target * 1.10
    elif target <= 5:
        min_p, max_o, max_legs, min_ev = .61, 2.45, 5, .020
        min_target, max_target = target * .90, target * 1.12
    else:
        min_p, max_o, max_legs, min_ev = .57, 2.80, 7, .030
        min_target, max_target = target * .88, target * 1.15

    pool = []
    for p in day_picks:
        if not _trusted_source(p):
            continue
        odd = float(p.get('odds', 0) or 0)
        q = _conservative_prob(p)
        if q < min_p or not (1.10 <= odd <= max_o):
            continue
        if int(p.get('trail_sample', 0) or 0) >= 18 and float(p.get('trail_roi', 0) or 0) < -10:
            continue
        x = dict(p)
        x['_q'] = q
        pool.append(x)

    # Keep enough low-odds candidates for combinations such as 1.15 x 1.15 x 1.15.
    pool.sort(key=lambda p: (p['_q'], min(15.0, max(-10.0, float(p.get('ev', 0) or 0)))), reverse=True)
    pool = pool[:24]

    candidates = []
    for z in range(1, min(max_legs, len(pool)) + 1):
        for c in combinations(pool, z):
            # Never put two correlated markets from the same match on one ticket.
            if len({p.get('match') for p in c}) != len(c):
                continue
            odd = math.prod(float(p['odds']) for p in c)
            if odd < min_target or odd > max_target:
                continue
            joint = math.prod(p['_q'] for p in c)
            ticket_ev = joint * odd - 1.0
            if ticket_ev < min_ev:
                continue

            # Primary objective: probability of winning. Then closeness to target and EV.
            # Small diversification bonus makes 2-3 very safe legs preferable to one
            # borderline leg when their joint probability is genuinely higher.
            closeness = abs(math.log(odd / target))
            score = joint - closeness * .035 + max(-.03, min(.08, ticket_ev)) * .08
            if target <= 1.5 and z >= 2:
                score += .004
            candidates.append((score, joint, -closeness, ticket_ev, odd, c))

    if not candidates:
        return None

    _, joint, _, ticket_ev, odd, c = max(candidates, key=lambda x: (x[0], x[1], x[2], x[3]))
    won = all(p['won'] for p in c)
    profit = odd - 1 if won else -1
    return {
        'date': c[0]['date'], 'target': target, 'odds': round(odd, 2),
        'legs': len(c), 'won': won, 'profit': round(profit, 2),
        'estimated_ticket_probability': round(joint * 100, 1),
        'estimated_ticket_ev': round(ticket_ev * 100, 1),
        'selections': [
            {'match': p['match'], 'market': p['market'], 'odds': p['odds'],
             'probability': p['probability'],
             'conservative_probability': round(p['_q'] * 100, 1), 'won': p['won']}
            for p in c
        ]
    }


ob._ticket_for_day = ticket_for_day
_original_run_backtest = ob.run_backtest

try:
    consts = list(_original_run_backtest.__code__.co_consts)
    idx = consts.index((1.5, 2, 5, 10))
except ValueError:
    try:
        idx = consts.index((2, 5, 10))
        consts[idx] = (1.5, 2, 5, 10)
        ob.run_backtest.__code__ = ob.run_backtest.__code__.replace(co_consts=tuple(consts))
    except Exception:
        pass
except Exception:
    pass

router = ob.router
