# =============================================================
# selector.py — يقرر أي استراتيجية تناسب كل سهم
# الفكرة: بناءً على بيانات كل سهم يتم تصنيفه لاستراتيجية
#         محافظة (Breakout) أو ارتداد (Mean Reversion)
# =============================================================

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum

from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_DATA_URL,
    BENCHMARK_TICKER,
)

from strategy_conservative import (
    analyze as conservative_analyze,
    get_benchmark_data,
    ConservativeSignal,
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
# تعريف الاستراتيجيات
# ─────────────────────────────────────────

class Strategy(Enum):
    CONSERVATIVE = "المحافظة"
    MEAN_REV     = "الارتداد"
    NONE         = "لا شيء"


@dataclass
class SelectionResult:
    ticker:    str
    strategy:  Strategy
    reason:    str
    adx:       float    # قوة الاتجاه
    atr_pct:   float    # نسبة التقلب


# ─────────────────────────────────────────
# 1. جلب البيانات اليومية للتصنيف
# ─────────────────────────────────────────

def fetch_daily_bars(ticker: str, days: int = 30) -> pd.DataFrame:
    """
    يجلب الشموع اليومية للتصنيف.
    نستخدم الشموع اليومية لأنها أكثر استقراراً للتصنيف.
    """
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
        df = df.rename(columns={
            "o": "open", "h": "high",
            "l": "low",  "c": "close", "v": "volume"
        })
        df["time"] = pd.to_datetime(df["t"])
        return df.sort_values("time").reset_index(drop=True)

    except Exception as e:
        print(f"❌ خطأ في جلب بيانات {ticker}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────
# 2. حساب مؤشرات التصنيف
# ─────────────────────────────────────────

def calculate_adx(df: pd.DataFrame, period: int = 14) -> float:
    """
    يحسب مؤشر ADX — قوة الاتجاه.
    ADX > 25 = اتجاه قوي    → مناسب للاستراتيجية المحافظة
    ADX < 20 = سوق عرضي     → مناسب لاستراتيجية الارتداد
    ADX بين 20-25 = محايد
    """
    if len(df) < period + 1:
        return 0.0

    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Directional Movement
    up_move   = high.diff()
    down_move = -low.diff()

    plus_dm  = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    # Smoothed
    atr       = tr.rolling(period).mean()
    plus_di   = 100 * (plus_dm.rolling(period).mean()  / atr.replace(0, np.nan))
    minus_di  = 100 * (minus_dm.rolling(period).mean() / atr.replace(0, np.nan))

    dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.rolling(period).mean()

    last_adx = adx.iloc[-1]
    return round(float(last_adx) if not pd.isna(last_adx) else 0.0, 2)


def calculate_atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    """
    يحسب ATR كنسبة مئوية من السعر.
    يُستخدم لمعرفة مدى تقلب السهم.
    """
    if len(df) < period + 1:
        return 0.0

    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr      = tr.rolling(period).mean().iloc[-1]
    last_close = df["close"].iloc[-1]
    return round(float(atr / last_close) if last_close > 0 else 0.0, 4)


# ─────────────────────────────────────────
# 3. منطق التصنيف
# ─────────────────────────────────────────

def classify_stock(ticker: str) -> SelectionResult:
    """
    يصنّف السهم لإحدى الاستراتيجيتين بناءً على:

    ┌─────────────────────────────────────────────┐
    │  ADX > 25  →  اتجاه قوي  → المحافظة        │
    │  ADX < 20  →  سوق عرضي  → الارتداد         │
    │  بينهما   →  ATR يحسم   →                  │
    │    ATR < 2% → الارتداد                      │
    │    ATR ≥ 2% → المحافظة                      │
    └─────────────────────────────────────────────┘
    """
    df = fetch_daily_bars(ticker)

    if df.empty or len(df) < 15:
        return SelectionResult(
            ticker=ticker,
            strategy=Strategy.NONE,
            reason="بيانات غير كافية للتصنيف",
            adx=0.0,
            atr_pct=0.0,
        )

    adx     = calculate_adx(df)
    atr_pct = calculate_atr_pct(df)

    # ── قرار التصنيف
    if adx > 25:
        strategy = Strategy.CONSERVATIVE
        reason   = f"ADX={adx:.1f} (اتجاه قوي) → الاستراتيجية المحافظة"

    elif adx < 20:
        strategy = Strategy.MEAN_REV
        reason   = f"ADX={adx:.1f} (سوق عرضي) → استراتيجية الارتداد"

    else:
        # المنطقة الرمادية — ATR يحسم
        if atr_pct < 0.02:
            strategy = Strategy.MEAN_REV
            reason   = f"ADX={adx:.1f} (محايد) + ATR={atr_pct:.1%} (منخفض) → الارتداد"
        else:
            strategy = Strategy.CONSERVATIVE
            reason   = f"ADX={adx:.1f} (محايد) + ATR={atr_pct:.1%} (مرتفع) → المحافظة"

    return SelectionResult(
        ticker=ticker,
        strategy=strategy,
        reason=reason,
        adx=adx,
        atr_pct=atr_pct,
    )


# ─────────────────────────────────────────
# 4. الدالة الرئيسية
# ─────────────────────────────────────────

def run_selector(tickers: list[str]) -> dict:
    """
    يصنّف كل أسهم اليوم ثم يحللها بالاستراتيجية المناسبة.

    يُرجع dict يحتوي:
    {
      "conservative": [ConservativeSignal, ...],
      "meanrev":      [MeanRevSignal, ...],
      "summary":      [SelectionResult, ...]
    }
    """
    print("\n📊 جاري تصنيف الأسهم واختيار الاستراتيجية المناسبة...")
    print("─" * 55)

    # جلب بيانات QQQ مرة واحدة فقط للاستراتيجية المحافظة
    benchmark_df = get_benchmark_data()

    conservative_signals = []
    meanrev_signals      = []
    summary              = []

    for ticker in tickers:
        # ── تصنيف السهم
        result = classify_stock(ticker)
        summary.append(result)

        strategy_label = result.strategy.value
        print(f"  {ticker:6s} | ADX={result.adx:5.1f} | ATR={result.atr_pct:.1%} | → {strategy_label}")

        # ── تحليل بالاستراتيجية المختارة
        if result.strategy == Strategy.CONSERVATIVE:
            signal = conservative_analyze(ticker, benchmark_df)
            if signal.has_signal:
                conservative_signals.append(signal)
                print(f"         ✅ إشارة دخول محافظة | entry=${signal.entry_price:.2f} | stop=${signal.stop_loss:.2f} | target=${signal.target:.2f}")
            else:
                print(f"         ⏭  لا إشارة — {signal.reason[:50]}")

        elif result.strategy == Strategy.MEAN_REV:
            signal = meanrev_analyze(ticker)
            if signal.has_signal:
                meanrev_signals.append(signal)
                print(f"         ✅ إشارة ارتداد | entry=${signal.entry_price:.2f} | stop=${signal.stop_loss:.2f} | target=${signal.target:.2f}")
            else:
                print(f"         ⏭  لا إشارة — {signal.reason[:50]}")

        else:
            print(f"         ⚠️  تم تخطي السهم — {result.reason}")

    # ── ملخص نهائي
    total_signals = len(conservative_signals) + len(meanrev_signals)
    print("─" * 55)
    print(f"✅ إجمالي الإشارات: {total_signals}")
    print(f"   محافظة : {len(conservative_signals)}")
    print(f"   ارتداد : {len(meanrev_signals)}")

    return {
        "conservative": conservative_signals,
        "meanrev":      meanrev_signals,
        "summary":      summary,
    }
