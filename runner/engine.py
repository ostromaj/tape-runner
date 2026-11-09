"""Two pretend portfolios ("raw" = spread only, "real" = spread + real fees) that take the
same order-book trades. Everything here is pure logic with an explicit clock, so it can be
tested with simulated data."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import config as C

NY = ZoneInfo("America/New_York")
LEDGERS = ("raw", "real")


def nice(ts):
    return datetime.fromtimestamp(ts, NY).strftime("%a %b %-d, %-I:%M:%S %p ET")


def new_state(now, run_number=1, past_runs=None):
    return {
        "version": 1, "run_number": run_number, "run_started": now,
        "starting_cash": C.STARTING_CASH,
        "ledgers": {k: {"equity": C.STARTING_CASH} for k in LEDGERS},
        "positions": {},            # product -> position
        "trades": [], "next_id": 1,
        "totals": {"trades": 0, "wins": 0, "move_bps_sum": 0.0, "hold_sum": 0.0,
                   "fees_real": 0.0, "longs": 0, "shorts": 0,
                   "by_reason": {}, "by_product": {}},
        "equity_curve": [[int(now), C.STARTING_CASH, C.STARTING_CASH]],
        "cooldown": {}, "live": {}, "past_runs": past_runs or [],
        "feed": {"connected": False, "reconnects": 0, "since": None, "msgs": 0},
        "runner": {}, "events": [],
    }


def log_event(state, now, text):
    state["events"].insert(0, {"t": now, "text": text})
    del state["events"][40:]


def _px_exit(pos, f):
    """Price you'd get closing right now: sell longs at the bid, buy back shorts at the ask."""
    return f["bid"] if pos["dir"] > 0 else f["ask"]


def move_bps(pos, px):
    return pos["dir"] * (px / pos["entry_px"] - 1) * 1e4


def ledger_value(state, name, live):
    eq = state["ledgers"][name]["equity"]
    for p in state["positions"].values():
        f = live.get(p["product"])
        if f and f.get("bid"):
            eq += p["notional"][name] * move_bps(p, _px_exit(p, f)) / 1e4
    return eq


def open_position(state, product, f, direction, now):
    entry = f["ask"] if direction > 0 else f["bid"]      # cross the spread like a market order
    pos = {"id": state["next_id"], "product": product, "dir": direction, "entry_px": entry,
           "entry_t": now, "score": round(f["score"], 3),
           "signal": {k: round(f[k], 3) for k in ("depth", "ofi", "flow", "spread_bps")},
           "notional": {}, "fee_open": {}, "best_bps": 0.0}
    state["next_id"] += 1
    for name in LEDGERS:
        led = state["ledgers"][name]
        n = max(led["equity"], 0) * C.ALLOC_PER_POSITION
        fee = n * C.REAL_TAKER_FEE if name == "real" else 0.0
        led["equity"] -= fee
        pos["notional"][name] = n
        pos["fee_open"][name] = fee
    state["positions"][product] = pos
    return pos


def close_position(state, product, f, now, reason):
    pos = state["positions"].pop(product)
    px = _px_exit(pos, f)
    mv = move_bps(pos, px)
    rec = {"id": pos["id"], "product": product, "side": "LONG" if pos["dir"] > 0 else "SHORT",
           "entry_t": pos["entry_t"], "exit_t": now, "entry_px": pos["entry_px"], "exit_px": px,
           "hold_s": round(now - pos["entry_t"], 1), "move_bps": round(mv, 2), "reason": reason,
           "score": pos["score"], "signal": pos["signal"], "pnl": {}, "fees": {}}
    for name in LEDGERS:
        led = state["ledgers"][name]
        n = pos["notional"][name]
        gross = n * mv / 1e4
        fee_close = (n + gross) * C.REAL_TAKER_FEE if name == "real" else 0.0
        led["equity"] += gross - fee_close
        rec["pnl"][name] = round(gross - fee_close - pos["fee_open"][name], 5)
        rec["fees"][name] = round(fee_close + pos["fee_open"][name], 5)
    state["trades"].append(rec)
    del state["trades"][:-C.MAX_TRADES_KEPT]
    tot = state["totals"]
    tot["trades"] += 1
    tot["wins"] += 1 if mv > 0 else 0
    tot["move_bps_sum"] += mv
    tot["hold_sum"] += now - pos["entry_t"]
    tot["fees_real"] += rec["fees"]["real"]
    tot["longs" if pos["dir"] > 0 else "shorts"] += 1
    tot["by_reason"][reason] = tot["by_reason"].get(reason, 0) + 1
    bp = tot["by_product"].setdefault(product, {"trades": 0, "move_bps_sum": 0.0})
    bp["trades"] += 1
    bp["move_bps_sum"] += mv
    state["cooldown"][product] = now + C.COOLDOWN_SEC
    return rec


def step(state, product, f, now):
    """One decision for one product, given fresh features `f`. Returns 'open'/'close'/None."""
    if not f:
        return None
    pos = state["positions"].get(product)
    if pos:
        mv = move_bps(pos, _px_exit(pos, f))
        pos["best_bps"] = max(pos["best_bps"], mv)
        held = now - pos["entry_t"]
        reason = None
        if mv >= C.TAKE_PROFIT_BPS:
            reason = "Profit target"
        elif mv <= -C.STOP_LOSS_BPS:
            reason = "Stop-loss"
        elif held >= C.MAX_HOLD_SEC:
            reason = "Time limit"
        elif held >= C.MIN_HOLD_SEC and pos["dir"] * f["score"] <= -C.FLIP_EXIT_SCORE:
            reason = "Order book flipped"
        if reason:
            close_position(state, product, f, now, reason)
            return "close"
        return None
    if state["cooldown"].get(product, 0) > now or len(state["positions"]) >= C.MAX_POSITIONS:
        return None
    if f["spread_bps"] > C.MAX_SPREAD_BPS[product] or f["trades_in_window"] < C.MIN_TRADES_IN_WINDOW:
        return None
    if f["streak"] >= C.CONFIRM_EVALS:
        open_position(state, product, f, +1, now)
        return "open"
    if C.ALLOW_SHORTS and f["streak"] <= -C.CONFIRM_EVALS:
        open_position(state, product, f, -1, now)
        return "open"
    return None


def stats(state):
    tot = state["totals"]
    n = tot["trades"]
    avg_move = tot["move_bps_sum"] / n if n else None
    raw = ledger_value(state, "raw", state["live"])
    real = ledger_value(state, "real", state["live"])
    return {
        "raw": round(raw, 4), "real": round(real, 4),
        "raw_pct": round((raw / state["starting_cash"] - 1) * 100, 3),
        "real_pct": round((real / state["starting_cash"] - 1) * 100, 3),
        "trades": n, "win_rate": round(tot["wins"] / n * 100, 1) if n else None,
        "avg_move_bps": round(avg_move, 2) if avg_move is not None else None,
        "avg_hold_s": round(tot["hold_sum"] / n, 1) if n else None,
        "fees_real": round(tot["fees_real"], 4),
        "round_trip_fee_bps": round(2 * C.REAL_TAKER_FEE * 1e4, 1),
        # fee per side at which the average trade would break even (only if the edge is positive)
        "breakeven_fee_pct": round(avg_move / 2 / 100, 4) if avg_move and avg_move > 0 else 0.0,
        "longs": tot["longs"], "shorts": tot["shorts"], "by_reason": tot["by_reason"],
        "by_product": {k: {"trades": v["trades"], "avg_move_bps": round(v["move_bps_sum"] / v["trades"], 2)}
                       for k, v in tot["by_product"].items() if v["trades"]},
    }


def snapshot_live(state, books, feats, now):
    live = {}
    for p in C.PRODUCTS:
        f = feats[p].last
        if not f:
            continue
        bids, asks = books[p].top(8)
        live[p] = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in f.items()}
        live[p]["book"] = {"bids": [[x, round(s, 6)] for x, s in bids], "asks": [[x, round(s, 6)] for x, s in asks]}
    state["live"] = live


def record_equity(state, now):
    s = stats(state)
    state["equity_curve"].append([int(now), s["raw"], s["real"]])
    pts = state["equity_curve"]
    if len(pts) > C.MAX_EQUITY_POINTS:
        keep = C.MAX_EQUITY_POINTS // 2
        old, recent = pts[:-keep], pts[-keep:]
        step_ = max(1, len(old) // (C.MAX_EQUITY_POINTS - keep))
        state["equity_curve"] = old[::step_] + recent


def finalize(state, now):
    state["stats"] = stats(state)
    state["saved_at"] = now
    state["saved_at_text"] = nice(now)
    return state


def reset(state, now):
    """Close everything at the last prices, archive the run, start over at $100."""
    for product in list(state["positions"]):
        f = state["live"].get(product)
        if f and f.get("bid"):
            close_position(state, product, f, now, "Reset")
        else:
            state["positions"].pop(product)
    record_equity(state, now)
    st = stats(state)
    archive = {"run_number": state["run_number"], "started": state["run_started"], "ended": now,
               "started_text": nice(state["run_started"]), "ended_text": nice(now),
               "starting_cash": state["starting_cash"], "stats": st,
               "trades": state["trades"][-100:],
               "equity_curve": state["equity_curve"][::max(1, len(state["equity_curve"]) // 300)]}
    fresh = new_state(now, state["run_number"] + 1, ([archive] + state.get("past_runs", []))[:20])
    fresh["live"] = state.get("live", {})
    log_event(fresh, now, f"Reset. Run #{archive['run_number']} ended at ${st['raw']:.2f} before fees / "
                          f"${st['real']:.2f} after fees. Starting run #{fresh['run_number']} with $100.")
    return finalize(fresh, now)
