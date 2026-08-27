"""Runtime tennis coverage hotfix for The Odds API.

Why this exists:
- tennis events that already started were being discarded, although The Odds API
  returns live events;
- /sports?all=false can omit a tournament around qualifying week;
- tennis h2h coverage is much broader than spreads/totals, so discovery should not
  fail just because secondary markets are sparse.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone


US_OPEN_FALLBACKS = (
    {"key": "tennis_atp_us_open", "group": "Tennis", "title": "ATP US Open", "description": "Men's Singles", "active": True, "has_outrights": False},
    {"key": "tennis_wta_us_open", "group": "Tennis", "title": "WTA US Open", "description": "Women's Singles", "active": True, "has_outrights": False},
)


def install(mod):
    """Patch only tennis discovery/odds behavior; basketball and soccer stay intact."""
    original_choose = mod._choose_leagues
    original_odds = mod._odds_for_league

    def event_rows(sport_key, start, end):
        """Keep both pre-match and in-play tennis events inside the selected day window."""
        try:
            rows = mod._get(
                f"/sports/{sport_key}/events",
                {
                    "dateFormat": "iso",
                    "commenceTimeFrom": start.isoformat().replace("+00:00", "Z"),
                    "commenceTimeTo": end.isoformat().replace("+00:00", "Z"),
                },
                ttl=300,
            )
        except Exception:
            return []

        out = []
        for x in rows if isinstance(rows, list) else []:
            try:
                dt = datetime.fromisoformat(str(x.get("commence_time")).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            # Do NOT reject events whose scheduled start is in the past: they may be
            # live, and the odds endpoint explicitly returns live + upcoming events.
            if start <= dt < end:
                out.append(x)
        return out

    def tennis_catalog():
        try:
            rows = mod._get("/sports/", {"all": "true"}, ttl=1800)
        except Exception:
            rows = []
        sports = [
            x for x in rows
            if isinstance(x, dict)
            and str(x.get("group") or "").lower() == "tennis"
            and not x.get("has_outrights")
            and x.get("key")
        ]
        known = {str(x.get("key")) for x in sports}
        # During Grand Slam qualifying, the tournament can temporarily be absent
        # from the in-season catalogue. Probe the canonical keys anyway; /events is free.
        for item in US_OPEN_FALLBACKS:
            if item["key"] not in known:
                sports.append(dict(item))
        return sports

    def choose_leagues(group, start, end, limit=None):
        if str(group).lower() != "tennis":
            return original_choose(group, start, end, limit)

        sports = tennis_catalog()
        if not sports:
            return []

        # Normal tennis view can scan six active tournaments; MIX can explicitly
        # request fewer to conserve quota.
        max_leagues = 6 if limit is None else max(1, min(int(limit), 8))
        found = []
        with ThreadPoolExecutor(max_workers=min(10, len(sports))) as pool:
            future_map = {pool.submit(event_rows, s["key"], start, end): s for s in sports}
            for fut in as_completed(future_map):
                sport = future_map[fut]
                try:
                    events = fut.result()
                except Exception:
                    events = []
                if events:
                    found.append((sport, len(events)))

        def score(pair):
            sport, count = pair
            title = str(sport.get("title") or "")
            key = str(sport.get("key") or "")
            us_open = 1 if ("us open" in title.lower() or "us_open" in key.lower()) else 0
            # Priority first, then amount of playable matches. This prevents a Grand
            # Slam from being pushed out merely because a smaller event has more rows.
            return (us_open, mod._priority_score("tennis", sport), count)

        found.sort(key=score, reverse=True)
        return [sport for sport, _ in found[:max_leagues]]

    def odds_for_league(sport_key, start, end):
        if not str(sport_key).startswith("tennis_"):
            return original_odds(sport_key, start, end)

        # Match-winner is the market The Odds API says has the broadest tennis
        # coverage. EU+UK gives much better bookmaker coverage while costing fewer
        # credits than the old 3-market EU request.
        return mod._get(
            f"/sports/{sport_key}/odds/",
            {
                "regions": "eu,uk",
                "markets": "h2h",
                "oddsFormat": "decimal",
                "dateFormat": "iso",
                "commenceTimeFrom": start.isoformat().replace("+00:00", "Z"),
                "commenceTimeTo": end.isoformat().replace("+00:00", "Z"),
            },
            ttl=300,
        )

    mod._event_rows = event_rows
    mod._choose_leagues = choose_leagues
    mod._odds_for_league = odds_for_league
    mod.MAX_LEAGUES["tennis"] = 6
