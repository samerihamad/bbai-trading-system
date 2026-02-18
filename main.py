import os
import time
import pandas as pd
import requests
import pytz
import csv

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

SYMBOLS = ["BBAI", "ONDS", "NVTS"]
BENCHMARK = "QQQ"

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

account = trading_client.get_account()
print("Connected Equity:", account.equity, flush=True)

# ==============================
# LOGGER SETUP
# ==============================

LOG_FILE = "trades_log.csv"

if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, mode="w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([
            "date_open",
            "symbol",
            "entry",
            "stop",
            "target",
            "exit",
            "result",
            "r_multiple"
        ])

def log_trade(trade_data):
    with open(LOG_FILE, mode="a", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([
            trade_data["date_open"],
            trade_data["symbol"],
            trade_data["entry"],
            trade_data["stop"],
            trade_data["target"],
            trade_data["exit"],
            trade_data["result"],
            trade_data["r_multiple"]
        ])

# ==============================
# GLOBAL STATE
# ==============================

current_trade = None
daily_loss_count = 0
last_premarket_alert_date = None

# ==============================
# DATA
# ==============================

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

    return df.reset_index()

def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

# ==============================
# TRADE ENGINE
# ==============================

def check_breakout_and_open_trade(symbol, df):
    global current_trade, daily_loss_count

    if current_trade is not None:
        return

    if daily_loss_count >= 2:
        return

    recent_high = df["high"].iloc[-51:-1].max()

    if df["close"].iloc[-2] > recent_high and df["close"].iloc[-1] > recent_high:

        entry = df["close"].iloc[-1]
        stop = df["low"].iloc[-6:-1].min()
        risk = entry - stop

        if risk <= 0:
            return

        target = entry + (2 * risk)

        current_trade = {
            "date_open": datetime.now(timezone.utc),
            "symbol": symbol,
            "entry": entry,
            "stop": stop,
            "target": target,
            "risk": risk,
            "moved_to_half_r": False
        }

        message = (
            f"📈 PAPER TRADE OPENED\n\n"
            f"Symbol: {symbol}\n"
            f"Entry: {round(entry,2)}\n"
            f"Stop: {round(stop,2)}\n"
            f"Target: {round(target,2)}"
        )

        print(message, flush=True)
        send_telegram(message)

def manage_open_trade(symbol, df):
    global current_trade, daily_loss_count

    if current_trade is None:
        return

    if current_trade["symbol"] != symbol:
        return

    high_price = df["high"].iloc[-1]
    low_price = df["low"].iloc[-1]

    entry = current_trade["entry"]
    stop = current_trade["stop"]
    target = current_trade["target"]
    risk = current_trade["risk"]

    # 2R target
    if high_price >= target:

        trade_data = {
            **current_trade,
            "exit": target,
            "result": "WIN",
            "r_multiple": 2
        }

        log_trade(trade_data)

        send_telegram(f"🎯 TARGET HIT 2R | {symbol}")
        current_trade = None
        return

    # Stop
    if low_price <= stop:

        trade_data = {
            **current_trade,
            "exit": stop,
            "result": "LOSS",
            "r_multiple": -1
        }

        log_trade(trade_data)

        send_telegram(f"❌ STOP HIT | {symbol}")
        daily_loss_count += 1
        current_trade = None
        return

# ==============================
# ANALYSIS
# ==============================

def analyze_symbol(symbol, benchmark_df):
    df = fetch_bars(symbol)

    if df is None or len(df) < 250:
        return

    df["EMA200"] = calculate_ema(df["close"], 200)
    df["EMA50"] = calculate_ema(df["close"], 50)

    check_breakout_and_open_trade(symbol, df)
    manage_open_trade(symbol, df)

# ==============================
# TELEGRAM
# ==============================

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}

    try:
        requests.post(url, data=payload)
    except:
        pass

# ==============================
# MAIN LOOP
# ==============================

while True:

    clock = trading_client.get_clock()

    if not clock.is_open:
        print("🛑 Market Closed", flush=True)
        time.sleep(60)
        continue

    benchmark_df = fetch_bars(BENCHMARK)

    if benchmark_df is None:
        time.sleep(900)
        continue

    for symbol in SYMBOLS:
        analyze_symbol(symbol, benchmark_df)

    time.sleep(900)
