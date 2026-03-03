# =============================================================
# universe.py — اختيار أفضل الأسهم ديناميكياً كل يوم
# يفحص حتى 500 مرشح، يحسب EMA200 بـ batch واحد
# يستخدم Snapshots API لجلب السيولة (متوافق مع IEX free tier)
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


# -----------------------------------------
# 1. جلب قائمة الأسهم
# -----------------------------------------

def get_tradable_assets() -> list:
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
            print(f"Error fetching {exchange} assets: {e}")

    all_assets = all_assets[:UNIVERSE_MAX_CANDIDATES]
    print(f"   بعد الفلتر المبكر: {len(all_assets)} سهم")
    return all_assets


# -----------------------------------------
# 2. جلب بيانات السيولة والسعر عبر Snapshots
# Snapshots يعمل مع IEX بدون اشتراك مدفوع
# -----------------------------------------

def get_volume_data(assets: list) -> pd.DataFrame:
    """
    يستخدم /v2/stocks/snapshots بدلاً من /bars
    لأن bars مع feed=iex يُرجع 403 في حسابات Paper Trading.
    Snapshots تُرجع dailyBar و prevDailyBar و latestTrade
    وهي كافية لحساب السيولة والسعر.
    """
    tickers    = [a["symbol"] for a in assets]
    asset_map  = {a["symbol"]: a for a in assets}
    results    = []
    batch_size = 100

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        try:
            response = requests.get(
                f"{ALPACA_DATA_URL}/v2/stocks/snapshots",
                headers=HEADERS,
                params={
                    "symbols": ",".join(batch),
                    "feed":    "iex",
                },
                timeout=15,
            )

            # طباعة debug للـ batch الأول فقط
            if i == 0:
                print(f"   [DEBUG] snapshots status={response.status_code}")
                if response.status_code != 200:
                    print(f"   [DEBUG] error body: {response.text[:300]}")

            if response.status_code != 200:
                continue

            data = response.json()

            for symbol, snap in data.items():
                try:
                    # نحاول dailyBar أولاً ثم prevDailyBar ثم latestTrade
                    daily = snap.get("dailyBar") or {}
                    prev  = snap.get("prevDailyBar") or {}
                    trade = snap.get("latestTrade") or {}

                    last_price = float(
                        daily.get("c") or
                        prev.get("c") or
                        trade.get("p") or 0
                    )
                    volume = float(
                        daily.get("v") or
                        prev.get("v") or 0
                    )

                    if not last_price or not volume:
                        continue

                    if volume >= MIN_AVG_VOLUME and MIN_PRICE <= last_price <= MAX_PRICE:
                        info = asset_map.get(symbol, {})
                        results.append({
                            "symbol":         symbol,
                            "exchange":       info.get("exchange", ""),
                            "easy_to_borrow": info.get("easy_to_borrow", True),
                            "avg_volume":     volume,
                            "last_price":     last_price,
                        })
                except Exception:
                    continue

        except Exception as e:
            print(f"   Batch {i // batch_size + 1} error: {e}")

    print(f"   [DEBUG] إجمالي الأسهم التي اجتازت الفلتر: {len(results)}")

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    return df.sort_values("avg_volume", ascending=False).reset_index(drop=True)


# -----------------------------------------
# 3. حساب EMA200 عبر Daily Bars
# -----------------------------------------

def get_ema200_batch(symbols: list) -> dict:
    """
    يجلب الشموع اليومية لحساب EMA200.
    يستخدم iex feed مع تواريخ محددة.
    """
    end   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (datetime.utcnow() - timedelta(days=320)).strftime("%Y-%m-%dT%H:%M:%SZ")

    ema_map    = {}
    batch_size = 50

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

            if response.status_code != 200:
                if i == 0:
                    print(f"   [DEBUG] EMA200 bars status={response.status_code}: {response.text[:200]}")
                continue

            data = response.json().get("bars", {})

            for symbol, bars in data.items():
                if len(bars) < EMA_TREND:
                    continue
                closes = [b["c"] for b in bars]
                k      = 2 / (EMA_TREND + 1)
                ema    = closes[0]
                for c in closes[1:]:
                    ema = c * k + ema * (1 - k)
                ema_map[symbol] = round(ema, 4)

        except Exception as e:
            print(f"   EMA200 batch {i // batch_size + 1} error: {e}")

    return ema_map


# -----------------------------------------
# 4. كشف السوق الهابط
# -----------------------------------------

def is_bear_market(ema_map: dict, prices: dict) -> bool:
    if not ema_map or not prices:
        return False
    below = sum(1 for sym, ema in ema_map.items() if sym in prices and prices[sym] < ema)
    ratio = below / max(len(ema_map), 1)
    print(f"   نسبة الأسهم تحت EMA200: {ratio:.0%}")
    return ratio >= 0.40


# -----------------------------------------
# 5. الدالة الرئيسية
# -----------------------------------------

def get_daily_universe() -> dict:
    print("\n🔍 جاري اختيار أسهم اليوم...")
    print("─" * 55)

    # الخطوة 1: جلب الأسهم
    assets = get_tradable_assets()
    if not assets:
        print("فشل جلب قائمة الأسهم")
        return {}

    # الخطوة 2: فلترة السيولة عبر Snapshots
    df = get_volume_data(assets)
    if df.empty:
        print("لا توجد أسهم تجتاز شروط السيولة والسعر")
        return {}

    candidates   = df.head(UNIVERSE_SIZE * 4)
    symbols_list = candidates["symbol"].tolist()
    prices_map   = dict(zip(candidates["symbol"], candidates["last_price"]))
    print(f"   مرشحون للفلترة النهائية: {len(symbols_list)} سهم")

    # الخطوة 3: EMA200
    print("📈 حساب EMA200...")
    ema_map = get_ema200_batch(symbols_list)
    print(f"   تم حساب EMA200 لـ {len(ema_map)} سهم")

    # إذا فشل EMA200 نكمل بدونه (نقبل كل الأسهم)
    skip_ema = len(ema_map) == 0
    if skip_ema:
        print("   ⚠️  فشل جلب EMA200 — سيتم قبول الأسهم بدون شرط EMA")

    # الخطوة 4: كشف السوق الهابط
    bear = is_bear_market(ema_map, prices_map) if not skip_ema else False

    # الخطوة 5: بناء القائمة النهائية
    result = {}
    for _, row in candidates.iterrows():
        if len(result) >= UNIVERSE_SIZE:
            break

        symbol = row["symbol"]
        price  = row["last_price"]
        ema200 = ema_map.get(symbol)

        if ema200 is None and not skip_ema:
            continue

        ema_above = (price > ema200) if ema200 else True

        if not ema_above and not bear and not skip_ema:
            continue

        result[symbol] = {
            "ema_above":      ema_above,
            "exchange":       row["exchange"],
            "easy_to_borrow": row["easy_to_borrow"],
            "last_price":     price,
            "avg_volume":     int(row["avg_volume"]),
            "ema200":         ema200 or 0.0,
        }

    print("─" * 55)
    print(f"✅ تم اختيار {len(result)} سهم لليوم | {'هابط ⚠️' if bear else 'صاعد 📈'}")
    return result
