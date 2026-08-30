"""CSV-seeded walk-forward football backtest v10.1.

Prediction is independent of bookmaker prices:
- seed Elo/form/scoring state from local historical league CSV files, using only rows
  strictly before the simulated warm-up period;
- roll recent API results forward chronologically;
- predict from Elo + recent/home-away goals with regression to conservative priors;
- only then compare model probability with bookmaker price.

Markets: 1X2, double chance and goal totals. No future result is used before a pick.
"""
import csv
import math
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

API_WARMUP_DAYS = 10
MIN_HISTORY = 8
MAX_LEGS = 4
MAX_FIXTURES_IN_SEARCH = 30
BEAM = 850
CSV_LOOKBACK_DAYS = 760

# Common football-data.co.uk <-> API naming differences.
ALIASES = {
    'man united': 'manchester united',
    'man utd': 'manchester united',
    'man city': 'manchester city',
    'nottm forest': 'nottingham forest',
    'nottingham forest fc': 'nottingham forest',
    'wolves': 'wolverhampton wanderers',
    'wolverhampton': 'wolverhampton wanderers',
    'newcastle utd': 'newcastle united',
    'west ham utd': 'west ham united',
    'tottenham hotspur': 'tottenham',
    'spurs': 'tottenham',
    'brighton hove albion': 'brighton',
    'brighton and hove albion': 'brighton',
    'bayern munich': 'bayern munchen',
    'bayern munchen': 'bayern munchen',
    'fc bayern munchen': 'bayern munchen',
    'ein frankfurt': 'eintracht frankfurt',
    'eintracht frankfurt': 'eintracht frankfurt',
    'mgladbach': 'borussia monchengladbach',
    'monchengladbach': 'borussia monchengladbach',
    'borussia monchengladbach': 'borussia monchengladbach',
    'leverkusen': 'bayer leverkusen',
    'bayer 04 leverkusen': 'bayer leverkusen',
    'dortmund': 'borussia dortmund',
    'rb leipzig': 'rb leipzig',
    'paris sg': 'paris saint germain',
    'psg': 'paris saint germain',
    'paris saint germain': 'paris saint germain',
    'marseille': 'olympique marseille',
    'lyon': 'olympique lyonnais',
    'inter': 'inter milan',
    'internazionale': 'inter milan',
    'ac milan': 'milan',
    'ath madrid': 'atletico madrid',
    'atletico de madrid': 'atletico madrid',
    'sociedad': 'real sociedad',
    'betis': 'real betis',
    'ath bilbao': 'athletic bilbao',
    'athletic club': 'athletic bilbao',
}


def _norm_name(value):
    s = str(value or '').strip().lower()
    s = ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))
    for ch in ".,'’`-_()/":
        s = s.replace(ch, ' ')
    words = [w for w in s.split() if w not in {'fc', 'afc', 'cf', 'sc'}]
    s = ' '.join(words)
    return ALIASES.get(s, s)


def _parse_csv_date(value):
    s = str(value or '').strip()
    for fmt in ('%d/%m/%Y', '%d/%m/%y', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _historical_csv_paths():
    # Only files with football-data style HomeTeam/AwayTeam/FTHG/FTAG headers are used.
    return sorted(Path('.').glob('*.csv'))


def install(bt, engine):
    original_summary = bt._summary

    def new_stat():
        return {'elo': 1500.0, 'matches': 0, 'recent': [], 'home': [], 'away': []}

    def stat_for(state, key):
        if key not in state:
            state[key] = new_stat()
        return state[key]

    def fixture_names(f):
        h, a = engine._teams(f)
        hn = str(h.get('name') or 'Home')
        an = str(a.get('name') or 'Away')
        return _norm_name(hn), _norm_name(an), hn, an

    def update_raw(state, hk, ak, hg, ag):
        hs, ass = stat_for(state, hk), stat_for(state, ak)
        eh = 1.0 / (1.0 + 10.0 ** ((ass['elo'] - (hs['elo'] + 58.0)) / 400.0))
        actual = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
        margin = abs(hg - ag)
        k = 17.0 * (1.0 + min(3.0, margin) * 0.10)
        delta = k * (actual - eh)
        hs['elo'] += delta
        ass['elo'] -= delta
        hp = 3 if hg > ag else 1 if hg == ag else 0
        ap = 3 if ag > hg else 1 if hg == ag else 0
        hs['recent'].append((hg, ag, hp))
        ass['recent'].append((ag, hg, ap))
        hs['home'].append((hg, ag, hp))
        ass['away'].append((ag, hg, ap))
        hs['recent'] = hs['recent'][-14:]
        ass['recent'] = ass['recent'][-14:]
        hs['home'] = hs['home'][-10:]
        ass['away'] = ass['away'][-10:]
        hs['matches'] += 1
        ass['matches'] += 1

    def update_fixture(state, f):
        hg, ag = bt._score_values(f)
        if hg is None:
            return
        hk, ak, _, _ = fixture_names(f)
        if hk and ak:
            update_raw(state, hk, ak, hg, ag)

    def seed_from_csv(state, cutoff):
        rows = []
        seen = set()
        min_day = cutoff - timedelta(days=CSV_LOOKBACK_DAYS)
        files_used = 0
        for path in _historical_csv_paths():
            try:
                with path.open('r', encoding='utf-8-sig', newline='') as fh:
                    reader = csv.DictReader(fh)
                    fields = set(reader.fieldnames or [])
                    if not {'Date', 'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG'}.issubset(fields):
                        continue
                    used_here = False
                    for r in reader:
                        d = _parse_csv_date(r.get('Date'))
                        if d is None or d >= cutoff or d < min_day:
                            continue
                        try:
                            hg, ag = int(float(r.get('FTHG'))), int(float(r.get('FTAG')))
                        except (TypeError, ValueError):
                            continue
                        hk, ak = _norm_name(r.get('HomeTeam')), _norm_name(r.get('AwayTeam'))
                        if not hk or not ak:
                            continue
                        key = (d.isoformat(), hk, ak, hg, ag)
                        if key in seen:
                            continue
                        seen.add(key)
                        rows.append((d, hk, ak, hg, ag))
                        used_here = True
                    if used_here:
                        files_used += 1
            except Exception:
                continue
        rows.sort(key=lambda x: x[0])
        for _, hk, ak, hg, ag in rows:
            update_raw(state, hk, ak, hg, ag)
        return {'matches': len(rows), 'teams': len(state), 'files': files_used}

    def wavg(items, idx, default, limit=10):
        vals = items[-limit:]
        if not vals:
            return float(default)
        total = den = 0.0
        for age, item in enumerate(reversed(vals)):
            w = 0.86 ** age
            total += float(item[idx]) * w
            den += w
        return total / den if den else float(default)

    def model_probs(state, f):
        hk, ak, _, _ = fixture_names(f)
        hs = state.get(hk)
        ass = state.get(ak)
        if not hs or not ass or min(hs['matches'], ass['matches']) < MIN_HISTORY:
            return None

        # Recent and venue-specific scoring, regressed toward conservative league priors.
        hrgf = wavg(hs['recent'], 0, 1.45, 12)
        hrga = wavg(hs['recent'], 1, 1.20, 12)
        argf = wavg(ass['recent'], 0, 1.20, 12)
        arga = wavg(ass['recent'], 1, 1.45, 12)
        hhgf = wavg(hs['home'], 0, hrgf, 9)
        hhga = wavg(hs['home'], 1, hrga, 9)
        aagf = wavg(ass['away'], 0, argf, 9)
        aaga = wavg(ass['away'], 1, arga, 9)

        h_att = 0.60 * hrgf + 0.40 * hhgf
        h_def = 0.60 * hrga + 0.40 * hhga
        a_att = 0.60 * argf + 0.40 * aagf
        a_def = 0.60 * arga + 0.40 * aaga

        raw_h = 0.52 * h_att + 0.48 * a_def + 0.08
        raw_a = 0.52 * a_att + 0.48 * h_def - 0.03
        sample = min(hs['matches'], ass['matches'])
        conf = max(0.35, min(0.88, (sample - 4) / 24.0))
        lam_h = conf * raw_h + (1.0 - conf) * 1.45
        lam_a = conf * raw_a + (1.0 - conf) * 1.18

        # Elo only tilts the independently estimated scoring rates; it does not set them.
        elo_diff = (hs['elo'] + 58.0) - ass['elo']
        tilt = math.exp(max(-260.0, min(260.0, elo_diff)) / 1500.0)
        lam_h *= tilt
        lam_a /= tilt
        lam_h = max(0.35, min(3.25, lam_h))
        lam_a = max(0.30, min(2.95, lam_a))

        mass = 0.0
        grid = []
        for hg in range(10):
            ph = math.exp(-lam_h) * lam_h ** hg / math.factorial(hg)
            row = []
            for ag in range(10):
                pa = math.exp(-lam_a) * lam_a ** ag / math.factorial(ag)
                v = ph * pa
                row.append(v)
                mass += v
            grid.append(row)
        mass = mass or 1.0
        p1 = sum(grid[h][a] for h in range(10) for a in range(10) if h > a) / mass
        px = sum(grid[i][i] for i in range(10)) / mass
        p2 = max(0.0, 1.0 - p1 - px)
        return {
            '1': p1, 'X': px, '2': p2,
            '1X': p1 + px, 'X2': px + p2, '12': p1 + p2,
            'lambda_total': lam_h + lam_a,
            'samples': sample,
            'elo_home': hs['elo'], 'elo_away': ass['elo'],
        }

    def pois_over(lam, line):
        cutoff = int(math.floor(line))
        under_eq = sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(cutoff + 1))
        return max(0.001, min(0.999, 1.0 - under_eq))

    def devig3(a, x, b):
        try:
            vals = [float(a), float(x), float(b)]
            if any(v <= 1.01 for v in vals):
                return None
            inv = [1 / v for v in vals]
            z = sum(inv)
            return tuple(v / z for v in inv)
        except Exception:
            return None

    def devig2(a, b):
        try:
            a, b = float(a), float(b)
            if a <= 1.01 or b <= 1.01:
                return None
            x, y = 1 / a, 1 / b
            z = x + y
            return x / z, y / z
        except Exception:
            return None

    def comp_name(f):
        c = f.get('competition') or f.get('league') or {}
        if isinstance(c, dict):
            c = c.get('name') or c.get('title') or ''
        return str(c or '').lower()

    def allowed_fixture(f):
        c = comp_name(f)
        banned = ('friendly', 'u21', 'u23', 'reserve', 'women', 'youth')
        return not any(x in c for x in banned)

    def score_candidate(c):
        # Prefer robust probability, then independent model edge and sample depth.
        return c['p'] + min(0.12, max(0.0, c['edge'])) * 0.48 + min(20, c['samples']) * 0.0015

    def fixture_candidates(state, f):
        if not allowed_fixture(f):
            return []
        mp = model_probs(state, f)
        if not mp:
            return []
        parsed = bt._prematch_only(bt._extract_inline_odds(f))
        if not parsed:
            return []
        _, _, hn, an = fixture_names(f)
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
                    p, o = mp[sel], prices[sel]
                    edge = p - bp[sel]
                    floor = 0.38 if sel == 'X' else 0.45
                    edge_floor = 0.075 if sel == 'X' else 0.060
                    if 1.35 <= o <= 3.10 and p >= floor and edge >= edge_floor and p * o >= 1.035:
                        out.append({'fixture_key': fk, 'fixture': f, 'home': hn, 'away': an, 'selection': sel, 'kind': '1X2', 'odds': o, 'p': p, 'book_p': bp[sel], 'edge': edge, 'samples': mp['samples']})

                for sel, parts in (('1X', ('1', 'X')), ('X2', ('X', '2')), ('12', ('1', '2'))):
                    book_p = bp[parts[0]] + bp[parts[1]]
                    p = mp[sel]
                    o = max(1.05, 0.96 / book_p)
                    edge = p - book_p
                    if 1.14 <= o <= 1.95 and p >= 0.68 and edge >= 0.050 and p * o >= 1.025:
                        out.append({'fixture_key': fk, 'fixture': f, 'home': hn, 'away': an, 'selection': sel, 'kind': 'DOUBLE_CHANCE', 'odds': round(o, 2), 'p': p, 'book_p': book_p, 'edge': edge, 'samples': mp['samples'], 'synthetic_price': True})

        gm = engine._stage(parsed.get('goal_line') or parsed.get('goalline') or parsed.get('goals') or parsed.get('total_goals'))
        if isinstance(gm, dict):
            try:
                line = float(gm.get('line')); oo = float(gm.get('over')); ou = float(gm.get('under'))
            except Exception:
                line = oo = ou = None
            # Only half-goal totals: no push/quarter-line ambiguity.
            if line is not None and abs(line * 2 - round(line * 2)) < 1e-8 and int(round(line * 2)) % 2 == 1:
                pair = devig2(oo, ou)
                if pair:
                    po = pois_over(mp['lambda_total'], line)
                    pu = 1.0 - po
                    for sel, p, o, bp in ((f'Over {line:g}', po, oo, pair[0]), (f'Under {line:g}', pu, ou, pair[1])):
                        edge = p - bp
                        if 1.30 <= o <= 2.20 and p >= 0.57 and edge >= 0.055 and p * o >= 1.035:
                            out.append({'fixture_key': fk, 'fixture': f, 'home': hn, 'away': an, 'selection': sel, 'kind': 'GOALS', 'odds': o, 'p': p, 'book_p': bp, 'edge': edge, 'samples': mp['samples']})

        out.sort(key=score_candidate, reverse=True)
        return out[:3]

    def build_ticket(candidates, target):
        by_fixture = {}
        for c in candidates:
            by_fixture.setdefault(c['fixture_key'], []).append(c)
        groups = list(by_fixture.values())
        groups.sort(key=lambda opts: score_candidate(opts[0]), reverse=True)
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
                avg_edge = es / len(picks) if picks else 0.0
                return (1 if in_band else 0, jp + min(0.12, avg_edge) * 0.10, -closeness, -len(picks))
            expanded.sort(key=rank, reverse=True)
            states = expanded[:BEAM]
        valid = [s for s in states if s[3] and low <= s[0] <= high]
        if not valid:
            return None
        valid.sort(key=lambda s: (s[1], s[2] / len(s[3]), -len(s[3])), reverse=True)
        prod, jp, es, picks = valid[0]
        if es / len(picks) < 0.052:
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

    def analyze_day(state, day, target, evaluate=True):
        payload = bt._fetch_finished_day(day)
        fixtures = [f for f in payload.get('matches', []) if bt._score_values(f)[0] is not None]
        candidates = []
        mature = 0
        if evaluate:
            for f in fixtures:
                if model_probs(state, f):
                    mature += 1
                    candidates.extend(fixture_candidates(state, f))
            ticket = build_ticket(candidates, float(target))
        else:
            ticket = None
        base = {'date': day, 'requested_odds': round(float(target), 2), 'fixtures': len(payload.get('matches', [])), 'analyzed': mature if evaluate else 0, 'truncated': bool(payload.get('truncated')), 'model_candidates': len(candidates)}
        if not evaluate:
            result = None
        elif not ticket:
            result = {**base, 'status': 'NO_TICKET', 'actual_odds': None, 'legs': 0, 'profit': 0.0, 'return_factor': None, 'market_mix': {}, 'diagnostics': {'reason': 'NO_INDEPENDENT_EDGE_OR_TARGET_COMBO'}}
        else:
            factor = 1.0; details = []; mix = {}; unresolved = False
            for c in ticket['picks']:
                lf = settle(c['selection'], c['odds'], c['fixture'])
                if lf is None:
                    unresolved = True; break
                factor *= lf
                h, a = bt._score_values(c['fixture'])
                mix[c['kind']] = mix.get(c['kind'], 0) + 1
                details.append({'match': f"{c['home']} - {c['away']}", 'selection': c['selection'], 'odds': round(c['odds'], 2), 'score': f'{h}-{a}', 'kind': c['kind'], 'model_probability': round(c['p'] * 100, 1), 'book_probability': round(c['book_p'] * 100, 1), 'edge_pp': round(c['edge'] * 100, 1), 'history_matches': c['samples'], 'return_factor': round(lf, 3)})
            if unresolved:
                result = {**base, 'status': 'UNSETTLED', 'actual_odds': round(ticket['odds'], 2), 'legs': len(ticket['picks']), 'profit': 0.0, 'return_factor': None, 'leg_results': details, 'market_mix': mix}
            else:
                profit = bt.STAKE * (factor - 1.0)
                status = 'WIN' if profit > .005 else 'LOSE' if profit < -.005 else 'PUSH'
                result = {**base, 'status': status, 'actual_odds': round(ticket['odds'], 2), 'legs': len(ticket['picks']), 'estimated_probability': round(ticket['joint_p'] * 100, 1), 'return_factor': round(factor, 4), 'profit': round(profit, 2), 'leg_results': details, 'market_mix': mix, 'average_edge_pp': round(ticket['edge_sum'] / len(ticket['picks']) * 100, 1)}
        # Walk-forward: today's result enters state only after today's prediction/settlement.
        for f in fixtures:
            update_fixture(state, f)
        return result

    seed_meta = {'matches': 0, 'teams': 0, 'files': 0}

    def summary(days, target, daily):
        out = original_summary(days, target, daily)
        mix = {}; edges = []
        for r in daily:
            for k, n in (r.get('market_mix') or {}).items():
                mix[k] = mix.get(k, 0) + int(n or 0)
            if r.get('average_edge_pp') is not None:
                edges.append(float(r['average_edge_pp']))
        out.update({
            'market_mix': mix,
            'average_model_edge_pp': round(sum(edges) / len(edges), 1) if edges else None,
            'seed_matches': seed_meta.get('matches', 0),
            'seed_teams': seed_meta.get('teams', 0),
            'seed_files': seed_meta.get('files', 0),
            'api_warmup_days': API_WARMUP_DAYS,
            'min_team_history': MIN_HISTORY,
            'mode': 'CSV-SEEDED WALK-FORWARD v10.1',
            'note': 'Elo/forma/golurile sunt initializate din CSV-uri istorice anterioare perioadei testate, apoi actualizate walk-forward. Bookmakerul intra doar dupa predictie pentru pret/comparatie. Fara viitor. 1X2/DC/goluri; DC poate avea pret estimat.'
        })
        return out

    def run(job_id, days, target):
        nonlocal seed_meta
        try:
            end = date.today() - timedelta(days=1)
            start = end - timedelta(days=days - 1)
            warm_start = start - timedelta(days=API_WARMUP_DAYS)
            state = {}
            with bt.JOBS_LOCK:
                bt.JOBS[job_id]['current_day'] = 'seed CSV istoric'
            seed_meta = seed_from_csv(state, warm_start)
            daily = []

            for i in range(API_WARMUP_DAYS):
                d = (warm_start + timedelta(days=i)).isoformat()
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
                bt.JOBS[job_id].update({'status': 'error', 'error': f'{type(exc).__name__}: {str(exc)[:500]}', 'finished_at': time.time()})

    bt._summary = summary
    bt._run = run
