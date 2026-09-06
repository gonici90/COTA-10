"""v15 multimarket walk-forward backtest.

Uses the independent State model from backtest_pro_walkforward and the empirical
bucket calibrator from v14, but candidates are no longer restricted to 1X2.
Real Bet365 goal-line prices present in the Pro historical payload are evaluated
alongside 1/X/2. No synthetic bookmaker odds are created.
"""
import time
from collections import defaultdict
from datetime import timedelta

import backtest_engine as bt
import backtest_pro_walkforward as base
import backtest_pro_bucketcal as core


def _cal_key(market):
    if str(market).startswith("Over "):
        return "GOALS_OVER"
    if str(market).startswith("Under "):
        return "GOALS_UNDER"
    return str(market)


def _candidate(market, model_p, odd, book_p, fixture, cal, target):
    q, sample, corr, bucket, source = cal.probability(_cal_key(market), model_p, book_p)
    edge = q - float(book_p)
    ev = q * float(odd) - 1.0
    if edge < 0.0025 or ev < 0.004:
        return None
    if target <= 1.60:
        if not (1.10 <= odd <= 1.75 and q >= .60): return None
    elif target <= 2.30:
        if not (1.10 <= odd <= 2.30 and q >= .49): return None
    else:
        if not (1.10 <= odd <= 2.75 and q >= .45): return None
    return {
        "market": market, "cal_market": _cal_key(market), "prob": q,
        "raw_prob": float(model_p), "odd": float(odd), "bookp": float(book_p),
        "edge": edge, "raw_edge": float(model_p)-float(book_p), "ev": ev,
        "cal_sample": sample, "cal_correction": corr, "cal_bucket": bucket,
        "cal_source": source, "fixture": fixture,
        "fixture_id": fixture.get("id") or fixture.get("fixture_id"),
    }


def _market_rows(f, model):
    """Return (market, model_p, odd, book_p) only for prices really in Pro payload."""
    odds = base._inline_odds(f)
    rows = []
    prices = base._three_prices(odds)
    if prices:
        book = base._devig(prices)
        rows.extend(zip(("1","X","2"), (model["1"],model["X"],model["2"]), prices, book))
    gs = base._goal_stage(odds)
    if gs:
        line, over, under = gs
        book = base._devig((over, under))
        lam = model["lh"] + model["la"]
        rows.append((f"Over {line:g}", base._total_prob(lam,line,True), over, book[0]))
        rows.append((f"Under {line:g}", base._total_prob(lam,line,False), under, book[1]))
    return list(rows)


def _settle(ticket):
    factor=1.0; legs=[]; mix=defaultdict(int)
    for c in ticket["legs"]:
        ret=bt._settle_selection(c["market"],c["odd"],c["fixture"])
        if ret is None: return None,legs,dict(mix)
        factor*=ret
        h,a=base._score(c["fixture"]); home,away=base._teams(c["fixture"])
        kind="GOALS" if c["market"].startswith(("Over ","Under ")) else "1X2"
        mix[kind]+=1
        legs.append({"match":f"{home} - {away}","selection":c["market"],"odds":round(c["odd"],2),"score":f"{h}-{a}","model_probability":round(c["raw_prob"]*100,1),"market_probability":round(c["bookp"]*100,1),"calibrated_probability":round(c["prob"]*100,1),"edge_pp":round(c["edge"]*100,1),"bucket":c["cal_bucket"],"bucket_sample":c["cal_sample"]})
    return factor,legs,dict(mix)


def _summary(days,target,daily,meta,cal):
    s=core._summary(days,target,daily,meta,cal)
    mix=defaultdict(int)
    for row in daily:
        for k,v in (row.get("market_mix") or {}).items(): mix[k]+=int(v or 0)
    s["market_mix"]=dict(mix)
    s["mode"]="Pro independent multimarket v15"
    s["note"]="Walk-forward fara look-ahead. Selectii din 1/X/2 si Goals numai cand exista cote Bet365 reale in istoricul Pro; fara cote sintetice."
    return s


def run_job(job_id,days,target):
    try:
        days=int(days); target=float(target); meta=base._load(job_id,days)
        end_day=meta["end_day"]; start_test=end_day-timedelta(days=days-1)
        byday=defaultdict(list)
        for f in meta["matches"]:
            d=base._day(f)
            if d: byday[d].append(f)
        state=base.State(); cal=core.BucketCalibrator(); daily=[]
        for d in sorted(byday):
            fixtures=byday[d]; in_test=start_test.isoformat()<=d<=end_day.isoformat()
            records=[]; candidates=[]; modeled=0
            for f in fixtures:
                h,a=base._score(f)
                if h is None: continue
                model=state.predict(f)
                if model is None: continue
                modeled+=int(in_test)
                mrows=_market_rows(f,model)
                if not mrows: continue
                records.append((f,mrows))
                if in_test:
                    for market,mp,odd,bp in mrows:
                        c=_candidate(market,mp,odd,bp,f,cal,target)
                        if c: candidates.append(c)
            if in_test:
                ticket=core._ticket(candidates,target)
                br={"date":d,"requested_odds":round(target,2),"fixtures":len(fixtures),"analyzed":modeled,"truncated":False,"deep_requests":0,"deep_cache_hits":0}
                if ticket is None:
                    daily.append({**br,"status":"NO_TICKET","actual_odds":None,"legs":0,"profit":0.0,"market_mix":{}})
                else:
                    factor,legs,mix=_settle(ticket)
                    if factor is None:
                        daily.append({**br,"status":"UNSETTLED","actual_odds":ticket["odds"],"legs":len(ticket["legs"]),"profit":0.0,"leg_results":legs,"market_mix":mix})
                    else:
                        profit=bt.STAKE*(factor-1.0); status="WIN" if profit>.005 else "LOSE" if profit<-.005 else "PUSH"
                        daily.append({**br,"status":status,"actual_odds":ticket["odds"],"legs":len(ticket["legs"]),"estimated_probability":round(ticket["joint"]*100,2),"expected_return":round(ticket["expected_return"],4),"profit":round(profit,2),"return_factor":round(factor,4),"leg_results":legs,"market_mix":mix})
                with bt.JOBS_LOCK:
                    job=bt.JOBS.get(job_id)
                    if job:
                        job["current_day"]=d; job["progress"]=min(days,len(daily)); job["partial"]=_summary(days,target,daily,meta,cal)
            # Train calibration only after the whole day's predictions are frozen.
            for f,mrows in records:
                for market,mp,odd,bp in mrows:
                    ret=bt._settle_selection(market,odd,f)
                    if ret is None or abs(float(ret)-1.0)<1e-9: continue
                    cal.update(_cal_key(market),mp,bp,float(ret)>1.0)
            for f in fixtures: state.update(f)
        present={x["date"] for x in daily}
        for i in range(days):
            d=(start_test+timedelta(days=i)).isoformat()
            if d not in present:
                daily.append({"date":d,"requested_odds":round(target,2),"fixtures":0,"analyzed":0,"status":"NO_TICKET","actual_odds":None,"legs":0,"profit":0.0,"market_mix":{},"truncated":False,"deep_requests":0,"deep_cache_hits":0})
        daily.sort(key=lambda x:x["date"])
        result={"summary":_summary(days,target,daily,meta,cal),"daily":list(reversed(daily))}
        with bt.JOBS_LOCK:
            bt.JOBS[job_id].update({"status":"done","progress":days,"result":result,"partial":result["summary"],"finished_at":time.time(),"current_day":None})
    except Exception as exc:
        with bt.JOBS_LOCK:
            if job_id in bt.JOBS: bt.JOBS[job_id].update({"status":"error","error":f"{type(exc).__name__}: {str(exc)[:500]}","finished_at":time.time()})


def install():
    bt._run=run_job
