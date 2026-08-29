"""Walk-forward backtest with an independent football model.

The model uses only results that existed BEFORE each simulated day:
- Elo team strength
- recent scoring/conceding form
- home/away form
- independent Poisson score model

Bookmaker prices are used only after the model probability exists, to measure edge and
price the bet. No future results are used while generating a ticket.
"""
import math
import time
from datetime import date, timedelta

WARMUP_DAYS = 21
MAX_LEGS = 4
MAX_FIXTURES_IN_SEARCH = 28
BEAM = 700


def install(bt, engine):
    original_summary = bt._summary

    def team_key(team):
        if not isinstance(team, dict):
            return str(team).strip().lower()
        ident = team.get('id') or team.get('team_id') or team.get('slug')
        if ident is not None:
            return f'id:{ident}'
        return str(team.get('name') or team.get('short_name') or '').strip().lower()

    def names(f):
        h, a = engine._teams(f)
        return h, a, str(h.get('name') or 'Home'), str(a.get('name') or 'Away')

    def new_stat():
        return {'elo': 1500.0, 'matches': 0, 'recent': [], 'home': [], 'away': []}

    def stat_for(state, key):
        if key not in state:
            state[key] = new_stat()
        return state[key]

    def wavg(items, idx, default):
        if not items:
            return float(default)
        vals = items[-8:]
        total = 0.0
        den = 0.0
        for age, item in enumerate(reversed(vals)):
            w = 0.82 ** age
            total += float(item[idx]) * w
            den += w
        return total / den if den else float(default)

    def blend(a, b, na, nb, default):
        wa = min(1.0, na / 6.0)
        wb = min(1.0, nb / 5.0)
        den = wa + wb
        if den <= 0:
            return default
        return (a * wa + b * wb) / den

    def model_probs(state, f):
        ht, at, _, _ = names(f)
        hs = stat_for(state, team_key(ht))
        ass = stat_for(state, team_key(at))
        if min(hs['matches'], ass['matches']) < 3:
            return None

        h_recent_gf = wavg(hs['recent'], 0, 1.35)
        h_recent_ga = wavg(hs['recent'], 1, 1.20)
        a_recent_gf = wavg(ass['recent'], 0, 1.20)
        a_recent_ga = wavg(ass['recent'], 1, 1.35)
        h_home_gf = wavg(hs['home'], 0, h_recent_gf)
        h_home_ga = wavg(hs['home'], 1, h_recent_ga)
        a_away_gf = wavg(ass['away'], 0, a_recent_gf)
        a_away_ga = wavg(ass['away'], 1, a_recent_ga)

        h_attack = blend(h_recent_gf, h_home_gf, len(hs['recent']), len(hs['home']), 1.35)
        h_def = blend(h_recent_ga, h_home_ga, len(hs['recent']), len(hs['home']), 1.20)
        a_attack = blend(a_recent_gf, a_away_gf, len(ass['recent']), len(ass['away']), 1.20)
        a_def = blend(a_recent_ga, a_away_ga, len(ass['recent']), len(ass['away']), 1.35)

        lam_h = 0.56 * h_attack + 0.44 * a_def + 0.10
        lam_a = 0.56 * a_attack + 0.44 * h_def - 0.02

        # Elo affects relative strength but never uses bookmaker information.
        elo_diff = (hs['elo'] + 62.0) - ass['elo']
        tilt = math.exp(max(-300.0, min(300.0, elo_diff)) / 1150.0)
        lam_h *= tilt
        lam_a /= tilt
        lam_h = max(0.30, min(3.60, lam_h))
        lam_a = max(0.25, min(3.30, lam_a))

        grid = []
        total_mass = 0.0
        for hg in range(9):
            ph = math.exp(-lam_h) * lam_h ** hg / math.factorial(hg)
            row = []
            for ag in range(9):
                pa = math.exp(-lam_a) * lam_a ** ag / math.factorial(ag)
                v = ph * pa
                row.append(v)
                total_mass += v
            grid.append(row)
        total_mass = total_mass or 1.0
        p1 = sum(grid[h][a] for h in range(9) for a in range(9) if h > a) / total_mass
        px = sum(grid[i][i] for i in range(9)) / total_mass
        p2 = max(0.0, 1.0 - p1 - px)

        return {
            '1': p1, 'X': px, '2': p2,
            '1X': p1 + px, 'X2': px + p2, '12': p1 + p2,
            'lambda_total': lam_h + lam_a,
            'samples': min(hs['matches'], ass['matches']),
            'elo_home': hs['elo'], 'elo_away': ass['elo'],
        }

    def pois_over(lam, line):
        # Only .5 goal lines are accepted, so there is no push ambiguity.
        cutoff = int(math.floor(line))
        under_eq = sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(cutoff + 1))
        return max(0.001, min(0.999, 1.0 - under_eq))

    def devig3(a, x, b):
        try:
            vals = [float(a), float(x), float(b)]
            if any(v <= 1.01 for v in vals):
                return None
            inv = [1.0 / v for v in vals]
            z = sum(inv)
            return [v / z for v in inv]
        except Exception:
            return None

    def devig2(a, b):
        try:
            a, b = float(a), float(b)
            if a <= 1.01 or b <= 1.01:
                return None
            x, y = 1.0 / a, 1.0 / b
            z = x + y
            return x / z, y / z
        except Exception:
            return None

    def candidate_score(c):
        # Probability is primary; edge and sample depth are confidence modifiers.
        return c['p'] + min(0.10, max(0.0, c['edge'])) * 0.55 + min(8, c['samples']) * 0.002

    def fixture_candidates(state, f):
        mp = model_probs(state, f)
        if not mp:
            return []
        parsed = bt._prematch_only(bt._extract_inline_odds(f))
        if not parsed:
            return []
        ht, at, hn, an = names(f)
        fk = str(f.get('id') or f.get('fixture_id') or f'{hn}|{an}|{f.get("kickoff") or f.get("start_time")}')
        out = []

        mx = engine._stage(parsed.get('1x2') or parsed.get('match_winner'))
        if isinstance(mx, dict):
            try:
                prices = {'1': float(mx.get('home')), 'X': float(mx.get('draw')), '2': float(mx.get('away'))}
            except Exception:
                prices = {}
            book = devig3(prices.get('1'), prices.get('X'), prices.get('2')) if prices else None
            if book:
                bp = {'1': book[0], 'X': book[1], '2': book[2]}
                for sel in ('1', 'X', '2'):
                    p = mp[sel]; o = prices[sel]; edge = p - bp[sel]
                    if 1.35 <= o <= 3.20 and p >= 0.43 and edge >= 0.045:
                        out.append({'fixture_key': fk, 'fixture': f, 'home': hn, 'away': an, 'selection': sel, 'kind': '1X2', 'odds': o, 'p': p, 'book_p': bp[sel], 'edge': edge, 'samples': mp['samples']})

                # Approximate DC quote from de-vigged 1X2 probability plus a small margin.
                for sel, parts in (('1X', ('1', 'X')), ('X2', ('X', '2')), ('12', ('1', '2'))):
                    book_p = bp[parts[0]] + bp[parts[1]]
                    p = mp[sel]
                    o = max(1.05, 0.965 / book_p)
                    edge = p - book_p
                    if 1.12 <= o <= 2.05 and p >= 0.64 and edge >= 0.035:
                        out.append({'fixture_key': fk, 'fixture': f, 'home': hn, 'away': an, 'selection': sel, 'kind': 'DOUBLE_CHANCE', 'odds': round(o, 2), 'p': p, 'book_p': book_p, 'edge': edge, 'samples': mp['samples'], 'synthetic_price': True})

        gm = engine._stage(parsed.get('goal_line') or parsed.get('goalline') or parsed.get('goals') or parsed.get('total_goals'))
        if isinstance(gm, dict):
            try:
                line = float(gm.get('line')); oo = float(gm.get('over')); ou = float(gm.get('under'))
            except Exception:
                line = oo = ou = None
            # Keep only half-goal lines for clean settlement/probability.
            if line is not None and abs(line * 2 - round(line * 2)) < 1e-8 and int(round(line * 2)) % 2 == 1:
                pair = devig2(oo, ou)
                if pair:
                    po = pois_over(mp['lambda_total'], line); pu = 1.0 - po
                    for sel, p, o, bp in ((f'Over {line:g}', po, oo, pair[0]), (f'Under {line:g}', pu, ou, pair[1])):
                        edge = p - bp
                        if 1.30 <= o <= 2.25 and p >= 0.56 and edge >= 0.04:
                            out.append({'fixture_key': fk, 'fixture': f, 'home': hn, 'away': an, 'selection': sel, 'kind': 'GOALS', 'odds': o, 'p': p, 'book_p': bp, 'edge': edge, 'samples': mp['samples']})

        out.sort(key=candidate_score, reverse=True)
        return out[:3]

    def build_ticket(candidates, target):
        by_fixture = {}
        for c in candidates:
            by_fixture.setdefault(c['fixture_key'], []).append(c)
        groups = list(by_fixture.values())
        groups.sort(key=lambda opts: candidate_score(opts[0]), reverse=True)
        groups = groups[:MAX_FIXTURES_IN_SEARCH]

        low, high = target * 0.94, target * 1.06
        hard_high = target * 1.10
        states = [(1.0, 1.0, 0.0, [])]
        for opts in groups:
            expanded = list(states)
            for prod, jp, es, picks in states:
                if len(picks) >= MAX_LEGS:
                    continue
                for c in opts:
                    np = prod * c['odds']
                    if np > hard_high:
                        continue
                    expanded.append((np, jp * c['p'], es + c['edge'], picks + [c]))
            def rank(s):
                prod, jp, es, picks = s
                in_band = low <= prod <= high and bool(picks)
                closeness = abs(math.log(max(prod, 1.0001) / target))
                return (1 if in_band else 0, jp + min(0.12, es) * 0.12, -closeness, -len(picks))
            expanded.sort(key=rank, reverse=True)
            states = expanded[:BEAM]

        valid = [s for s in states if s[3] and low <= s[0] <= high]
        if not valid:
            return None
        valid.sort(key=lambda s: (s[1] + min(0.12, s[2]) * 0.12, -len(s[3])), reverse=True)
        prod, jp, es, picks = valid[0]
        # A ticket still needs a real aggregate model advantage; no daily forcing.
        if es / len(picks) < 0.035:
            return None
        return {'odds': prod, 'joint_p': jp, 'edge_sum': es, 'picks': picks}

    def settle(sel, odd, f):
        if sel in {'1X', 'X2', '12'}:
            h, a = bt._score_values(f)
            if h is None:
                return None
            won = (sel == '1X' and h >= a) or (sel == 'X2' and a >= h) or (sel == '12' and h != a)
            return float(odd) if won else 0.0
        return bt._settle_selection(sel, odd, f)

    def update_state(state, f):
        hscore, ascore = bt._score_values(f)
        if hscore is None:
            return
        ht, at, _, _ = names(f)
        hk, ak = team_key(ht), team_key(at)
        hs, ass = stat_for(state, hk), stat_for(state, ak)
        eh = 1.0 / (1.0 + 10.0 ** ((ass['elo'] - (hs['elo'] + 62.0)) / 400.0))
        actual = 1.0 if hscore > ascore else 0.5 if hscore == ascore else 0.0
        margin = abs(hscore - ascore)
        k = 18.0 * (1.0 + min(2.0, margin) * 0.12)
        delta = k * (actual - eh)
        hs['elo'] += delta
        ass['elo'] -= delta
        hp = 3 if hscore > ascore else 1 if hscore == ascore else 0
        ap = 3 if ascore > hscore else 1 if hscore == ascore else 0
        hs['recent'].append((hscore, ascore, hp)); ass['recent'].append((ascore, hscore, ap))
        hs['home'].append((hscore, ascore, hp)); ass['away'].append((ascore, hscore, ap))
        hs['recent'] = hs['recent'][-10:]; ass['recent'] = ass['recent'][-10:]
        hs['home'] = hs['home'][-8:]; ass['away'] = ass['away'][-8:]
        hs['matches'] += 1; ass['matches'] += 1

    def analyze_day(state, day, target, evaluate=True):
        payload = bt._fetch_finished_day(day)
        fixtures = [f for f in payload.get('matches', []) if bt._score_values(f)[0] is not None]
        candidates = []
        mature = 0
        if evaluate:
            for f in fixtures:
                mp = model_probs(state, f)
                if mp:
                    mature += 1
                    candidates.extend(fixture_candidates(state, f))
            ticket = build_ticket(candidates, float(target))
        else:
            ticket = None

        base = {'date': day, 'requested_odds': round(float(target), 2), 'fixtures': len(payload.get('matches', [])), 'analyzed': mature if evaluate else 0, 'truncated': bool(payload.get('truncated')), 'model_candidates': len(candidates)}
        if not evaluate:
            result = None
        elif not ticket:
            result = {**base, 'status': 'NO_TICKET', 'actual_odds': None, 'legs': 0, 'profit': 0.0, 'return_factor': None, 'market_mix': {}, 'diagnostics': {'reason': 'NO_MODEL_EDGE_OR_TARGET_COMBO'}}
        else:
            factor = 1.0; details = []; mix = {}; unresolved = False
            for c in ticket['picks']:
                lf = settle(c['selection'], c['odds'], c['fixture'])
                if lf is None:
                    unresolved = True; break
                factor *= lf
                h, a = bt._score_values(c['fixture'])
                mix[c['kind']] = mix.get(c['kind'], 0) + 1
                details.append({'match': f"{c['home']} - {c['away']}", 'selection': c['selection'], 'odds': round(c['odds'], 2), 'score': f'{h}-{a}', 'kind': c['kind'], 'model_probability': round(c['p'] * 100, 1), 'book_probability': round(c['book_p'] * 100, 1), 'edge_pp': round(c['edge'] * 100, 1), 'return_factor': round(lf, 3)})
            if unresolved:
                result = {**base, 'status': 'UNSETTLED', 'actual_odds': round(ticket['odds'], 2), 'legs': len(ticket['picks']), 'profit': 0.0, 'return_factor': None, 'leg_results': details, 'market_mix': mix}
            else:
                profit = bt.STAKE * (factor - 1.0)
                status = 'WIN' if profit > .005 else 'LOSE' if profit < -.005 else 'PUSH'
                result = {**base, 'status': status, 'actual_odds': round(ticket['odds'], 2), 'legs': len(ticket['picks']), 'estimated_probability': round(ticket['joint_p'] * 100, 1), 'return_factor': round(factor, 4), 'profit': round(profit, 2), 'leg_results': details, 'market_mix': mix, 'average_edge_pp': round(ticket['edge_sum'] / len(ticket['picks']) * 100, 1)}

        # Critical walk-forward rule: update only AFTER today's ticket was generated/settled.
        for f in fixtures:
            update_state(state, f)
        return result

    def summary(days, target, daily):
        out = original_summary(days, target, daily)
        mix = {}; edges = []
        for r in daily:
            for k, n in (r.get('market_mix') or {}).items():
                mix[k] = mix.get(k, 0) + int(n or 0)
            if r.get('average_edge_pp') is not None:
                edges.append(float(r['average_edge_pp']))
        out.update({'market_mix': mix, 'average_model_edge_pp': round(sum(edges) / len(edges), 1) if edges else None, 'warmup_days': WARMUP_DAYS, 'mode': 'WALK-FORWARD ELO + FORM + GOALS', 'note': 'Model independent: Elo + forma + goluri anterioare. Cotele sunt folosite doar pentru pret/comparatie. Fara date viitoare; fiecare zi actualizeaza modelul numai dupa selectie. Max 50 meciuri/zi din bulk; DC poate avea pret estimat din 1X2.'})
        return out

    def run(job_id, days, target):
        try:
            end = date.today() - timedelta(days=1)
            start = end - timedelta(days=days - 1)
            warm = start - timedelta(days=WARMUP_DAYS)
            state = {}
            daily = []

            # Warm-up results are never scored as backtest tickets.
            for i in range(WARMUP_DAYS):
                d = (warm + timedelta(days=i)).isoformat()
                with bt.JOBS_LOCK:
                    bt.JOBS[job_id]['current_day'] = f'warmup {d}'
                analyze_day(state, d, target, evaluate=False)

            for i in range(days):
                d = (start + timedelta(days=i)).isoformat()
                with bt.JOBS_LOCK:
                    job = bt.JOBS[job_id]
                    job['current_day'] = d
                    job['progress'] = i
                r = analyze_day(state, d, target, evaluate=True)
                daily.append(r)
                with bt.JOBS_LOCK:
                    job = bt.JOBS[job_id]
                    job['progress'] = i + 1
                    job['partial'] = summary(days, target, daily)

            result = {'summary': summary(days, target, daily), 'daily': list(reversed(daily))}
            with bt.JOBS_LOCK:
                bt.JOBS[job_id].update({'status': 'done', 'result': result, 'finished_at': time.time(), 'current_day': None})
        except Exception as exc:
            with bt.JOBS_LOCK:
                bt.JOBS[job_id].update({'status': 'error', 'error': f'{type(exc).__name__}: {str(exc)[:400]}', 'finished_at': time.time()})

    bt._summary = summary
    bt._run = run
