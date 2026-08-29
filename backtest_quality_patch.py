"""Quality-first historical backtest patch for Analiza Cota AI.

Goals:
- avoid one single ~2.00 straight 1X2 bet being treated as a "safe ticket";
- use at least two lower-priced legs for normal backtest targets;
- for low total odds, test genuinely non-1X2 tickets instead of a forced 1X2+other mix;
- deep-fetch full Bet365 markets for a wider historical shortlist;
- recover a second page of historical fixtures when a day has >50 matches.

The deep requests are cached. First 60-day runs can therefore take longer, but later
runs reuse the historical full-odds cache.
"""
import json
import math
from datetime import datetime, timedelta, timezone


# Four deep fixtures/day was too restrictive: with two-leg tickets it mechanically
# produced many 1X2 + non-1X2 pairs and too many NO_TICKET days. Eight keeps the
# Pro cost bounded while giving the optimiser a real multi-market pool.
DETAILS_PER_DAY = 8


def install(bt, engine, fd):
    full_cache = bt.CACHE / "full-odds-v2"
    page2_cache = bt.CACHE / "page2-v2"
    full_cache.mkdir(parents=True, exist_ok=True)
    page2_cache.mkdir(parents=True, exist_ok=True)

    original_summary = bt._summary

    def _day_ts(day):
        start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())

    def _fetch_day_wider(day):
        base = bt._fetch_finished_day(day)
        matches = list(base.get("matches") or [])
        still_truncated = bool(base.get("truncated"))
        page2_used = False
        if still_truncated:
            cache_file = page2_cache / f"{day}.json"
            payload = None
            if cache_file.exists():
                try:
                    payload = json.loads(cache_file.read_text(encoding="utf-8"))
                except Exception:
                    payload = None
            if not isinstance(payload, dict):
                start, end = _day_ts(day)
                raw = fd._get(
                    "/fixtures",
                    {
                        "start_time": start,
                        "end_time": end,
                        "status": "finished",
                        "include": "odds",
                        "per_page": 50,
                        "page": 2,
                        "lang": "ro",
                    },
                )
                if isinstance(raw, dict):
                    rows = raw.get("fixtures") or raw.get("data") or []
                    pag = raw.get("pagination") or {}
                else:
                    rows, pag = raw or [], {}
                payload = {
                    "matches": [x for x in rows if isinstance(x, dict)],
                    "truncated": bool(pag.get("has_more")),
                }
                try:
                    cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
            matches.extend(x for x in (payload.get("matches") or []) if isinstance(x, dict))
            still_truncated = bool(payload.get("truncated"))
            page2_used = True

        seen = set()
        deduped = []
        for f in matches:
            fid = f.get("id") or f.get("fixture_id")
            key = fid or (
                str((f.get("home_team") or f.get("home") or {}).get("name") if isinstance(f.get("home_team") or f.get("home"), dict) else f.get("home")),
                str((f.get("away_team") or f.get("away") or {}).get("name") if isinstance(f.get("away_team") or f.get("away"), dict) else f.get("away")),
                str(f.get("start_time") or f.get("kickoff") or f.get("date")),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(f)
        return {"matches": deduped, "truncated": still_truncated, "page2_used": page2_used}

    def _full_odds(fixture):
        fid = fixture.get("id") or fixture.get("fixture_id")
        if not fid:
            return None, False
        cache_file = full_cache / f"{fid}.json"
        if cache_file.exists():
            try:
                odds = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(odds, dict) and odds:
                    return odds, False
            except Exception:
                pass
        try:
            raw = fd._get(f"/fixtures/{fid}/odds", {"bookmakers": "bet365"})
            odds = engine._normalize_odds(raw)
            odds = bt._prematch_only(odds)
        except Exception:
            return None, True
        if not isinstance(odds, dict) or not odds:
            return None, True
        try:
            cache_file.write_text(json.dumps(odds, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return odds, True

    def _market_kind(name):
        name = str(name or "")
        if name in {"1", "X", "2"}:
            return "1X2"
        if name.startswith("Over ") or name.startswith("Under "):
            return "GOALS"
        if name.startswith("AH "):
            return "AH"
        if name in {"GG", "NG"}:
            return "BTTS"
        if name.startswith("Corners "):
            return "CORNERS"
        if name.startswith("Cards "):
            return "CARDS"
        return "OTHER"

    def _analyze_fixture(fixture, forced_odds=None):
        if bt._score_values(fixture)[0] is None:
            return None
        ef = dict(fixture)
        ef["_pro_bulk_odds"] = True
        parsed = forced_odds
        if parsed is None:
            parsed = bt._prematch_only(bt._extract_inline_odds(fixture))
        if parsed:
            ef["odds"] = parsed
        try:
            row = engine.analyze_fixture(ef)
        except Exception:
            return None
        markets = [
            m for m in (row.get("markets") or [])
            if bt._market_data_available(m.get("market"), fixture)
        ]
        if not markets:
            return None
        row["markets"] = markets
        row["best_market"] = max(
            markets,
            key=lambda x: (
                not bool(x.get("suspicious")),
                float(x.get("ticket_probability") or x.get("probability") or 0),
                float(x.get("recommendation_score") or 0),
            ),
        )
        return row

    def _quality_combo(rows, target):
        target = max(1.05, float(target))
        min_legs = 2 if target >= 1.45 else 1
        max_legs = 5 if target <= 5 else 7

        # At target 2.00 the old 60% / 1.60 gate plus max-one-1X2 rule left only
        # 16 tickets in 60 days. Widen the non-solo pool without allowing wild legs.
        if target <= 2.5:
            max_leg_odd, min_prob = 1.70, 0.58
        elif target <= 5:
            max_leg_odd, min_prob = 1.90, 0.56
        elif target <= 10:
            max_leg_odd, min_prob = 2.10, 0.54
        else:
            max_leg_odd, min_prob = 2.30, 0.52

        # For low requested total odds we explicitly test non-solo construction.
        # Above 3.00, at most one straight result may be used if it is genuinely best.
        max_solists = 0 if target <= 3.0 else 1

        min_leg_odd = 1.08
        preferred_low, preferred_high = target * 0.97, target * 1.05
        fallback_low, fallback_high = target * 0.90, target * 1.10

        fixtures = []
        for row in rows:
            opts = []
            for pick in row.get("markets") or []:
                try:
                    odd = float(pick.get("bookmaker_odds") or 0)
                    prob = float(pick.get("ticket_probability") or pick.get("probability") or 0) / 100.0
                except (TypeError, ValueError):
                    continue
                if pick.get("suspicious") or not (min_leg_odd <= odd <= max_leg_odd) or prob < min_prob:
                    continue
                kind = _market_kind(pick.get("market"))
                if kind == "1X2" and max_solists == 0:
                    continue
                # Non-1X2 is the point of the low-odds quality test. Corners/cards get
                # a tiny uncertainty haircut; goals/AH/BTTS stay neutral.
                reliability = 0.985 if kind == "1X2" else (0.995 if kind in {"CORNERS", "CARDS"} else 1.0)
                opts.append({
                    **pick,
                    "fixture_id": row.get("fixture_id"),
                    "home": row.get("home"),
                    "away": row.get("away"),
                    "kickoff": row.get("kickoff"),
                    "kind": kind,
                    "actual_prob": prob,
                    "quality_prob": prob * reliability,
                    "odd": odd,
                })
            if opts:
                opts.sort(key=lambda x: (x["quality_prob"], x.get("recommendation_score", 0), -x["odd"]), reverse=True)
                fixtures.append(opts[:10])

        diag = {
            "candidate_matches": len(fixtures),
            "min_legs": min_legs,
            "max_legs": max_legs,
            "max_leg_odds": max_leg_odd,
            "min_probability": round(min_prob * 100, 1),
            "max_1x2_legs": max_solists,
            "deep_fixtures_per_day": DETAILS_PER_DAY,
        }
        if not fixtures:
            return None, diag

        scale = 150
        states = {(0, 0, 0): (1.0, 1.0, 1.0, [], 1.0)}
        for opts in fixtures:
            nxt = dict(states)
            for cur_odd, quality_joint, actual_joint, path, max_leg in list(states.values()):
                if len(path) >= max_legs:
                    continue
                for x in opts:
                    solists = sum(1 for p in path if p["kind"] == "1X2") + (1 if x["kind"] == "1X2" else 0)
                    if solists > max_solists:
                        continue
                    no = cur_odd * x["odd"]
                    if no > fallback_high:
                        continue
                    nq = quality_joint * x["quality_prob"]
                    na = actual_joint * x["actual_prob"]
                    nl = len(path) + 1
                    bucket = round(math.log(max(no, 1.0)) * scale)
                    key = (bucket, nl, solists)
                    nm = max(max_leg, x["odd"])
                    old = nxt.get(key)
                    if old is None or nq > old[1] + 1e-12 or (abs(nq - old[1]) <= 1e-12 and na > old[2]):
                        nxt[key] = (no, nq, na, path + [x], nm)
            states = nxt

        paths = [v for v in states.values() if min_legs <= len(v[3]) <= max_legs]
        if not paths:
            return None, diag

        def choose(lo, hi):
            valid = [v for v in paths if lo <= v[0] <= hi]
            if not valid:
                return None
            return max(
                valid,
                key=lambda v: (
                    v[1],
                    v[2],
                    -abs(math.log(v[0] / target)),
                    -v[4],
                    sum(1 for p in v[3] if p["kind"] != "1X2"),
                ),
            )

        best = choose(preferred_low, preferred_high) or choose(fallback_low, fallback_high)
        if best is None:
            return None, diag
        odd, _, actual_joint, path, used_max = best
        mix = {}
        for p in path:
            mix[p["kind"]] = mix.get(p["kind"], 0) + 1
        return {
            "combined_odds": round(odd, 2),
            "estimated_joint_probability": round(actual_joint * 100, 2),
            "matches": [
                {
                    "fixture_id": p.get("fixture_id"),
                    "home": p.get("home"),
                    "away": p.get("away"),
                    "kickoff": p.get("kickoff"),
                    "selection": p.get("market"),
                    "odds": p.get("odd"),
                    "probability": p.get("probability"),
                    "ticket_probability": p.get("ticket_probability"),
                    "kind": p.get("kind"),
                }
                for p in path
            ],
            "market_mix": mix,
            "max_leg_odds": round(used_max, 2),
        }, diag

    def _analyze_day(day, target):
        payload = _fetch_day_wider(day)
        fixtures = payload["matches"]
        rows = []
        fixture_by_id = {}
        fixture_by_key = {}
        bulk_row_by_id = {}

        for fixture in fixtures:
            fid = fixture.get("id") or fixture.get("fixture_id")
            if fid:
                fixture_by_id[fid] = fixture
            row = _analyze_fixture(fixture)
            if not row:
                continue
            rows.append(row)
            if row.get("fixture_id"):
                bulk_row_by_id[row["fixture_id"]] = row
            fixture_by_key[bt._fixture_key_from_row(row)] = fixture

        def deep_score(fixture):
            parsed = bt._extract_inline_odds(fixture)
            names = " ".join(str(k).lower() for k in (parsed.keys() if isinstance(parsed, dict) else []))
            hints = sum(token in names for token in ("goal", "asian", "btts", "corner", "card"))
            fid = fixture.get("id") or fixture.get("fixture_id")
            row = bulk_row_by_id.get(fid) or {}
            best = row.get("best_market") or {}
            prob = float(best.get("ticket_probability") or best.get("probability") or 0)
            return (hints, prob, float(best.get("recommendation_score") or 0))

        shortlist = sorted(fixtures, key=deep_score, reverse=True)[:DETAILS_PER_DAY]
        replacements = {}
        deep_network_calls = 0
        deep_success = 0
        for fixture in shortlist:
            odds, network_call = _full_odds(fixture)
            deep_network_calls += int(network_call)
            if not odds:
                continue
            row = _analyze_fixture(fixture, odds)
            if not row:
                continue
            fid = row.get("fixture_id") or fixture.get("id") or fixture.get("fixture_id")
            if fid:
                replacements[fid] = row
                deep_success += 1

        final_rows = []
        used = set()
        for row in rows:
            fid = row.get("fixture_id")
            if fid in replacements:
                final_rows.append(replacements[fid])
                used.add(fid)
            else:
                final_rows.append(row)
        for fid, row in replacements.items():
            if fid not in used and all(r.get("fixture_id") != fid for r in final_rows):
                final_rows.append(row)

        final_rows.sort(key=lambda x: float((x.get("best_market") or {}).get("recommendation_score") or 0), reverse=True)
        combo, diag = _quality_combo(final_rows, target)
        base = {
            "date": day,
            "requested_odds": round(float(target), 2),
            "fixtures": len(fixtures),
            "analyzed": len(final_rows),
            "truncated": bool(payload.get("truncated")),
            "page2_used": bool(payload.get("page2_used")),
            "deep_network_calls": deep_network_calls,
            "deep_success": deep_success,
        }
        if not combo:
            return {**base, "status": "NO_TICKET", "actual_odds": None, "legs": 0, "profit": 0.0, "return_factor": None, "diagnostics": diag, "market_mix": {}}

        factor = 1.0
        leg_results = []
        unresolved = False
        for leg in combo.get("matches") or []:
            fixture = fixture_by_id.get(leg.get("fixture_id"))
            if fixture is None:
                key = (str(leg.get("home")), str(leg.get("away")), str(leg.get("kickoff")))
                fixture = fixture_by_key.get(key)
            if fixture is None:
                unresolved = True
                break
            leg_factor = bt._settle_selection(leg.get("selection"), leg.get("odds"), fixture)
            if leg_factor is None:
                unresolved = True
                break
            factor *= leg_factor
            h, a = bt._score_values(fixture)
            leg_results.append({
                "match": f"{leg.get('home')} - {leg.get('away')}",
                "selection": leg.get("selection"),
                "odds": leg.get("odds"),
                "score": f"{h}-{a}",
                "kind": leg.get("kind"),
                "return_factor": round(leg_factor, 3),
            })

        if unresolved:
            return {**base, "status": "UNSETTLED", "actual_odds": combo.get("combined_odds"), "legs": len(combo.get("matches") or []), "profit": 0.0, "return_factor": None, "leg_results": leg_results, "market_mix": combo.get("market_mix") or {}}

        profit = bt.STAKE * (factor - 1.0)
        status = "WIN" if profit > 0.005 else "LOSE" if profit < -0.005 else "PUSH"
        return {
            **base,
            "status": status,
            "actual_odds": combo.get("combined_odds"),
            "legs": len(combo.get("matches") or []),
            "estimated_probability": combo.get("estimated_joint_probability"),
            "return_factor": round(factor, 4),
            "profit": round(profit, 2),
            "leg_results": leg_results,
            "market_mix": combo.get("market_mix") or {},
        }

    def _summary(days, target, daily):
        out = original_summary(days, target, daily)
        settled = [x for x in daily if x.get("status") in {"WIN", "LOSE", "PUSH"}]
        legs = sum(int(x.get("legs") or 0) for x in settled)
        mix = {}
        for row in settled:
            for kind, n in (row.get("market_mix") or {}).items():
                mix[kind] = mix.get(kind, 0) + int(n or 0)
        solists = mix.get("1X2", 0)
        non_solists = max(0, legs - solists)
        strict_no_1x2 = float(target) <= 3.0
        out.update({
            "average_legs": round(legs / len(settled), 2) if settled else 0.0,
            "market_mix": mix,
            "solist_legs": solists,
            "non_solist_legs": non_solists,
            "non_solist_share": round(non_solists / legs * 100.0, 1) if legs else 0.0,
            "page2_days": sum(bool(x.get("page2_used")) for x in daily),
            "deep_network_calls": sum(int(x.get("deep_network_calls") or 0) for x in daily),
            "deep_success": sum(int(x.get("deep_success") or 0) for x in daily),
            "quality_mode": "FARA_1X2" if strict_no_1x2 else "MAX_1X2",
            "note": (
                "Quality v2: la cote cerute <=3.00 nu foloseste deloc 1/X/2; cauta Goals/AH/BTTS/Corners/Cards, "
                "minimum 2 selectii si 8 meciuri/zi cu cote Bet365 complete. Pentru cote mai mari permite maximum un 1X2. "
                "Prima rulare poate dura mai mult; deep odds se cache-uiesc."
            ),
        })
        return out

    bt._analyze_day = _analyze_day
    bt._summary = _summary
