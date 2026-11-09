"""Tape Runner settings. Change numbers here, not in the logic."""

STARTING_CASH = 100.00
PRODUCTS = ["BTC-USD", "ETH-USD", "SOL-USD"]
NAMES = {"BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "SOL-USD": "Solana"}
WS_URL = "wss://advanced-trade-ws.coinbase.com"

# ---- Fees -------------------------------------------------------------------------------
# Two scoreboards trade the exact same signals:
#   "raw":  pays the bid/ask spread only (does the order-book signal work at all?)
#   "real": spread + Coinbase Advanced's US entry-tier taker fee on every buy and sell
REAL_TAKER_FEE = 0.0090          # 0.90% per side (Coinbase Advanced, US, entry tier)

# ---- Signal -----------------------------------------------------------------------------
EVAL_EVERY_SEC = 0.25            # how often the bot re-reads the book and decides
DEPTH_LEVELS = 10                # order-book levels per side used for depth imbalance
OFI_WINDOW_SEC = 5.0             # order-flow imbalance window
TRADE_WINDOW_SEC = 10.0          # aggressive buy/sell flow window
WEIGHTS = {"depth": 0.40, "ofi": 0.35, "flow": 0.25}
ENTER_SCORE = 0.55               # |score| needed to open a trade (score runs -1 .. +1)
CONFIRM_EVALS = 2                # must stay past the line this many checks in a row
MIN_TRADES_IN_WINDOW = 3         # need some live trading activity
MAX_SPREAD_BPS = {"BTC-USD": 1.0, "ETH-USD": 1.5, "SOL-USD": 3.0}
ALLOW_SHORTS = True              # pretend shorts (most spot apps can't short; this is watch-only)

# ---- Exits (bps = hundredths of a percent, measured on the price you'd actually exit at) --
TAKE_PROFIT_BPS = 8.0
STOP_LOSS_BPS = 6.0
MAX_HOLD_SEC = 60.0
MIN_HOLD_SEC = 3.0               # signal-flip exits only after this
FLIP_EXIT_SCORE = 0.25           # exit a long if score falls below -0.25 (mirror for shorts)
COOLDOWN_SEC = 20.0              # pause per coin after a trade closes

# ---- Money ------------------------------------------------------------------------------
MAX_POSITIONS = 3                # one per coin
ALLOC_PER_POSITION = 1 / 3       # each trade uses a third of that scoreboard's value

# ---- Running on GitHub -------------------------------------------------------------------
RUN_SECONDS = 5 * 3600 + 40 * 60  # hand off to a fresh run before GitHub's 6-hour job limit
SAVE_EVERY_SEC = 30
STALE_FEED_SEC = 20              # reconnect if no messages for this long
MAX_TRADES_KEPT = 200
MAX_EQUITY_POINTS = 3000
