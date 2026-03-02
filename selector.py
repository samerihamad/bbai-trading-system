# =============================================================
# selector.py — يصنّف الأسهم ويشغّل استراتيجية Mean Reversion
# يفرض حدود MAX_LONG / MAX_SHORT / MAX_TOTAL
# =============================================================

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass

from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_DATA_URL,
    MAX_LONG,
    MAX_SHORT,
    MAX_TOTAL,
)
from strategy_meanrev import (
    analyze as meanrev_analyze,
    MeanRevSignal,
)

HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
}


# ─────────────────────────────────────────
# نموذج نتيجة التحليل
# ─────────────────────────────────────────

@dataclass
class SelectionResult:
    ticker:  str
    adx:     float    # قوة الاتجاه — ADX < 25 → مناسب لـ MeanRev
    atr_pct: float    # نسبة التقلب
    reason:  str


# ─────────────────────────────────────────
# 1. جلب البيانات اليومية
# ─────────────────────────────────────────

def fetch_daily_bars(ticker: str, days: int = 30) -> pd.DataFrame:
    """يجلب الشموع اليومية لحساب مؤشرات التصنيف."""
    end   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

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
        df = df.rename(columns={"o": "open", "h": "high",
                                 "l": "low",  "c": "close", "v": "volume"})
        df["time"] = pd.to_datetime(df["t"])
        return df.sort_values("time").reset_index(drop=True)

    except Exception as e:
        print(f"❌ خطأ في جلب بيانات {ticker}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────
# 2. حساب مؤشرات الفحص السريع
# ─────────────────────────────────────────

def calculate_adx(df: pd.DataFrame, period: int = 14) -> float:
    """
    يحسب ADX — قوة الاتجاه.
    ADX < 25 → سوق عرضي ← مثالي لاستراتيجية MeanRev
    ADX > 25 → اتجاه قوي ← يُعامَل بحذر
    """
    if len(df) < period + 1:
        return 0.0

    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    up_move   = high.diff()
    down_move = -low.diff()

    plus_dm  = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    atr      = tr.rolling(period).mean()
    plus_di  = 100 * (plus_dm.rolling(period).mean()  / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr.replace(0, np.nan))
    dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx      = dx.rolling(period).mean()

    val = adx.iloc[-1]
    return round(float(val) if not pd.isna(val) else 0.0, 2)


def calculate_atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    """يحسب ATR كنسبة مئوية من السعر الحالي."""
    if len(df) < period + 1:
        return 0.0

    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr        = tr.rolling(period).mean().iloc[-1]
    last_close = df["close"].iloc[-1]
    return round(float(atr / last_close) if last_close > 0 else 0.0, 4)


# ─────────────────────────────────────────
# 3. تطبيق حدود المراكز
# ─────────────────────────────────────────

def apply_position_limits(
    signals:           list[MeanRevSignal],
    current_positions: dict,
) -> list[MeanRevSignal]:
    """
    يُصفّي الإشارات بناءً على حدود المراكز:
    - MAX_LONG  = 2 صفقة شراء كحد أقصى
    - MAX_SHORT = 1 صفقة بيع على المكشوف كحد أقصى
    - MAX_TOTAL = 3 مراكز إجمالية كحد أقصى

    current_positions: {symbol: side} للمراكز المفتوحة حالياً
    """
    open_longs  = sum(1 for s in current_positions.values() if s == "long")
    open_shorts = sum(1 for s in current_positions.values() if s == "short")
    open_total  = len(current_positions)

    # استبعاد الأسهم المفتوحة مسبقاً
    signals = [s for s in signals if s.ticker not in current_positions]

    # ترتيب حسب الجودة: high قبل standard
    signals.sort(key=lambda x: (x.signal_quality != "high", not x.liquidity_sweep))

    selected      = []
    added_longs   = 0
    added_shorts  = 0

    for sig in signals:
        if open_total + len(selected) >= MAX_TOTAL:
            break

        if sig.side == "long":
            if open_longs + added_longs < MAX_LONG:
                selected.append(sig)
                added_longs += 1

        elif sig.side == "short":
            if open_shorts + added_shorts < MAX_SHORT:
                selected.append(sig)
                added_shorts += 1

    return selected


# ─────────────────────────────────────────
# 4. الدالة الرئيسية
# ─────────────────────────────────────────

def run_selector(
    tickers:           dict,
    current_positions: dict = None,
) -> dict:
    """
    يحلل كل الأسهم بـ MeanRev ويُرجع الإشارات المتاحة.

    tickers: dict من universe.py {symbol: {ema_above, exchange, ...}}
    current_positions: {symbol: side} للمراكز المفتوحة

    يُرجع dict:
    {
      "meanrev": [MeanRevSignal, ...],  ← الإشارات بعد تطبيق الحدود
      "summary": [SelectionResult, ...]
    }
    """
    if current_positions is None:
        current_positions = {}

    print("\n📊 جاري تحليل الأسهم باستراتيجية الارتداد...")
    print("─" * 55)

    all_signals = []
    summary     = []

    for ticker, info in tickers.items():
        ema_above = info.get("ema_above", False) if isinstance(info, dict) else bool(info)
        exchange  = info.get("exchange", "NASDAQ") if isinstance(info, dict) else "NASDAQ"
        ema200    = info.get("ema200", 0.0) if isinstance(info, dict) else 0.0

        df = fetch_daily_bars(ticker)

        if df.empty or len(df) < 15:
            print(f"  {ticker:6s} | ⚠️  بيانات غير كافية")
            continue

        adx     = calculate_adx(df)
        atr_pct = calculate_atr_pct(df)

        print(f"  {ticker:6s} | ADX={adx:5.1f} | ATR={atr_pct:.1%}", end="")

        signal = meanrev_analyze(
            ticker=ticker,
            ema_above=ema_above,
            exchange=exchange,
            ema200=ema200,
        )

        if signal.has_signal:
            all_signals.append(signal)
            side_tag = "🟢 LONG" if signal.side == "long" else "🔴 SHORT"
            print(
                f" | {side_tag} ✅ "
                f"entry=${signal.entry_price:.2f} | "
                f"TP1=${signal.target_tp1:.2f} | "
                f"TP2=${signal.target_tp2:.2f}"
            )
        else:
            short_reason = signal.reason[:45] + "..." if len(signal.reason) > 45 else signal.reason
            print(f" | ⏭  {short_reason}")

        summary.append(SelectionResult(
            ticker=ticker,
            adx=adx,
            atr_pct=atr_pct,
            reason=signal.reason,
        ))

    # تطبيق حدود المراكز
    filtered_signals = apply_position_limits(all_signals, current_positions)

    print("─" * 55)
    print(f"✅ إشارات متاحة: {len(all_signals)} | بعد تطبيق الحدود: {len(filtered_signals)}")

    return {
        "meanrev": filtered_signals,
        "summary": summary,
    }
