"""
risk.py — Position sizing and trailing stop / profit-factor cut logic.
"""
import logging
from typing import Dict, Optional
import config

logger = logging.getLogger(__name__)


def calculate_shares(signal: Dict, equity: float) -> int:
    """
    Risk RISK_PER_TRADE_PCT of equity per trade, sized by ATR stop distance.
    Clamps to MIN/MAX_POSITION_VALUE.
    """
    price       = signal["price"]
    stop        = signal["stop"]
    risk_amount = equity * config.RISK_PER_TRADE_PCT
    stop_dist   = abs(price - stop)

    if stop_dist == 0:
        return 0

    shares = int(risk_amount / stop_dist)
    value  = shares * price

    # Clamp
    if value < config.MIN_POSITION_VALUE:
        shares = int(config.MIN_POSITION_VALUE / price)
    if value > config.MAX_POSITION_VALUE:
        shares = int(config.MAX_POSITION_VALUE / price)

    return max(shares, 0)


def should_exit(position: Dict, current_price: float) -> Optional[str]:
    """
    Checks trailing PF cut and hard stop.
    position: {symbol, side, entry_price, shares, peak_price, stop, target}
    Returns reason string if exit needed, else None.
    """
    side        = position["side"]
    entry_price = position["entry_price"]
    peak_price  = position.get("peak_price", entry_price)
    stop        = position["stop"]
    target      = position["target"]

    if side == "long":
        pnl_from_peak = (current_price - peak_price) / peak_price if peak_price else 0
        if current_price <= stop:
            return "stop_loss"
        if current_price >= target:
            return "take_profit"
        if pnl_from_peak <= -config.PROFIT_FACTOR_CUT:
            return "trailing_cut"

    elif side == "short":
        pnl_from_peak = (peak_price - current_price) / peak_price if peak_price else 0
        if current_price >= stop:
            return "stop_loss"
        if current_price <= target:
            return "take_profit"
        if pnl_from_peak <= -config.PROFIT_FACTOR_CUT:
            return "trailing_cut"

    return None


def update_peak(position: Dict, current_price: float) -> Dict:
    """Update peak price for trailing logic."""
    side  = position["side"]
    peak  = position.get("peak_price", position["entry_price"])

    if side == "long"  and current_price > peak:
        position["peak_price"] = current_price
    elif side == "short" and current_price < peak:
        position["peak_price"] = current_price

    return position
