# =============================================================
# selector.py — يصنّف الأسهم ويشغّل استراتيجية Mean Reversion
# بعد حذف الاستراتيجية المحافظة، يعمل مع meanrev فقط
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
    adx:     float    # قوة الاتجاه
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
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
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
    ADX < 25 → سوق عرضي → مناسب لـ MeanRev
    ADX > 25 → اتجاه قوي → يُعامَل بحذر في MeanRev
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

    last_adx = adx.iloc[-1]
    return round(float(last_adx) if not pd.isna(last_adx) else 0.0, 2)


def calculate_atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    """يحسب ATR كنسبة مئوية من السعر."""
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
# 3. الدالة الرئيسية
# ─────────────────────────────────────────

def run_selector(tickers: list[str]) -> dict:
    """
    يحلل كل الأسهم بـ MeanRev ويُرجع الإشارات المتاحة.

    يُرجع dict يحتوي:
    {
      "meanrev": [MeanRevSignal, ...],
      "summary": [SelectionResult, ...]
    }
    """
    print("\n📊 جاري تحليل الأسهم باستراتيجية الارتداد...")
    print("─" * 55)

    meanrev_signals = []
    summary         = []

    for ticker in tickers:
        df = fetch_daily_bars(ticker)

        if df.empty or len(df) < 15:
            print(f"  {ticker:6s} | ⚠️  بيانات غير كافية")
            continue

        adx     = calculate_adx(df)
        atr_pct = calculate_atr_pct(df)

        print(f"  {ticker:6s} | ADX={adx:5.1f} | ATR={atr_pct:.1%}", end="")

        signal = meanrev_analyze(ticker)

        if signal.has_signal:
            meanrev_signals.append(signal)
            print(f" | ✅ إشارة | entry=${signal.entry_price:.2f} | TP1=${signal.target_tp1:.2f} | TP2=${signal.target_tp2:.2f}")
        else:
            short_reason = signal.reason[:45] + "..." if len(signal.reason) > 45 else signal.reason
            print(f" | ⏭  {short_reason}")

        summary.append(SelectionResult(
            ticker=ticker,
            adx=adx,
            atr_pct=atr_pct,
            reason=signal.reason,
        ))

    print("─" * 55)
    print(f"✅ إجمالي الإشارات: {len(meanrev_signals)}")

    return {
        "meanrev": meanrev_signals,
        "summary": summary,
    }
