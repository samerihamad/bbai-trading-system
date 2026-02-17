import os
import time
import pandas as pd
import requests

from datetime import datetime, timedelta, timezone

from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

print("🚀 Trend + RS + Breakout System Starting...", flush=True)

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


if not API_KEY or not SECRET_KEY:
    print("❌ API keys not found")
    exit()

SYMBOLS = ["BBAI", "AAPL", "TSLA"]
BENCHMARK = "QQQ"

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)


def fetch_bars(symbol):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=40)

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(15, TimeFrameUnit.Minute),
        start=start,
        end=end,
        feed=DataFeed.IEX
    )

    bars = data_client.get_stock_bars(request)
    df = bars.df

    if df.empty:
        return None

    df = df.reset_index()
    return df


def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def analyze_symbol(symbol, benchmark_df):

    df = fetch_bars(symbol)

    if df is None or len(df) < 250:
        print(f"{symbol} → Not enough data", flush=True)
        return

    df["EMA200"] = calculate_ema(df["close"], 200)
    df["EMA50"] = calculate_ema(df["close"], 50)

    latest = df.iloc[-1]

    # Trend
    if latest["close"] > latest["EMA200"] and latest["EMA50"] > latest["EMA200"]:
        trend = "UP"
    else:
        trend = "DOWN"

    # Relative Strength
    if len(df) < 120 or len(benchmark_df) < 120:
        rs_status = "N/A"
    else:
        stock_return = (df["close"].iloc[-1] - df["close"].iloc[-100]) / df["close"].iloc[-100]
        bench_return = (benchmark_df["close"].iloc[-1] - benchmark_df["close"].iloc[-100]) / benchmark_df["close"].iloc[-100]
        rs_status = "STRONG" if stock_return > bench_return else "WEAK"

    message = f"{symbol} → Trend: {trend} | RS: {rs_status}"

    # Breakout logic
    if trend == "UP" and rs_status == "STRONG":
        recent_high = df["high"].iloc[-51:-1].max()
        current_close = df["close"].iloc[-1]

        if current_close > recent_high:
            message += " | 🚀 BREAKOUT DETECTED"
        else:
            message += " | No Breakout"

    print(message, flush=True)



def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram not configured", flush=True)
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message
    }

    try:
        response = requests.post(url, data=payload)
        print("Telegram response:", response.text, flush=True)
    except Exception as e:
        print("Telegram error:", e, flush=True)




while True:

    send_telegram("🔥 Telegram test from trading system")

    clock = trading_client.get_clock()

    if not clock.is_open:
        print("🛑 Market is CLOSED. Waiting 15 minutes...\n", flush=True)
        time.sleep(900)
        continue

    print("\n🔄 Market OPEN — Running Analysis...\n", flush=True)

    benchmark_df = fetch_bars(BENCHMARK)

    if benchmark_df is None:
        print("Failed to fetch benchmark data", flush=True)
        time.sleep(900)
        continue

    for symbol in SYMBOLS:
        analyze_symbol(symbol, benchmark_df)

    print("\n⏳ Waiting 15 minutes...\n", flush=True)
    time.sleep(900)
