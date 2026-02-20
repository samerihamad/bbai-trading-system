from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

from datetime import datetime, timedelta, timezone
import os

# ==============================
# CONFIG
# ==============================

PRICE_LIMIT = 20
LOOKBACK_DAYS = 180

CANDIDATE_SYMBOLS = [
    "ONDS", "BBAI", "RCAT", "NVTS", "MARA",
    "RIOT", "SOFI", "PLTR", "FUBO", "LCID",
    "HOOD", "DKNG", "RUN", "OPEN", "BB"
]

# ==============================
# ENV
# ==============================

#API_KEY = os.getenv("APK6VKM4IJFHPY5JFFFIYFJQWHR")
#SECRET_KEY = os.getenv("5FWbPVJSf5EGZy7ZNSnPqWoaFW7zhmwnB7HdZw4pAGiL")

API_KEY = "APK6VKM4IJFHPY5JFFFIYFJQWHR"
SECRET_KEY = "5FWbPVJSf5EGZy7ZNSnPqWoaFW7zhmwnB7HdZw4pAGiL"

data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

# ==============================
# MAIN
# ==============================

end = datetime.now(timezone.utc)
start = end - timedelta(days=LOOKBACK_DAYS)

results = []

print("Scanning symbols using Alpaca...\n")

for symbol in CANDIDATE_SYMBOLS:
    try:
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            feed=DataFeed.IEX
        )

        bars = data_client.get_stock_bars(request).df

        if bars.empty:
            continue

        bars = bars.reset_index()

        current_price = float(bars["close"].iloc[-1])

        if current_price > PRICE_LIMIT:
            continue

        avg_volume = bars["volume"].mean()

        results.append({
            "symbol": symbol,
            "price": round(current_price, 2),
            "avg_volume": avg_volume
        })

    except Exception as e:
        continue

# ==============================
# SORT & PRINT
# ==============================

if len(results) == 0:
    print("No valid symbols found.")
else:
    results = sorted(results, key=lambda x: x["avg_volume"], reverse=True)

    print("\n====================================")
    print("TOP 3 MOST LIQUID (< $20)")
    print("====================================\n")

    for stock in results[:3]:
        print(f"Symbol: {stock['symbol']}")
        print(f"Price: ${stock['price']}")
        print(f"Avg Volume (6m): {int(stock['avg_volume']):,}")
        print("------------------------------------")
