# =============================================================
# selector.py — يصنّف الأسهم ويشغّل استراتيجية Mean Reversion
# يفرض حدود MAX_LONG / MAX_SHORT / MAX_TOTAL
# =============================================================

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass

from universe import _safe_get
from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_DATA_URL,
    MAX_LONG,
    MAX_SHORT,
    MAX_TOTAL,
    MAX_MEANREV_LONG,
    MAX_MEANREV_SHORT,
    MAX_MOMENTUM_LONG,
    MAX_MOMENTUM_SHORT,
)
from strategy_meanrev import (
    analyze as meanrev_analyze,
    MeanRevSignal,
    calc_adx as calculate_adx,
)
from strategy_momentum import (
    analyze as momentum_analyze,
    MomentumSignal,
)


# ─────────────────────────────────────────
# نموذج نتيجة التحليل
# ─────────────────────────────────────────

@dataclass
class SelectionResult:
    ticker:  str
    adx:     float
    atr_pct: float
    reason:  str


# ─────────────────────────────────────────
# 2. Signal Scoring System
# ─────────────────────────────────────────

def score_signal(sig: MeanRevSignal) -> float:
    """
    يحسب score لكل إشارة من 0 إلى 100.
    كلما كان أعلى كلما كانت الإشارة أقوى.

    المعايير:
    ┌─────────────────────────┬────────┐
    │ الجودة                  │ النقاط │
    ├─────────────────────────┼────────┤
    │ signal_quality = high   │  +30   │
    │ liquidity_sweep         │  +25   │
    │ RSI < 20 (تشبع شديد)   │  +20   │
    │ RSI < 25                │  +10   │
    │ RSI > 80 (SHORT شديد)   │  +20   │
    │ تايم فريم 1Day          │  +10   │
    │ تايم فريم 1Hour         │  +5    │
    │ ADX مناسب (15-30 REV)   │  +5    │
    │ R/R ratio جيد           │  +5    │
    └─────────────────────────┴────────┘
    """
    score = 0.0

    # جودة الإشارة
    if sig.signal_quality == "high":
        score += 30
    if sig.liquidity_sweep:
        score += 25

    # RSI — تشبع بيعي أو شرائي
    if sig.side == "long":
        if sig.rsi < 20:
            score += 20
        elif sig.rsi < 25:
            score += 10
    else:
        if sig.rsi > 80:
            score += 20
        elif sig.rsi > 75:
            score += 10

    # تايم فريم — الأولوية لـ 1Day
    if "[1Day]" in sig.reason:
        score += 10
    elif "[1Hour]" in sig.reason:
        score += 5
    # 15Min = 0 (الأكثر ضوضاء)

    # ADX مناسب لـ MeanRev (15-30)
    if "MOM" not in sig.reason and 15 <= sig.adx <= 30:
        score += 5

    # R/R ratio
    if sig.entry_price > 0 and sig.stop_loss > 0:
        risk   = abs(sig.entry_price - sig.stop_loss)
        reward = abs(sig.target_tp2 - sig.entry_price)
        rr     = reward / risk if risk > 0 else 0
        if rr >= 2.5:
            score += 5

    return round(score, 2)


# ─────────────────────────────────────────
# 1. جلب البيانات اليومية
# ─────────────────────────────────────────

def fetch_daily_bars(ticker: str, days: int = 30) -> pd.DataFrame:
    """يجلب الشموع اليومية لحساب مؤشرات التصنيف."""
    end   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        response = _safe_get(
            f"{ALPACA_DATA_URL}/v2/stocks/{ticker}/bars",
            {
                "timeframe": "1Day",
                "start":     start,
                "end":       end,
                "limit":     days,
                "feed":      "iex",
            },
        )
        if not response:
            return pd.DataFrame()
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
    يُصفّي الإشارات بناءً على حصة كل استراتيجية:
    MeanRev  : MAX_MEANREV_LONG=2  | MAX_MEANREV_SHORT=1
    Momentum : MAX_MOMENTUM_LONG=1 | MAX_MOMENTUM_SHORT=1
    الإجمالي : MAX_TOTAL=5
    """
    # المراكز المفتوحة حالياً حسب الاستراتيجية
    open_rev_long   = sum(1 for t in current_positions.values() if t == ("long",  "meanrev"))
    open_rev_short  = sum(1 for t in current_positions.values() if t == ("short", "meanrev"))
    open_mom_long   = sum(1 for t in current_positions.values() if t == ("long",  "momentum"))
    open_mom_short  = sum(1 for t in current_positions.values() if t == ("short", "momentum"))
    open_total      = len(current_positions)

    # استبعاد الأسهم المفتوحة مسبقاً
    signals = [s for s in signals if s.ticker not in current_positions]

    # ── رفض الإشارات ضعيفة الجودة (Score < 10)
    MIN_SCORE = 10
    rejected_weak = [s for s in signals if score_signal(s) < MIN_SCORE]
    signals       = [s for s in signals if score_signal(s) >= MIN_SCORE]
    if rejected_weak:
        print(f"  ⛔ رُفض {len(rejected_weak)} إشارة Score < {MIN_SCORE}: {[s.ticker for s in rejected_weak]}")

    # ── ترتيب بالـ Score (الأعلى أولاً) — التعديل الجديد
    signals.sort(key=lambda x: score_signal(x), reverse=True)

    # طباعة الترتيب
    print("\n📊 Signal Ranking:")
    for i, s in enumerate(signals[:10], 1):
        sc = score_signal(s)
        tf = "[1Day]" if "[1Day]" in s.reason else "[1Hour]" if "[1Hour]" in s.reason else "[15Min]"
        print(f"   #{i} {s.ticker:6s} {s.side.upper():5s} | Score={sc:.0f} | RSI={s.rsi:.1f} | {tf} | {'⭐' if s.signal_quality=='high' else ''} {'🎯' if s.liquidity_sweep else ''}")

    selected     = []
    add_rev_l    = 0
    add_rev_s    = 0
    add_mom_l    = 0
    add_mom_s    = 0

    for sig in signals:
        if open_total + len(selected) >= MAX_TOTAL:
            break

        is_momentum = "MOM" in sig.reason

        if sig.side == "long":
            if is_momentum:
                if open_mom_long + add_mom_l < MAX_MOMENTUM_LONG:
                    selected.append(sig)
                    add_mom_l += 1
            else:
                if open_rev_long + add_rev_l < MAX_MEANREV_LONG:
                    selected.append(sig)
                    add_rev_l += 1

        elif sig.side == "short":
            if is_momentum:
                if open_mom_short + add_mom_s < MAX_MOMENTUM_SHORT:
                    selected.append(sig)
                    add_mom_s += 1
            else:
                if open_rev_short + add_rev_s < MAX_MEANREV_SHORT:
                    selected.append(sig)
                    add_rev_s += 1

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

        # ── تشغيل الاستراتيجيتين على كل سهم
        candidates = []

        # 1. MeanRev
        rev_signal = meanrev_analyze(
            ticker=ticker,
            ema_above=ema_above,
            exchange=exchange,
            ema200=ema200,
        )
        if rev_signal.has_signal:
            candidates.append(rev_signal)

        # 2. Momentum — نحوّله لـ MeanRevSignal للتوحيد
        mom_raw = momentum_analyze(
            ticker=ticker,
            exchange=exchange,
            ema200=ema200,
        )
        if mom_raw.has_signal:
            mom_unified = MeanRevSignal(
                ticker=mom_raw.ticker,
                side=mom_raw.side,
                has_signal=True,
                reason=mom_raw.reason,
                entry_price=mom_raw.entry_price,
                stop_loss=mom_raw.stop_loss,
                target_tp1=mom_raw.target_tp1,
                target_tp2=mom_raw.target_tp2,
                trail_step=mom_raw.trail_step,
                rsi=mom_raw.rsi,
                atr=mom_raw.atr,
                atr_pct=mom_raw.atr_pct,
                vwap=mom_raw.vwap,
                adx=mom_raw.adx,
                ema200=ema200,
                signal_quality=mom_raw.signal_quality,
                liquidity_sweep=False,
            )
            candidates.append(mom_unified)

        # ── اختيار الأفضل Score من بين الاستراتيجيتين
        if not candidates:
            # لا توجد إشارة من أي استراتيجية
            last_reason = rev_signal.reason if not rev_signal.has_signal else mom_raw.reason
            print(f" | ⏭  {last_reason[:50]}")
        elif len(candidates) == 1:
            # استراتيجية واحدة أعطت إشارة
            best = candidates[0]
            all_signals.append(best)
            sc       = score_signal(best)
            tag      = "MOM" if "MOM" in best.reason else "REV"
            side_tag = "🟢 LONG" if best.side == "long" else "🔴 SHORT"
            print(f" | [{tag}] {side_tag} ✅ Score={sc:.0f} | RSI={best.rsi:.1f} | entry=${best.entry_price:.2f} | TP2=${best.target_tp2:.2f}")
        else:
            # كلتا الاستراتيجيتين أعطتا إشارة — اختر الأعلى Score
            best      = max(candidates, key=lambda x: score_signal(x))
            rejected  = [c for c in candidates if c is not best][0]
            all_signals.append(best)
            sc        = score_signal(best)
            sc_rej    = score_signal(rejected)
            tag       = "MOM" if "MOM" in best.reason else "REV"
            rej_tag   = "MOM" if "MOM" in rejected.reason else "REV"
            side_tag  = "🟢 LONG" if best.side == "long" else "🔴 SHORT"
            print(
                f" | [{tag}] {side_tag} ✅ Score={sc:.0f} | RSI={best.rsi:.1f} | entry=${best.entry_price:.2f} | TP2=${best.target_tp2:.2f}"
                f"  ← فاز على [{rej_tag}] Score={sc_rej:.0f}"
            )

        summary.append(SelectionResult(
            ticker=ticker,
            adx=adx,
            atr_pct=atr_pct,
            reason=rev_signal.reason if not rev_signal.has_signal else rev_signal.reason,
        ))

    # تطبيق حدود المراكز (مرتبة بالـ Score)
    filtered_signals = apply_position_limits(all_signals, current_positions)

    print("─" * 60)
    print(f"✅ إشارات متاحة: {len(all_signals)} | مختارة بعد Score+Limits: {len(filtered_signals)}")
    if filtered_signals:
        print("🏆 الإشارات المختارة:")
        for s in filtered_signals:
            sc = score_signal(s)
            print(f"   ✅ {s.ticker:6s} {s.side.upper():5s} | Score={sc:.0f} | entry=${s.entry_price:.2f} | TP2=${s.target_tp2:.2f}")

    return {
        "meanrev": filtered_signals,
        "summary": summary,
    }
