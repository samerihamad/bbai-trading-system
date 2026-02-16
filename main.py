import os
import time
from datetime import datetime, timedelta
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

print("🚀 Multi-Stock Trading System Starting...", flush=True)

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

if not API_KEY or not SECRET_KEY:
    print("❌ API keys not found!", flush=True)
    exit()

# قائمة الأسهم (تقدر تضيف أي سهم هنا)
SYMBOLS = ["BBAI", "AAPL", "TSLA"]

try:
    trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
    account = trading_client.get_account()
    print("✅ Connected to Alpaca!", flush=True)
    print(f"Account Equity: ${account.equity}", flush=True)

    data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

except Exception as e:
    print(f"❌ Connection failed: {e}", flush=True)
    exit()

def fetch_data(symbol):
    end = datetime.utcnow()
    start = end - timedelta(days=10)

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute(15),
        start=start,
        end=end
    )

    bars = data_client.get_stock_bars(request)
    df = bars.df

    if not df.empty:
        latest_price = df["close"].iloc[-1]
        print(f"📈 {symbol} | Latest Price: ${latest_price}", flush=True)
    else:
        print(f"⚠️ No data for {symbol}", flush=True)


while True:
    print("🔄 Checking symbols...", flush=True)

    for symbol in SYMBOLS:
        fetch_data(symbol)

    print("⏳ Waiting 15 minutes...\n", flush=True)
    time.sleep(900)  # 15 minutes
