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
    side:           str       # 'long' أو 'short'
    has_signal:     bool
    reason:         str

    # أسعار الدخول والخروج
    entry_price:    float = 0.0
    stop_loss:      float = 0.0
    target_tp1:     float = 0.0
    target_tp2:     float = 0.0
    trail_step:     float = 0.0

    # مؤشرات التحليل
    rsi:            float = 0.0
    atr:            float = 0.0
    atr_pct:        float = 0.0
    vwap:           float = 0.0
    ema200:         float = 0.0
    signal_quality: str   = "standard"   # 'high' أو 'standard'
    liquidity_sweep: bool = False


# ─────────────────────────────────────────
# 1. جلب البيانات
# ─────────────────────────────────────────

def fetch_bars(ticker: str, days: int = HISTORY_BARS) -> pd.DataFrame:
    """
    يجلب الشموع اليومية للتحليل.
    يُرجع DataFrame مع أعمدة: open, high, low, close, volume
    """
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
        df = df.rename(columns={"o": "open", "h": "high", "l": "low",
                                 "c": "close", "v": "volume"})
        df["time"] = pd.to_datetime(df["t"])
        return df.sort_values("time").reset_index(drop=True)

    except Exception as e:
        print(f"❌ خطأ في جلب بيانات {ticker}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────
# 2. حساب المؤشرات الفنية
# ─────────────────────────────────────────

def calc_rsi(closes: pd.Series, period: int = S2_RSI_PERIOD) -> float:
    """يحسب مؤشر RSI ويُرجع آخر قيمة."""
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    val   = rsi.iloc[-1]
    return round(float(val) if not pd.isna(val) else 50.0, 2)


def calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """يحسب Average True Range ويُرجع آخر قيمة."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return round(float(atr) if not pd.isna(atr) else 0.0, 4)


def calc_vwap(df: pd.DataFrame) -> float:
    """
    يحسب VWAP اليومي.
    VWAP = مجموع(السعر النموذجي × الحجم) ÷ مجموع الحجم
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vwap    = (typical * df["volume"]).sum() / df["volume"].sum()
    return round(float(vwap) if not pd.isna(vwap) else 0.0, 4)


def calc_ema(closes: pd.Series, span: int) -> float:
    """يحسب EMA ويُرجع آخر قيمة."""
    ema = closes.ewm(span=span, adjust=False).mean().iloc[-1]
    return round(float(ema) if not pd.isna(ema) else 0.0, 4)


# ─────────────────────────────────────────
# 3. Liquidity Sweep
# ─────────────────────────────────────────

def check_liquidity_sweep_long(df: pd.DataFrame) -> bool:
    """
    Bullish Liquidity Sweep:
    الشمعة الأخيرة كسرت قاع أمس (امتصاص البيع)
    ثم أغلقت فوق قاع أمس → إشارة قوة.
    """
    if not LIQUIDITY_SWEEP_ENABLED or len(df) < 2:
        return False

    prev_low   = df["low"].iloc[-2]
    curr_low   = df["low"].iloc[-1]
    curr_close = df["close"].iloc[-1]

    return curr_low < prev_low and curr_close > prev_low


def check_liquidity_sweep_short(df: pd.DataFrame) -> bool:
    """
    Bearish Liquidity Sweep:
    الشمعة الأخيرة كسرت قمة أمس (امتصاص الشراء)
    ثم أغلقت تحت قمة أمس → إشارة ضعف.
    """
    if not LIQUIDITY_SWEEP_ENABLED or len(df) < 2:
        return False

    prev_high  = df["high"].iloc[-2]
    curr_high  = df["high"].iloc[-1]
    curr_close = df["close"].iloc[-1]

    return curr_high > prev_high and curr_close < prev_high


# ─────────────────────────────────────────
# 4. تحديث الوقف المتحرك
# ─────────────────────────────────────────

def update_trailing_stop(
    current_price: float,
    current_stop:  float,
    trail_step:    float,
) -> float:
    """
    يحرّك وقف الخسارة لأعلى مع ارتفاع السعر.
    trail_step: المسافة المطلوبة للتحريك (بالدولار)
    """
    new_stop = current_price - trail_step
    return round(max(new_stop, current_stop), 4)


# ─────────────────────────────────────────
# 5. تحديث قائمة الأسهم المسموح بها
# ─────────────────────────────────────────

def refresh_allowed_tickers(candidate_tickers: list[str]):
    """
    يُحدّث الفلترة الديناميكية للأسهم.
    يُستدعى من main.py في روتين ما قبل الافتتاح.
    """
    print(f"🔄 تحديث قائمة الأسهم المسموح بها: {len(candidate_tickers)} سهم")
    # يمكن توسيع هذه الدالة لاحقاً لإضافة فلاتر أداء تاريخي


# ─────────────────────────────────────────
# 6. التحليل الرئيسي — LONG
# ─────────────────────────────────────────

def _analyze_long(
    ticker:    str,
    df:        pd.DataFrame,
    ema_above: bool,
    ema200:    float,
) -> MeanRevSignal:
    """
    يبحث عن فرصة LONG (شراء) بشروط:
    1. RSI < 30 (تشبع بيعي)
    2. السعر بالقرب من VWAP أو تحته (لم يبتعد أكثر من 1.2%)
    3. ATR في النطاق المقبول (0.7% - 3.5%)
    4. السهم فوق EMA200 (اتجاه صاعد) — اختياري في السوق الهابط
    """
    price   = df["close"].iloc[-1]
    rsi     = calc_rsi(df["close"])
    atr     = calc_atr(df)
    vwap    = calc_vwap(df)
    atr_pct = atr / price if price > 0 else 0

    def no_signal(reason: str) -> MeanRevSignal:
        return MeanRevSignal(ticker=ticker, side="long", has_signal=False,
                             reason=reason, rsi=rsi, atr=atr,
                             atr_pct=atr_pct, vwap=vwap, ema200=ema200)

    # ── فلتر ATR
    if not (S2_ATR_MIN_PCT <= atr_pct <= S2_ATR_MAX_PCT):
        return no_signal(f"ATR خارج النطاق: {atr_pct:.1%} (المطلوب {S2_ATR_MIN_PCT:.1%}-{S2_ATR_MAX_PCT:.1%})")

    # ── فلتر RSI
    if rsi >= S2_RSI_OVERSOLD:
        return no_signal(f"RSI={rsi:.1f} — لم يصل لمنطقة التشبع البيعي (<{S2_RSI_OVERSOLD})")

    # ── فلتر VWAP — السعر يجب أن يكون بالقرب من VWAP أو تحته
    if vwap > 0:
        vwap_dev = (price - vwap) / vwap
        if vwap_dev > S2_VWAP_MIN_DEV:
            return no_signal(f"السعر بعيد عن VWAP: {vwap_dev:.1%} (الحد {S2_VWAP_MIN_DEV:.1%})")

    # ── فلتر EMA200 (اختياري في السوق الهابط)
    if not ema_above:
        trend_note = "تحت EMA200 (bear fallback)"
    else:
        trend_note = "فوق EMA200"

    # ── جودة الإشارة
    is_high_quality = rsi < S2_RSI_HIGH_QUALITY
    sweep_long      = check_liquidity_sweep_long(df)
    quality         = "high" if is_high_quality or sweep_long else "standard"

    # ── حساب أسعار الدخول والخروج
    stop     = round(price - atr * S2_STOP_ATR_MULT, 2)
    tp1      = round(price + atr * S2_TP1_R, 2)
    tp2      = round(price + atr * S2_TP2_R, 2)
    trail    = round(atr * 0.5, 4)

    reason = (
        f"LONG ✅ | RSI={rsi:.1f} | ATR={atr_pct:.1%} | {trend_note}"
        + (" | Liquidity Sweep 🎯" if sweep_long else "")
        + (" | جودة عالية ⭐" if is_high_quality else "")
    )

    return MeanRevSignal(
        ticker=ticker, side="long", has_signal=True, reason=reason,
        entry_price=price, stop_loss=stop,
        target_tp1=tp1, target_tp2=tp2, trail_step=trail,
        rsi=rsi, atr=atr, atr_pct=atr_pct, vwap=vwap, ema200=ema200,
        signal_quality=quality, liquidity_sweep=sweep_long,
    )


# ─────────────────────────────────────────
# 7. التحليل الرئيسي — SHORT
# ─────────────────────────────────────────

def _analyze_short(
    ticker:    str,
    df:        pd.DataFrame,
    exchange:  str,
    ema200:    float,
) -> MeanRevSignal:
    """
    يبحث عن فرصة SHORT (بيع على المكشوف) بشروط:
    1. RSI > 70 (تشبع شرائي) — إلزامي
    2. السعر فوق VWAP (ممتد صعوداً) — أو
    3. السعر تحت EMA200 (اتجاه هابط رئيسي)
       يكفي شرط واحد من الثاني والثالث
    """
    price   = df["close"].iloc[-1]
    rsi     = calc_rsi(df["close"])
    atr     = calc_atr(df)
    vwap    = calc_vwap(df)
    atr_pct = atr / price if price > 0 else 0

    def no_signal(reason: str) -> MeanRevSignal:
        return MeanRevSignal(ticker=ticker, side="short", has_signal=False,
                             reason=reason, rsi=rsi, atr=atr,
                             atr_pct=atr_pct, vwap=vwap, ema200=ema200)

    # ── التحقق من تفعيل SHORT
    if not SHORT_ENABLED:
        return no_signal("SHORT غير مفعّل (live mode)")

    if exchange not in SHORT_EXCHANGES:
        return no_signal(f"بورصة {exchange} غير مدعومة للشورت")

    # ── فلتر ATR
    if not (S2_ATR_MIN_PCT <= atr_pct <= S2_ATR_MAX_PCT):
        return no_signal(f"ATR خارج النطاق: {atr_pct:.1%}")

    # ── الشرط الأول — RSI إلزامي
    if rsi <= S2_RSI_OVERBOUGHT:
        return no_signal(f"RSI={rsi:.1f} — لم يصل لمنطقة التشبع الشرائي (>{S2_RSI_OVERBOUGHT})")

    # ── الشرط الثاني أو الثالث — يكفي شرط واحد
    above_vwap   = price > vwap if vwap > 0 else False
    below_ema200 = price < ema200 if ema200 > 0 else False

    if not above_vwap and not below_ema200:
        return no_signal(
            f"RSI={rsi:.1f} ✅ لكن السعر ليس فوق VWAP ولا تحت EMA200"
        )

    # ── Liquidity Sweep للشورت
    sweep_short = check_liquidity_sweep_short(df)

    # ── حساب أسعار الدخول والخروج (عكس LONG)
    stop  = round(price + atr * S2_STOP_ATR_MULT, 2)
    tp1   = round(price - atr * S2_TP1_R, 2)
    tp2   = round(price - atr * S2_TP2_R, 2)
    trail = round(atr * 0.5, 4)

    cond_str = []
    if above_vwap:   cond_str.append("فوق VWAP")
    if below_ema200: cond_str.append("تحت EMA200")

    reason = (
        f"SHORT ✅ | RSI={rsi:.1f} | {' + '.join(cond_str)}"
        + (" | Liquidity Sweep 🎯" if sweep_short else "")
    )

    quality = "high" if above_vwap and below_ema200 else "standard"

    return MeanRevSignal(
        ticker=ticker, side="short", has_signal=True, reason=reason,
        entry_price=price, stop_loss=stop,
        target_tp1=tp1, target_tp2=tp2, trail_step=trail,
        rsi=rsi, atr=atr, atr_pct=atr_pct, vwap=vwap, ema200=ema200,
        signal_quality=quality, liquidity_sweep=sweep_short,
    )


# ─────────────────────────────────────────
# 8. الدالة الرئيسية
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
