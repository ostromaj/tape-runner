"""Turns Coinbase Advanced Trade WebSocket messages into order books, features and trades."""
import json

import config as C
import engine
from book import OrderBook
from signals import Features


class Feed:
    def __init__(self, state):
        self.state = state
        self.books = {p: OrderBook(p) for p in C.PRODUCTS}
        self.feats = {p: Features(p) for p in C.PRODUCTS}
        self.last_seq = None
        self.msgs = 0
        self.last_msg_t = 0.0
        self.last_eval = 0.0

    def subscribe_messages(self):
        return [json.dumps({"type": "subscribe", "channel": ch, "product_ids": C.PRODUCTS})
                for ch in ("level2", "market_trades", "heartbeats")]

    def reset_books(self):
        for p in C.PRODUCTS:
            self.books[p].clear()
            self.feats[p].reset_book_state()
        self.last_seq = None

    def handle(self, raw, now):
        """Process one message. Returns False if the stream is out of sync (reconnect)."""
        msg = json.loads(raw)
        self.msgs += 1
        self.last_msg_t = now
        seq = msg.get("sequence_num")
        if seq is not None:
            if self.last_seq is not None and seq != self.last_seq + 1:
                self.last_seq = seq
                return False
            self.last_seq = seq
        ch = msg.get("channel")
        if ch == "l2_data":
            touched = set()
            for ev in msg.get("events", []):
                p = ev.get("product_id")
                book = self.books.get(p)
                if book is None:
                    continue
                if ev.get("type") == "snapshot":
                    book.clear()
                    self.feats[p].reset_book_state()
                for u in ev.get("updates", []):
                    try:
                        book.set_level(u["side"], float(u["price_level"]), float(u["new_quantity"]))
                    except (KeyError, ValueError):
                        continue
                if ev.get("type") == "snapshot":
                    book.ready = True
                book.last_update = now
                touched.add(p)
            for p in touched:
                if self.books[p].ready:
                    self.feats[p].on_book(self.books[p], now)
        elif ch == "market_trades":
            for ev in msg.get("events", []):
                if ev.get("type") == "snapshot":
                    continue          # old trades from before we connected
                for tr in ev.get("trades", []):
                    p = tr.get("product_id")
                    if p in self.books and self.books[p].ready:
                        try:
                            self.feats[p].on_trade(float(tr["price"]), float(tr["size"]), tr.get("side"),
                                                   self.books[p], now)
                        except (KeyError, ValueError):
                            continue
        elif msg.get("type") == "error":
            engine.log_event(self.state, now, f"Feed error: {msg.get('message', '')[:120]}")
        return True

    def evaluate(self, now):
        """Re-read every book and let the engine decide. Returns list of (product, action)."""
        self.last_eval = now
        acts = []
        for p in C.PRODUCTS:
            book = self.books[p]
            if not book.ready or book.crossed():
                continue
            f = self.feats[p].evaluate(book, now)
            if f:
                self.state["live"].setdefault(p, {}).update({"bid": f["bid"], "ask": f["ask"]})
                a = engine.step(self.state, p, f, now)
                if a:
                    acts.append((p, a))
        return acts
