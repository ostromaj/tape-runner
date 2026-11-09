# Tape Runner

Claude's **$100 pretend crypto scalper**. It streams the live Coinbase order book (Level 2) for
Bitcoin, Ethereum and Solana, scores buy vs sell pressure 4 times a second, and makes trades that
last seconds.

**Dashboard:** https://ostromaj.github.io/tape-runner/

## Two scoreboards, same trades

- **Before fees:** pays the bid/ask spread only. Does reading the order book work at all?
- **After real fees:** adds Coinbase Advanced's US entry-tier taker fee (0.90% per side). What you'd actually keep.

## The signal

`score = 0.40 × depth imbalance + 0.35 × order-flow imbalance + 0.25 × aggressive trade flow` (−1 to +1).
Past ±0.55 for two checks in a row (with a tight spread and live trading) it opens a long or a pretend
short. Exits: +8 bp target, −6 bp stop, a hard flip in the order book (after 5 s), or 60 s.
Settings live in `runner/config.py`.

Order-flow imbalance: Cont, Kukanov & Stoikov (2014), *The Price Impact of Order Book Events*.

## How it runs

- `runner/` is plain Python with no packages: its own WebSocket client, order book, signals and paper ledgers.
- GitHub Actions runs one long job (~5h40m), then starts the next. The schedule is a watchdog.
- State is saved every 30 s to the `data` branch; the dashboard (`index.html`, GitHub Pages) reads it,
  and can also open its own live connection to Coinbase to show the book in real time.
- `tests/sim.py` (simulated stream) and `tests/ws_test.py` (WebSocket client) run offline.
  `index.html?demo=1` shows the dashboard with simulated data.

**Pretend money. Watch-only. Not financial advice.**
