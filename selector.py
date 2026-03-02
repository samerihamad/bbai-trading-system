"""
selector.py — Ranks and filters signals, enforces MAX_LONG / MAX_SHORT / MAX_TOTAL.
"""
import logging
from typing import List, Dict
import config

logger = logging.getLogger(__name__)


def select_signals(signals: List[Dict], current_positions: Dict) -> List[Dict]:
    """
    signals: list of dicts from strategy_meanrev.evaluate()
    current_positions: {symbol: side} of open positions

    Returns filtered list respecting MAX_LONG, MAX_SHORT, MAX_TOTAL.
    """
    open_longs  = sum(1 for s in current_positions.values() if s == "long")
    open_shorts = sum(1 for s in current_positions.values() if s == "short")
    open_total  = len(current_positions)

    # Skip symbols already held
    signals = [s for s in signals if s["symbol"] not in current_positions]

    # Sort by confidence descending
    signals.sort(key=lambda x: x["confidence"], reverse=True)

    selected = []
    for sig in signals:
        if open_total + len(selected) >= config.MAX_TOTAL:
            break

        if sig["side"] == "long":
            if open_longs + sum(1 for s in selected if s["side"] == "long") < config.MAX_LONG:
                selected.append(sig)

        elif sig["side"] == "short":
            if open_shorts + sum(1 for s in selected if s["side"] == "short") < config.MAX_SHORT:
                selected.append(sig)

    logger.info(f"Selected {len(selected)} new signals from {len(signals)} candidates")
    return selected
