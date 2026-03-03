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
    Request wrapper:
    - Retry ×3
    - Exponential backoff
    - Feed fallback if 403
    - Detailed debug logging
    """
    attempt = 0
    original_params = params.copy()

    while attempt < MAX_RETRIES:
        try:
            print(f"\n🔎 Attempt {attempt+1}/{MAX_RETRIES}")
            print(f"URL: {url}")
            print(f"Params: {params}")

            response = requests.get(
                url,
                headers=HEADERS,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            print(f"Status Code: {response.status_code}")
            print(f"Response Preview: {response.text[:300]}")

            # 403 → محاولة بدون feed
            if response.status_code == 403 and "feed" in params:
                print("⚠️  403 received — retrying without feed...")
                params = original_params.copy()
                params.pop("feed", None)
                attempt += 1
                time.sleep(RETRY_DELAY)
                continue

            if response.status_code == 200:
                return response

        except Exception as e:
            print(f"❌ Request exception: {e}")

        attempt += 1
        time.sleep(RETRY_DELAY * attempt)

    print(f"\n❌ Failed request after {MAX_RETRIES} attempts: {url}")
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

        time.sleep(0.4)  # rate protection

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    return df.sort_values("avg_volume", ascending=False).reset_index(drop=True)


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
# 5. Market Regime Detection
# ─────────────────────────────────────────

def is_bear_market(ema_map: dict, prices: dict) -> bool:
    if not ema_map or not prices:
        return False
    below = sum(1 for s, e in ema_map.items() if s in prices and prices[s] < e)
    ratio = below / max(len(ema_map), 1)
    print(f"Below EMA200 ratio: {ratio:.0%}")
    return ratio >= 0.40


# ─────────────────────────────────────────
# 6. Main Entry
# ─────────────────────────────────────────

def get_daily_universe() -> dict:

    print("🔍 Selecting daily universe...")

    assets = get_tradable_assets()
    if not assets:
        return {}

    df = get_volume_data(assets)
    if df.empty:
        return {}

    candidates   = df.head(UNIVERSE_SIZE * 4)
    symbols_list = candidates["symbol"].tolist()
    prices_map   = dict(zip(candidates["symbol"], candidates["last_price"]))

    ema_map = get_ema200_batch(symbols_list)
    skip_ema = len(ema_map) == 0

    bear = is_bear_market(ema_map, prices_map) if not skip_ema else False

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

    print(f"✅ Selected {len(result)} stocks")
    return result
