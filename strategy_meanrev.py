# =============================================================
# strategy_meanrev.py — استراتيجية Mean Reversion
# تدعم LONG و SHORT — كل القيم مستوردة من config.py
# =============================================================

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_DATA_URL,
    S2_RSI_PERIOD,
    S2_RSI_OVERSOLD,
    S2_RSI_HIGH_QUALITY,
    S2_RSI_OVERBOUGHT,
    S2_VWAP_MIN_DEV,
    S2_ATR_MIN_PCT,
    S2_ATR_MAX_PCT,
    S2_TP1_R,
    S2_TP2_R,
    S2_STOP_ATR_MULT,
    LIQUIDITY_SWEEP_ENABLED,
    SHORT_ENABLED,
    SHORT_EXCHANGES,
    EMA_TREND,
    HISTORY_BARS,
)

HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
}

# ─────────────────────────────────────────
# نموذج إشارة التداول
# ─────────────────────────────────────────

@dataclass
class MeanRevSignal:
    ticker:         str
    side:           str
    has_signal:     bool
    reason:         str

    entry_price:    float = 0.0
    stop_loss:      float = 0.0
    target_tp1:     float = 0.0
    target_tp2:     float = 0.0
    trail_step:     float = 0.0

    rsi:            float = 0.0
    atr:            float = 0.0
    atr_pct:        float = 0.0
    vwap:           float = 0.0
    ema200:         float = 0.0
    signal_quality: str   = "standard"
    liquidity_sweep: bool = False

# ─────────────────────────────────────────
# 1. جلب البيانات
# ─────────────────────────────────────────

def fetch_bars(ticker: str, days: int = HISTORY_BARS) -> pd.DataFrame:

    end   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (datetime.utcnow() - timedelta(days=days + 30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        response = requests.get(
            f"{ALPACA_DATA_URL}/v2/stocks/{ticker}/bars",
            headers=HEADERS,
            params={
                "timeframe": "1Day",
                "start":     start,
                "end":       end,
                "limit":     days,
                "feed":      "iex",
            },
            timeout=15,
        )

        bars = response.json().get("bars", [])
        if not bars:
            return pd.DataFrame()

        df = pd.DataFrame(bars)
        df = df.rename(columns={
            "o": "open",
            "h": "high",
            "l": "low",
            "c": "close",
            "v": "volume",
        })

        df["time"] = pd.to_datetime(df["t"])
        return df.sort_values("time").reset_index(drop=True)

    except Exception as e:
        print(f"❌ خطأ في جلب بيانات {ticker}: {e}")
        return pd.DataFrame()

# ─────────────────────────────────────────
# بقية الدوال (_analyze_long و _analyze_short) تبقى كما هي عندك
# ─────────────────────────────────────────


# ─────────────────────────────────────────
# 8. الدالة الرئيسية (التصحيح هنا فقط)
# ─────────────────────────────────────────

def analyze(
    ticker:    str,
    ema_above: bool = True,
    exchange:  str  = "NASDAQ",
    ema200:    float = 0.0,
) -> MeanRevSignal:

    df = fetch_bars(ticker)

    MIN_REQUIRED_BARS = max(50, S2_RSI_PERIOD * 3)

    if df.empty or len(df) < MIN_REQUIRED_BARS:
        return MeanRevSignal(
            ticker=ticker,
            side="long",
            has_signal=False,
            reason="بيانات غير كافية للتحليل",
        )

    # محاولة LONG أولاً
    long_signal = _analyze_long(ticker, df, ema_above, ema200)
    if long_signal.has_signal:
        return long_signal

    # محاولة SHORT إذا لم تنجح LONG
    short_signal = _analyze_short(ticker, df, exchange, ema200)
    return short_signal
