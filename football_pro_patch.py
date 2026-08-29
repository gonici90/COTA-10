"""Fast football scan for 5DollarFootballAPI Pro.

Uses include=odds on the paginated fixture list so one page returns fixtures +
markets. Fixtures loaded this way never trigger one-request-per-match fallback.
"""


def install(engine, fd):
    original_odds_payload = engine._odds_payload

    def _parse_inline(fixture):
        inline = fixture.get("odds") or fixture.get("markets") or fixture.get("bookmakers")
        if inline is None:
            return {}
        candidates = []
        if isinstance(inline, dict):
            candidates.extend((inline, {"odds": inline}, {"markets": inline}))
        elif isinstance(inline, list):
            candidates.append({"bookmakers": inline})
        for payload in candidates:
            try:
                parsed = engine._normalize_odds(payload)
            except Exception:
                parsed = {}
            if engine._has_prices(parsed):
                return parsed
        return {}

    def odds_payload(fixture):
        parsed = _parse_inline(fixture)
        if parsed:
            return parsed
        # Bulk Pro rows have already spent the API request for this fixture page.
        # Do not fall back to one odds request per match: that was the multi-minute bottleneck.
        if fixture.get("_pro_bulk_odds"):
            return {}
        return original_odds_payload(fixture)

    def day_fixtures(day):
        rows = fd._fixtures(day, True)
        out, seen = [], set()
        for fixture in rows if isinstance(rows, list) else []:
            if not isinstance(fixture, dict):
                continue
            fixture = dict(fixture)
            fixture["_pro_bulk_odds"] = True
            fid = fixture.get("id") or fixture.get("fixture_id")
            home, away = engine._teams(fixture)
            key = fid or (home.get("name"), away.get("name"), engine._kickoff_ts(fixture))
            if key in seen:
                continue
            seen.add(key)
            out.append(fixture)
        return out

    engine._odds_payload = odds_payload
    engine._day_fixtures = day_fixtures
