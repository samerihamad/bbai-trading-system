# =============================================================
# universe.py — اختيار أفضل الأسهم ديناميكياً كل يوم
# يفحص حتى 500 مرشح، يحسب EMA200 بـ batch واحد
# يدعم Fallback ذكي في السوق الهابط
# =============================================================

import requests
import pandas as pd
from datetime import datetime, timedelta

from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_BASE_URL,
    ALPACA_DATA_URL,
    UNIVERSE_SIZE,
    UNIVERSE_MAX_CANDIDATES,
    MIN_AVG_VOLUME,
    MIN_PRICE,
    MAX_PRICE,
    EMA_TREND,
)

HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
}


# ─────────────────────────────────────────
# 1. جلب قائمة الأسهم مع فلتر مبكر
# ─────────────────────────────────────────

def get_tradable_assets() -> list[dict]:
    """
    يجلب أسهم NASDAQ و NYSE النشطة القابلة للتداول.
    الفلتر المبكر يقلص القائمة قبل أي طلب بيانات إضافي.

    يُرجع list من dict: {symbol, exchange, easy_to_borrow}
    """
    all_assets = []

    for exchange in ["NASDAQ", "NYSE"]:
        try:
            response = requests.get(
                f"{ALPACA_BASE_URL}/v2/assets",
                headers=HEADERS,
                params={
                    "status":      "active",
                    "exchange":    exchange,
                    "asset_class": "us_equity",
                },
                timeout=15,
            )
            assets = response.json()

            for a in assets:
                if (
                    a.get("tradable")
                    and a.get("status") == "active"
                    and len(a["symbol"]) <= 5
                    and "." not in a["symbol"]
                    and "/" not in a["symbol"]
                ):
                    all_assets.append({
                        "symbol":         a["symbol"],
                        "exchange":       a["exchange"],
                        "easy_to_borrow": a.get("easy_to_borrow", True),
                    })

        except Exception as e:
            print(f"❌ خطأ في جلب أسهم {exchange}: {e}")

    all_assets = all_assets[:UNIVERSE_MAX_CANDIDATES]
    print(f"   بعد الفلتر المبكر: {len(all_assets)} سهم")
    return all_assets


# ─────────────────────────────────────────
# 2. جلب بيانات السيولة والسعر
# ─────────────────────────────────────────

def get_volume_data(assets: list[dict]) -> pd.DataFrame:
    """
    يجلب متوسط حجم التداول وآخر سعر إغلاق لكل سهم.
    يعمل بـ batches حجم كل منها 100 سهم ويُطبّق فلتر
    السعر والحجم مباشرة لتوفير الذاكرة.
    """
    tickers   = [a["symbol"] for a in assets]
    asset_map = {a["symbol"]: a for a in assets}

    end   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    results    = []
    batch_size = 100

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]

        try:
            response = requests.get(
                f"{ALPACA_DATA_URL}/v2/stocks/bars",
                headers=HEADERS,
                params={
                    "symbols":   ",".join(batch),
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

                # نحفظ فقط ما يجتاز فلتر السعر والحجم
                if avg_vol >= MIN_AVG_VOLUME and MIN_PRICE <= last_close <= MAX_PRICE:
                    info = asset_map.get(symbol, {})
                    results.append({
                        "symbol":         symbol,
                        "exchange":       info.get("exchange", ""),
                        "easy_to_borrow": info.get("easy_to_borrow", True),
                        "avg_volume":     avg_vol,
                        "last_price":     last_close,
                    })

        except Exception as e:
            print(f"⚠️  خطأ في batch {i // batch_size + 1}: {e}")

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    return df.sort_values("avg_volume", ascending=False).reset_index(drop=True)


# ─────────────────────────────────────────
# 3. حساب EMA200 بـ batch واحد (الأسرع)
# ─────────────────────────────────────────

def get_ema200_batch(symbols: list[str]) -> dict[str, float]:
    """
    يجلب 210 شمعة يومية لكل الأسهم في طلبات batch
    ثم يحسب EMA200 لكل سهم محلياً بدون مكتبات إضافية.

    أسرع بكثير من طلب منفصل لكل سهم.
    يُرجع dict: {symbol: ema200_value}
    """
    end   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (datetime.utcnow() - timedelta(days=320)).strftime("%Y-%m-%dT%H:%M:%SZ")

    ema_map    = {}
    batch_size = 50  # أصغر لأن كل سهم يحمل 210 شمعة

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]

        try:
            response = requests.get(
                f"{ALPACA_DATA_URL}/v2/stocks/bars",
                headers=HEADERS,
                params={
                    "symbols":   ",".join(batch),
                    "timeframe": "1Day",
                    "start":     start,
                    "end":       end,
                    "limit":     EMA_TREND + 10,
                    "feed":      "iex",
                },
                timeout=20,
            )
            data = response.json().get("bars", {})

            for symbol, bars in data.items():
                if len(bars) < EMA_TREND:
                    continue

                # حساب EMA يدوياً — بدون pandas
                closes = [b["c"] for b in bars]
                k      = 2 / (EMA_TREND + 1)
                ema    = closes[0]
                for c in closes[1:]:
                    ema = c * k + ema * (1 - k)

                ema_map[symbol] = round(ema, 4)

        except Exception as e:
            print(f"⚠️  خطأ في batch EMA200 {i // batch_size + 1}: {e}")

    return ema_map


# ─────────────────────────────────────────
# 4. كشف السوق الهابط (Bear Market)
# ─────────────────────────────────────────

def is_bear_market(ema_map: dict, prices: dict) -> bool:
    """
    يكشف إذا كنا في سوق هابط عام.
    المنطق: إذا كان أكثر من 40% من الأسهم تحت EMA200 → سوق هابط.

    في السوق الهابط يُفعَّل Fallback:
    - تخفيف شرط EMA200 للـ LONG (نقبل الأسهم تحت EMA200)
    - زيادة الاهتمام بإشارات SHORT تلقائياً
    """
    if not ema_map or not prices:
        return False

    below = sum(
        1 for sym, ema in ema_map.items()
        if sym in prices and prices[sym] < ema
    )
    ratio = below / max(len(ema_map), 1)
    print(f"   نسبة الأسهم تحت EMA200: {ratio:.0%}")
    return ratio >= 0.40


# ─────────────────────────────────────────
# 5. الدالة الرئيسية
# ─────────────────────────────────────────

def get_daily_universe() -> dict:
    """
    الدالة الوحيدة التي يستدعيها main.py كل يوم.

    الخطوات بالترتيب:
    1. جلب قائمة NASDAQ + NYSE مع فلتر مبكر (≤ 500 سهم)
    2. فلترة السيولة والسعر بـ batches
    3. حساب EMA200 بـ batch واحد (أسرع طريقة)
    4. كشف السوق الهابط وتطبيق Fallback ذكي
    5. إرجاع dict: {symbol: {ema_above, exchange, easy_to_borrow, ...}}

    الوقت المتوقع: 45 - 90 ثانية
    """
    print("\n🔍 جاري اختيار أسهم اليوم...")
    print("─" * 55)

    # الخطوة 1: جلب الأسهم
    assets = get_tradable_assets()
    if not assets:
        print("❌ فشل جلب قائمة الأسهم")
        return {}

    # الخطوة 2: فلترة السيولة
    df = get_volume_data(assets)
    if df.empty:
        print("❌ لا توجد أسهم تجتاز شروط السيولة والسعر")
        return {}

    # نأخذ ضعف الحجم المطلوب كمرشحين للمرحلة التالية
    candidates   = df.head(UNIVERSE_SIZE * 4)
    symbols_list = candidates["symbol"].tolist()
    prices_map   = dict(zip(candidates["symbol"], candidates["last_price"]))

    print(f"   مرشحون للفلترة النهائية: {len(symbols_list)} سهم")

    # الخطوة 3: EMA200 بـ batch واحد
    print("📈 حساب EMA200 بـ batch واحد...")
    ema_map = get_ema200_batch(symbols_list)
    print(f"   تم حساب EMA200 لـ {len(ema_map)} سهم")

    # الخطوة 4: كشف السوق الهابط
    bear = is_bear_market(ema_map, prices_map)
    if bear:
        print("⚠️  سوق هابط — تطبيق Fallback: قبول أسهم تحت EMA200")
    else:
        print("✅ سوق صاعد — تطبيق الشروط الكاملة")

    # الخطوة 5: بناء القائمة النهائية
    result = {}

    for _, row in candidates.iterrows():
        if len(result) >= UNIVERSE_SIZE:
            break

        symbol   = row["symbol"]
        price    = row["last_price"]
        ema200   = ema_map.get(symbol)

        if ema200 is None:
            continue

        ema_above = price > ema200

        # في السوق الصاعد: نشترط فوق EMA200 للـ LONG
        # في السوق الهابط: نقبل الكل ونُعلّم ema_above=False للحذر
        if not ema_above and not bear:
            print(f"   ❌ {symbol:6s} | تحت EMA200 — مُستبعد")
            continue

        result[symbol] = {
            "ema_above":      ema_above,
            "exchange":       row["exchange"],
            "easy_to_borrow": row["easy_to_borrow"],
            "last_price":     price,
            "avg_volume":     int(row["avg_volume"]),
            "ema200":         ema200,
        }

        flag = "فوق EMA200 ✅" if ema_above else "تحت EMA200 ⚠️ (bear fallback)"
        print(f"   ✅ {symbol:6s} | {flag} | ${price:>8.2f} | {int(row['avg_volume']):>12,}")

    print("─" * 55)
    print(f"✅ تم اختيار {len(result)} سهم لليوم | سوق {'هابط ⚠️' if bear else 'صاعد 📈'}")
    return result
