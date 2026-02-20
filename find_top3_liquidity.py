
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

# ==============================
# CONFIG
# ==============================

LOOKBACK_DAYS = 180
PRICE_LIMIT = 20
MAX_SYMBOLS_TO_SCAN = 100  # لتسريع العملية

# ==============================
# GET NASDAQ SYMBOLS
# ==============================

def get_nasdaq_symbols():
    url = "https://old.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
    df = pd.read_csv(url, sep="|")
    symbols = df["Symbol"].tolist()
    return symbols[:MAX_SYMBOLS_TO_SCAN]

# ==============================
# MAIN
# ==============================

symbols = get_nasdaq_symbols()

results = []

end_date = datetime.now()
start_date = end_date - timedelta(days=LOOKBACK_DAYS)

print("Scanning symbols...\n")

for symbol in symbols:
    try:
        df = yf.download(
            symbol,
            start=start_date,
            end=end_date,
            interval="1d",
            progress=False
        )

        if df.empty or len(df) < 30:
            continue

        current_price = df["Close"].iloc[-1]

        if current_price > PRICE_LIMIT:
            continue

        avg_volume = df["Volume"].mean()

        results.append({
            "symbol": symbol,
            "price": round(current_price, 2),
            "avg_volume": avg_volume
        })

    except:
        continue

# ==============================
# SORT & PICK TOP 3
# ==============================

results_df = pd.DataFrame(results)

if results_df.empty:
    print("No valid symbols found.")
else:
    results_df = results_df.sort_values(by="avg_volume", ascending=False)
    top3 = results_df.head(3)

    print("\n====================================")
    print("TOP 3 MOST LIQUID (< $20)")
    print("====================================\n")

    for _, row in top3.iterrows():
        print(f"Symbol: {row['symbol']}")
        print(f"Price: ${row['price']}")
        print(f"Avg Volume (6m): {int(row['avg_volume']):,}")
        print("------------------------------------")
