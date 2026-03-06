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
# 1. Safe Request Wrapper
# ─────────────────────────────────────────

def _safe_get(url: str, params: dict) -> requests.Response | None:
    """
    Production request wrapper:
    - Retry ×3
    - Exponential backoff
    - Feed fallback if 403
    - Clean logging (no JSON spam)
    """
    attempt = 0
    original_params = params.copy()

    while attempt < MAX_RETRIES:
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            # fallback إذا 403 وكان فيه feed
            if response.status_code == 403 and "feed" in params:
                params = original_params.copy()
                params.pop("feed", None)
                attempt += 1
                time.sleep(RETRY_DELAY)
                continue

            if response.status_code == 200:
                return response

        except Exception as e:
            print(f"Request error: {e}")

        attempt += 1
        time.sleep(RETRY_DELAY * attempt)

    print(f"❌ Failed request after {MAX_RETRIES} attempts: {url}")
    return None


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
                volume    = max(daily_vol, prev_vol)

                if not last_price or not volume:
                    continue

                if volume >= MIN_AVG_VOLUME and MIN_PRICE <= last_price <= MAX_PRICE:
                    info = asset_map.get(symbol, {})

                    # ── Volatility Ranking: نحسب حركة اليوم %
                    daily_open  = float(daily.get("o") or last_price)
                    daily_high  = float(daily.get("h") or last_price)
                    daily_low   = float(daily.get("l") or last_price)
                    prev_close  = float(prev.get("c") or last_price)

                    # نسبة التغيير عن إغلاق أمس
                    change_pct  = abs(last_price - prev_close) / prev_close if prev_close > 0 else 0

                    # نطاق اليوم كنسبة من السعر (Intraday Range)
                    intraday_range = (daily_high - daily_low) / last_price if last_price > 0 else 0

                    # مضاعف الحجم عن المعتاد (Volume Spike)
                    vol_spike = daily_vol / prev_vol if prev_vol > 0 else 1.0

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

    print("🔍 Selecting Mean Reversion universe...")

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
