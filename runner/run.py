"""Tape Runner: streams the live Coinbase order book and paper-trades in seconds.

python3 runner/run.py            run until the hand-off time, then exit (the workflow restarts it)
python3 runner/run.py --reset    archive the current run and start over at $100
"""
import json
import os
import queue
import signal
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))

import config as C  # noqa: E402
import engine  # noqa: E402
import storage  # noqa: E402
from feed import Feed  # noqa: E402
from ws import WebSocket, WebSocketClosed  # noqa: E402

STOP = threading.Event()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Saver(threading.Thread):
    """Pushes saves in the background so the trading loop never waits on the network."""

    def __init__(self, sha):
        super().__init__(daemon=True)
        self.sha = sha
        self.q = queue.Queue(maxsize=1)
        self.adopt = queue.Queue()
        self.my_run = None
        self.ok = 0
        self.fail = 0

    def submit(self, text, run_number):
        self.my_run = run_number
        try:
            self.q.get_nowait()       # keep only the newest snapshot
        except queue.Empty:
            pass
        self.q.put(text)

    def push(self, text):
        res, val = storage.save(text, self.sha)
        if res == "ok":
            self.sha = val
            self.ok += 1
            return True
        if res == "conflict":
            remote, sha = storage.load()
            self.sha = sha
            if remote and remote.get("run_number") != self.my_run:
                log("Someone reset the portfolio. Switching to the new run.")
                self.adopt.put(remote)
                return True
            res, val = storage.save(text, self.sha)
            if res == "ok":
                self.sha = val
                self.ok += 1
                return True
        self.fail += 1
        log("save failed:", res, val)
        return False

    def run(self):
        while True:
            text = self.q.get()
            if text is None:
                return
            try:
                self.push(text)
            except Exception as e:  # never let saving kill the bot
                self.fail += 1
                log("save error:", e)


def dump(state):
    return json.dumps(state, separators=(",", ":"))


def main():
    t_start = time.time()
    dry = "--dry" in sys.argv          # test mode: no saving
    minutes = None
    if "--minutes" in sys.argv:
        minutes = float(sys.argv[sys.argv.index("--minutes") + 1])
    state, sha = (None, "") if dry else storage.load()
    if not state or state.get("version") != 1:
        log("No saved portfolio. Starting fresh with $100.")
        state = engine.new_state(t_start)
    if "--reset" in sys.argv:
        state = engine.reset(state, time.time())
        res = storage.save(dump(state), sha)
        log("reset:", res[0])
        sys.exit(0 if res[0] == "ok" else 1)

    run_url = ""
    if os.environ.get("GITHUB_RUN_ID"):
        run_url = f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    handoff_at = t_start + (minutes * 60 if minutes else C.RUN_SECONDS)
    state["runner"] = {"started": t_start, "handoff_at": handoff_at, "url": run_url}
    engine.log_event(state, t_start, "Bot started its shift and is connecting to the Coinbase order book.")

    saver = Saver(sha)
    saver.start()
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())

    feed = Feed(state)
    last_save = 0.0
    backoff = 2
    while not STOP.is_set() and time.time() < handoff_at:
        try:
            log("connecting to", C.WS_URL)
            ws = WebSocket(C.WS_URL, timeout=15)
            for m in feed.subscribe_messages():
                ws.send(m)
            ws.settimeout(C.EVAL_EVERY_SEC)
            feed.reset_books()
            state["feed"].update(connected=True, since=time.time())
            feed.last_msg_t = time.time()
            backoff = 2
            while not STOP.is_set() and time.time() < handoff_at:
                now = time.time()
                try:
                    raw = ws.recv()
                    if not feed.handle(raw, time.time()):
                        log("missed a message (sequence gap); resyncing the order book")
                        break
                except socket.timeout:
                    pass
                now = time.time()
                if now - feed.last_msg_t > C.STALE_FEED_SEC:
                    log("feed went quiet; reconnecting")
                    break
                if now - feed.last_eval >= C.EVAL_EVERY_SEC:
                    for p, a in feed.evaluate(now):
                        if a == "open":
                            pos = state["positions"][p]
                            log(f"OPEN {'LONG' if pos['dir'] > 0 else 'SHORT'} {p} @ {pos['entry_px']} score {pos['score']}")
                        else:
                            tr = state["trades"][-1]
                            log(f"CLOSE {p} {tr['move_bps']:+.2f} bps ({tr['reason']}) raw {tr['pnl']['raw']:+.4f} real {tr['pnl']['real']:+.4f}")
                try:
                    adopted = saver.adopt.get_nowait()
                    state = adopted
                    state["runner"] = {"started": t_start, "handoff_at": handoff_at, "url": run_url}
                    feed.state = state
                except queue.Empty:
                    pass
                if now - last_save >= C.SAVE_EVERY_SEC:
                    last_save = now
                    state["feed"]["msgs"] = feed.msgs
                    engine.snapshot_live(state, feed.books, feed.feats, now)
                    engine.record_equity(state, now)
                    engine.finalize(state, now)
                    if dry:
                        s = state["stats"]
                        log(f"[test] msgs {feed.msgs}, trades {s['trades']}, open {len(state['positions'])}, "
                            + " | ".join(f"{p} spr {v.get('spread_bps', 0):.2f}bps score {v.get('score', 0):+.2f} "
                                         f"lvls {len(feed.books[p].bids)}/{len(feed.books[p].asks)} tr/10s {v.get('trades_in_window')}"
                                         for p, v in state["live"].items()))
                    else:
                        saver.submit(dump(state), state["run_number"])
            ws.close()
        except (WebSocketClosed, OSError, ValueError) as e:
            log("connection problem:", e)
            state["feed"]["reconnects"] += 1
            state["feed"]["connected"] = False
            engine.log_event(state, time.time(), "Lost the connection to Coinbase. Reconnecting.")
            STOP.wait(backoff)
            backoff = min(backoff * 2, 60)
        else:
            if not STOP.is_set() and time.time() < handoff_at:
                state["feed"]["reconnects"] += 1

    # ---- shift over: close open trades so the next run starts clean, then save
    now = time.time()
    for p in list(state["positions"]):
        f = state["live"].get(p)
        if f and f.get("bid"):
            engine.close_position(state, p, f, now, "Shift change")
    state["feed"]["connected"] = False
    engine.log_event(state, now, "Shift over. A fresh run takes over in about a minute.")
    engine.record_equity(state, now)
    engine.finalize(state, now)
    if dry:
        s = state["stats"]
        log(f"[test] done: {feed.msgs} msgs, {s['trades']} trades, avg move {s['avg_move_bps']} bps, "
            f"raw ${s['raw']:.2f}, real ${s['real']:.2f}, reasons {s['by_reason']}")
        for tr in state["trades"][-8:]:
            log("[test] trade", tr["product"], tr["side"], tr["move_bps"], "bps", tr["hold_s"], "s", tr["reason"], tr["signal"])
        return
    saver.submit(dump(state), state["run_number"])
    try:
        saver.q.put(None, timeout=90)     # waits until the final snapshot is picked up
    except queue.Full:
        pass
    saver.join(timeout=90)
    s = state["stats"]
    log(f"Done. {s['trades']} trades. Raw ${s['raw']:.2f}, real ${s['real']:.2f}. Saves ok {saver.ok}, failed {saver.fail}.")


if __name__ == "__main__":
    main()
