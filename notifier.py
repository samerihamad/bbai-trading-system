"""
notifier.py — Telegram notifications.
"""
import logging
import requests
import config

logger = logging.getLogger(__name__)

BASE_URL = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"


def send(message: str) -> bool:
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.debug("Telegram not configured, skipping notification")
        return False
    try:
        r = requests.post(
            f"{BASE_URL}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")
        return False


def notify_entry(signal: dict, shares: int, equity: float):
    side_emoji = "🟢" if signal["side"] == "long" else "🔴"
    msg = (
        f"{side_emoji} <b>ENTER {signal['side'].upper()}</b> — {signal['symbol']}\n"
        f"Price: ${signal['price']:.2f} | Shares: {shares}\n"
        f"RSI: {signal['rsi']:.1f} | ATR: {signal['atr']:.2f}\n"
        f"Stop: ${signal['stop']:.2f} | Target: ${signal['target']:.2f}\n"
        f"Confidence: {signal['confidence']:.0%} | Equity: ${equity:,.0f}"
    )
    send(msg)


def notify_exit(symbol: str, side: str, pnl: float, reason: str):
    emoji = "✅" if pnl >= 0 else "❌"
    msg = (
        f"{emoji} <b>EXIT {side.upper()}</b> — {symbol}\n"
        f"PnL: ${pnl:+.2f} | Reason: {reason}"
    )
    send(msg)


def notify_error(error: str):
    send(f"⚠️ <b>ERROR</b>\n{error}")


def notify_daily_summary(report: dict):
    msg = (
        f"📊 <b>Daily Summary</b>\n"
        f"Trades: {report.get('total_trades', 0)}\n"
        f"Wins: {report.get('wins', 0)} | Losses: {report.get('losses', 0)}\n"
        f"Total PnL: ${report.get('total_pnl', 0):+.2f}\n"
        f"Win Rate: {report.get('win_rate', 0):.0%}"
    )
    send(msg)
