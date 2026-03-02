"""
config.py — Single source of truth for all configuration values.
All modules import from here. Never hardcode values elsewhere.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── Alpaca ────────────────────────────────────────────────────────────────
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL   = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
IS_PAPER          = "paper" in ALPACA_BASE_URL.lower()

# ─── Telegram ─────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── Universe ─────────────────────────────────────────────────────────────
UNIVERSE_MAX_CANDIDATES = 500
UNIVERSE_MIN_PRICE      = 5.0
UNIVERSE_MAX_PRICE      = 500.0
UNIVERSE_MIN_VOLUME     = 500_000

# ─── Timing ───────────────────────────────────────────────────────────────
LOOP_INTERVAL_SECONDS = 30
TIMEZONE              = "America/New_York"   # pytz handles EST/EDT automatically
MARKET_OPEN_TIME      = "09:35"
MARKET_CLOSE_TIME     = "15:45"

# ─── Risk & Position Sizing ───────────────────────────────────────────────
MAX_LONG            = 2
MAX_SHORT           = 1
MAX_TOTAL           = 3
RISK_PER_TRADE_PCT  = 0.01
MIN_POSITION_VALUE  = 500.0
MAX_POSITION_VALUE  = 5_000.0

# ─── Strategy Parameters ──────────────────────────────────────────────────
RSI_OVERSOLD          = 30
RSI_OVERBOUGHT        = 70
VWAP_THRESHOLD_PCT    = 1.2 / 100
ATR_MIN_PCT           = 0.7 / 100
ATR_MAX_PCT           = 3.5 / 100
EMA_SHORT             = 9
EMA_LONG              = 21
EMA_TREND             = 200

PROFIT_FACTOR_CUT     = 0.20
STOP_LOSS_ATR_MULT    = 1.5
TAKE_PROFIT_ATR_MULT  = 3.0

# ─── Liquidity Sweep ──────────────────────────────────────────────────────
LIQUIDITY_SWEEP_ENABLED = True

# ─── SHORT Selling ────────────────────────────────────────────────────────
SHORT_ENABLED      = IS_PAPER
SHORT_EXCHANGES    = {"NASDAQ", "NYSE"}
SHORT_RSI_MIN      = RSI_OVERBOUGHT   # RSI > 70

# ─── Logging ──────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
LOG_FILE  = "trading.log"
