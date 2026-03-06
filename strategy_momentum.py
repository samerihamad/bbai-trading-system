# =============================================================
# strategy_momentum.py — استراتيجية Momentum (الزخم)
# التايم فريم: 15 دقيقة intraday
# المبدأ: Buy High Sell Higher / Short Low Cover Lower
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
    MOM_RSI_MIN,
    MOM_RSI_MAX_SHORT,
    MOM_ADX_MIN,
    MOM_VOLUME_MULT,
    MOM_GAP_MIN,
    MOM_ATR_MULT_STOP,
    MOM_TP1_R,
    MOM_TP2_R,
    MOM_VWAP_BUFFER,
    SHORT_ENABLED,
    SHORT_EXCHANGES,
)

HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
}

NEWS_API_URL = "https://data.alpaca.markets/v1beta1/news"


# ─────────────────────────────────────────
# نموذج إشارة Momentum
# ─────────────────────────────────────────

@dataclass
class MomentumSignal:
    ticker:          str
    side:            str        # long / short
    has_signal:      bool
    reason:          str
    entry_price:     float = 0.0
    stop_loss:       float = 0.0
    target_tp1:      float = 0.0
    target_tp2:      float = 0.0
    trail_step:      float = 0.0
    rsi:             float = 0.0
    adx:             float = 0.0
    atr:             float = 0.0
    atr_pct:         float = 0.0
    vwap:            float = 0.0
    volume_ratio:    float = 0.0   # حجم الشمعة الحالية ÷ المتوسط
    gap_pct:         float = 0.0   # نسبة الـ Gap الصباحي
    has_news:        bool  = False  # هل يوجد خبر محرك
    signal_quality:  str   = "standard"


# ─────────────────────────────────────────
# 1. جلب شموع 15 دقيقة
# ─────────────────────────────────────────

def fetch_15min_bars(ticker: str, bars: int = 100) -> pd.DataFrame:
    end   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (datetime.utcnow() - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        response = requests.get(
            f"{ALPACA_DATA_URL}/v2/stocks/{ticker}/bars",
            headers=HEADERS,
            params={
                "timeframe": "15Min",
                "start":     start,
                "end":       end,
                "limit":     bars,
                "feed":      "iex",
            },
            timeout=15,
        )
        data = response.json().get("bars", [])
        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data)
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df["time"] = pd.to_datetime(df["t"])
        return df.sort_values("time").reset_index(drop=True)

    except Exception as e:
        print(f"❌ خطأ في جلب 15min {ticker}: {e}")
        return pd.DataFrame()


def fetch_daily_bars(ticker: str, days: int = 5) -> pd.DataFrame:
    """يجلب آخر 5 شموع يومية لحساب الـ Gap."""
    end   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (datetime.utcnow() - timedelta(days=days + 5)).strftime("%Y-%m-%dT%H:%M:%SZ")

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
        data = response.json().get("bars", [])
        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data)
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df["time"] = pd.to_datetime(df["t"])
        return df.sort_values("time").reset_index(drop=True)

    except Exception as e:
        return pd.DataFrame()


# ─────────────────────────────────────────
# 2. حساب المؤشرات
# ─────────────────────────────────────────

def calc_rsi(closes: pd.Series, period: int = 14) -> float:
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    val   = rsi.iloc[-1]
    return round(float(val) if not pd.isna(val) else 50.0, 2)


def calc_adx(df: pd.DataFrame, period: int = 14) -> float:
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


def calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev_close).abs(),
                    (df["low"]  - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return round(float(atr) if not pd.isna(atr) else 0.0, 4)


def calc_vwap(df: pd.DataFrame) -> float:
    """VWAP تراكمي من بداية الجلسة."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vwap    = (typical * df["volume"]).cumsum() / df["volume"].cumsum()
    val     = vwap.iloc[-1]
    return round(float(val) if not pd.isna(val) else 0.0, 4)


def calc_ema(closes: pd.Series, period: int) -> float:
    ema = closes.ewm(span=period, adjust=False).mean()
    return round(float(ema.iloc[-1]), 4)


def calc_macd(closes: pd.Series) -> tuple[float, float]:
    """يُرجع (macd_line, signal_line)."""
    ema12  = closes.ewm(span=12, adjust=False).mean()
    ema26  = closes.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return round(float(macd.iloc[-1]), 4), round(float(signal.iloc[-1]), 4)


def calc_volume_ratio(df: pd.DataFrame, lookback: int = 20) -> float:
    """نسبة حجم الشمعة الأخيرة مقارنة بالمتوسط."""
    if len(df) < lookback + 1:
        return 1.0
    avg_vol = df["volume"].iloc[-lookback-1:-1].mean()
    if avg_vol <= 0:
        return 1.0
    return round(float(df["volume"].iloc[-1] / avg_vol), 2)


def calc_gap_pct(df_daily: pd.DataFrame, df_15min: pd.DataFrame) -> float:
    """
    نسبة الـ Gap = (أول سعر فتح اليوم - إغلاق أمس) / إغلاق أمس
    موجب = Gap Up | سالب = Gap Down
    """
    if df_daily.empty or df_15min.empty or len(df_daily) < 2:
        return 0.0
    prev_close  = float(df_daily["close"].iloc[-2])
    today_open  = float(df_15min["open"].iloc[0])
    if prev_close <= 0:
        return 0.0
    return round((today_open - prev_close) / prev_close, 4)


# ─────────────────────────────────────────
# 3. فحص الأخبار
# ─────────────────────────────────────────

def check_news(ticker: str) -> bool:
    """
    يتحقق إذا كان هناك خبر محرك في آخر 24 ساعة.
    يستخدم Alpaca News API.
    """
    try:
        since = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        response = requests.get(
            NEWS_API_URL,
            headers=HEADERS,
            params={
                "symbols": ticker,
                "start":   since,
                "limit":   5,
            },
            timeout=10,
        )
        if response.status_code != 200:
            return False

        news = response.json().get("news", [])
        return len(news) > 0

    except Exception:
        return False


# ─────────────────────────────────────────
# 4. تحليل LONG Momentum
# ─────────────────────────────────────────

def _analyze_momentum_long(
    ticker: str,
    df: pd.DataFrame,
    df_daily: pd.DataFrame,
    ema200: float,
    has_news: bool,
) -> MomentumSignal:

    price = df["close"].iloc[-1]

    rsi          = calc_rsi(df["close"])
    adx          = calc_adx(df)
    atr          = calc_atr(df)
    atr_pct      = atr / price if price > 0 else 0
    vwap         = calc_vwap(df)
    ema9         = calc_ema(df["close"], 9)
    ema20        = calc_ema(df["close"], 20)
    macd, signal = calc_macd(df["close"])
    vol_ratio    = calc_volume_ratio(df)
    gap_pct      = calc_gap_pct(df_daily, df)

    def no_signal(reason: str) -> MomentumSignal:
        return MomentumSignal(
            ticker=ticker, side="long", has_signal=False, reason=reason,
            rsi=rsi, adx=adx, atr=atr, atr_pct=atr_pct, vwap=vwap,
            volume_ratio=vol_ratio, gap_pct=gap_pct, has_news=has_news,
        )

    # ── فلتر 1: ADX > 25 (نريد اتجاه قوي — عكس MeanRev)
    if adx < MOM_ADX_MIN:
        return no_signal(f"ADX={adx:.1f} (زخم ضعيف)")

    # ── فلتر 2: RSI في منطقة الزخم الصاعد (60-80)
    if rsi < MOM_RSI_MIN or rsi > 85:
        return no_signal(f"RSI={rsi:.1f} (خارج نطاق الزخم 60-85)")

    # ── فلتر 3: السعر فوق VWAP + EMA9 > EMA20 (اتجاه صاعد)
    if price < vwap * (1 + MOM_VWAP_BUFFER):
        return no_signal(f"السعر تحت VWAP ({price:.2f} < {vwap:.2f})")

    if ema9 < ema20:
        return no_signal(f"EMA9 < EMA20 (لا يوجد uptrend)")

    # ── فلتر 4: MACD فوق Signal Line (تأكيد الزخم)
    if macd < signal:
        return no_signal("MACD تحت Signal — زخم ضعيف")

    # ── فلتر 5: حجم تداول مرتفع (تأكيد الحركة)
    if vol_ratio < MOM_VOLUME_MULT:
        return no_signal(f"حجم ضعيف ({vol_ratio:.1f}x < {MOM_VOLUME_MULT}x)")

    # ── جودة الإشارة
    # HIGH: Gap Up + خبر + ADX قوي جداً
    # STANDARD: بدون Gap أو بدون خبر
    is_high = (gap_pct >= MOM_GAP_MIN and has_news) or (adx >= 40 and vol_ratio >= 2.0)
    quality  = "high" if is_high else "standard"

    # ── مستويات الدخول
    stop  = round(max(price - atr * MOM_ATR_MULT_STOP, vwap * 0.995), 2)
    tp1   = round(price + atr * MOM_TP1_R, 2)
    tp2   = round(price + atr * MOM_TP2_R, 2)
    trail = round(atr * 0.6, 4)

    reason = (
        f"MOM-LONG ✅ | RSI={rsi:.1f} | ADX={adx:.1f} | Vol={vol_ratio:.1f}x"
        + (f" | Gap={gap_pct:.1%}" if gap_pct >= MOM_GAP_MIN else "")
        + (" | 📰 خبر" if has_news else "")
        + (" | ⭐ جودة عالية" if is_high else "")
    )

    return MomentumSignal(
        ticker=ticker, side="long", has_signal=True, reason=reason,
        entry_price=price, stop_loss=stop, target_tp1=tp1, target_tp2=tp2,
        trail_step=trail, rsi=rsi, adx=adx, atr=atr, atr_pct=atr_pct,
        vwap=vwap, volume_ratio=vol_ratio, gap_pct=gap_pct,
        has_news=has_news, signal_quality=quality,
    )


# ─────────────────────────────────────────
# 5. تحليل SHORT Momentum
# ─────────────────────────────────────────

def _analyze_momentum_short(
    ticker: str,
    df: pd.DataFrame,
    df_daily: pd.DataFrame,
    exchange: str,
    ema200: float,
    has_news: bool,
) -> MomentumSignal:

    price = df["close"].iloc[-1]

    rsi          = calc_rsi(df["close"])
    adx          = calc_adx(df)
    atr          = calc_atr(df)
    atr_pct      = atr / price if price > 0 else 0
    vwap         = calc_vwap(df)
    ema9         = calc_ema(df["close"], 9)
    ema20        = calc_ema(df["close"], 20)
    macd, signal = calc_macd(df["close"])
    vol_ratio    = calc_volume_ratio(df)
    gap_pct      = calc_gap_pct(df_daily, df)

    def no_signal(reason: str) -> MomentumSignal:
        return MomentumSignal(
            ticker=ticker, side="short", has_signal=False, reason=reason,
            rsi=rsi, adx=adx, atr=atr, atr_pct=atr_pct, vwap=vwap,
            volume_ratio=vol_ratio, gap_pct=gap_pct, has_news=has_news,
        )

    if not SHORT_ENABLED:
        return no_signal("SHORT غير مفعّل")
    if exchange not in SHORT_EXCHANGES:
        return no_signal("بورصة غير مدعومة للـ SHORT")

    # ── فلتر 1: ADX > 25
    if adx < MOM_ADX_MIN:
        return no_signal(f"ADX={adx:.1f} (زخم ضعيف)")

    # ── فلتر 2: RSI في منطقة الزخم الهابط (< 40)
    if rsi > MOM_RSI_MAX_SHORT:
        return no_signal(f"RSI={rsi:.1f} (ليس في نطاق زخم هابط < {MOM_RSI_MAX_SHORT})")

    # ── فلتر 3: السعر تحت VWAP + EMA9 < EMA20
    if price > vwap * (1 - MOM_VWAP_BUFFER):
        return no_signal(f"السعر فوق VWAP — لا يوجد ضغط بيعي كافٍ")

    if ema9 > ema20:
        return no_signal("EMA9 > EMA20 — لا يوجد downtrend")

    # ── فلتر 4: MACD تحت Signal
    if macd > signal:
        return no_signal("MACD فوق Signal — زخم صاعد لا هابط")

    # ── فلتر 5: حجم مرتفع
    if vol_ratio < MOM_VOLUME_MULT:
        return no_signal(f"حجم ضعيف ({vol_ratio:.1f}x)")

    # ── جودة الإشارة
    is_high = (gap_pct <= -MOM_GAP_MIN and has_news) or (adx >= 40 and vol_ratio >= 2.0)
    quality  = "high" if is_high else "standard"

    stop  = round(min(price + atr * MOM_ATR_MULT_STOP, vwap * 1.005), 2)
    tp1   = round(price - atr * MOM_TP1_R, 2)
    tp2   = round(price - atr * MOM_TP2_R, 2)
    trail = round(atr * 0.6, 4)

    reason = (
        f"MOM-SHORT ✅ | RSI={rsi:.1f} | ADX={adx:.1f} | Vol={vol_ratio:.1f}x"
        + (f" | Gap={gap_pct:.1%}" if gap_pct <= -MOM_GAP_MIN else "")
        + (" | 📰 خبر" if has_news else "")
        + (" | ⭐ جودة عالية" if is_high else "")
    )

    return MomentumSignal(
        ticker=ticker, side="short", has_signal=True, reason=reason,
        entry_price=price, stop_loss=stop, target_tp1=tp1, target_tp2=tp2,
        trail_step=trail, rsi=rsi, adx=adx, atr=atr, atr_pct=atr_pct,
        vwap=vwap, volume_ratio=vol_ratio, gap_pct=gap_pct,
        has_news=has_news, signal_quality=quality,
    )


# ─────────────────────────────────────────
# 6. الدالة الرئيسية
# ─────────────────────────────────────────

def analyze(
    ticker:    str,
    exchange:  str  = "NASDAQ",
    ema200:    float = 0.0,
) -> MomentumSignal:
    """
    يحلل السهم بإستراتيجية Momentum على 15 دقيقة.
    يجرب LONG أولاً ثم SHORT.
    """
    df       = fetch_15min_bars(ticker)
    df_daily = fetch_daily_bars(ticker)

    min_bars = 30
    if df.empty or len(df) < min_bars:
        return MomentumSignal(
            ticker=ticker, side="long", has_signal=False,
            reason="بيانات 15min غير كافية",
        )

    # فحص الأخبار مرة واحدة لكلا الاتجاهين
    has_news = check_news(ticker)

    long_signal = _analyze_momentum_long(ticker, df, df_daily, ema200, has_news)
    if long_signal.has_signal:
        return long_signal

    return _analyze_momentum_short(ticker, df, df_daily, exchange, ema200, has_news)
