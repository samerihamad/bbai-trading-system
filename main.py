"""
main.py — Main trading loop.
No 'schedule' library — pure pytz time check every 30 seconds.
Handles EST/EDT automatically.
"""
import logging
import time
from datetime import datetime
import pytz

import config
import universe
import strategy_meanrev as strategy
import selector
import risk
import executor
import notifier
import reporter
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import timedelta

# ─── Logging Setup ───────────────────────────────────────────────────────────
logging.basicConfig(
    level   = getattr(logging, config.LOG_LEVEL),
    format  = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.LOG_FILE),
    ]
)
logger = logging.getLogger("main")

data_client = StockHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)

NY_TZ = pytz.timezone(config.TIMEZONE)


def ny_now() -> datetime:
    """Current New York time — pytz handles EST/EDT transition automatically."""
    return datetime.now(NY_TZ)


def is_market_open() -> bool:
    now = ny_now()
    if now.weekday() >= 5:   # Saturday / Sunday
        return False
    t = now.strftime("%H:%M")
    return config.MARKET_OPEN_TIME <= t <= config.MARKET_CLOSE_TIME


def is_near_close() -> bool:
    t = ny_now().strftime("%H:%M")
    return t >= config.MARKET_CLOSE_TIME


def fetch_bars(symbol: str, bars: int = 260) -> any:
    end   = datetime.utcnow()
    start = end - timedelta(days=bars + 30)
    try:
        req  = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day,
                                start=start, end=end, limit=bars)
        df   = data_client.get_stock_bars(req).df
        return df.xs(symbol, level="symbol") if not df.empty else None
    except Exception as e:
        logger.warning(f"fetch_bars failed for {symbol}: {e}")
        return None


def run_once(universe_cache: list, positions: dict, account: dict):
    """Single iteration of the trading logic."""

    equity = account["equity"]

    # ── Exit checks ────────────────────────────────────────────────────────
    for sym, pos in list(positions.items()):
        current_price = pos["current_price"]
        pos = risk.update_peak(pos, current_price)

        reason = risk.should_exit(pos, current_price)
        if reason:
            bars = fetch_bars(sym)
            if bars is not None:
                exit_price = float(bars["close"].iloc[-1])
            else:
                exit_price = current_price

            pnl = reporter.record_trade(
                symbol=sym, side=pos["side"],
                entry=pos["entry_price"], exit_price=exit_price,
                qty=int(pos["qty"]), reason=reason
            )
            executor.exit_position(sym, int(pos["qty"]), pos["side"], reason)
            notifier.notify_exit(sym, pos["side"], pnl, reason)

    # ── Entry signals ─────────────────────────────────────────────────────
    signals = []
    for asset in universe_cache:
        bars = fetch_bars(asset["symbol"])
        if bars is None or len(bars) < config.EMA_TREND + 10:
            continue
        sig = strategy.evaluate(bars, asset)
        if sig:
            signals.append(sig)

    # Refresh positions after exits
    positions = executor.get_open_positions()
    selected  = selector.select_signals(signals, positions)

    for sig in selected:
        shares = risk.calculate_shares(sig, equity)
        if shares <= 0:
            continue
        result = executor.enter_position(sig, shares)
        if result:
            notifier.notify_entry(sig, shares, equity)


def main():
    logger.info("=== Trading System Started ===")
    logger.info(f"Mode: {'PAPER' if config.IS_PAPER else 'LIVE'} | SHORT: {config.SHORT_ENABLED}")

    universe_cache   = []
    universe_refresh = 0   # epoch seconds of last refresh
    UNIVERSE_TTL     = 3600  # refresh universe every hour

    while True:
        try:
            now = time.time()

            if not is_market_open():
                logger.debug(f"Market closed at {ny_now().strftime('%H:%M %Z')}, sleeping...")
                time.sleep(config.LOOP_INTERVAL_SECONDS)
                continue

            # Refresh universe periodically
            if now - universe_refresh > UNIVERSE_TTL or not universe_cache:
                logger.info("Refreshing universe...")
                universe_cache   = universe.build_universe()
                universe_refresh = now

            # EOD: close all and send report
            if is_near_close():
                logger.info("Near market close — closing all positions")
                executor.close_all_positions()
                report = reporter.daily_report()
                notifier.notify_daily_summary(report)
                logger.info(f"Daily report: {report}")
                # Sleep until next day
                time.sleep(60 * 20)
                continue

            account   = executor.get_account()
            positions = executor.get_open_positions()

            logger.info(
                f"[{ny_now().strftime('%H:%M %Z')}] "
                f"Equity=${account['equity']:,.0f} | "
                f"Positions={len(positions)} | "
                f"Universe={len(universe_cache)}"
            )

            run_once(universe_cache, positions, account)

        except KeyboardInterrupt:
            logger.info("Shutdown requested")
            break
        except Exception as e:
            logger.error(f"Main loop error: {e}", exc_info=True)
            notifier.notify_error(str(e))

        time.sleep(config.LOOP_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
```
