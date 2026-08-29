"""Fast quality-first historical backtest: 1X2, double chance, goals; bulk only."""


def install(bt, engine):
    original_summary = bt._summary

    def kind(name):
        n = str(name or '').strip()
        if n in {'1', 'X', '2'}:
            return '1X2'
        if n in {'1X', 'X2', '12'}:
            return 'DOUBLE_CHANCE'
        if n.startswith('Over ') or n.startswith('Under '):
            return 'GOALS'
        return None

    def prob(m):
        try:
            return float(m.get('ticket_probability') or m.get('probability') or 0) / 100.0
        except Exception:
            return 0.0

    def odd(m):
        try:
            return float(m.get('bookmaker_odds') or m.get('odds') or 0)
        except Exception:
            return 0.0

    def edge_pp(m):
        try:
            if m.get('model_edge_pp') is not None:
                return float(m.get('model_edge_pp'))
            return float(m.get('raw_probability') or 0) - float(m.get('book_probability') or 0)
        except Exception:
            return 0.0

    def synth_double_chance(markets):
        by = {str(x.get('market')): x for x in markets if str(x.get('market')) in {'1', 'X', '2'}}
        out = []
        for label, pair in [('1X', ('1', 'X')), ('X2', ('X', '2')), ('12', ('1', '2'))]:
            if not all(x in by for x in pair):
                continue
            try:
                tp = min(.98, sum(float(by[x].get('ticket_probability') or by[x].get('probability') or 0) for x in pair) / 100.0)
                rp = min(.99, sum(float(by[x].get('raw_probability') or by[x].get('probability') or 0) for x in pair) / 100.0)
                bp = min(.99, sum(float(by[x].get('book_probability') or 0) for x in pair) / 100.0)
                if tp <= 0 or bp <= 0:
                    continue
                # Approximate direct double-chance bookmaker price from de-vigged 1X2 consensus,
                # then apply a small bookmaker margin. It remains an estimate, not an exact quote.
                o = max(1.05, round(.96 / bp, 2))
                ev = (tp * o - 1.0) * 100.0
                epp = (rp - bp) * 100.0
            except Exception:
                continue
            out.append({
                'market': label,
                'bookmaker_odds': o,
                'probability': round(tp * 100, 2),
                'ticket_probability': round(tp * 100, 2),
                'raw_probability': round(rp * 100, 2),
                'book_probability': round(bp * 100, 2),
                'model_edge_pp': round(epp, 2),
                'ev': round(ev, 2),
                'recommendation_score': round(tp * 100 + max(-4.0, min(8.0, epp)) * .15, 2),
                'safe': tp >= .60,
                'value': epp >= 2.0,
                'suspicious': False,
                'synthetic_price': True,
            })
        return out

    def qualifies(m):
        k = kind(m.get('market'))
        p = prob(m)
        o = odd(m)
        if not k or m.get('suspicious') or not (1.08 <= o <= 2.40):
            return False
        # Important: ticket_probability is de-vigged/market-calibrated, so comparing it
        # directly with 1/odds and demanding a positive margin incorrectly rejects nearly
        # everything. Use sensible probability floors instead.
        floors = {'1X2': .57, 'DOUBLE_CHANCE': .66, 'GOALS': .57}
        return p >= floors[k]

    def is_anchor(m):
        k = kind(m.get('market'))
        if k in {'1X2', 'DOUBLE_CHANCE'}:
            return edge_pp(m) >= 2.0
        try:
            return k == 'GOALS' and float(m.get('ev') or 0) >= 1.0 and prob(m) >= .58
        except Exception:
            return False

    def settle(sel, o, f):
        n = str(sel or '')
        if n not in {'1X', 'X2', '12'}:
            return bt._settle_selection(n, o, f)
        h, a = bt._score_values(f)
        if h is None:
            return None
        win = (n == '1X' and h >= a) or (n == 'X2' and a >= h) or (n == '12' and h != a)
        return float(o) if win else 0.0

    def analyze_day(day, target):
        payload = bt._fetch_finished_day(day)
        fixtures = payload['matches']
        rows = []
        lookup = {}
        analyzed_count = 0
        anchor_count = 0

        for f in fixtures:
            if bt._score_values(f)[0] is None:
                continue
            ef = dict(f)
            ef['_pro_bulk_odds'] = True
            parsed = bt._prematch_only(bt._extract_inline_odds(f))
            if parsed:
                ef['odds'] = parsed
            try:
                row = engine.analyze_fixture(ef)
            except Exception:
                continue

            base_markets = [
                m for m in (row.get('markets') or [])
                if kind(m.get('market')) in {'1X2', 'GOALS'} and bt._market_data_available(m.get('market'), f)
            ]
            if not base_markets:
                continue

            # Count a fixture as analyzed before applying the quality filter.
            analyzed_count += 1
            all_markets = base_markets + synth_double_chance(base_markets)
            markets = [m for m in all_markets if qualifies(m)]
            if not markets:
                continue

            anchor_count += sum(1 for m in markets if is_anchor(m))
            markets.sort(key=lambda m: (prob(m), edge_pp(m), float(m.get('recommendation_score') or 0)), reverse=True)
            row['markets'] = markets[:6]
            row['best_market'] = row['markets'][0]
            rows.append(row)
            lookup[bt._fixture_key_from_row(row)] = f

        rows.sort(key=lambda r: (prob(r['best_market']), edge_pp(r['best_market'])), reverse=True)
        combo, diag = engine.build_combo(rows, float(target))
        diag = dict(diag or {})
        diag.update({'quality_rows': len(rows), 'quality_anchors': anchor_count})

        base = {
            'date': day,
            'requested_odds': round(float(target), 2),
            'fixtures': len(fixtures),
            'analyzed': analyzed_count,
            'quality_candidates': len(rows),
            'quality_anchors': anchor_count,
            'truncated': bool(payload.get('truncated')),
        }

        # No forced daily ticket: a day must contain at least one genuine model/value signal.
        if anchor_count == 0:
            combo = None
            diag['no_ticket_reason'] = 'NO_QUALITY_ANCHOR'

        if combo:
            actual = float(combo.get('combined_odds') or 0)
            if actual < float(target) * .92 or actual > float(target) * 1.08:
                combo = None
                diag['no_ticket_reason'] = 'TARGET_TOO_FAR'

        if not combo:
            return {**base, 'status': 'NO_TICKET', 'actual_odds': None, 'legs': 0, 'profit': 0.0, 'return_factor': None, 'diagnostics': diag, 'market_mix': {}}

        factor = 1.0
        details = []
        mix = {}
        unresolved = False
        for leg in combo.get('matches') or []:
            f = lookup.get((str(leg.get('home')), str(leg.get('away')), str(leg.get('kickoff'))))
            if f is None:
                unresolved = True
                break
            lf = settle(leg.get('selection'), leg.get('odds'), f)
            if lf is None:
                unresolved = True
                break
            factor *= lf
            h, a = bt._score_values(f)
            k = kind(leg.get('selection')) or 'OTHER'
            mix[k] = mix.get(k, 0) + 1
            details.append({
                'match': f"{leg.get('home')} - {leg.get('away')}",
                'selection': leg.get('selection'),
                'odds': leg.get('odds'),
                'score': f'{h}-{a}',
                'kind': k,
                'return_factor': round(lf, 3),
            })

        if unresolved:
            return {**base, 'status': 'UNSETTLED', 'actual_odds': combo.get('combined_odds'), 'legs': len(combo.get('matches') or []), 'profit': 0.0, 'return_factor': None, 'leg_results': details, 'market_mix': mix}

        profit = bt.STAKE * (factor - 1.0)
        status = 'WIN' if profit > .005 else 'LOSE' if profit < -.005 else 'PUSH'
        return {
            **base,
            'status': status,
            'actual_odds': combo.get('combined_odds'),
            'legs': len(combo.get('matches') or []),
            'estimated_probability': combo.get('estimated_joint_probability'),
            'return_factor': round(factor, 4),
            'profit': round(profit, 2),
            'leg_results': details,
            'market_mix': mix,
        }

    def summary(days, target, daily):
        out = original_summary(days, target, daily)
        mix = {}
        for r in daily:
            for k, n in (r.get('market_mix') or {}).items():
                mix[k] = mix.get(k, 0) + int(n or 0)
        out.update({
            'market_mix': mix,
            'quality_candidate_rows': sum(int(x.get('quality_candidates') or 0) for x in daily),
            'quality_anchors': sum(int(x.get('quality_anchors') or 0) for x in daily),
            'mode': 'QUALITY FAST v2: 1X2 + DOUBLE CHANCE + GOALS',
            'note': 'Nu forteaza bilet zilnic. Pragurile folosesc probabilitati de-vigged corect; ziua trebuie sa aiba cel putin un semnal de calitate. Bulk + cache, fara deep requests. Cotele DC sintetice sunt orientative.',
        })
        return out

    bt._analyze_day = analyze_day
    bt._summary = summary
