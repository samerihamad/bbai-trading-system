# =============================================================
# strategy_meanrev.py — استراتيجية ارتداد القنوات
# الفكرة: الشراء عند ابتعاد السعر عن VWAP + RSI تحت 30
#         والخروج عند عودة السعر للمتوسط أو تجاوزه
# التحسينات: فلتر ATR + تأكيد شمعة الارتداد + فلتر وقت التداول
# =============================================================

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass
import pytz

from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_DATA_URL,
    CANDLE_INTERVAL,
    HISTORY_BARS,
    S2_RSI_PERIOD,
    S2_RSI_OVERSOLD,
    S2_VWAP_DEVIATION,
    RISK_PER_TRADE,
    STRATEGY2_LEVERAGE,
    TIMEZONE,
)

HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
}

TZ = pytz.timezone(TIMEZONE)

# ─────────────────────────────────────────
# إعدادات الاستراتيجية
# ─────────────────────────────────────────
TRAIL_STEP_PCT      = 0.005   # خطوة تحريك الوقف المتحرك 0.5%
MIN_RISK_REWARD     = 1.5     # أقل نسبة مخاطرة/عائد مقبولة
MAX_OPEN_TRADES     = 3       # أقصى عدد صفقات مفتوحة في نفس الوقت
VWAP_BUFFER_PCT     = 0.001   # هامش 0.1% تحت VWAP للتأكيد

# ── إعدادات التحسينات ──
ATR_PERIOD          = 14      # فترة حساب ATR
ATR_MAX_PCT         = 0.03    # الحد الأقصى للتقلب 3% من السعر
TRADE_START_HOUR    = 10      # بداية التداول 10:00 AM
TRADE_START_MINUTE  = 0
TRADE_END_HOUR      = 15      # نهاية التداول 3:30 PM
TRADE_END_MINUTE    = 30


# ─────────────────────────────────────────
# نموذج إشارة الدخول
# ─────────────────────────────────────────

@dataclass
class MeanRevSignal:
    ticker:        str
    has_signal:    bool
    entry_price:   float
    stop_loss:     float
    target:        float
    vwap:          float
    rsi:           float
    atr:           float
    trail_step:    float
    reason:        str
    timestamp:     datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()


def _no_signal(ticker: str, reason: str) -> MeanRevSignal:
    """دالة مساعدة لإرجاع رفض سريع."""
    return MeanRevSignal(
        ticker=ticker, has_signal=False,
        entry_price=0, stop_loss=0, target=0,
        vwap=0, rsi=0, atr=0, trail_step=0,
        reason=reason,
    )


# ─────────────────────────────────────────
# 1. جلب البيانات اليومية (Intraday)
# ─────────────────────────────────────────

def fetch_intraday_bars(ticker: str) -> pd.DataFrame:
    """
    يجلب شموع اليوم الحالي فقط (Intraday).
    VWAP يُحسب من بداية جلسة التداول اليومية.
    """
    now_ny    = datetime.now(TZ)
    start_ny  = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
    start_utc = start_ny.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        response = requests.get(
            f"{ALPACA_DATA_URL}/v2/stocks/{ticker}/bars",
            headers=HEADERS,
            params={
                "timeframe": CANDLE_INTERVAL,
                "start":     start_utc,
                "end":       end_utc,
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

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """
    يحسب VWAP من بداية الجلسة.
    VWAP = مجموع (السعر النموذجي × الحجم) ÷ مجموع الحجم
    """
    typical_price     = (df["high"] + df["low"] + df["close"]) / 3
    cumulative_tp_vol = (typical_price * df["volume"]).cumsum()
    cumulative_vol    = df["volume"].cumsum()
    return cumulative_tp_vol / cumulative_vol


def calculate_rsi(prices: pd.Series, period: int = S2_RSI_PERIOD) -> pd.Series:
    """
    يحسب RSI.
    أقل من 30 = تشبع بيعي → فرصة شراء محتملة.
    """
    delta    = prices.diff()
    gain     = delta.where(delta > 0, 0.0)
    loss     = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    window_complete = avg_gain.notna()
    rs  = avg_gain / avg_loss.where(avg_loss.abs() > 1e-12, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(
        avg_loss.abs() > 1e-12,
        pd.Series(np.where(window_complete, 100.0, np.nan), index=rsi.index)
    )
    return rsi


def calculate_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """
    تحسين ① — يحسب Average True Range (ATR).
    يقيس مدى تقلب السهم — كلما كان أعلى كلما السهم أكثر عنفاً.
    True Range = أكبر قيمة من:
      - High - Low
      - |High - Close السابق|
      - |Low  - Close السابق|
    """
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """يضيف VWAP و RSI و ATR للبيانات."""
    df         = df.copy()
    df["vwap"] = calculate_vwap(df)
    df["rsi"]  = calculate_rsi(df["close"])
    df["atr"]  = calculate_atr(df)
    return df


# ─────────────────────────────────────────
# 3. شروط الدخول
# ─────────────────────────────────────────

def check_trading_window() -> tuple[bool, str]:
    """
    تحسين ③ — فلتر وقت التداول.
    يسمح بالدخول فقط بين 10:00 AM و 3:30 PM بتوقيت نيويورك.
    - أول 30 دقيقة: ضوضاء وتقلبات عشوائية عند الافتتاح
    - آخر 30 دقيقة: سيولة منخفضة وتحركات غير طبيعية عند الإغلاق
    """
    now_ny = datetime.now(TZ)

    trade_start = now_ny.replace(
        hour=TRADE_START_HOUR, minute=TRADE_START_MINUTE,
        second=0, microsecond=0,
    )
    trade_end = now_ny.replace(
        hour=TRADE_END_HOUR, minute=TRADE_END_MINUTE,
        second=0, microsecond=0,
    )

    if now_ny < trade_start:
        remaining = int((trade_start - now_ny).total_seconds() / 60)
        return False, f"⏳ قبل وقت التداول — يبدأ بعد {remaining} دقيقة (10:00 AM)"

    if now_ny > trade_end:
        return False, "⏰ انتهى وقت التداول — السوق يُغلق بعد 30 دقيقة"

    return True, f"✅ وقت التداول مناسب ({now_ny.strftime('%H:%M')})"


def check_atr_filter(df: pd.DataFrame) -> tuple[bool, str, float]:
    """
    تحسين ① — فلتر ATR.
    يرفض الأسهم التي تتقلب أكثر من 3% يومياً
    لأن الوقف الضيق سيُضرب قبل حدوث الارتداد.
    """
    last     = df.iloc[-1]
    atr      = last["atr"]
    atr_pct  = atr / last["close"]

    if pd.isna(atr):
        return False, "ATR غير متاح — بيانات غير كافية", 0.0

    if atr_pct > ATR_MAX_PCT:
        return False, (
            f"تقلب السهم عالٍ جداً — "
            f"ATR={atr_pct:.1%} يتجاوز الحد {ATR_MAX_PCT:.0%}"
        ), atr

    return True, f"✅ ATR مقبول ({atr_pct:.1%})", atr


def check_price_below_vwap(df: pd.DataFrame) -> tuple[bool, str, float]:
    """
    🔹 شرط ① — السعر تحت VWAP
    السعر يجب أن يكون تحت VWAP بهامش 0.1% على الأقل.
    """
    last      = df.iloc[-1]
    price     = last["close"]
    vwap      = last["vwap"]
    deviation = (vwap - price) / vwap

    if price >= vwap * (1 - VWAP_BUFFER_PCT):
        return False, f"السعر ({price:.2f}) ليس تحت VWAP ({vwap:.2f}) بشكل كافٍ", vwap

    return True, f"✅ السعر تحت VWAP بنسبة {deviation:.2%}", vwap


def check_rsi_oversold(df: pd.DataFrame) -> tuple[bool, str, float]:
    """
    🔹 شرط ② — RSI في منطقة التشبع البيعي (< 30).
    """
    last_rsi = df["rsi"].iloc[-1]

    if pd.isna(last_rsi):
        return False, "RSI غير متاح — بيانات غير كافية", 0.0

    if last_rsi >= S2_RSI_OVERSOLD:
        return False, f"RSI ({last_rsi:.1f}) فوق {S2_RSI_OVERSOLD} — لا تشبع بيعي", last_rsi

    return True, f"✅ RSI في تشبع بيعي ({last_rsi:.1f})", last_rsi


def check_reversal_candle(df: pd.DataFrame) -> tuple[bool, str]:
    """
    تحسين ② — تأكيد شمعة الارتداد.
    الشمعة الأخيرة يجب أن تُغلق أعلى من فتحها (شمعة خضراء)
    كدليل على بدء الارتداد الفعلي وليس مجرد استمرار الهبوط.
    """
    last = df.iloc[-1]

    if last["close"] <= last["open"]:
        diff = last["open"] - last["close"]
        return False, f"الشمعة الأخيرة حمراء (close={last['close']:.2f} < open={last['open']:.2f}) — لا تأكيد ارتداد"

    body_pct = (last["close"] - last["open"]) / last["open"]
    return True, f"✅ شمعة ارتداد خضراء ({body_pct:.2%})"


def check_risk_reward(
    entry: float,
    stop_loss: float,
    target: float,
) -> tuple[bool, str]:
    """
    🔹 شرط ③ — نسبة المخاطرة/العائد ≥ 1.5x
    """
    risk   = entry - stop_loss
    reward = target - entry

    if risk <= 0:
        return False, "وقف الخسارة أعلى من سعر الدخول"

    rr_ratio = reward / risk
    if rr_ratio < MIN_RISK_REWARD:
        return False, f"نسبة R/R ضعيفة ({rr_ratio:.1f}x) — المطلوب {MIN_RISK_REWARD}x"

    return True, f"✅ نسبة R/R: {rr_ratio:.1f}x"


# ─────────────────────────────────────────
# 4. حساب نقاط الدخول والخروج
# ─────────────────────────────────────────

def calculate_levels(df: pd.DataFrame) -> tuple[float, float, float, float]:
    """
    يحسب:
    - entry     : آخر سعر إغلاق
    - stop_loss : أدنى Low لآخر 3 شموع
    - target    : VWAP كهدف متحرك
    - trail_step: خطوة الوقف المتحرك (0.5% من سعر الدخول)
    """
    last       = df.iloc[-1]
    entry      = round(last["close"], 2)
    stop_loss  = round(df["low"].iloc[-3:].min(), 2)
    target     = round(last["vwap"], 2)
    trail_step = round(entry * TRAIL_STEP_PCT, 2)
    return entry, stop_loss, target, trail_step


# ─────────────────────────────────────────
# 5. الوقف المتحرك (Trailing Stop)
# ─────────────────────────────────────────

def update_trailing_stop(
    current_price: float,
    current_stop:  float,
    trail_step:    float,
) -> float:
    """
    يحدّث الوقف المتحرك عند ارتفاع السعر.
    الوقف يتحرك للأعلى فقط، لا يرجع للأسفل أبداً.
    """
    new_stop = current_price - trail_step
    return round(max(new_stop, current_stop), 2)


# ─────────────────────────────────────────
# 6. الدالة الرئيسية
# ─────────────────────────────────────────

def analyze(ticker: str) -> MeanRevSignal:
    """
    يحلل السهم ويُرجع إشارة دخول أو رفض.
    الترتيب: وقت → ATR → VWAP → RSI → شمعة → R/R → إشارة
    """

    # ── شرط 0: فلتر وقت التداول (تحسين ③)
    time_ok, time_msg = check_trading_window()
    if not time_ok:
        return _no_signal(ticker, time_msg)

    # جلب بيانات اليوم
    df = fetch_intraday_bars(ticker)
    if df.empty or len(df) < ATR_PERIOD + 5:
        return _no_signal(ticker, "بيانات اليوم غير كافية")

    # إضافة المؤشرات
    df = add_indicators(df)

    # ── شرط 1: فلتر ATR — تقلب مقبول (تحسين ①)
    atr_ok, atr_msg, atr_val = check_atr_filter(df)
    if not atr_ok:
        return _no_signal(ticker, f"❌ ATR: {atr_msg}")

    # ── شرط 2: السعر تحت VWAP
    vwap_ok, vwap_msg, vwap = check_price_below_vwap(df)
    if not vwap_ok:
        return _no_signal(ticker, f"❌ VWAP: {vwap_msg}")

    # ── شرط 3: RSI في تشبع بيعي
    rsi_ok, rsi_msg, rsi_val = check_rsi_oversold(df)
    if not rsi_ok:
        return _no_signal(ticker, f"❌ RSI: {rsi_msg}")

    # ── شرط 4: تأكيد شمعة الارتداد (تحسين ②)
    candle_ok, candle_msg = check_reversal_candle(df)
    if not candle_ok:
        return _no_signal(ticker, f"❌ الشمعة: {candle_msg}")

    # ── حساب المستويات
    entry, stop_loss, target, trail_step = calculate_levels(df)

    # ── شرط 5: نسبة R/R مقبولة
    rr_ok, rr_msg = check_risk_reward(entry, stop_loss, target)
    if not rr_ok:
        return _no_signal(ticker, f"❌ R/R: {rr_msg}")

    return MeanRevSignal(
        ticker=ticker, has_signal=True,
        entry_price=entry,
        stop_loss=stop_loss,
        target=target,
        vwap=vwap,
        rsi=rsi_val,
        atr=atr_val,
        trail_step=trail_step,
        reason=f"{atr_msg} | {vwap_msg} | {rsi_msg} | {candle_msg} | {rr_msg}",
    )
