"""Fast history loader for the independent live football model.

Keeps the v16 probability model intact, but avoids the old worst case of up to
9 rate-limited API pages for every new league. Reuses backtest history when
available, persists live history on disk, and normally needs only one API page.
"""
import json
import os
import time
from pathlib import Path

import football_independent_live as base

CACHE = Path(os.getenv("COTA_CACHE_DIR", "/tmp/cota10-cache")) / "live-independent-history"
CACHE.mkdir(parents=True, exist_ok=True)
DISK_TTL = 12 * 3600
MAX_PAGES = 2
MIN_TEAM_MATCHES = 4


def _rows(raw):
    if isinstance(raw, dict):
        rows = raw.get("fixtures") or raw.get("data") or []
        pag = raw.get("pagination") or {}
    else:
        rows, pag = raw or [], {}
    if isinstance(rows, dict):
        rows = rows.get("fixtures") or rows.get("data") or []
    return [x for x in rows if isinstance(x, dict)], pag


def _team_names(engine, fixture):
    h, a = engine._teams(fixture)
    return str(h.get("name") or "").lower(), str(a.get("name") or "").lower()


def _appearances(engine, rows, wanted):
    counts = {n: 0 for n in wanted if n}
    for row in rows:
        h, a = engine._teams(row)
        names = {str(h.get("name") or "").lower(), str(a.get("name") or "").lower()}
        for name in counts:
            if name in names:
                counts[name] += 1
    return counts


def _usable(engine, rows, fixture):
    wanted = _team_names(engine, fixture)
    counts = _appearances(engine, rows, wanted)
    return bool(counts) and all(v >= MIN_TEAM_MATCHES for v in counts.values())


def _read_disk(path):
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(obj.get("saved_at") or 0) <= DISK_TTL:
            rows = obj.get("matches") or []
            if isinstance(rows, list):
                return rows
    except Exception:
        pass
    return None


def _write_disk(path, rows):
    try:
        path.write_text(json.dumps({"saved_at": time.time(), "matches": rows}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _backtest_cache(engine, lid, fixture):
    root = Path(os.getenv("COTA_CACHE_DIR", "/tmp/cota10-cache")) / "backtest-pro-wf-v12" / "ranges"
    try:
        candidates = sorted(root.glob(f"*-{lid}.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return None
    cutoff = engine._kickoff_ts(fixture)
    for path in candidates[:4]:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            rows = [x for x in (obj.get("matches") or []) if isinstance(x, dict) and engine._kickoff_ts(x) < cutoff]
            rows.sort(key=engine._kickoff_ts)
            if _usable(engine, rows, fixture):
                return rows
        except Exception:
            continue
    return None


def fast_history(engine, fd, fixture):
    lid = base._league_id(fixture)
    if not lid:
        return []
    key = str(lid)
    now = time.time()

    cached = base._HISTORY.get(key)
    if cached and now - cached[0] < base.TTL:
        return cached[1]

    disk = CACHE / f"{key}.json"
    rows = _read_disk(disk)
    if rows is not None and _usable(engine, rows, fixture):
        base._HISTORY[key] = (now, rows)
        return rows

    rows = _backtest_cache(engine, lid, fixture)
    if rows:
        base._HISTORY[key] = (now, rows)
        _write_disk(disk, rows)
        return rows

    kickoff = engine._kickoff_ts(fixture)
    end = max(1, kickoff - 1)
    start = end - base.HISTORY_DAYS * 86400
    rows = []
    for page in range(1, MAX_PAGES + 1):
        raw = fd._get(
            f"/leagues/{lid}/fixtures",
            {"start_time": start, "end_time": end, "status": "finished", "per_page": 50, "page": page, "lang": "en"},
        )
        part, pag = _rows(raw)
        rows.extend(part)
        if _usable(engine, rows, fixture) or not pag.get("has_more"):
            break

    rows.sort(key=engine._kickoff_ts)
    base._HISTORY[key] = (now, rows)
    _write_disk(disk, rows)
    return rows


# Patch only data loading. Prediction math, Elo/form/H2H and odds separation stay unchanged.
base._history = fast_history
install = base.install
