"""
executor.py — Places and manages orders via Alpaca API.
Handles LONG buy/sell and SHORT sell/cover.
"""
import logging
from typing import Dict, Optional
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, PositionSide
import config

logger = logging.getLogger(__name__)

client = TradingClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=config.IS_PAPER)


def get_account() -> Dict:
    acct = client.get_account()
    return {
        "equity":        float(acct.equity),
        "buying_power":  float(acct.buying_power),
        "cash":          float(acct.cash),
        "shorting_enabled": acct.shorting_enabled,
    }


def get_open_positions() -> Dict[str, Dict]:
    positions = {}
    for p in client.get_all_positions():
        side = "long" if float(p.qty) > 0 else "short"
        positions[p.symbol] = {
            "symbol":      p.symbol,
            "side":        side,
            "qty":         abs(float(p.qty)),
            "entry_price": float(p.avg_entry_price),
            "market_value": float(p.market_value),
            "unrealized_pl": float(p.unrealized_pl),
            "current_price": float(p.current_price),
        }
    return positions


def enter_position(signal: Dict, shares: int) -> Optional[Dict]:
    """Submit market order for entry."""
    if shares <= 0:
        return None
    try:
        side = OrderSide.BUY if signal["side"] == "long" else OrderSide.SELL
        req  = MarketOrderRequest(
            symbol       = signal["symbol"],
            qty          = shares,
            side         = side,
            time_in_force= TimeInForce.DAY,
        )
        order = client.submit_order(req)
        logger.info(f"ENTER {signal['side'].upper()} {signal['symbol']} x{shares} @ ~{signal['price']:.2f}")
        return {"order_id": str(order.id), "symbol": signal["symbol"],
                "side": signal["side"], "qty": shares}
    except Exception as e:
        logger.error(f"Enter position failed for {signal['symbol']}: {e}")
        return None


def exit_position(symbol: str, qty: int, side: str, reason: str = "") -> bool:
    """Submit market order to close position."""
    try:
        # Close long → SELL; close short → BUY
        exit_side = OrderSide.SELL if side == "long" else OrderSide.BUY
        req  = MarketOrderRequest(
            symbol        = symbol,
            qty           = qty,
            side          = exit_side,
            time_in_force = TimeInForce.DAY,
        )
        client.submit_order(req)
        logger.info(f"EXIT {side.upper()} {symbol} x{qty} reason={reason}")
        return True
    except Exception as e:
        logger.error(f"Exit position failed for {symbol}: {e}")
        return False


def close_all_positions():
    """Emergency close — used at end of day."""
    try:
        client.close_all_positions(cancel_orders=True)
        logger.info("All positions closed (EOD)")
    except Exception as e:
        logger.error(f"close_all_positions failed: {e}")
