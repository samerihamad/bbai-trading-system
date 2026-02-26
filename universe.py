# =============================================================
# universe.py — اختيار أعلى 20 سهم سيولة ديناميكياً
# =============================================================

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_DATA_URL,
    UNIVERSE_SIZE,
    MIN_AVG_VOLUME,
    MIN_PRICE,
)

HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
}


# ─────────────────────────────────────────
# 1. جلب قائمة أسهم Nasdaq مع فلتر مبكر
# ─────────────────────────────────────────

def get_nasdaq_assets() -> list[str]:
    """
    يجلب أسهم Nasdaq النشطة والقابلة للتداول فقط.
    الفلتر المبكر يقلص القائمة من ~4,000 إلى ~800 سهم
    قبل أي طلب بيانات إضافي.
    """
    try:
        response = requests.get(
            "https://paper-api.alpaca.markets/v2/assets",
            headers=HEADERS,
            params={
                "status":      "active",
                "exchange":    "NASDAQ",
                "asset_class": "us_equity",
            },
            timeout=15,
        )
        assets = response.json()

        # فلتر مبكر: نشط + قابل للتداول + رمز نظيف
        tickers = [
            a["symbol"] for a in assets
            if a.get("tradable")
            and a.get("status") == "active"
            and len(a["symbol"]) <= 5      # يستبعد الرموز الطويلة غير الاعتيادية
            and "." not in a["symbol"]     # يستبعد أسهم الفئات الخاصة مثل BRK.B
        ]
        print(f"   بعد الفلتر المبكر: {len(tickers)} سهم")
        return tickers

    except Exception as e:
        print(f"❌ خطأ في جلب الأسهم: {e}")
        return []


# ─────────────────────────────────────────
# 2. جلب بيانات السيولة بـ batches سريعة
# ─────────────────────────────────────────

def get_volume_data(tickers: list[str]) -> pd.DataFrame:
    """
    يجلب متوسط حجم التداول وآخر سعر إغلاق لكل سهم.
    - يعمل بـ batches حجم كل منها 100 سهم
    - يُطبّق فلتر السعر والحجم مباشرة داخل الـ loop
    """
    end   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    results    = []
    batch_size = 100  # أكبر batch مسموح به في Alpaca

    for i in range(0, len(tickers), batch_size):
        batch       = tickers[i:i + batch_size]
        symbols_str = ",".join(batch)

        try:
            response = requests.get(
                f"{ALPACA_DATA_URL}/v2/stocks/bars",
                headers=HEADERS,
                params={
                    "symbols":   symbols_str,
                    "timeframe": "1Day",
                    "start":     start,
                    "end":       end,
                    "limit":     7,
                    "feed":      "iex",
                },
                timeout=15,
            )
            data = response.json().get("bars", {})

            for symbol, bars in data.items():
                if not bars:
                    continue
                df_bar     = pd.DataFrame(bars)
                avg_vol    = df_bar["v"].mean()
                last_close = df_bar["c"].iloc[-1]

                # فلتر السعر والحجم مبكراً — لا نحفظ إلا ما يجتاز الشرط
                if avg_vol >= MIN_AVG_VOLUME and last_close >= MIN_PRICE:
                    results.append({
                        "ticker":     symbol,
                        "avg_volume": avg_vol,
                        "last_price": last_close,
                    })

        except Exception as e:
            print(f"⚠️  خطأ في batch {i // batch_size + 1}: {e}")
            continue

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df = df.sort_values("avg_volume", ascending=False).reset_index(drop=True)
    return df




# ─────────────────────────────────────────
# فلتر EMA200 السريع
# ─────────────────────────────────────────

def _is_above_ema200(ticker: str) -> bool:
    """يتحقق إذا كان السهم فوق EMA200 اليومي."""
    end   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (datetime.utcnow() - timedelta(days=320)).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        response = requests.get(
            f"{ALPACA_DATA_URL}/v2/stocks/{ticker}/bars",
            headers=HEADERS,
            params={
                "timeframe": "1Day",
                "start":     start,
                "end":       end,
                "limit":     400,
                "feed":      "iex",
            },
            timeout=10,
        )
        bars = response.json().get("bars", [])
        if len(bars) < 200:
            return False

        closes = [b["c"] for b in bars]
        k   = 2 / (200 + 1)
        ema = closes[0]
        for c in closes[1:]:
            ema = c * k + ema * (1 - k)

        return closes[-1] > ema

    except Exception:
        return False

# ─────────────────────────────────────────
# 3. الدالة الرئيسية — أعلى 20 سهم سيولة
# ─────────────────────────────────────────

def get_daily_universe() -> list[str]:
    """
    الدالة الوحيدة التي يستدعيها main.py كل يوم.
    تُرجع قائمة بأعلى 20 سهم سيولة في السوق.

    الوقت المتوقع: 30 - 60 ثانية
    """
    print("🔍 جاري اختيار أسهم اليوم...")

    # الخطوة 1: جلب القائمة مع فلتر مبكر
    all_tickers = get_nasdaq_assets()
    if not all_tickers:
        print("❌ فشل جلب قائمة الأسهم")
        return []

    # الخطوة 2: جلب بيانات السيولة وتصفيتها
    df = get_volume_data(all_tickers)
    if df.empty:
        print("❌ لا توجد أسهم تجتاز شروط التصفية")
        return []

    # الخطوة 3: فلتر EMA200 — نحتفظ فقط بالأسهم في uptrend
    print("📈 فلترة الأسهم بـ EMA200 (فوق المتوسط = uptrend فقط)...")
    candidates = df.head(UNIVERSE_SIZE * 3)  # نأخذ 3× للتعويض عن المرفوضين
    filtered_rows = []

    for _, row in candidates.iterrows():
        if len(filtered_rows) >= UNIVERSE_SIZE:
            break
        ticker = row["ticker"]
        if _is_above_ema200(ticker):
            filtered_rows.append(row)
            print(f"   ✅ {ticker:6s} | فوق EMA200 | ${row['last_price']:>8.2f} | {int(row['avg_volume']):>12,}")
        else:
            print(f"   ❌ {ticker:6s} | تحت EMA200 — مُستبعد")

    if filtered_rows:
        top20 = pd.DataFrame(filtered_rows).reset_index(drop=True)
    else:
        print("⚠️  لا توجد أسهم فوق EMA200 — استخدام القائمة بدون فلتر")
        top20 = df.head(UNIVERSE_SIZE)

    selected = top20["ticker"].tolist()
    print(f"✅ تم اختيار {len(selected)} سهم لليوم")
    return selected
