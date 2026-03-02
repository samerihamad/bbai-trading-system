"""
strategy_meanrev.py — Mean Reversion + Liquidity Sweep strategy.
Reads ALL parameters from config.py.
Supports LONG and SHORT signals.
"""
import logging
import numpy as np
import pandas as pd
from typing import Optional, Dict, Tuple
import config

logger = logging.getLogger(__name__)


def compute_rsi(closes: pd.Series, period: int = 14) -> float:
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def compute_vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> float:
    typical = (high + low + close) / 3
    return float((typical * volume).cumsum() / volume.cumsum().iloc[-1])


def compute_ema(closes: pd.Series, span: int) -> float:
    return float(closes.ewm(span=span, adjust=False).mean().iloc[-1])


def liquidity_sweep_long(high: pd.Series, low: pd.Series, close: pd.Series) -> bool:
    """
    Bullish liquidity sweep:
    Yesterday's low was broken intrabar but current candle CLOSED above yesterday's low.
    """
    if not config.LIQUIDITY_SWEEP_ENABLED or len(low) < 2:
        return False
    prev_low    = float(low.iloc[-2])
    curr_low    = float(low.iloc[-1])
    curr_close  = float(close.iloc[-1])
    return curr_low < prev_low and curr_close > prev_low


def liquidity_sweep_short(high: pd.Series, low: pd.Series, close: pd.Series) -> bool:
    """
    Bearish liquidity sweep:
    Yesterday's high was broken intrabar but current candle CLOSED below yesterday's high.
    """
    if not config.LIQUIDITY_SWEEP_ENABLED or len(high) < 2:
        return False
    prev_high   = float(high.iloc[-2])
    curr_high   = float(high.iloc[-1])
    curr_close  = float(close.iloc[-1])
    return curr_high > prev_high and curr_close < prev_high


def evaluate(bars: pd.DataFrame, asset: Dict) -> Optional[Dict]:
    """
    Evaluate a symbol and return a signal dict or None.
    bars: DataFrame with columns [open, high, low, close, volume] — last 250 rows minimum.
    asset: dict with keys symbol, exchange, ema200, easy_to_borrow.
    """
    if len(bars) < config.EMA_TREND + 10:
        return None

    closes  = bars["close"]
    highs   = bars["high"]
    lows    = bars["low"]
    volumes = bars["volume"]
    price   = float(closes.iloc[-1])

    # ── Indicators ──────────────────────────────────────────────────────────
    rsi    = compute_rsi(closes)
    atr    = compute_atr(highs, lows, closes)
    vwap   = compute_vwap(highs, lows, closes, volumes)
    ema200 = asset.get("ema200") or compute_ema(closes, config.EMA_TREND)
    ema9   = compute_ema(closes, config.EMA_SHORT)
    ema21  = compute_ema(closes, config.EMA_LONG)

    atr_pct = atr / price if price else 0

    # ── ATR filter (volatility window) ───────────────────────────────────────
    if not (config.ATR_MIN_PCT <= atr_pct <= config.ATR_MAX_PCT):
        return None

    # ── LONG Signal ──────────────────────────────────────────────────────────
    vwap_diff_pct = (price - vwap) / vwap if vwap else 0
    long_vwap_ok  = vwap_diff_pct <= config.VWAP_THRESHOLD_PCT   # price ≤ VWAP+1.2%
    long_rsi_ok   = rsi < config.RSI_OVERSOLD
    long_trend_ok = price > ema200
    long_sweep    = liquidity_sweep_long(highs, lows, closes)

    if long_rsi_ok and long_vwap_ok and long_trend_ok:
        confidence = 0.6 + (0.2 if long_sweep else 0) + (0.2 if ema9 > ema21 else 0)
        return {
            "symbol":     asset["symbol"],
            "side":       "long",
            "price":      price,
            "atr":        atr,
            "rsi":        rsi,
            "vwap":       vwap,
            "ema200":     ema200,
            "confidence": confidence,
            "stop":       price - atr * config.STOP_LOSS_ATR_MULT,
            "target":     price + atr * config.TAKE_PROFIT_ATR_MULT,
        }

    # ── SHORT Signal ─────────────────────────────────────────────────────────
    if config.SHORT_ENABLED and asset.get("exchange") in config.SHORT_EXCHANGES:
        short_rsi_ok    = rsi > config.SHORT_RSI_MIN               # RSI > 70
        above_vwap      = price > vwap                             # overextended above VWAP
        below_ema200    = price < ema200                           # bearish trend
        short_cond      = short_rsi_ok and (above_vwap or below_ema200)  # ONE of two
        short_sweep     = liquidity_sweep_short(highs, lows, closes)

        if short_cond:
            confidence = 0.6 + (0.2 if short_sweep else 0) + (0.2 if above_vwap and below_ema200 else 0)
            return {
                "symbol":     asset["symbol"],
                "side":       "short",
                "price":      price,
                "atr":        atr,
                "rsi":        rsi,
                "vwap":       vwap,
                "ema200":     ema200,
                "confidence": confidence,
                "stop":       price + atr * config.STOP_LOSS_ATR_MULT,
                "target":     price - atr * config.TAKE_PROFIT_ATR_MULT,
            }

    return None
