import os
import pandas as pd
from datetime import datetime, timedelta, timezone

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

# ======================
# CONFIG
# ======================

SYMBOLS = ["ONDS", "BBAI", "RCAT"]
BENCHMARK = "QQQ"

RISK_PERCENT = 0.03
INITIAL_EQUITY = 1000

LOOKBACK_DAYS = 180  # 6 months
TIMEFRAME = TimeFrame.Hour

#API_KEY = os.getenv("PK6VKM4IJFHPY5JFFFIYFJQWHR")
#SECRET_KEY = os.getenv("5FWbPVJSf5EGZy7ZNSnPqWoaFW7zhmwnB7HdZw4pAGiL")

API_KEY = "PK6VKM4IJFHPY5JFFFIYFJQWHR"
SECRET_KEY = "5FWbPVJSf5EGZy7ZNSnPqWoaFW7zhmwnB7HdZw4pAGiL"

# if not API_KEY or not SECRET_KEY:
#    print("Missing API keys")
#    exit()


data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

# ======================
# FETCH DATA
# ======================

def fetch_data(symbol):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TIMEFRAME,
        start=start,
        end=end,
        feed=DataFeed.IEX
    )

    bars = data_client.get_stock_bars(request).df

    if bars.empty:
        return None

    bars = bars.reset_index()
    return bars

# ======================
# BACKTEST ENGINE
# ======================

def run_backtest(symbol):

    print(f"\nRunning Backtest for {symbol}\n")

    df = fetch_data(symbol)
    benchmark_df = fetch_data(BENCHMARK)

    if df is None or benchmark_df is None:
        print("No data")
        return

    df["EMA200"] = df["close"].ewm(span=200).mean()
    df["EMA50"] = df["close"].ewm(span=50).mean()

    equity = INITIAL_EQUITY
    current_trade = None

    wins = 0
    losses = 0
    total_trades = 0

    for i in range(200, len(df)):

        high = df["high"].iloc[i]
        low = df["low"].iloc[i]
        close = df["close"].iloc[i]

        # ===== Manage Trade =====
        if current_trade:

            entry = current_trade["entry"]
            stop = current_trade["stop"]
            target = current_trade["target"]
            risk = current_trade["risk"]

            if not current_trade["moved"]:
                if high >= entry + risk:
                    current_trade["stop"] = entry + (0.5 * risk)
                    current_trade["moved"] = True

            if high >= target:
                equity += equity * RISK_PERCENT * 2
                wins += 1
                total_trades += 1
                current_trade = None
                continue

            if low <= current_trade["stop"]:
                equity += equity * RISK_PERCENT * -1
                losses += 1
                total_trades += 1
                current_trade = None
                continue

        # ===== Entry =====
        if not current_trade:

            ema200_ok = close > df["EMA200"].iloc[i]
            ema50_ok = df["EMA50"].iloc[i] > df["EMA200"].iloc[i]

            # RS calculation
            if i >= 100:
                stock_return = (
                    df["close"].iloc[i] - df["close"].iloc[i-100]
                ) / df["close"].iloc[i-100]

                bench_return = (
                    benchmark_df["close"].iloc[i] - benchmark_df["close"].iloc[i-100]
                ) / benchmark_df["close"].iloc[i-100]

                rs_ok = stock_return > bench_return
            else:
                rs_ok = False

            if not (ema200_ok and ema50_ok and rs_ok):
                continue

            recent_high = df["high"].iloc[i-51:i-1].max()

            if (
                df["high"].iloc[i] > recent_high and
                df["high"].iloc[i-1] > recent_high
            ):

                entry = close
                stop = df["low"].iloc[i-6:i-1].min()
                risk = entry - stop

                if risk <= 0:
                    continue

                target = entry + (2 * risk)

                current_trade = {
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "risk": risk,
                    "moved": False
                }

    winrate = (wins / total_trades * 100) if total_trades > 0 else 0

    print("===================================")
    print(f"Symbol: {symbol}")
    print(f"Trades: {total_trades}")
    print(f"Wins: {wins}")
    print(f"Losses: {losses}")
    print(f"Winrate: {round(winrate,2)}%")
    print(f"Final Equity: ${round(equity,2)}")
    print("===================================")


# ======================
# RUN
# ======================

for symbol in SYMBOLS:
    run_backtest(symbol)
