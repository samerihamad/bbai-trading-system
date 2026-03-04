# =============================================================
# strategy_meanrev.py — استراتيجية Mean Reversion المتقدمة
# الإصدار: 2.0 (تحسين جودة الـ SHORT و Liquidity Sweep)
# =============================================================

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass
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
    HISTORY_BARS,
)

HEADERS = {
    "APCA-API-KEY-ID":      ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
}

# ─────────────────────────────────────────
# نموذج إشارة التداول
# ─────────────────────────────────────────

@dataclass
class MeanRevSignal:
    ticker:          str
    side:            str
    has_signal:      bool
    reason:          str
    entry_price:     float = 0.0
    stop_loss:       float = 0.0
    target_tp1:      float = 0.0
    target_tp2:      float = 0.0
    trail_step:      float = 0.0
    rsi:             float = 0.0
    atr:             float = 0.0
    atr_pct:         float = 0.0
    vwap:            float = 0.0
    ema200:          float = 0.0
    signal_quality:  str   = "standard"
    liquidity_sweep: bool  = False

# ─────────────────────────────────────────
# 1. جلب البيانات وحساب المؤشرات
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
        if not bars: return pd.DataFrame()

        df = pd.DataFrame(bars)
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df["time"] = pd.to_datetime(df["t"])
        return df.sort_values("time").reset_index(drop=True)
    except Exception as e:
        print(f"❌ خطأ في جلب بيانات {ticker}: {e}")
        return pd.DataFrame()

def calc_rsi(closes: pd.Series, period: int = S2_RSI_PERIOD) -> float:
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    val   = rsi.iloc[-1]
    return round(float(val) if not pd.isna(val) else 50.0, 2)

def calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return round(float(atr) if not pd.isna(atr) else 0.0, 4)

def calc_vwap(df: pd.DataFrame) -> float:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vwap    = (typical * df["volume"]).sum() / df["volume"].sum()
    return round(float(vwap) if not pd.isna(vwap) else 0.0, 4)

def update_trailing_stop(current_price: float, current_stop: float, trail_step: float) -> float:
    new_stop = current_price - trail_step
    return round(max(new_stop, current_stop), 4)

def refresh_allowed_tickers(candidate_tickers: list):
    print(f"🔄 تحديث قائمة الأسهم المسموح بها: {len(candidate_tickers)} سهم")

# ─────────────────────────────────────────
# 2. منطق Liquidity Sweep المحسن (VSA)
# ─────────────────────────────────────────

def check_liquidity_sweep_long(df: pd.DataFrame) -> bool:
    if not LIQUIDITY_SWEEP_ENABLED or len(df) < 20: return False
    prev_low, curr_low, curr_close = df["low"].iloc[-2], df["low"].iloc[-1], df["close"].iloc[-1]
    return curr_low < prev_low and curr_close > prev_low

def check_liquidity_sweep_short(df: pd.DataFrame) -> bool:
    """تحقق من اختراق القمة الكاذب مع تأكيد حجم التداول."""
    if not LIQUIDITY_SWEEP_ENABLED or len(df) < 20: return False

    curr_high  = df["high"].iloc[-1]
    curr_close = df["close"].iloc[-1]
    curr_vol   = df["volume"].iloc[-1]
    prev_high  = df["high"].iloc[-2]
    avg_volume = df["volume"].iloc[-21:-1].mean()

    is_sweep       = curr_high > prev_high and curr_close < prev_high
    is_volume_high = curr_vol > (avg_volume * 1.2)

    return is_sweep and is_volume_high

# ─────────────────────────────────────────
# 3. التحليل الرئيسي - LONG
# ─────────────────────────────────────────

def _analyze_long(ticker: str, df: pd.DataFrame, ema_above: bool, ema200: float) -> MeanRevSignal:
    price   = df["close"].iloc[-1]
    rsi     = calc_rsi(df["close"])
    atr     = calc_atr(df)
    vwap    = calc_vwap(df)
    atr_pct = atr / price if price > 0 else 0

    def no_signal(reason: str):
        return MeanRevSignal(ticker=ticker, side="long", has_signal=False, reason=reason,
                             rsi=rsi, atr=atr, atr_pct=atr_pct, vwap=vwap, ema200=ema200)

    if not (S2_ATR_MIN_PCT <= atr_pct <= S2_ATR_MAX_PCT):
        return no_signal(f"ATR خارج النطاق: {atr_pct:.1%}")
    if rsi >= S2_RSI_OVERSOLD:
        return no_signal(f"RSI={rsi:.1f} (ليس تشبع)")
    if vwap > 0:
        vwap_dev = (price - vwap) / vwap
        if vwap_dev > S2_VWAP_MIN_DEV:
            return no_signal(f"السعر بعيد عن VWAP: {vwap_dev:.1%}")

    sweep_long      = check_liquidity_sweep_long(df)
    is_high_quality = rsi < S2_RSI_HIGH_QUALITY or sweep_long
    quality         = "high" if is_high_quality else "standard"

    stop  = round(price - atr * S2_STOP_ATR_MULT, 2)
    tp1   = round(price + atr * S2_TP1_R, 2)
    tp2   = round(price + atr * S2_TP2_R, 2)
    trail = round(atr * 0.5, 4)

    return MeanRevSignal(ticker=ticker, side="long", has_signal=True, reason="LONG ✅",
                         entry_price=price, stop_loss=stop, target_tp1=tp1, target_tp2=tp2,
                         trail_step=trail, rsi=rsi, atr=atr, atr_pct=atr_pct, vwap=vwap,
                         ema200=ema200, signal_quality=quality, liquidity_sweep=sweep_long)

# ─────────────────────────────────────────
# 4. التحليل الرئيسي المحسن - SHORT
# ─────────────────────────────────────────

# قيمة RSI المستقلة لجودة إشارة SHORT (تشبع شرائي شديد)
S2_RSI_HIGH_QUALITY_SHORT = 80  # RSI فوق 80 = جودة عالية للـ SHORT

def _analyze_short(ticker: str, df: pd.DataFrame, exchange: str, ema200: float) -> MeanRevSignal:
    price      = df["close"].iloc[-1]
    prev_close = df["close"].iloc[-2]
    open_price = df["open"].iloc[-1]
    high_price = df["high"].iloc[-1]

    rsi     = calc_rsi(df["close"])
    atr     = calc_atr(df)
    vwap    = calc_vwap(df)
    atr_pct = atr / price if price > 0 else 0

    def no_signal(reason: str):
        return MeanRevSignal(ticker=ticker, side="short", has_signal=False, reason=reason,
                             rsi=rsi, atr=atr, atr_pct=atr_pct, vwap=vwap, ema200=ema200)

    # ── الفلاتر الأساسية
    if not SHORT_ENABLED:
        return no_signal("SHORT غير مفعّل")
    if exchange not in SHORT_EXCHANGES:
        return no_signal("بورصة غير مدعومة")
    if not (S2_ATR_MIN_PCT <= atr_pct <= S2_ATR_MAX_PCT):
        return no_signal(f"ATR غير مناسب: {atr_pct:.1%}")

    # ── 1. فلتر RSI إلزامي
    if rsi <= S2_RSI_OVERBOUGHT:
        return no_signal(f"RSI={rsi:.1f} (ليس تشبع شرائي)")

    # ── 2. فلتر انحراف VWAP (1.5% كحد أدنى)
    vwap_dev = (price - vwap) / vwap if vwap > 0 else 0

    # ── 3. تأكيد الشمعة السلبية
    is_bearish_action = price < open_price and price < prev_close

    # ── 4. Liquidity Sweep المطور (مع Volume)
    sweep_short = check_liquidity_sweep_short(df)

    # ── قرار الدخول
    if not is_bearish_action:
        return no_signal("بانتظار تأكيد شمعة سلبية")
    if vwap_dev < 0.015 and not sweep_short:
        return no_signal(f"الانحراف عن VWAP ضئيل ({vwap_dev:.1%}) ولا يوجد Sweep")

    # ── حساب المستويات
    stop_lvl = max(high_price, price + (atr * S2_STOP_ATR_MULT))
    stop     = round(stop_lvl, 2)
    tp1      = round(price - (atr * S2_TP1_R), 2)
    tp2      = round(price - (atr * S2_TP2_R), 2)
    trail    = round(atr * 0.4, 4)

    # ── جودة الإشارة — مستقلة عن LONG
    # HIGH: RSI فوق 80 (تشبع شرائي شديد) + تحت EMA200 (اتجاه هابط) أو Sweep مؤكد
    is_high_quality = (rsi >= S2_RSI_HIGH_QUALITY_SHORT) and (price < ema200 or sweep_short)
    quality         = "high" if is_high_quality else "standard"

    reason = (
        f"SHORT ✅ | RSI={rsi:.1f} | Dev={vwap_dev:.1%}"
        + (" | Sweep 🎯" if sweep_short else "")
        + (" | جودة عالية ⭐" if is_high_quality else "")
    )

    return MeanRevSignal(ticker=ticker, side="short", has_signal=True, reason=reason,
                         entry_price=price, stop_loss=stop, target_tp1=tp1, target_tp2=tp2,
                         trail_step=trail, rsi=rsi, atr=atr, atr_pct=atr_pct, vwap=vwap,
                         ema200=ema200, signal_quality=quality, liquidity_sweep=sweep_short)

# ─────────────────────────────────────────
# 5. الدالة الرئيسية
# ─────────────────────────────────────────

def analyze(ticker: str, ema_above: bool = True, exchange: str = "NASDAQ", ema200: float = 0.0) -> MeanRevSignal:
    df = fetch_bars(ticker)
    min_bars = max(50, S2_RSI_PERIOD * 3)

    if df.empty or len(df) < min_bars:
        return MeanRevSignal(ticker=ticker, side="long", has_signal=False, reason="بيانات غير كافية")

    long_signal = _analyze_long(ticker, df, ema_above, ema200)
    if long_signal.has_signal:
        return long_signal

    return _analyze_short(ticker, df, exchange, ema200)
