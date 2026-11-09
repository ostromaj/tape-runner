"""A live limit order book (Level 2) for one product.

Prices are kept in sorted lists so the best bid/ask and the top N levels are cheap to read.
Coinbase sends absolute sizes per price level (0 = remove the level).
"""
from bisect import bisect_left, insort


class OrderBook:
    def __init__(self, product):
        self.product = product
        self.bids = {}       # price -> size
        self.asks = {}
        self._bp = []        # bid prices ascending (best = last)
        self._ap = []        # ask prices ascending (best = first)
        self.ready = False
        self.last_update = 0.0

    def clear(self):
        self.bids.clear(); self.asks.clear(); self._bp.clear(); self._ap.clear()
        self.ready = False

    def set_level(self, side, price, size):
        book, prices = (self.bids, self._bp) if side == "bid" else (self.asks, self._ap)
        if size <= 0:
            if price in book:
                del book[price]
                i = bisect_left(prices, price)
                if i < len(prices) and prices[i] == price:
                    prices.pop(i)
        else:
            if price not in book:
                insort(prices, price)
            book[price] = size

    # ------------------------------------------------------------------ reads
    def best_bid(self):
        return (self._bp[-1], self.bids[self._bp[-1]]) if self._bp else (None, 0.0)

    def best_ask(self):
        return (self._ap[0], self.asks[self._ap[0]]) if self._ap else (None, 0.0)

    def mid(self):
        (b, _), (a, _) = self.best_bid(), self.best_ask()
        return (a + b) / 2 if a and b else None

    def top(self, n=10):
        bids = [(p, self.bids[p]) for p in reversed(self._bp[-n:])]
        asks = [(p, self.asks[p]) for p in self._ap[:n]]
        return bids, asks

    def crossed(self):
        (b, _), (a, _) = self.best_bid(), self.best_ask()
        return b is not None and a is not None and b >= a

    def walk(self, side, dollars):
        """Average fill price for a market order of `dollars` (buy walks asks, sell walks bids)."""
        prices = self._ap if side == "buy" else list(reversed(self._bp))
        book = self.asks if side == "buy" else self.bids
        left, qty, cost = dollars, 0.0, 0.0
        for p in prices:
            take = min(left, book[p] * p)
            q = take / p
            qty += q; cost += take; left -= take
            if left <= 1e-9:
                break
        return cost / qty if qty else None
