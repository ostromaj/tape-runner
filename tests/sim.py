"""Offline test: a fake Coinbase Advanced Trade stream (same message format) through the real
feed + engine with a simulated clock.   python3 tests/sim.py"""
import json, math, os, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "runner"))
import config as C, engine
from feed import Feed

def run(seed, seconds=3 * 3600):
    rng = random.Random(seed)
    t0 = 1762700000.0
    state = engine.new_state(t0)
    feed = Feed(state)
    mids = {"BTC-USD": 104000.0, "ETH-USD": 3500.0, "SOL-USD": 165.0}
    tick = {"BTC-USD": 0.01, "ETH-USD": 0.01, "SOL-USD": 0.01}
    seq = 0
    def msg(ch, events):
        nonlocal seq
        m = {"channel": ch, "timestamp": "x", "sequence_num": seq, "events": events}
        seq += 1
        return json.dumps(m)
    # snapshots
    for p, m in mids.items():
        ups = []
        for k in range(1, 60):
            ups.append({"side": "bid", "event_time": "x", "price_level": f"{m - k*tick[p]*3:.2f}", "new_quantity": f"{rng.uniform(0.1, 2):.6f}"})
            ups.append({"side": "offer", "event_time": "x", "price_level": f"{m + k*tick[p]*3:.2f}", "new_quantity": f"{rng.uniform(0.1, 2):.6f}"})
        assert feed.handle(msg("l2_data", [{"type": "snapshot", "product_id": p, "updates": ups}]), t0)
    feed.handle(msg("market_trades", [{"type": "snapshot", "trades": []}]), t0)
    t, pressure = t0, {p: 0.0 for p in mids}
    opens = closes = 0
    while t < t0 + seconds:
        t += 0.1
        for p in mids:
            pressure[p] = 0.995 * pressure[p] + rng.gauss(0, 0.02)
            book = feed.books[p]
            (b, _), (a, _) = book.best_bid(), book.best_ask()
            drift = pressure[p] * tick[p] * 6 + rng.gauss(0, tick[p] * 2)
            mid = (a + b) / 2 + drift
            ups = []
            # pressure tilts resting size and trade direction (so the signal has something to find)
            for k in range(1, 6):
                bp, ap = round(mid - k * tick[p] * 3, 2), round(mid + k * tick[p] * 3, 2)
                ups.append({"side": "bid", "price_level": f"{bp:.2f}", "new_quantity": f"{max(0.05, rng.uniform(0.3, 1.5) * (1 + 3*pressure[p])):.6f}"})
                ups.append({"side": "offer", "price_level": f"{ap:.2f}", "new_quantity": f"{max(0.05, rng.uniform(0.3, 1.5) * (1 - 3*pressure[p])):.6f}"})
            # remove levels that crossed
            for x in list(book.bids):
                if x >= mid - tick[p]: ups.append({"side": "bid", "price_level": f"{x:.2f}", "new_quantity": "0"})
            for x in list(book.asks):
                if x <= mid + tick[p]: ups.append({"side": "offer", "price_level": f"{x:.2f}", "new_quantity": "0"})
            if not feed.handle(msg("l2_data", [{"type": "update", "product_id": p, "updates": ups}]), t):
                raise SystemExit("sequence problem")
            if rng.random() < 0.6:
                (b, _), (a, _) = book.best_bid(), book.best_ask()
                buy = rng.random() < 0.5 + pressure[p]
                tr = {"trade_id": "1", "product_id": p, "price": f"{a if buy else b}", "size": f"{rng.uniform(0.01, 0.5):.5f}", "side": "SELL" if buy else "BUY", "time": "x"}
                feed.handle(msg("market_trades", [{"type": "update", "trades": [tr]}]), t)
        if t - feed.last_eval >= C.EVAL_EVERY_SEC:
            for p, a in feed.evaluate(t):
                opens += a == "open"; closes += a == "close"
        if int(t) % 30 == 0 and abs(t - round(t)) < 0.05:
            engine.snapshot_live(state, feed.books, feed.feats, t)
            engine.record_equity(state, t)
        assert all(not b.crossed() for b in feed.books.values())
    engine.snapshot_live(state, feed.books, feed.feats, t)
    engine.finalize(state, t)
    s = state["stats"]
    json.loads(json.dumps(state))
    # bookkeeping checks
    fees = sum(tr["fees"]["real"] for tr in state["trades"])
    assert abs(fees - s["fees_real"]) < 1e-6 or len(state["trades"]) == C.MAX_TRADES_KEPT
    assert s["real"] < s["raw"] + 1e-9, "real fees can't beat no fees"
    for tr in state["trades"]:
        assert tr["hold_s"] <= C.MAX_HOLD_SEC + 1
    print(f"seed {seed}: opens {opens} closes {closes} | raw ${s['raw']:.2f} ({s['raw_pct']:+.2f}%) real ${s['real']:.2f} | "
          f"win {s['win_rate']}% avg move {s['avg_move_bps']} bps hold {s['avg_hold_s']}s | reasons {s['by_reason']} | "
          f"longs {s['longs']} shorts {s['shorts']} | breakeven fee {s['breakeven_fee_pct']}% | open now {len(state['positions'])}")
    return state

if __name__ == "__main__":
    for seed in (1, 2):
        st = run(seed)
    st2 = engine.reset(st, st["saved_at"] + 5)
    assert st2["past_runs"][0]["run_number"] == 1 and st2["ledgers"]["raw"]["equity"] == 100 and not st2["positions"]
    out = os.path.join(os.path.dirname(__file__), "sim_dashboard.json")
    json.dump(st, open(out, "w"), separators=(",", ":"))
    print("sample:", out, f"{os.path.getsize(out)/1024:.0f} KB")
