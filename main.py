import os import time import pandas as pd import requests import pytz
import csv

from datetime import datetime, timedelta, timezone from
alpaca.trading.client import TradingClient from alpaca.data.historical
import StockHistoricalDataClient from alpaca.data.requests import
StockBarsRequest from alpaca.data.timeframe import TimeFrame,
TimeFrameUnit from alpaca.data.enums import DataFeed

==============================

CONFIG

==============================

SYMBOLS = ["BBAI", "ONDS", "NVTS"]
BENCHMARK = "QQQ"


RISK_PERCENT = 0.03 LOG_FILE = "trades_log.csv"

==============================

ENV

==============================

API_KEY = os.getenv("ALPACA_API_KEY") SECRET_KEY =
os.getenv("ALPACA_SECRET_KEY") BOT_TOKEN =
os.getenv("TELEGRAM_BOT_TOKEN") CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not API_KEY or not SECRET_KEY: print("Missing API keys") exit()

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

account = trading_client.get_account() current_equity =
float(account.equity)

==============================

GLOBAL STATE

==============================

current_trade = None daily_loss_count = 0 last_premarket_alert_date =
None last_report_sent_date = None last_no_setup_hour = None

==============================

CSV INIT

==============================

if not os.path.exists(LOG_FILE): with open(LOG_FILE, mode="w",
newline="") as file: writer = csv.writer(file) writer.writerow([
"date_open", "symbol", "entry", "stop", "target", "exit", "result",
"r_multiple" ])

==============================

UTIL

==============================

def send_telegram(message): if not BOT_TOKEN or not CHAT_ID: return url
= f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
requests.post(url, data={"chat_id": CHAT_ID, "text": message})

def log_trade(data): with open(LOG_FILE, mode="a", newline="") as file:
writer = csv.writer(file) writer.writerow([ data["date_open"],
data["symbol"], data["entry"], data["stop"], data["target"],
data["exit"], data["result"], data["r_multiple"] ])

==============================

DATA

==============================

def fetch_bars(symbol): end = datetime.now(timezone.utc) start = end -
timedelta(days=40)

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

def calculate_ema(series, period): return series.ewm(span=period,
adjust=False).mean()

==============================

TRADE ENGINE

==============================

def manage_open_trade(symbol, df): global current_trade,
daily_loss_count, current_equity

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

    if not current_trade["moved"]:
        if high_price >= entry + risk:
            new_stop = entry + (0.5 * risk)
            current_trade["stop"] = new_stop
            current_trade["moved"] = True

            send_telegram(
                f"🔄 تم تحريك الوقف / Stop moved\n\n"
                f"{symbol}\nNew Stop: {round(new_stop,2)}"
            )

    if high_price >= target:
        r_multiple = 2
        risk_amount = current_equity * RISK_PERCENT
        pnl = r_multiple * risk_amount
        current_equity += pnl

        log_trade({
            **current_trade,
            "exit": target,
            "result": "WIN",
            "r_multiple": r_multiple
        })

        send_telegram(
            f"🎯 الهدف تحقق / TARGET HIT\n\n"
            f"{symbol}\n+2R\nPnL: ${round(pnl,2)}\nEquity: ${round(current_equity,2)}"
        )

        current_trade = None
        return

    if low_price <= current_trade["stop"]:
        r_multiple = -1
        risk_amount = current_equity * RISK_PERCENT
        pnl = r_multiple * risk_amount
        current_equity += pnl

        log_trade({
            **current_trade,
            "exit": current_trade["stop"],
            "result": "LOSS",
            "r_multiple": r_multiple
        })

        daily_loss_count += 1

        send_telegram(
            f"❌ وقف الخسارة / STOP HIT\n\n"
            f"{symbol}\n-1R\nPnL: ${round(pnl,2)}\nEquity: ${round(current_equity,2)}"
        )

        if daily_loss_count >= 2:
            send_telegram(
                "🛑 تم إيقاف التداول اليوم\n\n"
                "Daily loss limit reached."
            )

        current_trade = None
        return

def check_breakout(symbol, df): global current_trade, daily_loss_count

    if current_trade is not None:
        return
    if daily_loss_count >= 2:
        return

    recent_high = df["high"].iloc[-51:-1].max()

    if df["high"].iloc[-1] > recent_high and df["high"].iloc[-2] > recent_high:

        entry = df["close"].iloc[-1]
        stop = df["low"].iloc[-6:-1].min()
        risk = entry - stop

        if risk <= 0:
            return

        target = entry + (2 * risk)

        current_trade = {
            "date_open": datetime.now(pytz.timezone("America/New_York")).date(),
            "symbol": symbol,
            "entry": entry,
            "stop": stop,
            "target": target,
            "risk": risk,
            "moved": False
        }

        send_telegram(
            f"📈 صفقة جديدة / NEW TRADE\n\n"
            f"{symbol}\nEntry: {round(entry,2)}\n"
            f"Stop: {round(stop,2)}\nTarget: {round(target,2)}"
        )

==============================

DAILY REPORT

==============================

def send_daily_report(): global last_report_sent_date

    ny = pytz.timezone("America/New_York")
    today_ny = datetime.now(ny).date()
    yesterday = today_ny - timedelta(days=1)

    df = pd.read_csv(LOG_FILE)
    if df.empty:
        trades = []
    else:
        trades = df[df["date_open"] == str(yesterday)]

    total = len(trades)
    wins = len(trades[trades["result"] == "WIN"])
    losses = len(trades[trades["result"] == "LOSS"])
    net_r = trades["r_multiple"].sum() if total > 0 else 0

    risk_amount = current_equity * RISK_PERCENT
    net_dollar = net_r * risk_amount

    message = (
        f"📊 تقرير جلسة أمس\n\n"
        f"عدد الصفقات: {total}\n"
        f"الرابحة: {wins}\n"
        f"الخاسرة: {losses}\n"
        f"صافي R: {net_r}\n"
        f"صافي $: {round(net_dollar,2)}\n\n"
        f"---\n"
        f"📊 YESTERDAY REPORT\n\n"
        f"Trades: {total}\n"
        f"Wins: {wins}\n"
        f"Losses: {losses}\n"
        f"Net R: {net_r}\n"
        f"Net $: {round(net_dollar,2)}"
    )

    send_telegram(message)
    last_report_sent_date = today_ny

==============================

MAIN LOOP

==============================

while True:

    clock = trading_client.get_clock()
    now = datetime.now(pytz.timezone("America/New_York"))

    if not clock.is_open:

        next_open = clock.next_open
        minutes_to_open = (next_open - datetime.now(timezone.utc)).total_seconds() / 60

        if 0 < minutes_to_open <= 30:
            if last_report_sent_date != now.date():
                send_daily_report()

            send_telegram(
                "🚨 السوق سيفتتح بعد 30 دقيقة\n\n"
                "NASDAQ opens in 30 minutes"
            )

        time.sleep(60)
        continue

    benchmark_df = fetch_bars(BENCHMARK)
    setup_found = False

    for symbol in SYMBOLS:
        df = fetch_bars(symbol)
        if df is None:
            continue

        manage_open_trade(symbol, df)
        check_breakout(symbol, df)

        if current_trade is not None:
            setup_found = True

    if current_trade is None:
        hour_now = now.hour
        if last_no_setup_hour != hour_now:
            send_telegram(
                "🔍 لا توجد فرصة حالياً\n\nNo valid setup."
            )
            last_no_setup_hour = hour_now

    time.sleep(900)
