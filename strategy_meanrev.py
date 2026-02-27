# =============================================================
# strategy_meanrev.py — استراتيجية ارتداد المتوسطات (محسّنة)
# التحسينات الجديدة:
#   ① فلتر اتجاه EMA200 (Long فوق EMA فقط)
#   ② جودة الدخول: RSI < 25 + ابتعاد VWAP ≥ 1.5%
#   ③ شموع انعكاسية: Hammer أو Bullish Engulfing
#   ④ منع الدخول في ATR عالٍ جداً أو منخفض جداً (Volatility Regime)
#   ⑤ خروج مزدوج: TP1 عند 1R (50%) + TP2 عند 3R (50%) + Trailing Stop
#   ⑥ فلترة الأسهم: NVDA, COST, AMAT فقط (Profit Factor ≥ 1.19)
# =============================================================

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass, field
import pytz

from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_DATA_URL,
    CANDLE_INTERVAL,
    HISTORY_BARS,
    S2_RSI_PERIOD,
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
# ⑥ فلترة الأسهم ديناميكياً من آخر 30 يوم تداول حي
# يتم حساب Profit Factor لكل سهم وحذف أسوأ 30%
# يُحدَّث الكاش مرة واحدة يومياً في بداية الجلسة
# ─────────────────────────────────────────
_ticker_cache: dict = {
    "allowed": set(),      # الأسهم المسموحة بعد الفلترة
    "pf_scores": {},       # Profit Factor لكل سهم
    "last_updated": None,  # آخر وقت تحديث
}
CACHE_TTL_HOURS     = 23   # تحديث مرة يومياً
MIN_TRADES_REQUIRED = 5    # الحد الأدنى للصفقات لاعتبار السهم
PF_PERCENTILE_CUT   = 0.20 # حذف أسوأ 20% (PF منخفض)

# ─────────────────────────────────────────
# إعدادات الاستراتيجية
# ─────────────────────────────────────────
TRAIL_STEP_PCT          = 0.005   # خطوة تحريك الوقف المتحرك 0.5%
MAX_OPEN_TRADES         = 3       # أقصى عدد صفقات مفتوحة في نفس الوقت
VWAP_MIN_DEVIATION_PCT  = 0.012   # ② الحد الأدنى للابتعاد عن VWAP = 1.2%

# ① فلتر EMA200
EMA_TREND_PERIOD        = 200

# ② جودة الدخول
RSI_HIGH_QUALITY        = 25      # RSI < 25 للدخول عالي الجودة

# ③ شمعة انعكاسية — أي شمعة خضراء تكفي (close > open)

# ④ نطاق ATR المقبول (Volatility Regime)
ATR_PERIOD              = 14
ATR_MIN_PCT             = 0.007   # الحد الأدنى 0.7% — أقل من ذلك السوق راكد
ATR_MAX_PCT             = 0.035   # الحد الأقصى 3.5% — أعلى من ذلك تقلب خطر

# ⑤ إعدادات الخروج المزدوج
TP1_R                   = 1.0     # الهدف الأول عند 1R
TP2_R                   = 3.0     # الهدف الثاني عند 3R
TP1_QTY_PCT             = 0.50    # 50% من الكمية عند TP1
TP2_QTY_PCT             = 0.50    # 50% من الكمية عند TP2
TRAILING_TRIGGER_R      = 1.0     # تفعيل الوقف المتحرك بعد 1R

# وقت التداول
TRADE_START_HOUR        = 9
TRADE_START_MINUTE      = 30
TRADE_END_HOUR          = 15
TRADE_END_MINUTE        = 30


# ─────────────────────────────────────────
# نموذج إشارة الدخول
# ─────────────────────────────────────────

@dataclass
class MeanRevSignal:
    ticker:        str
    has_signal:    bool
    entry_price:   float
    stop_loss:     float
    target_tp1:    float           # TP1 = 1R (50% من الكمية)
    target_tp2:    float           # TP2 = 3R (50% من الكمية)
    target:        float           # للتوافق مع executor (= TP2)
    vwap:          float
    rsi:           float
    atr:           float
    trail_step:    float
    signal_quality: str            # 'high' | 'standard'
    reason:        str
    timestamp:     datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()


def _no_signal(ticker: str, reason: str) -> MeanRevSignal:
    return MeanRevSignal(
        ticker=ticker, has_signal=False,
        entry_price=0, stop_loss=0,
        target_tp1=0, target_tp2=0, target=0,
        vwap=0, rsi=0, atr=0, trail_step=0,
        signal_quality='none', reason=reason,
    )


# ─────────────────────────────────────────
# 1. جلب البيانات
# ─────────────────────────────────────────

def fetch_intraday_bars(ticker: str) -> pd.DataFrame:
    """يجلب شموع اليوم الحالي (Intraday)."""
    now_ny   = datetime.now(TZ)
    start_ny = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
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
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df["time"] = pd.to_datetime(df["t"])
        df = df.sort_values("time").reset_index(drop=True)
        return df[["time", "open", "high", "low", "close", "volume"]]

    except Exception as e:
        print(f"❌ خطأ في جلب بيانات {ticker}: {e}")
        return pd.DataFrame()


def fetch_daily_bars_for_ema(ticker: str, days: int = 320) -> pd.DataFrame:
    """يجلب شموع يومية لحساب EMA200."""
    end   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        response = requests.get(
            f"{ALPACA_DATA_URL}/v2/stocks/{ticker}/bars",
            headers=HEADERS,
            params={
                "timeframe": "1Day",
                "start": start,
                "end": end,
                "limit": 400,
                "feed": "iex",
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
        print(f"❌ خطأ في جلب بيانات يومية {ticker}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────
# 2. حساب المؤشرات
# ─────────────────────────────────────────

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    typical_price     = (df["high"] + df["low"] + df["close"]) / 3
    cumulative_tp_vol = (typical_price * df["volume"]).cumsum()
    cumulative_vol    = df["volume"].cumsum()
    return cumulative_tp_vol / cumulative_vol


def calculate_rsi(prices: pd.Series, period: int = S2_RSI_PERIOD) -> pd.Series:
    delta    = prices.diff()
    gain     = delta.where(delta > 0, 0.0)
    loss     = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs  = avg_gain / avg_loss.where(avg_loss.abs() > 1e-12, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calculate_ema200(df_daily: pd.DataFrame) -> float:
    """يحسب EMA200 من الشموع اليومية."""
    if len(df_daily) < EMA_TREND_PERIOD:
        return 0.0
    ema = df_daily["close"].ewm(span=EMA_TREND_PERIOD, adjust=False).mean()
    return round(float(ema.iloc[-1]), 2)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df         = df.copy()
    df["vwap"] = calculate_vwap(df)
    df["rsi"]  = calculate_rsi(df["close"])
    df["atr"]  = calculate_atr(df)
    return df


# ─────────────────────────────────────────
# 3. شروط الدخول
# ─────────────────────────────────────────

def _load_recent_trades(days: int = 30) -> list[dict]:
    """
    يحمّل صفقات آخر N يوم من مجلد logs/trades.
    يعمل مع نظام reporter.py الحالي.
    """
    import json, os
    from datetime import date, timedelta

    logs_dir  = os.getenv("RENDER_DISK_PATH", "logs")
    logs_dir  = os.path.join(logs_dir, "trades")
    all_trades = []

    for i in range(days):
        day      = (date.today() - timedelta(days=i)).isoformat()
        log_path = os.path.join(logs_dir, f"trades_{day}.json")
        if not os.path.exists(log_path):
            continue
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                day_trades = json.load(f)
                # فقط صفقات meanrev
                all_trades.extend([t for t in day_trades if t.get("strategy") == "meanrev"])
        except Exception:
            continue

    return all_trades


def _compute_pf_per_ticker(trades: list[dict]) -> dict[str, float]:
    """
    يحسب Profit Factor لكل سهم.
    PF = مجموع الأرباح ÷ مجموع الخسائر
    """
    from collections import defaultdict

    gross_win  = defaultdict(float)
    gross_loss = defaultdict(float)

    for t in trades:
        ticker = t.get("ticker", "")
        pnl    = float(t.get("pnl", 0))
        if pnl > 0:
            gross_win[ticker]  += pnl
        elif pnl < 0:
            gross_loss[ticker] += abs(pnl)

    scores = {}
    all_tickers = set(gross_win) | set(gross_loss)
    for ticker in all_tickers:
        win  = gross_win.get(ticker, 0)
        loss = gross_loss.get(ticker, 1e-9)
        scores[ticker] = round(win / loss, 3)

    return scores


def refresh_allowed_tickers(candidate_tickers: list[str] | None = None) -> set[str]:
    """
    ⑥ يُحدّث قائمة الأسهم المسموحة ديناميكياً.

    المنطق:
    1. يحمّل صفقات آخر 30 يوم من السجلات الحية
    2. يحسب PF لكل سهم
    3. يحذف أسوأ 30% (PF منخفض)
    4. إذا لا توجد بيانات كافية → يسمح بكل الأسهم (fallback)

    يُستدعى من pre_market_routine في main.py مرة يومياً.
    """
    from datetime import datetime as _dt

    trades = _load_recent_trades(days=30)

    if not trades:
        print("⚠️  لا توجد بيانات تداول لـ 30 يوم — لا فلترة (كل الأسهم مسموحة)")
        if candidate_tickers:
            _ticker_cache["allowed"]      = set(candidate_tickers)
            _ticker_cache["pf_scores"]    = {}
            _ticker_cache["last_updated"] = _dt.utcnow()
        return _ticker_cache["allowed"]

    pf_scores = _compute_pf_per_ticker(trades)

    # فقط الأسهم التي لديها صفقات كافية
    qualified = {t: pf for t, pf in pf_scores.items()
                 if sum(1 for tr in trades if tr.get("ticker") == t) >= MIN_TRADES_REQUIRED}

    if not qualified:
        print("⚠️  لا توجد أسهم بصفقات كافية — لا فلترة")
        if candidate_tickers:
            _ticker_cache["allowed"] = set(candidate_tickers)
        _ticker_cache["last_updated"] = _dt.utcnow()
        return _ticker_cache["allowed"]

    # ترتيب تصاعدي وحذف أسوأ 30%
    sorted_tickers = sorted(qualified.items(), key=lambda x: x[1])
    cut_idx        = max(1, int(len(sorted_tickers) * PF_PERCENTILE_CUT))
    blocked        = {t for t, _ in sorted_tickers[:cut_idx]}
    allowed        = {t for t, _ in sorted_tickers[cut_idx:]}

    # إذا أُعطيت قائمة مرشحة، نتقاطع معها فقط
    if candidate_tickers:
        allowed = allowed & set(candidate_tickers)
        # الأسهم الجديدة التي ليس لها سجل → مسموحة تلقائياً (لا بيانات = لا حكم)
        new_tickers = set(candidate_tickers) - set(qualified.keys())
        allowed     = allowed | new_tickers

    _ticker_cache["allowed"]      = allowed
    _ticker_cache["pf_scores"]    = qualified
    _ticker_cache["last_updated"] = _dt.utcnow()

    print(f"📊 فلترة الأسهم الديناميكية — آخر 30 يوم:")
    for ticker, pf in sorted(qualified.items(), key=lambda x: -x[1]):
        status = "✅" if ticker in allowed else "❌"
        print(f"   {status} {ticker:6s} | PF={pf:.2f}")
    print(f"   محظورة: {blocked} | مسموحة: {allowed}")

    return allowed


def check_allowed_ticker(ticker: str) -> tuple[bool, str]:
    """
    ⑥ فلتر الأسهم الديناميكي.
    إذا الكاش فارغ (أول تشغيل) → يسمح بالمرور ويكتفي بتحذير.
    الكاش يُحدَّث يومياً من pre_market_routine.
    """
    from datetime import datetime as _dt

    # إذا الكاش فارغ تماماً → اسمح وسجّل تحذير
    if not _ticker_cache["allowed"]:
        return True, f"⚠️  كاش الأسهم فارغ — {ticker} مسموح مؤقتاً (يُحدَّث عند pre_market)"

    if ticker not in _ticker_cache["allowed"]:
        pf = _ticker_cache["pf_scores"].get(ticker, None)
        pf_str = f"PF={pf:.2f}" if pf is not None else "لا بيانات كافية"
        return False, f"❌ {ticker} محظور بناءً على الأداء الأخير ({pf_str})"

    pf = _ticker_cache["pf_scores"].get(ticker, None)
    pf_str = f"PF={pf:.2f}" if pf is not None else "سهم جديد"
    return True, f"✅ {ticker} مسموح ({pf_str})"


def check_trading_window() -> tuple[bool, str]:
    """منع الدخول في أول 30 دقيقة وآخر 30 دقيقة."""
    now_ny = datetime.now(TZ)
    trade_start = now_ny.replace(hour=TRADE_START_HOUR, minute=TRADE_START_MINUTE, second=0, microsecond=0)
    trade_end   = now_ny.replace(hour=TRADE_END_HOUR,   minute=TRADE_END_MINUTE,   second=0, microsecond=0)

    if now_ny < trade_start:
        remaining = int((trade_start - now_ny).total_seconds() / 60)
        return False, f"⏳ قبل وقت التداول — يبدأ بعد {remaining} دقيقة (10:00 AM)"
    if now_ny > trade_end:
        return False, "⏰ انتهى وقت التداول (بعد 3:30 PM)"

    return True, f"✅ وقت التداول مناسب ({now_ny.strftime('%H:%M')})"


def check_trend_filter(ema200: float, current_price: float) -> tuple[bool, str]:
    """
    ① فلتر الاتجاه — Long فوق EMA200 فقط
    MeanRev في اتجاه صاعد = احتمالية أعلى للارتداد
    """
    if ema200 <= 0:
        return False, "EMA200 غير متاح — بيانات يومية غير كافية"

    if current_price < ema200:
        return False, f"السعر ({current_price:.2f}) تحت EMA200 ({ema200:.2f}) — لا Long في اتجاه هابط"

    gap_pct = (current_price - ema200) / ema200
    return True, f"✅ السعر فوق EMA200 بنسبة {gap_pct:.1%}"


def check_volatility_regime(df: pd.DataFrame) -> tuple[bool, str, float]:
    """
    ④ فلتر Volatility Regime
    ATR بين 0.8% و 3.0% فقط — MeanRev تعيش في التقلب المتوسط
    """
    last    = df.iloc[-1]
    atr     = last["atr"]
    atr_pct = atr / last["close"]

    if pd.isna(atr):
        return False, "ATR غير متاح — بيانات غير كافية", 0.0

    if atr_pct < ATR_MIN_PCT:
        return False, f"ATR منخفض جداً ({atr_pct:.2%}) — السوق راكد، ارتداد ضعيف", atr

    if atr_pct > ATR_MAX_PCT:
        return False, f"ATR مرتفع جداً ({atr_pct:.2%}) — تقلب خطر، الوقف سيُضرب", atr

    return True, f"✅ ATR في النطاق المثالي ({atr_pct:.2%})", atr


def check_vwap_quality(df: pd.DataFrame) -> tuple[bool, str, float, float]:
    """
    ② جودة الدخول — السعر بعيد عن VWAP ≥ 1.5%
    كلما ابتعد السعر عن VWAP كلما كان الارتداد أقوى
    """
    last      = df.iloc[-1]
    price     = last["close"]
    vwap      = last["vwap"]
    deviation = (vwap - price) / vwap

    if price >= vwap:
        return False, f"السعر ({price:.2f}) فوق VWAP ({vwap:.2f}) — لا تشبع بيعي", vwap, deviation

    if deviation < VWAP_MIN_DEVIATION_PCT:
        return False, (
            f"ابتعاد ضعيف عن VWAP ({deviation:.2%}) — "
            f"المطلوب ≥ {VWAP_MIN_DEVIATION_PCT:.0%}"
        ), vwap, deviation

    return True, f"✅ السعر أسفل VWAP بنسبة {deviation:.2%}", vwap, deviation


def check_rsi_quality(df: pd.DataFrame) -> tuple[bool, str, float, str]:
    """
    ② جودة الدخول — RSI < 25 (جودة عالية) أو RSI < 30 (جودة عادية)
    """
    last_rsi = df["rsi"].iloc[-1]

    if pd.isna(last_rsi):
        return False, "RSI غير متاح — بيانات غير كافية", 0.0, 'none'

    if last_rsi < RSI_HIGH_QUALITY:
        return True, f"✅ RSI تشبع عالي الجودة ({last_rsi:.1f} < {RSI_HIGH_QUALITY})", last_rsi, 'high'

    if last_rsi < 30:
        return True, f"✅ RSI تشبع بيعي ({last_rsi:.1f} < 30)", last_rsi, 'standard'

    return False, f"RSI ({last_rsi:.1f}) فوق 30 — لا تشبع بيعي كافٍ", last_rsi, 'none'


def check_reversal_candle(df: pd.DataFrame) -> tuple[bool, str]:
    """
    ③ شمعة انعكاسية — أي شمعة خضراء تكفي
    الشمعة الأخيرة يجب أن تُغلق أعلى من فتحها
    كدليل على بدء الارتداد وليس استمرار الهبوط.
    """
    curr = df.iloc[-1]

    if curr["close"] <= curr["open"]:
        diff = curr["open"] - curr["close"]
        return False, f"الشمعة الأخيرة حمراء — close={curr['close']:.2f} < open={curr['open']:.2f} (فرق {diff:.2f})"

    body_pct = (curr["close"] - curr["open"]) / curr["open"]
    return True, f"✅ شمعة خضراء ({body_pct:.2%})"


def check_risk_reward(entry: float, stop_loss: float, tp2: float) -> tuple[bool, str]:
    """نسبة R/R مقارنة بـ TP2 يجب أن تكون ≥ 2.0x"""
    risk   = entry - stop_loss
    reward = tp2 - entry

    if risk <= 0:
        return False, "وقف الخسارة أعلى من سعر الدخول"

    rr_ratio = reward / risk
    if rr_ratio < 2.0:
        return False, f"نسبة R/R ضعيفة ({rr_ratio:.1f}x) — المطلوب 2.0x (TP2)"

    return True, f"✅ نسبة R/R: {rr_ratio:.1f}x"


# ─────────────────────────────────────────
# 4. حساب نقاط الدخول والخروج
# ─────────────────────────────────────────

def calculate_levels(df: pd.DataFrame) -> tuple[float, float, float, float, float]:
    """
    يحسب:
    - entry      : آخر سعر إغلاق
    - stop_loss  : أدنى Low لآخر 3 شموع
    - tp1        : entry + risk × 1R (50% خروج هنا)
    - tp2        : entry + risk × 3R (50% خروج هنا)
    - trail_step : خطوة الوقف المتحرك (0.5% من سعر الدخول)
    """
    last       = df.iloc[-1]
    entry      = round(last["close"], 2)
    stop_loss  = round(df["low"].iloc[-3:].min(), 2)
    risk       = entry - stop_loss

    tp1        = round(entry + risk * TP1_R, 2)
    tp2        = round(entry + risk * TP2_R, 2)
    trail_step = round(entry * TRAIL_STEP_PCT, 2)

    return entry, stop_loss, tp1, tp2, trail_step


# ─────────────────────────────────────────
# 5. الوقف المتحرك
# ─────────────────────────────────────────

def update_trailing_stop(
    current_price: float,
    current_stop:  float,
    trail_step:    float,
) -> float:
    """يحدّث الوقف المتحرك — يتحرك للأعلى فقط."""
    new_stop = current_price - trail_step
    return round(max(new_stop, current_stop), 2)




# ─────────────────────────────────────────
# فلتر Liquidity Sweep
# ─────────────────────────────────────────

def fetch_prev_day_low(ticker: str) -> float | None:
    """يجلب أدنى سعر ليوم التداول السابق."""
    end   = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=5)  # 5 أيام للأمان (عطل نهاية الأسبوع)

    try:
        response = requests.get(
            f"{ALPACA_DATA_URL}/v2/stocks/{ticker}/bars",
            headers=HEADERS,
            params={
                "timeframe": "1Day",
                "start":     start.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end":       end.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit":     5,
                "feed":      "iex",
            },
            timeout=10,
        )
        bars = response.json().get("bars", [])
        if not bars:
            return None
        # آخر شمعة يومية مكتملة = يوم التداول السابق
        return float(bars[-1]["l"])

    except Exception:
        return None


def check_liquidity_sweep(df: pd.DataFrame, prev_day_low: float | None) -> tuple[bool, str]:
    """
    فلتر Liquidity Sweep:
    الشرط: آخر شمعة كسرت Low اليوم السابق ثم أغلقت فوقه.
    هذا يعني أن السوق كنس السيولة ثم ارتد — إشارة ارتداد قوية.
    """
    if prev_day_low is None:
        return True, "⚠️ Sweep: لا يوجد Low أمس — تم التخطي"

    last = df.iloc[-1]

    # الشرط ①: الشمعة كسرت Low أمس (low < prev_day_low)
    swept = last["low"] < prev_day_low

    # الشرط ②: الإغلاق فوق Low أمس (close > prev_day_low)
    recovered = last["close"] > prev_day_low

    if swept and recovered:
        return True, f"✅ Liquidity Sweep | كسر {prev_day_low:.2f} وأغلق فوقه ({last['close']:.2f})"

    if not swept:
        return False, f"لم يكسر Low أمس ({prev_day_low:.2f}) — لا Sweep"

    # swept لكن لم يُغلق فوقه
    return False, f"كسر Low أمس ({prev_day_low:.2f}) لكن أغلق تحته ({last['close']:.2f}) — Sweep فاشل"

# ─────────────────────────────────────────
# 6. الدالة الرئيسية
# ─────────────────────────────────────────

def analyze(ticker: str, ema_above: bool = False) -> MeanRevSignal:
    """
    يحلل السهم ويُرجع إشارة دخول أو رفض.
    الترتيب:
      ⑥ فلتر الأسهم → وقت → ① EMA200 → جلب البيانات →
      ④ ATR Regime → ② VWAP+RSI → ③ شمعة → R/R → إشارة
    """

    # ── ⑥ فلتر الأسهم المسموحة
    allowed_ok, allowed_msg = check_allowed_ticker(ticker)
    if not allowed_ok:
        return _no_signal(ticker, allowed_msg)

    # ── فلتر الوقت
    time_ok, time_msg = check_trading_window()
    if not time_ok:
        return _no_signal(ticker, time_msg)

    # ── ① فلتر EMA200
    if ema_above:
        # تم التحقق مسبقاً في universe.py — نتخطى جلب البيانات مرة ثانية
        pass
    else:
        daily_df = fetch_daily_bars_for_ema(ticker)
        if daily_df.empty:
            return _no_signal(ticker, "❌ لا توجد بيانات يومية لحساب EMA200")

        ema200 = calculate_ema200(daily_df)
        current_close = daily_df["close"].iloc[-1]

        trend_ok, trend_msg = check_trend_filter(ema200, current_close)
        if not trend_ok:
            return _no_signal(ticker, f"❌ EMA200: {trend_msg}")

    # ── جلب بيانات اليوم
    df = fetch_intraday_bars(ticker)
    if df.empty or len(df) < ATR_PERIOD + 5:
        return _no_signal(ticker, "بيانات اليوم غير كافية")

    df = add_indicators(df)

    # ── ④ فلتر Volatility Regime
    atr_ok, atr_msg, atr_val = check_volatility_regime(df)
    if not atr_ok:
        return _no_signal(ticker, f"❌ ATR: {atr_msg}")

    # ── ② جودة VWAP
    vwap_ok, vwap_msg, vwap, deviation = check_vwap_quality(df)
    if not vwap_ok:
        return _no_signal(ticker, f"❌ VWAP: {vwap_msg}")

    # ── ② جودة RSI
    rsi_ok, rsi_msg, rsi_val, quality = check_rsi_quality(df)
    if not rsi_ok:
        return _no_signal(ticker, f"❌ RSI: {rsi_msg}")

    # ── Liquidity Sweep
    prev_low = fetch_prev_day_low(ticker)
    sweep_ok, sweep_msg = check_liquidity_sweep(df, prev_low)
    if not sweep_ok:
        return _no_signal(ticker, f"❌ Sweep: {sweep_msg}")

    # ── ③ شمعة الانعكاس
    candle_ok, candle_msg = check_reversal_candle(df)
    if not candle_ok:
        return _no_signal(ticker, f"❌ الشمعة: {candle_msg}")

    # ── حساب المستويات
    entry, stop_loss, tp1, tp2, trail_step = calculate_levels(df)

    # ── فلتر R/R
    rr_ok, rr_msg = check_risk_reward(entry, stop_loss, tp2)
    if not rr_ok:
        return _no_signal(ticker, f"❌ R/R: {rr_msg}")

    # ── تحديد جودة الإشارة
    signal_quality = 'high' if quality == 'high' and candle_msg.startswith('✅ Hammer') or 'Engulfing' in candle_msg else 'standard'

    return MeanRevSignal(
        ticker=ticker,
        has_signal=True,
        entry_price=entry,
        stop_loss=stop_loss,
        target_tp1=tp1,
        target_tp2=tp2,
        target=tp2,           # للتوافق مع executor
        vwap=vwap,
        rsi=rsi_val,
        atr=atr_val,
        trail_step=trail_step,
        signal_quality=signal_quality,
        reason=(
            f"{trend_msg} | {atr_msg} | {vwap_msg} | "
            f"{rsi_msg} | {sweep_msg} | {candle_msg} | {rr_msg}"
        ),
    )
