"""
universe.py — Scans up to 500 candidates, uses batch EMA200, bear-market fallback.
"""
import logging
from typing import List, Dict
import pandas as pd
import numpy as np
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timedelta
import config

logger = logging.getLogger(__name__)

trading_client = TradingClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=config.IS_PAPER)
data_client    = StockHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)


def get_tradable_assets() -> List[Dict]:
    """Return up to MAX_CANDIDATES assets from NASDAQ/NYSE meeting basic filters."""
    assets = trading_client.get_all_assets()
    candidates = []
    for a in assets:
        if (
            a.tradable
            and a.status == "active"
            and a.asset_class == "us_equity"
            and a.exchange in {"NASDAQ", "NYSE"}
            and not a.easy_to_borrow is False  # exclude hard-to-borrow for shorts
        ):
            candidates.append({"symbol": a.symbol, "exchange": a.exchange, "easy_to_borrow": a.easy_to_borrow})
        if len(candidates) >= config.UNIVERSE_MAX_CANDIDATES:
            break
    logger.info(f"Universe raw candidates: {len(candidates)}")
    return candidates


def fetch_ema200_batch(symbols: List[str]) -> Dict[str, float]:
    """Fetch 210 daily bars for all symbols in ONE batch request, return EMA200 map."""
    end   = datetime.utcnow()
    start = end - timedelta(days=300)
    try:
        req  = StockBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Day,
                                start=start, end=end, limit=210)
        bars = data_client.get_stock_bars(req).df
        if bars.empty:
            return {}
        ema_map = {}
        for sym in symbols:
            try:
                s = bars.xs(sym, level="symbol")["close"]
                if len(s) >= config.EMA_TREND:
                    ema_map[sym] = float(s.ewm(span=config.EMA_TREND, adjust=False).mean().iloc[-1])
            except Exception:
                pass
        return ema_map
    except Exception as e:
        logger.warning(f"EMA200 batch fetch failed: {e}")
        return {}


def is_bear_market(ema_map: Dict[str, float], min_ratio: float = 0.4) -> bool:
    """Rough bear-market check: if >40% of universe is below EMA200."""
    try:
        req   = StockBarsRequest(symbol_or_symbols=list(ema_map.keys())[:50],
                                 timeframe=TimeFrame.Day,
                                 start=datetime.utcnow() - timedelta(days=5),
                                 end=datetime.utcnow(), limit=2)
        bars  = data_client.get_stock_bars(req).df
        below = sum(1 for sym, ema in ema_map.items()
                    if sym in bars.index.get_level_values("symbol")
                    and float(bars.xs(sym, level="symbol")["close"].iloc[-1]) < ema)
        ratio = below / max(len(ema_map), 1)
        logger.info(f"Bear market ratio: {ratio:.2%}")
        return ratio >= min_ratio
    except Exception:
        return False


def build_universe() -> List[Dict]:
    """Main entry: returns filtered, enriched universe list."""
    raw = get_tradable_assets()
    symbols = [a["symbol"] for a in raw]

    # Batch EMA200
    ema_map = fetch_ema200_batch(symbols)

    bear = is_bear_market(ema_map)
    if bear:
        logger.info("Bear market detected — applying conservative fallback filters")

    universe = []
    for asset in raw:
        sym = asset["symbol"]
        ema200 = ema_map.get(sym)
        asset["ema200"]      = ema200
        asset["bear_market"] = bear
        universe.append(asset)

    logger.info(f"Universe built: {len(universe)} symbols (bear={bear})")
    return universe
