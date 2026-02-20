# =============================================================
# strategy_conservative.py — الاستراتيجية المحافظة الصارمة
# الفكرة: كسر مستوى مقاومة مع تأكيد الاتجاه والقوة النسبية
# التحسينات: فلتر الحجم عند الكسر + فلتر وقت التداول
# =============================================================

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional
import pytz

from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_DATA_URL,
    BENCHMARK_TICKER,
    CANDLE_INTERVAL,
    HISTORY_BARS,
    RS_LOOKBACK,
    S1_EMA_FAST,
    S1_EMA_SLOW,
    S1_BREAKOUT_PERIOD,
    S1_CONFIRM_CANDLES,
    S1_STOP_LOOKBACK,
    S1_TARGET_R,
    S1_TRAIL_TRIGGER_R,
    S1_TRAIL_TO_R,
    TIMEZONE,
)

HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
}

TZ = pytz.timezone(TIMEZONE)

# ─────────────────────────────────────────
# إعدادات التحسينات
# ─────────────────────────────────────────
VOLUME_CONFIRM_MULTIPLIER = 1.5    # حجم الكسر يجب أن يكون × 1.5 من المتوسط
VOLUME_LOOKBACK           = 20     # متوسط حجم آخر كم شمعة
MARKET_OPEN_BUFFER_MIN    = 30     # تجاهل الإشارات في أول 30 دقيقة من الافتتاح
MARKET_OPEN_HOUR          = 9
MARKET_OPEN_MINUTE        = 30


# ─────────────────────────────────────────
# نموذج إشارة الدخول
# ─────────────────────────────────────────

@dataclass
class ConservativeSignal:
    ticker:      str
    has_signal:  bool
    entry_price: float
    stop_loss:   float
    target:      float
    trail_stop:  float
    reason:      str
    timestamp:   datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()


def _no_signal(ticker: str, reason: str) -> ConservativeSignal:
    """دالة مساعدة لإرجاع رفض سريع."""
    return ConservativeSignal(
        ticker=ticker, has_signal=False,
        entry_price=0, stop_loss=0,
        target=0, trail_stop=0,
        reason=reason,
    )


# ─────────────────────────────────────────
# 1. جلب البيانات التاريخية
# ─────────────────────────────────────────

def fetch_bars(ticker: str) -> pd.DataFrame:
    """
    يجلب الشموع التاريخية للسهم من Alpaca.
    يُرجع DataFrame يحتوي: time, open, high, low, close, volume
    """
    end   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        response = requests.get(
            f"{ALPACA_DATA_URL}/v2/stocks/{ticker}/bars",
            headers=HEADERS,
            params={
                "timeframe": CANDLE_INTERVAL,
                "start":     start,
                "end":       end,
                "limit":     HISTORY_BARS,
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
        df = df.sort_values("time").reset_index(drop=True)
        return df[["time", "open", "high", "low", "close", "volume"]]

    except Exception as e:
        print(f"❌ خطأ في جلب بيانات {ticker}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────
# 2. حساب المؤشرات
# ─────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """يضيف EMA50 و EMA200 و أعلى 50 شمعة ومتوسط الحجم."""
    df = df.copy()
    df["ema_fast"]   = df["close"].ewm(span=S1_EMA_FAST, adjust=False).mean()
    df["ema_slow"]   = df["close"].ewm(span=S1_EMA_SLOW, adjust=False).mean()
    df["high_50"]    = df["high"].rolling(S1_BREAKOUT_PERIOD).max()
    df["avg_volume"] = df["volume"].rolling(VOLUME_LOOKBACK).mean()
    return df


def calculate_relative_strength(
    ticker_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
) -> float:
    """
    يحسب القوة النسبية للسهم مقارنة بـ QQQ.
    قيمة موجبة = السهم أقوى من المؤشر ✅
    قيمة سالبة = السهم أضعف من المؤشر ❌
    """
    if len(ticker_df) < RS_LOOKBACK or len(benchmark_df) < RS_LOOKBACK:
        return 0.0

    ticker_return    = (ticker_df["close"].iloc[-1] / ticker_df["close"].iloc[-RS_LOOKBACK]) - 1
    benchmark_return = (benchmark_df["close"].iloc[-1] / benchmark_df["close"].iloc[-RS_LOOKBACK]) - 1
    return round(ticker_return - benchmark_return, 4)


# ─────────────────────────────────────────
# 3. شروط الدخول
# ─────────────────────────────────────────

def check_trading_window() -> tuple[bool, str]:
    """
    🔹 تحسين ① — فلتر وقت التداول
    يرفض الإشارات في أول 30 دقيقة من افتتاح السوق
    لأن هذه الفترة عشوائية وكثيرة الضوضاء.
    السوق يفتح 9:30 → نبدأ البحث من 10:00 فقط.
    """
    now_ny = datetime.now(TZ)

    # حساب وقت نهاية فترة الحظر: 9:30 + 30 دقيقة = 10:00
    buffer_end = now_ny.replace(
        hour=MARKET_OPEN_HOUR,
        minute=MARKET_OPEN_MINUTE + MARKET_OPEN_BUFFER_MIN,
        second=0,
        microsecond=0,
    )

    if now_ny < buffer_end:
        remaining = int((buffer_end - now_ny).total_seconds() / 60)
        return False, f"⏳ فترة الحظر — السوق فتح منذ أقل من 30 دقيقة (متبقي {remaining} دقيقة)"

    return True, "✅ وقت التداول مناسب"


def check_trend_filter(df: pd.DataFrame) -> tuple[bool, str]:
    """
    🔹 Trend Filter
    - Close > EMA200
    - EMA50 > EMA200
    """
    last = df.iloc[-1]
    if last["close"] <= last["ema_slow"]:
        return False, f"السعر ({last['close']:.2f}) تحت EMA200 ({last['ema_slow']:.2f})"
    if last["ema_fast"] <= last["ema_slow"]:
        return False, f"EMA50 ({last['ema_fast']:.2f}) تحت EMA200 ({last['ema_slow']:.2f})"
    return True, "✅ Trend Filter اجتاز"


def check_relative_strength(rs: float) -> tuple[bool, str]:
    """
    🔹 Relative Strength
    السهم يجب أن يكون أقوى من QQQ.
    """
    if rs <= 0:
        return False, f"القوة النسبية سالبة ({rs:.2%}) — السهم أضعف من QQQ"
    return True, f"✅ القوة النسبية: {rs:.2%}"


def check_breakout(df: pd.DataFrame) -> tuple[bool, str, float]:
    """
    🔹 Breakout + تحسين ② فلتر الحجم
    - كسر أعلى 50 شمعة
    - تأكيد بـ شمعتين Close فوق المستوى
    - حجم شمعة الكسر > متوسط الحجم × 1.5
    """
    if len(df) < S1_BREAKOUT_PERIOD + S1_CONFIRM_CANDLES:
        return False, "بيانات غير كافية", 0.0

    # مستوى الكسر
    breakout_level = df["high"].iloc[
        -(S1_BREAKOUT_PERIOD + S1_CONFIRM_CANDLES):-S1_CONFIRM_CANDLES
    ].max()

    # شرط ①: تأكيد السعر — آخر شمعتين فوق المستوى
    confirm_candles = df["close"].iloc[-S1_CONFIRM_CANDLES:]
    if not (confirm_candles > breakout_level).all():
        return False, f"لم يتم تأكيد الكسر فوق {breakout_level:.2f}", breakout_level

    # شرط ②: تأكيد الحجم — حجم شمعة الكسر أعلى من المتوسط × 1.5
    breakout_candle_volume = df["volume"].iloc[-S1_CONFIRM_CANDLES]
    avg_vol = df["avg_volume"].iloc[-S1_CONFIRM_CANDLES]

    if pd.isna(avg_vol) or avg_vol == 0:
        return False, "لا يمكن حساب متوسط الحجم", breakout_level

    volume_ratio = breakout_candle_volume / avg_vol
    if volume_ratio < VOLUME_CONFIRM_MULTIPLIER:
        return False, (
            f"حجم الكسر ضعيف ({volume_ratio:.1f}x) — "
            f"المطلوب {VOLUME_CONFIRM_MULTIPLIER}x على الأقل"
        ), breakout_level

    return True, (
        f"✅ كسر مؤكد فوق {breakout_level:.2f} "
        f"بحجم {volume_ratio:.1f}x"
    ), breakout_level


# ─────────────────────────────────────────
# 4. حساب نقاط الدخول والخروج
# ─────────────────────────────────────────

def calculate_levels(df: pd.DataFrame) -> tuple[float, float, float, float]:
    """
    يحسب:
    - entry     : آخر سعر إغلاق
    - stop_loss : أدنى Low لآخر 5 شموع
    - target    : entry + risk × R2
    - trail_stop: entry + risk × R0.5  (يُفعّل عند R1)
    """
    entry     = df["close"].iloc[-1]
    stop_loss = df["low"].iloc[-S1_STOP_LOOKBACK:].min()
    risk      = entry - stop_loss
    target    = round(entry + risk * S1_TARGET_R, 2)
    trail_stop = round(entry + risk * S1_TRAIL_TO_R, 2)
    return round(entry, 2), round(stop_loss, 2), target, trail_stop


# ─────────────────────────────────────────
# 5. الدالة الرئيسية
# ─────────────────────────────────────────

def analyze(ticker: str, benchmark_df: pd.DataFrame) -> ConservativeSignal:
    """
    يحلل السهم ويُرجع إشارة دخول أو رفض.
    الترتيب: وقت → بيانات → Trend → RS → Breakout → مستويات

    benchmark_df: بيانات QQQ جاهزة (تُجلب مرة واحدة خارجاً)
    """

    # ── شرط 0: فلتر وقت التداول (تحسين ①)
    time_ok, time_msg = check_trading_window()
    if not time_ok:
        return _no_signal(ticker, time_msg)

    # جلب البيانات
    df = fetch_bars(ticker)
    if df.empty or len(df) < S1_EMA_SLOW + 10:
        return _no_signal(ticker, "بيانات غير كافية")

    # إضافة المؤشرات
    df = add_indicators(df)

    # ── شرط 1: Trend Filter
    trend_ok, trend_msg = check_trend_filter(df)
    if not trend_ok:
        return _no_signal(ticker, f"❌ Trend: {trend_msg}")

    # ── شرط 2: Relative Strength
    rs = calculate_relative_strength(df, benchmark_df)
    rs_ok, rs_msg = check_relative_strength(rs)
    if not rs_ok:
        return _no_signal(ticker, f"❌ RS: {rs_msg}")

    # ── شرط 3: Breakout + حجم (تحسين ②)
    breakout_ok, breakout_msg, _ = check_breakout(df)
    if not breakout_ok:
        return _no_signal(ticker, f"❌ Breakout: {breakout_msg}")

    # ── حساب المستويات
    entry, stop_loss, target, trail_stop = calculate_levels(df)

    # تأكد أن المخاطرة منطقية (الوقف لا يكون بعيداً جداً)
    risk_pct = (entry - stop_loss) / entry
    if risk_pct > 0.05:
        return _no_signal(
            ticker,
            f"❌ وقف الخسارة بعيد جداً ({risk_pct:.1%}) — الحد الأقصى 5%"
        )

    return ConservativeSignal(
        ticker=ticker, has_signal=True,
        entry_price=entry,
        stop_loss=stop_loss,
        target=target,
        trail_stop=trail_stop,
        reason=f"{trend_msg} | {rs_msg} | {breakout_msg}",
    )


def get_benchmark_data() -> pd.DataFrame:
    """يجلب بيانات QQQ مرة واحدة لاستخدامها مع كل الأسهم."""
    return fetch_bars(BENCHMARK_TICKER)
