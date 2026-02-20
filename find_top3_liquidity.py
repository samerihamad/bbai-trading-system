import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

# ==============================
# CONFIG
# ==============================

LOOKBACK_DAYS = 180
PRICE_LIMIT = 20
MAX_SYMBOLS = 150   # لتسريع الفحص

# ==============================
# GET SYMBOL LIST FROM ETF
# ==============================

def get_symbols_from_etf():
    # نستخدم ETF يحتوي شركات NASDAQ
    etf = yf.Ticker("QQQ")
    holdings = etf.get_holdings()
    symbols = holdings.index.tolist()
    return symbols[:MAX_SYMBOLS]

# ==============================
# MAIN
# ==============================

symbols = get_symbols_from_etf()

end_date = datetime.now()
start_date = end_date - timedelta(days=LOOKBACK_DAYS)

results = []

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

        price = float(df["Close"].iloc[-1])

        if price > PRICE_LIMIT:
            continue

        avg_volume = df["Volume"].mean()

        results.append({
            "symbol": symbol,
            "price": round(price, 2),
            "avg_volume": avg_volume
        })

    except:
        continue

if len(results) == 0:
    print("No symbols found under price limit.")
else:
    df_results = pd.DataFrame(results)
    df_results = df_results.sort_values(by="avg_volume", ascending=False)

    print("\n====================================")
    print("TOP 3 MOST LIQUID (< $20)")
    print("====================================\n")

    for i in range(min(3, len(df_results))):
        row = df_results.iloc[i]
        print(f"Symbol: {row['symbol']}")
        print(f"Price: ${row['price']}")
        print(f"Avg Volume (6m): {int(row['avg_volume']):,}")
        print("------------------------------------")
