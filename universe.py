# =============================================================
# universe.py — Production Grade
# Robust Data Fetching + Retry + Feed Fallback + Rate Protection
# =============================================================

import requests
import pandas as pd
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any

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

REQUEST_TIMEOUT = 20
MAX_RETRIES     = 3
RETRY_DELAY     = 1.5

# ─────────────────────────────────────────
# Feed State — يتذكر أي feed نجح لهذه الجلسة
# ─────────────────────────────────────────
# القيم الممكنة: "iex" | "sip" | None (بلا feed)
# يُضبط تلقائياً عند أول طلب ناجح ويُستخدم في كل الطلبات التالية
_working_feed: str | None = "iex"   # نبدأ بـ iex كافتراض


# ─────────────────────────────────────────
# 1. Safe Request Wrapper
# ─────────────────────────────────────────

def _safe_get(url: str, params: dict) -> requests.Response | None:
    """
    Production request wrapper:
    - يتذكر أي feed نجح (_working_feed) ويُعيد استخدامه
    - عند 403: يجرب بدون feed مرة واحدة ويحفظ النتيجة
    - Retry ×3 مع exponential backoff
    - لا يُكرر فحص feed في كل استدعاء — يُقرر مرة واحدة للجلسة كلها
    """
    global _working_feed

    attempt        = 0
    tried_no_feed  = False   # منع تكرار محاولة "بلا feed" في نفس الاستدعاء

    # ── استخدم الـ feed المعروف بدلاً من ما أُرسل في params
    working_params = params.copy()
    if "feed" in working_params:
        if _working_feed is None:
            # نعرف مسبقاً أن feed محجوب — احذفه فوراً
            working_params.pop("feed", None)
        else:
            # استخدم الـ feed الناجح (iex أو sip)
            working_params["feed"] = _working_feed

    while attempt < MAX_RETRIES:
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                params=working_params,
                timeout=REQUEST_TIMEOUT,
            )

            # ── 403: feed محجوب على هذا الحساب
            if response.status_code == 403 and "feed" in working_params and not tried_no_feed:
                old_feed = working_params.get("feed", "iex")
                working_params.pop("feed", None)
                tried_no_feed = True
                print(f"⚠️  Feed '{old_feed}' محجوب (403) — التبديل لبيانات بدون feed تلقائياً")
                time.sleep(RETRY_DELAY)
                continue

            if response.status_code == 200:
                # ── تسجيل الـ feed الناجح للجلسة كلها
                if "feed" in working_params and _working_feed != working_params["feed"]:
                    _working_feed = working_params["feed"]
                    print(f"✅ Feed '{_working_feed}' نشط ومحفوظ للجلسة")
                elif "feed" not in working_params and tried_no_feed and _working_feed is not None:
                    _working_feed = None
                    print("✅ Feed مُعطَّل — الجلسة ستعمل بدون feed (بيانات افتراضية)")
                return response

            # ── أخطاء أخرى (429 rate limit, 5xx server)
            if response.status_code == 429:
                wait = RETRY_DELAY * (attempt + 2)
                print(f"⏳ Rate limit (429) — انتظار {wait:.1f}s")
                time.sleep(wait)
            elif response.status_code >= 500:
                print(f"⚠️  Server error {response.status_code} — محاولة {attempt + 1}/{MAX_RETRIES}")
                time.sleep(RETRY_DELAY * attempt)

        except Exception as e:
            print(f"Request error: {e}")
            time.sleep(RETRY_DELAY * attempt)

        attempt += 1

    print(f"❌ Failed request after {MAX_RETRIES} attempts: {url}")
    return None


def get_active_feed() -> str:
    """يُرجع اسم الـ feed النشط حالياً — للـ logging والـ debugging."""
    return _working_feed if _working_feed else "default (no feed)"


# ─────────────────────────────────────────
# 2. Tradable Assets
# ─────────────────────────────────────────

def get_tradable_assets() -> List[Dict[str, Any]]:
    all_assets = []

    for exchange in ["NASDAQ", "NYSE"]:
        response = _safe_get(
            f"{ALPACA_BASE_URL}/v2/assets",
            {
                "status":      "active",
                "exchange":    exchange,
                "asset_class": "us_equity",
            },
        )

        if not response:
            continue

        try:
            assets = response.json()
        except Exception:
            continue

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

    all_assets = all_assets[:UNIVERSE_MAX_CANDIDATES]
    print(f"Filtered assets: {len(all_assets)}")
    return all_assets


# ─────────────────────────────────────────
# 3. Snapshots Liquidity Filter
# ─────────────────────────────────────────

def get_volume_data(assets: list) -> pd.DataFrame:

    tickers    = [a["symbol"] for a in assets]
    asset_map  = {a["symbol"]: a for a in assets}
    results    = []
    batch_size = 200  # production optimized

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]

        response = _safe_get(
            f"{ALPACA_DATA_URL}/v2/stocks/snapshots",
                {
                    "symbols": ",".join(batch),
                    "feed": "iex",
                }
        )

        if not response:
            continue

        try:
            data = response.json()
        except Exception:
            continue

        for symbol, snap in data.items():
            try:
                daily = snap.get("dailyBar") or {}
                prev  = snap.get("prevDailyBar") or {}
                trade = snap.get("latestTrade") or {}

                last_price = float(
                    daily.get("c") or
                    prev.get("c") or
                    trade.get("p") or 0
                )

                daily_vol = float(daily.get("v") or 0)
                prev_vol  = float(prev.get("v") or 0)

                # في بداية اليوم daily_vol يكون صغيراً جداً أو صفر
                # نستخدم prev_vol كمرجع للسيولة الحقيقية
                volume = prev_vol if prev_vol > 0 else daily_vol

                if not last_price or not volume:
                    continue

                if volume >= MIN_AVG_VOLUME and MIN_PRICE <= last_price <= MAX_PRICE:
                    info = asset_map.get(symbol, {})

                    # ── Volatility Ranking: نحسب حركة اليوم %
                    daily_open  = float(daily.get("o") or last_price)
                    daily_high  = float(daily.get("h") or last_price)
                    daily_low   = float(daily.get("l") or last_price)
                    prev_close  = float(prev.get("c") or last_price)

                    # ── نسبة التغيير عن إغلاق أمس
                    change_pct  = abs(last_price - prev_close) / prev_close if prev_close > 0 else 0

                    # ── نطاق اليوم كنسبة من السعر (Intraday Range)
                    intraday_range = (daily_high - daily_low) / last_price if last_price > 0 else 0

                    # ── Volume Spike — نسبة بسيطة: حجم اليوم ÷ حجم أمس
                    # إذا daily_vol صغير (بداية اليوم) → vol_spike صغير طبيعياً
                    # لا نحتاج projection — الترتيب النسبي بين الأسهم هو المهم
                    if prev_vol > 0 and daily_vol > 0:
                        vol_spike = daily_vol / prev_vol
                    else:
                        vol_spike = 1.0

                    # Score الإجمالي للحركة — كلما كان أعلى كلما كان السهم أكثر حركة
                    volatility_score = round(
                        (change_pct * 0.4) +
                        (intraday_range * 0.4) +
                        (min(vol_spike, 5.0) / 5.0 * 0.2),  # نحدد vol_spike بـ 5x
                        6
                    )

                    results.append({
                        "symbol":           symbol,
                        "exchange":         info.get("exchange", ""),
                        "easy_to_borrow":   info.get("easy_to_borrow", True),
                        "avg_volume":       volume,
                        "last_price":       last_price,
                        "change_pct":       round(change_pct, 4),
                        "intraday_range":   round(intraday_range, 4),
                        "vol_spike":        round(vol_spike, 2),
                        "volatility_score": volatility_score,
                    })

            except Exception:
                continue

        time.sleep(0.4)  # rate protection

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    # ترتيب بـ volatility_score أولاً — الأكثر حركة في المقدمة
    # ثم avg_volume كمعيار ثانوي للسيولة
    df = df.sort_values(
        ["volatility_score", "avg_volume"],
        ascending=[False, False]
    ).reset_index(drop=True)
    return df


# ─────────────────────────────────────────
# 4. EMA200 Batch Calculation
# ─────────────────────────────────────────

def get_ema200_batch(symbols: list) -> dict:

    end   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (datetime.utcnow() - timedelta(days=320)).strftime("%Y-%m-%dT%H:%M:%SZ")

    ema_map    = {}
    batch_size = 50

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]

        response = _safe_get(
            f"{ALPACA_DATA_URL}/v2/stocks/bars",
            {
                "symbols":     ",".join(batch),
                "timeframe":   "1Day",
                "start":       start,
                "end":         end,
                "limit":       EMA_TREND + 10,
                "feed":        "iex",
                "adjustment":  "raw",
            },
        )

        if not response:
            continue

        try:
            data = response.json().get("bars", {})
        except Exception:
            continue

        for symbol, bars in data.items():
            if len(bars) < EMA_TREND:
                continue

            closes = [b["c"] for b in bars]
            k      = 2 / (EMA_TREND + 1)
            ema    = closes[0]

            for c in closes[1:]:
                ema = c * k + ema * (1 - k)

            ema_map[symbol] = round(ema, 4)

        time.sleep(0.4)

    return ema_map


# ─────────────────────────────────────────
# Mean Reversion Optimized Universe
# ─────────────────────────────────────────

def get_daily_universe() -> dict:

    print(f"🔍 Selecting Mean Reversion universe... [feed={get_active_feed()}]")

    assets = get_tradable_assets()
    if not assets:
        return {}

    df = get_volume_data(assets)
    if df.empty:
        print("❌ No stocks passed volume filter")
        return {}

    print(f"After volume filter: {len(df)}")

    # نختار أعلى سيولة فقط
    candidates = df.head(UNIVERSE_SIZE)

    symbols_list = candidates["symbol"].tolist()

    # نحسب EMA فقط كمعلومة — لا نفلتر بها
    ema_map = get_ema200_batch(symbols_list)

    result = {}

    for _, row in candidates.iterrows():
        symbol = row["symbol"]
        price  = row["last_price"]
        ema200 = ema_map.get(symbol, 0.0)

        result[symbol] = {
            "ema_above":        price > ema200 if ema200 else True,
            "exchange":         row["exchange"],
            "easy_to_borrow":   row["easy_to_borrow"],
            "last_price":       price,
            "avg_volume":       int(row["avg_volume"]),
            "ema200":           ema200,
            "change_pct":       row.get("change_pct", 0.0),
            "intraday_range":   row.get("intraday_range", 0.0),
            "vol_spike":        row.get("vol_spike", 1.0),
            "volatility_score": row.get("volatility_score", 0.0),
        }

    # طباعة أعلى 10 أسهم حركةً
    top10 = sorted(result.items(), key=lambda x: x[1].get("volatility_score", 0), reverse=True)[:10]
    print("🔥 Top 10 Movers:")
    for sym, info in top10:
        print(f"   {sym:6s} | Δ={info['change_pct']:.1%} | Range={info['intraday_range']:.1%} | VolSpike={info['vol_spike']:.1f}x")

    print(f"✅ Selected {len(result)} stocks (sorted by volatility)")

    return result
