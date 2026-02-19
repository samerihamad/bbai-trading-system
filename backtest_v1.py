import os
import pandas as pd
from datetime import datetime, timedelta, timezone

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

# ==============================
# CONFIG
# ==============================

SYMBOLS = [
    "BBAI","ONDS","NVTS","SOFI","PLTR","RIOT","MARA","LCID","RIVN",
    "FUBO","OPEN","RUN","QS","UPST","HOOD","AFRM","CLOV","RCAT","NKLA","HUT"
]

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

if not API_KEY or not SECRET_KEY:
    print("Missing API keys")
    exit()

data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

# ==============================
# FETCH DATA (6 MONTHS)
# ==============================

def fetch_6_months(symbol):

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=180)

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

# ==============================
# MAIN
# ==============================

for symbol in SYMBOLS:
    df = fetch_6_months(symbol)

    if df is None:
        print(f"{symbol} → No Data")
        continue

    print(f"{symbol} → Bars Loaded: {len(df)}")

