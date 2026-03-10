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

# ─── فلتر News Trap ───────────────────────
# ATR على 1Day أكثر من 8% = حدث استثنائي → رفض
NEWS_TRAP_ATR_THRESHOLD = 0.08

# نستورد check_news من momentum لتجنب التكرار
def _check_news(ticker: str) -> bool:
    try:
        from strategy_momentum import check_news
        return check_news(ticker)
    except Exception:
        return False

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
    adx:             float = 0.0
    signal_quality:  str   = "standard"
    liquidity_sweep: bool  = False
    score:           float = 0.0    # درجة قوة الإشارة — يُعيَّن في selector.py
    timeframe:       str   = "1Day" # التايم فريم الذي جاءت منه الإشارة

# ─────────────────────────────────────────
# 1. جلب البيانات وحساب المؤشرات
# ─────────────────────────────────────────

def fetch_bars(ticker: str, timeframe: str = "1Day", days: int = HISTORY_BARS) -> pd.DataFrame:
    """
    يجلب الشموع بأي تايم فريم.
    timeframe: '1Day' | '1Hour' | '15Min'
    """
    # عدد الأيام المطلوبة حسب التايم فريم
    lookback = {
        "1Day":  days + 30,
        "1Hour": 10,       # آخر 10 أيام تكفي للـ 1H
        "15Min": 5,        # آخر 5 أيام تكفي للـ 15Min
    }.get(timeframe, days + 30)

    end   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (datetime.utcnow() - timedelta(days=lookback)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # عدد الشموع المطلوبة
    bar_limit = {
        "1Day":  days,
        "1Hour": 120,   # آخر 120 شمعة ساعية
        "15Min": 200,   # آخر 200 شمعة 15 دقيقة
    }.get(timeframe, days)

    try:
        response = requests.get(
            f"{ALPACA_DATA_URL}/v2/stocks/{ticker}/bars",
            headers=HEADERS,
            params={
                "timeframe": timeframe,
                "start":     start,
                "end":       end,
                "limit":     bar_limit,
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
        print(f"❌ خطأ في جلب بيانات {ticker} [{timeframe}]: {e}")
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


def get_current_atr(ticker: str, timeframe: str = "15Min") -> float:
    """
    يجلب ATR الحالي (ديناميكي) أثناء التشغيل لاستخدامه في الـ Trailing Stop.
    يُستخدم بدلاً من ATR المحفوظ عند الفتح لأن التقلب يتغير خلال اليوم.
    يرجع 0.0 عند الفشل (safe fallback).
    """
    try:
        df = fetch_bars(ticker, timeframe=timeframe, days=5)
        if df is None or df.empty or len(df) < 15:
            return 0.0
        atr = calc_atr(df)
        return atr if atr > 0 else 0.0
    except Exception as e:
        print(f"  ⚠️  فشل جلب ATR الديناميكي لـ {ticker}: {e}")
        return 0.0

def calc_vwap(df: pd.DataFrame) -> float:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vwap    = (typical * df["volume"]).sum() / df["volume"].sum()
    return round(float(vwap) if not pd.isna(vwap) else 0.0, 4)

def calc_adx(df: pd.DataFrame, period: int = 14) -> float:
    """يحسب ADX — قوة الاتجاه. < 25 = سوق عرضي مناسب لـ MeanRev."""
    if len(df) < period * 2 + 1:
        return 0.0
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    up_move   = high.diff()
    down_move = -low.diff()
    plus_dm  = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    atr_s    = tr.ewm(alpha=1/period, adjust=False).mean()
    plus_di  = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean()  / atr_s.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr_s.replace(0, np.nan))
    dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx      = dx.ewm(alpha=1/period, adjust=False).mean()
    val      = adx.iloc[-1]
    return round(float(val) if not pd.isna(val) else 0.0, 2)


def check_volatility_expansion(df: pd.DataFrame, period: int = 20) -> tuple[bool, float]:
    """
    التعديل 2: Volatility Expansion Filter
    يتحقق إذا كان ATR الحالي أكبر من متوسط ATR — السوق بدأ يتحرك.
    يُرجع (is_expanding, ratio) حيث ratio = ATR_current / ATR_avg
    """
    if len(df) < period + 2:
        return True, 1.0  # نسمح بالمرور إذا البيانات غير كافية

    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr_series  = tr.rolling(14).mean()
    atr_current = float(atr_series.iloc[-1])
    atr_avg     = float(atr_series.iloc[-period:-1].mean())

    if atr_avg <= 0:
        return True, 1.0

    ratio = round(atr_current / atr_avg, 2)
    # Expansion: ATR الحالي أكبر من 80% من المتوسط (مرونة)
    return ratio >= 0.8, ratio


def check_liquidity_heatmap_long(df: pd.DataFrame, lookback: int = 20) -> bool:
    """
    التعديل 3: Liquidity Heatmap — Stop Hunt تحت Low 20
    يبحث عن: كسر أدنى Low خلال آخر 20 شمعة ثم الإغلاق فوقه.
    هذا يعني: السوق امتص وقف الخسارة تحت القاع ثم انعكس صعوداً.
    """
    if not LIQUIDITY_SWEEP_ENABLED or len(df) < lookback + 2:
        return False

    curr_low   = df["low"].iloc[-1]
    curr_close = df["close"].iloc[-1]
    curr_vol   = df["volume"].iloc[-1]

    # أدنى قاع خلال آخر 20 شمعة (ما عدا الأخيرة)
    low_20     = df["low"].iloc[-lookback-1:-1].min()
    avg_vol    = df["volume"].iloc[-lookback-1:-1].mean()

    # الشرط: كسر القاع + إغلاق فوقه + حجم مرتفع
    swept      = curr_low < low_20 and curr_close > low_20
    vol_confirm = curr_vol > avg_vol * 1.3

    return swept and vol_confirm


def check_liquidity_heatmap_short(df: pd.DataFrame, lookback: int = 20) -> bool:
    """
    التعديل 3: Liquidity Heatmap — Stop Hunt فوق High 20
    يبحث عن: كسر أعلى High خلال آخر 20 شمعة ثم الإغلاق تحته.
    هذا يعني: السوق امتص وقف الخسارة فوق القمة ثم انعكس هبوطاً.
    """
    if not LIQUIDITY_SWEEP_ENABLED or len(df) < lookback + 2:
        return False

    curr_high  = df["high"].iloc[-1]
    curr_close = df["close"].iloc[-1]
    curr_vol   = df["volume"].iloc[-1]

    # أعلى قمة خلال آخر 20 شمعة (ما عدا الأخيرة)
    high_20    = df["high"].iloc[-lookback-1:-1].max()
    avg_vol    = df["volume"].iloc[-lookback-1:-1].mean()

    swept      = curr_high > high_20 and curr_close < high_20
    vol_confirm = curr_vol > avg_vol * 1.3

    return swept and vol_confirm

def check_news_trap(ticker: str, df_1day: pd.DataFrame) -> tuple[bool, str]:
    """
    فلتر News Trap — يتجنب الدخول عكس خبر قوي.

    الفلتر 1: ATR اليومي > 8% → حركة استثنائية = خبر محتمل
    الفلتر 2: ATR مرتفع + خبر مؤكد → رفض قاطع

    يُرجع (is_trap, reason)
    is_trap=True  → لا تدخل الصفقة
    is_trap=False → آمن للدخول
    """
    if df_1day.empty or len(df_1day) < 2:
        return False, ""

    # ── الفلتر 1: ATR اليومي
    price       = df_1day["close"].iloc[-1]
    atr_1day    = calc_atr(df_1day)
    atr_1day_pct = atr_1day / price if price > 0 else 0

    if atr_1day_pct > NEWS_TRAP_ATR_THRESHOLD:
        # ── الفلتر 2: تأكيد الخبر
        has_news = _check_news(ticker)
        if has_news:
            return True, f"News Trap🚫 ATR_1D={atr_1day_pct:.1%} + خبر مؤكد"
        else:
            # ATR مرتفع بدون خبر مؤكد — تحذير فقط، لا رفض
            return False, f"⚠️ ATR_1D مرتفع ({atr_1day_pct:.1%}) بدون خبر"

    return False, ""


def update_trailing_stop(current_price: float, current_stop: float, trail_step: float) -> float:
    new_stop = current_price - trail_step
    return round(max(new_stop, current_stop), 4)

def refresh_allowed_tickers(candidate_tickers: list):
    print(f"🔄 تحديث قائمة الأسهم المسموح بها: {len(candidate_tickers)} سهم")

# ─────────────────────────────────────────
# 2. منطق Liquidity Sweep المحسن (VSA)
# ─────────────────────────────────────────

def check_liquidity_sweep_long(df: pd.DataFrame) -> bool:
    """استخدام Heatmap 20 — أقوى من prev شمعة واحدة."""
    return check_liquidity_heatmap_long(df, lookback=20)

def check_liquidity_sweep_short(df: pd.DataFrame) -> bool:
    """استخدام Heatmap 20 — أقوى من prev شمعة واحدة."""
    return check_liquidity_heatmap_short(df, lookback=20)


# ─────────────────────────────────────────
# 3. التحليل الرئيسي - LONG
# ─────────────────────────────────────────

def _analyze_long(ticker: str, df: pd.DataFrame, ema_above: bool, ema200: float, timeframe: str = "1Day") -> MeanRevSignal:
    price    = df["close"].iloc[-1]
    rsi      = calc_rsi(df["close"])
    atr      = calc_atr(df)
    vwap     = calc_vwap(df)
    adx      = calc_adx(df)
    atr_pct  = atr / price if price > 0 else 0
    vwap_dev = (price - vwap) / vwap if vwap > 0 else 0
    vol_expanding, vol_ratio = check_volatility_expansion(df)

    tf_tag = f"[{timeframe}]"

    def no_signal(reason: str):
        return MeanRevSignal(ticker=ticker, side="long", has_signal=False, reason=reason,
                             rsi=rsi, atr=atr, atr_pct=atr_pct, vwap=vwap, adx=adx, ema200=ema200,
                             timeframe=timeframe)

    # ── فلتر 1: ATR
    if not (S2_ATR_MIN_PCT <= atr_pct <= S2_ATR_MAX_PCT):
        return no_signal(f"ATR خارج النطاق: {atr_pct:.1%}")

    # ── فلتر 2: RSI تشبع بيعي
    if rsi >= S2_RSI_OVERSOLD:
        return no_signal(f"RSI={rsi:.1f} (ليس تشبع)")

    # ── فلتر 3: ADX
    if adx > 35:
        return no_signal(f"ADX={adx:.1f} (اتجاه قوي — رفض LONG)")

    # ── فلتر 4: VWAP
    if vwap > 0 and vwap_dev > S2_VWAP_MIN_DEV:
        return no_signal(f"السعر بعيد عن VWAP: {vwap_dev:.1%}")

    # ── فلتر 5: Volatility Expansion — التعديل 2
    if not vol_expanding:
        return no_signal(f"Volatility منخفض ({vol_ratio:.2f}x) — السوق راكد")

    # ── فلتر 6: EMA200
    sweep_long    = check_liquidity_sweep_long(df)
    below_ema_far = ema200 > 0 and price < ema200 * 0.97

    if below_ema_far and rsi >= S2_RSI_HIGH_QUALITY and not sweep_long:
        return no_signal(f"سعر تحت EMA200 بـ>{((ema200-price)/ema200*100):.1f}% ورسي={rsi:.1f} — ضعيف")

    is_high_quality = rsi < S2_RSI_HIGH_QUALITY or sweep_long
    quality         = "high" if is_high_quality else "standard"

    stop  = round(price - atr * S2_STOP_ATR_MULT, 2)
    tp1   = round(price + atr * S2_TP1_R, 2)
    tp2   = round(price + atr * S2_TP2_R, 2)
    trail = round(atr * 0.5, 4)

    reason = (
        f"LONG ✅ {tf_tag} | RSI={rsi:.1f} | ADX={adx:.1f} | Dev={vwap_dev:.1%} | Vol={vol_ratio:.2f}x"
        + (" | Sweep🎯" if sweep_long else "")
        + (" | ⭐عالي" if is_high_quality else "")
    )

    return MeanRevSignal(ticker=ticker, side="long", has_signal=True, reason=reason,
                         entry_price=price, stop_loss=stop, target_tp1=tp1, target_tp2=tp2,
                         trail_step=trail, rsi=rsi, atr=atr, atr_pct=atr_pct, vwap=vwap,
                         adx=adx, ema200=ema200, signal_quality=quality, liquidity_sweep=sweep_long,
                         timeframe=timeframe)

# ─────────────────────────────────────────
# 4. التحليل الرئيسي المحسن - SHORT
# ─────────────────────────────────────────

S2_RSI_HIGH_QUALITY_SHORT = 80

def _analyze_short(ticker: str, df: pd.DataFrame, exchange: str, ema200: float, timeframe: str = "1Day") -> MeanRevSignal:
    price      = df["close"].iloc[-1]
    prev_close = df["close"].iloc[-2]
    open_price = df["open"].iloc[-1]
    high_price = df["high"].iloc[-1]

    rsi      = calc_rsi(df["close"])
    atr      = calc_atr(df)
    vwap     = calc_vwap(df)
    adx      = calc_adx(df)
    atr_pct  = atr / price if price > 0 else 0
    vol_expanding, vol_ratio = check_volatility_expansion(df)
    tf_tag   = f"[{timeframe}]"

    def no_signal(reason: str):
        return MeanRevSignal(ticker=ticker, side="short", has_signal=False, reason=reason,
                             rsi=rsi, atr=atr, atr_pct=atr_pct, vwap=vwap, adx=adx, ema200=ema200,
                             timeframe=timeframe)

    if not SHORT_ENABLED:
        return no_signal("SHORT غير مفعّل")
    if exchange not in SHORT_EXCHANGES:
        return no_signal("بورصة غير مدعومة")
    if not (S2_ATR_MIN_PCT <= atr_pct <= S2_ATR_MAX_PCT):
        return no_signal(f"ATR غير مناسب: {atr_pct:.1%}")
    if rsi <= S2_RSI_OVERBOUGHT:
        return no_signal(f"RSI={rsi:.1f} (ليس تشبع شرائي)")
    if not vol_expanding:
        return no_signal(f"Volatility منخفض ({vol_ratio:.2f}x) — السوق راكد")

    vwap_dev          = (price - vwap) / vwap if vwap > 0 else 0
    is_bearish_action = price < open_price and price < prev_close
    sweep_short       = check_liquidity_sweep_short(df)

    if not is_bearish_action:
        return no_signal("بانتظار تأكيد شمعة سلبية")
    if vwap_dev < 0.015 and not sweep_short:
        return no_signal(f"الانحراف عن VWAP ضئيل ({vwap_dev:.1%}) ولا يوجد Sweep")

    stop_lvl        = max(high_price, price + (atr * S2_STOP_ATR_MULT))
    stop            = round(stop_lvl, 2)
    tp1             = round(price - (atr * S2_TP1_R), 2)
    tp2             = round(price - (atr * S2_TP2_R), 2)
    trail           = round(atr * 0.4, 4)
    is_high_quality = (rsi >= S2_RSI_HIGH_QUALITY_SHORT) and (price < ema200 or sweep_short)
    quality         = "high" if is_high_quality else "standard"

    reason = (
        f"SHORT ✅ {tf_tag} | RSI={rsi:.1f} | Dev={vwap_dev:.1%} | Vol={vol_ratio:.2f}x"
        + (" | Sweep🎯" if sweep_short else "")
        + (" | ⭐عالي" if is_high_quality else "")
    )

    return MeanRevSignal(ticker=ticker, side="short", has_signal=True, reason=reason,
                         entry_price=price, stop_loss=stop, target_tp1=tp1, target_tp2=tp2,
                         trail_step=trail, rsi=rsi, atr=atr, atr_pct=atr_pct, vwap=vwap,
                         adx=adx, ema200=ema200, signal_quality=quality, liquidity_sweep=sweep_short,
                         timeframe=timeframe)

# ─────────────────────────────────────────
# 5. الدالة الرئيسية — Multi-Timeframe
# ─────────────────────────────────────────

def analyze(ticker: str, ema_above: bool = True, exchange: str = "NASDAQ", ema200: float = 0.0) -> MeanRevSignal:
    """
    يحلل السهم على 3 تايم فريمات بالترتيب:
    1Day → 1Hour → 15Min
    يتحقق من News Trap أولاً قبل أي تحليل.
    """
    # ── فلتر News Trap — يجلب 1Day مرة واحدة للفحص
    df_1day = fetch_bars(ticker, timeframe="1Day")

    if not df_1day.empty:
        is_trap, trap_reason = check_news_trap(ticker, df_1day)
        if is_trap:
            return MeanRevSignal(
                ticker=ticker, side="long", has_signal=False,
                reason=trap_reason, timeframe="1Day",
            )

    timeframes = [
        ("1Day",  max(50, S2_RSI_PERIOD * 3)),
        ("1Hour", 30),
        ("15Min", 30),
    ]

    for tf, min_bars in timeframes:
        # 1Day جُلب مسبقاً — نعيد استخدامه
        df = df_1day if tf == "1Day" else fetch_bars(ticker, timeframe=tf)

        if df.empty or len(df) < min_bars:
            continue

        long_signal = _analyze_long(ticker, df, ema_above, ema200, timeframe=tf)
        if long_signal.has_signal:
            return long_signal

        short_signal = _analyze_short(ticker, df, exchange, ema200, timeframe=tf)
        if short_signal.has_signal:
            return short_signal

    return MeanRevSignal(ticker=ticker, side="long", has_signal=False, reason="لا إشارة على 1D/1H/15M", timeframe="1Day")
