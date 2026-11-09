"""Order-book signals for one product.

score (-1 .. +1) blends three published order-book ideas:
  depth  - depth imbalance: more resting size on the bid than the ask near the price
  ofi    - order-flow imbalance (Cont, Kukanov & Stoikov 2014): are buyers adding/lifting at
           the best prices faster than sellers?
  flow   - aggressive trade flow: are market orders mostly hitting the ask (buying)?
"""
import math
from collections import deque

import config as C


class Features:
    def __init__(self, product):
        self.product = product
        self.ofi = deque()        # (t, e)
        self.trades = deque()     # (t, dollars, sign)
        self.prev_best = None     # (bid, bid_size, ask, ask_size)
        self.streak = 0           # consecutive evals past the entry line (+ long / - short)
        self.last = {}

    def reset_book_state(self):
        self.prev_best = None
        self.ofi.clear()

    # ------------------------------------------------------------------ inputs
    def on_book(self, book, t):
        (b, qb), (a, qa) = book.best_bid(), book.best_ask()
        if b is None or a is None:
            return
        if self.prev_best is not None:
            pb, pqb, pa, pqa = self.prev_best
            e = 0.0
            e += qb if b >= pb else 0.0
            e -= pqb if b <= pb else 0.0
            e -= qa if a <= pa else 0.0
            e += pqa if a >= pa else 0.0
            if e:
                self.ofi.append((t, e))
        self.prev_best = (b, qb, a, qa)

    def on_trade(self, price, size, maker_side, book, t):
        """Classify the aggressor by where the trade printed vs the book (more reliable than the
        feed's side field), falling back to the maker side Coinbase reports."""
        (b, _), (a, _) = book.best_bid(), book.best_ask()
        if a and price >= a:
            sign = 1
        elif b and price <= b:
            sign = -1
        else:
            sign = -1 if maker_side == "BUY" else 1   # maker bought => taker sold
        self.trades.append((t, price * size, sign))

    # ------------------------------------------------------------------ evaluation
    def evaluate(self, book, t):
        while self.ofi and self.ofi[0][0] < t - C.OFI_WINDOW_SEC:
            self.ofi.popleft()
        while self.trades and self.trades[0][0] < t - C.TRADE_WINDOW_SEC:
            self.trades.popleft()
        (b, qb), (a, qa) = book.best_bid(), book.best_ask()
        if not b or not a or a <= b:
            self.last = {}
            return None
        mid = (a + b) / 2
        spread_bps = (a - b) / mid * 1e4
        bids, asks = book.top(C.DEPTH_LEVELS)
        wb = sum(s / (k + 1) for k, (_, s) in enumerate(bids))
        wa = sum(s / (k + 1) for k, (_, s) in enumerate(asks))
        depth = (wb - wa) / (wb + wa) if wb + wa else 0.0
        top_depth = max((qb + qa) / 2, 1e-12)
        ofi_raw = sum(e for _, e in self.ofi)
        ofi = math.tanh(ofi_raw / (2 * top_depth))
        buy = sum(d for _, d, s in self.trades if s > 0)
        sell = sum(d for _, d, s in self.trades if s < 0)
        flow = (buy - sell) / (buy + sell) if buy + sell else 0.0
        score = C.WEIGHTS["depth"] * depth + C.WEIGHTS["ofi"] * ofi + C.WEIGHTS["flow"] * flow
        micro = (b * qa + a * qb) / (qa + qb) if qa + qb else mid
        if score >= C.ENTER_SCORE:
            self.streak = self.streak + 1 if self.streak > 0 else 1
        elif score <= -C.ENTER_SCORE:
            self.streak = self.streak - 1 if self.streak < 0 else -1
        else:
            self.streak = 0
        self.last = {
            "t": t, "bid": b, "ask": a, "mid": mid, "spread_bps": spread_bps,
            "depth": depth, "ofi": ofi, "flow": flow, "score": score,
            "micro_bps": (micro - mid) / mid * 1e4, "trades_in_window": len(self.trades),
            "buy_dollars": buy, "sell_dollars": sell, "streak": self.streak,
        }
        return self.last
