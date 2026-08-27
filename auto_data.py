"""5DollarFootballAPI client/cache for COTA-10. Secrets stay in Render env."""
import os, json, time
from pathlib import Path
from datetime import date, timedelta, datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from fastapi import APIRouter, Query, HTTPException

router = APIRouter()
STORE = Path(os.getenv("COTA_CACHE_DIR", "/tmp/cota10-cache")) / "auto-data"
STORE.mkdir(parents=True, exist_ok=True)
BASE = "https://api.5dollarfootballapi.com/v1"

def _key():
    k = os.getenv("FIVEDOLLAR_API_KEY", "").strip()
    if not k:
        raise HTTPException(503, "Lipseste FIVEDOLLAR_API_KEY in Render Environment")
    return k

def _get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
    last = None
    for attempt in range(3):
        req = Request(
            url,
            headers={
                "Authorization": "Bearer " + _key(),
                "Accept": "application/json",
                "User-Agent": "COTA-10/7.0",
            },
        )
        try:
            with urlopen(req, timeout=25) as r:
                data = json.loads(r.read().decode("utf-8"))
            if isinstance(data, dict) and data.get("success") in (0, False):
                raise HTTPException(502, str(data.get("message") or data.get("error") or "API error"))
            return data.get("data", data) if isinstance(data, dict) else data
        except HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:400]
            last = HTTPException(502, f"5DollarFootballAPI HTTP {e.code}: {body}")
            if e.code == 429 or e.code >= 500:
                wait = e.headers.get("Retry-After")
                try:
                    delay = max(1.0, min(8.0, float(wait))) if wait else (1.0 + attempt * 1.5)
                except ValueError:
                    delay = 1.0 + attempt * 1.5
                if attempt < 2:
                    time.sleep(delay)
                    continue
            raise last
        except (URLError, TimeoutError) as e:
            last = HTTPException(502, "5DollarFootballAPI indisponibil: " + str(e)[:180])
            if attempt < 2:
                time.sleep(1.0 + attempt)
                continue
            raise last
        except HTTPException:
            raise
        except Exception as e:
            last = HTTPException(502, "5DollarFootballAPI: " + str(e)[:180])
            if attempt < 2:
                time.sleep(1.0 + attempt)
                continue
            raise last
    raise last or HTTPException(502, "5DollarFootballAPI indisponibil")

def _ts(day, end=False):
    d = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) + (timedelta(days=1) if end else timedelta())
    return int(d.timestamp())

def _fixtures(day, include_odds=False):
    params = {"start_time": _ts(day), "end_time": _ts(day, True), "per_page": 50}
    if include_odds:
        params["include"] = "odds"
    out = []
    for page in range(1, 11):
        params["page"] = page
        raw = _get("/fixtures", params)
        if isinstance(raw, dict):
            rows = raw.get("fixtures") or raw.get("data") or []
            pag = raw.get("pagination") or {}
        else:
            rows, pag = raw or [], {}
        out.extend(x for x in rows if isinstance(x, dict))
        if not rows or (not pag.get("has_more") and len(rows) < 50):
            break
    return out

def sync_day(day, force=False):
    f = STORE / (day + ".json")
    if f.exists() and not force:
        try:
            return json.loads(f.read_text(encoding="utf-8")), True
        except Exception:
            pass
    # Community Access may reject include=odds on fixture lists. The main engine
    # pulls Bet365 odds one fixture at a time, which the plan supports.
    rows = _fixtures(day, False)
    payload = {"source": "5DollarFootballAPI", "date": day, "matches": rows, "count": len(rows)}
    f.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload, False

@router.post("/api/data/sync")
def sync(days: int = Query(1, ge=1, le=14), force: bool = False):
    total = fetched = cached = 0
    errors = []
    for i in range(days):
        d = (date.today() - timedelta(days=i)).isoformat()
        try:
            p, c = sync_day(d, force)
            total += p["count"]
            cached += int(c)
            fetched += int(not c)
        except Exception as e:
            errors.append({"date": d, "error": str(e)[:220]})
    return {
        "ok": not errors,
        "source": "5DollarFootballAPI",
        "days": days,
        "api_days_fetched": fetched,
        "cached_days": cached,
        "matches": total,
        "errors": errors,
    }

@router.get("/api/data/fixture/{fixture_id}/odds")
def fixture_odds(fixture_id: int):
    return {
        "source": "5DollarFootballAPI",
        "fixture_id": fixture_id,
        "data": _get(f"/fixtures/{fixture_id}/odds", {"bookmakers": "bet365"}),
    }

@router.get("/api/data/status")
def status():
    files = sorted(STORE.glob("*.json"))
    return {
        "source": "5DollarFootballAPI",
        "configured": bool(os.getenv("FIVEDOLLAR_API_KEY")),
        "stored_days": len(files),
        "first_day": files[0].stem if files else None,
        "last_day": files[-1].stem if files else None,
    }
