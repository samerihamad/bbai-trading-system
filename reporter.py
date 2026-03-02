"""
reporter.py — Tracks trades and computes daily / session statistics.
"""
import json
import logging
from datetime import date
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

TRADES_FILE = Path("trades.json")


def _load_trades() -> List[Dict]:
    if TRADES_FILE.exists():
        try:
            return json.loads(TRADES_FILE.read_text())
        except Exception:
            return []
    return []


def _save_trades(trades: List[Dict]):
    TRADES_FILE.write_text(json.dumps(trades, indent=2, default=str))


def record_trade(symbol: str, side: str, entry: float, exit_price: float,
                 qty: int, reason: str):
    trades = _load_trades()
    pnl    = (exit_price - entry) * qty if side == "long" else (entry - exit_price) * qty
    trades.append({
        "date":        str(date.today()),
        "symbol":      symbol,
        "side":        side,
        "entry":       entry,
        "exit":        exit_price,
        "qty":         qty,
        "pnl":         round(pnl, 2),
        "reason":      reason,
    })
    _save_trades(trades)
    logger.info(f"Recorded trade: {symbol} {side} pnl=${pnl:.2f} reason={reason}")
    return pnl


def daily_report() -> Dict:
    trades = _load_trades()
    today  = str(date.today())
    today_trades = [t for t in trades if t["date"] == today]

    wins   = [t for t in today_trades if t["pnl"] > 0]
    losses = [t for t in today_trades if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in today_trades)

    return {
        "total_trades": len(today_trades),
        "wins":         len(wins),
        "losses":       len(losses),
        "total_pnl":    round(total_pnl, 2),
        "win_rate":     len(wins) / len(today_trades) if today_trades else 0,
        "trades":       today_trades,
    }
