# =============================================================
# main.py -- المحرك الرئيسي للنظام
# Loop كل 30 ثانية + pytz لقراءة وقت نيويورك (EST/EDT تلقائياً)
# يدعم أوامر Telegram: /maintenance /resume /status /stop /help
# =============================================================

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import time
import traceback
import pytz
from datetime import datetime, timedelta

from config import (
    TIMEZONE,
    MARKET_OPEN,
    MARKET_CLOSE,
    NO_OPPORTUNITY_INTERVAL,
    MAX_TOTAL,
)
from universe         import get_daily_universe
from selector         import run_selector
from executor         import (
    get_account,
    get_next_market_open,
    get_current_price,
    get_open_positions,
    sync_with_alpaca,
    open_meanrev_trade,
    monitor_trade,
    place_market_sell,
    close_all_positions,
    _save_open_trades,
    _delete_open_trades_sheets,
    OpenTrade,
)
from strategy_meanrev import refresh_allowed_tickers
from risk             import DailyRiskManager
from reporter         import record_trade, send_daily_report
from notifier         import (
    notify_pre_market,
    notify_no_opportunity,
    notify_trade_open,
    notify_trade_win,
    notify_trade_loss,
    notify_stop_updated,
    notify_system_stopped,
)
from telegram_commands import (
    system_state,
    start_command_listener,
    notify_error,
)

TZ = pytz.timezone(TIMEZONE)

# -----------------------------------------
# الحالة العامة للنظام
# -----------------------------------------

risk_manager : DailyRiskManager = DailyRiskManager()
open_trades  : list              = []
daily_stocks : dict              = {}
last_no_opp  : datetime          = datetime.now(TZ) - timedelta(hours=2)

_pre_market_done  : bool = False
_pre_alert_done   : bool = False
_close_done      : bool = False
_current_day     : str  = ""

# تتبع الأخطاء المتكررة لإرسال إشعار مرة واحدة فقط
_consecutive_errors : int  = 0
_error_notified     : bool = False


# -----------------------------------------
# الدوال المساعدة
# -----------------------------------------

def log(msg: str):
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"[{now}]  {msg}", flush=True)


def get_ny_time() -> datetime:
    return datetime.now(TZ)


def is_weekday() -> bool:
    return get_ny_time().weekday() < 5


def is_pre_market_alert_time() -> bool:
    """09:00 → رسالة تنبيه فقط 'السوق يفتح بعد 30 دقيقة'"""
    if not is_weekday():
        return False
    t = get_ny_time().strftime("%H:%M")
    return "09:00" <= t < "09:05"


def is_pre_market_time() -> bool:
    """09:35 → تشغيل Pre-Market الفعلي (اختيار الأسهم)"""
    if not is_weekday():
        return False
    t = get_ny_time().strftime("%H:%M")
    return "09:35" <= t < "09:45"


def is_market_hours() -> bool:
    if not is_weekday():
        return False
    t = get_ny_time().strftime("%H:%M")
    return MARKET_OPEN <= t <= MARKET_CLOSE


def is_close_time() -> bool:
    """
    يُرجع True فقط في نافذة إغلاق السوق: 15:45 → 16:05
    بعد 16:05 يُرجع False لتجنب إرسال التقرير عند كل Deploy.
    """
    if not is_weekday():
        return False
    t = get_ny_time().strftime("%H:%M")
    return MARKET_CLOSE <= t <= "16:05"


def check_new_day():
    global _pre_market_done, _close_done, _current_day, _pre_alert_done
    today = get_ny_time().strftime("%Y-%m-%d")
    if today != _current_day:
        _current_day     = today
        _pre_market_done = False
        _pre_alert_done  = False
        _close_done      = False
        log(f"New trading day: {today} -- flags reset")


def get_system_context() -> dict:
    """
    تُرجع الحالة الحالية للنظام.
    يستخدمها telegram_commands.py لأمر /status.
    """
    return {
        "open_trades":     open_trades,
        "risk_manager":    risk_manager,
        "daily_stocks":    daily_stocks,
        "pre_market_done": _pre_market_done,
        "close_done":      _close_done,
    }


# -----------------------------------------
# روتين ما قبل الافتتاح
# -----------------------------------------

def run_pre_market_alert():
    """09:00 — رسالة تنبيه فقط بدون اختيار أسهم."""
    global _pre_alert_done
    if _pre_alert_done:
        return
    _pre_alert_done = True
    try:
        from notifier import _send
        _send(
            "🔔 <b>تنبيه | Market Alert</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🇬🇧 Market opens in <b>30 minutes</b>\n\n"
            "🇦🇪 السوق يفتح بعد <b>30 دقيقة</b>"
        )
        log("Pre-market alert sent — market opens in 30 min")
    except Exception as e:
        log(f"Alert error: {e}")


def run_pre_market():
    global daily_stocks, _pre_market_done

    log("=== PRE-MARKET ROUTINE START ===")
    risk_manager.reset()

    # ── محاولة جلب الأسهم مع retry ×3
    for attempt in range(1, 4):
        try:
            daily_stocks = get_daily_universe()
            if daily_stocks:
                refresh_allowed_tickers(candidate_tickers=list(daily_stocks.keys()))
                break
            else:
                log(f"⚠️ get_daily_universe رجع فارغ — محاولة {attempt}/3")
        except Exception as e:
            log(f"❌ خطأ في get_daily_universe (محاولة {attempt}/3): {e}")
            traceback.print_exc()
            daily_stocks = {}

        if attempt < 3:
            time.sleep(10)

    if daily_stocks:
        try:
            notify_pre_market(list(daily_stocks.keys()))
        except Exception as e:
            log(f"Telegram error: {e}")
        log(f"Universe ready: {len(daily_stocks)} stocks selected")
    else:
        log("❌ CRITICAL: فشل تحميل الأسهم بعد 3 محاولات")
        try:
            from notifier import _send
            _send(
                "⚠️ <b>تحذير — فشل تحميل قائمة الأسهم</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "🇬🇧 Failed to load stock universe after 3 attempts.\n"
                "The system will retry at next pre-market cycle.\n\n"
                "🇦🇪 فشل تحميل قائمة الأسهم بعد 3 محاولات.\n"
                "النظام سيحاول مجدداً في الدورة القادمة.\n"
                "💡 تحقق من Alpaca API أو اضغط /resume لإعادة المحاولة."
            )
        except Exception:
            pass

    _pre_market_done = True
    log("=== PRE-MARKET ROUTINE END ===")


# -----------------------------------------
# مراقبة الصفقات المفتوحة
# -----------------------------------------

def monitor_open_trades():
    global open_trades

    if not open_trades:
        return

    # ── تحقق من التزامن مع Alpaca (يكتشف الإغلاق اليدوي)
    before = len(open_trades)
    sync_with_alpaca(open_trades)
    if len(open_trades) < before:
        if open_trades:
            _save_open_trades(open_trades)
        else:
            _delete_open_trades_sheets()

    if not open_trades:
        return

    log(f"Monitoring {len(open_trades)} open trades...")
    trades_to_remove = []

    for trade in open_trades:
        try:
            result = monitor_trade(trade)
            status = result["status"]
            price  = result["price"]
            r      = result["r"]
            side   = trade.side

            if status == "stopped":
                exit_qty = result.get("exit_qty", trade.quantity)
                log(f"STOP HIT: {trade.ticker} [{side.upper()}] @ ${price:.2f} | qty={exit_qty}")
                place_market_sell(trade.ticker, exit_qty, side=side)
                pnl = round(
                    (price - trade.entry_price) * exit_qty if side == "long"
                    else (trade.entry_price - price) * exit_qty, 2
                )
                record_trade(
                    ticker=trade.ticker, strategy=trade.strategy,
                    entry_price=trade.entry_price, exit_price=price,
                    quantity=exit_qty, stop_loss=trade.stop_loss,
                    target=trade.target, risk_amount=trade.risk_amount,
                    exit_reason="stopped", opened_at=trade.opened_at, side=side,
                )
                stopped = risk_manager.record_loss(pnl, r)
                try:
                    notify_trade_loss(
                        ticker=trade.ticker, entry_price=trade.entry_price,
                        exit_price=price, quantity=exit_qty,
                        loss=abs(pnl), daily_losses=risk_manager.daily_losses,
                    )
                    if stopped:
                        notify_system_stopped()
                except Exception as e:
                    log(f"Telegram error: {e}")
                trades_to_remove.append(trade)

            elif status == "tp1_hit":
                tp1_qty  = result.get("exit_qty", trade.quantity // 2)
                new_stop = result["new_stop"]
                log(f"TP1 HIT: {trade.ticker} [{side.upper()}] @ ${price:.2f} | R={r:.1f} | qty={tp1_qty}")
                place_market_sell(trade.ticker, tp1_qty, side=side)
                pnl_tp1 = round(
                    (price - trade.entry_price) * tp1_qty if side == "long"
                    else (trade.entry_price - price) * tp1_qty, 2
                )
                record_trade(
                    ticker=trade.ticker, strategy=trade.strategy,
                    entry_price=trade.entry_price, exit_price=price,
                    quantity=tp1_qty, stop_loss=trade.stop_loss,
                    target=trade.target_tp1, risk_amount=trade.risk_amount / 2,
                    exit_reason="tp1", opened_at=trade.opened_at, side=side,
                )
                risk_manager.record_win(pnl_tp1, r)
                try:
                    notify_trade_win(
                        ticker=trade.ticker, entry_price=trade.entry_price,
                        exit_price=price, quantity=tp1_qty,
                        profit=pnl_tp1, r_achieved=r,
                    )
                    notify_stop_updated(
                        ticker=trade.ticker, old_stop=trade.stop_loss,
                        new_stop=new_stop, current_price=price,
                    )
                except Exception as e:
                    log(f"Telegram error: {e}")
                trade.tp1_hit   = True
                trade.stop_loss = new_stop

            elif status == "target":
                exit_qty = result.get("exit_qty", trade.quantity_remaining if trade.tp1_hit else trade.quantity)
                label    = "TP2" if trade.tp1_hit else "TARGET"
                log(f"{label}: {trade.ticker} [{side.upper()}] @ ${price:.2f} | R={r:.1f}")
                place_market_sell(trade.ticker, exit_qty, side=side)
                pnl = round(
                    (price - trade.entry_price) * exit_qty if side == "long"
                    else (trade.entry_price - price) * exit_qty, 2
                )
                record_trade(
                    ticker=trade.ticker, strategy=trade.strategy,
                    entry_price=trade.entry_price, exit_price=price,
                    quantity=exit_qty, stop_loss=trade.stop_loss,
                    target=trade.target, risk_amount=trade.risk_amount,
                    exit_reason="target", opened_at=trade.opened_at, side=side,
                )
                risk_manager.record_win(pnl, r)
                try:
                    notify_trade_win(
                        ticker=trade.ticker, entry_price=trade.entry_price,
                        exit_price=price, quantity=exit_qty,
                        profit=pnl, r_achieved=r,
                    )
                except Exception as e:
                    log(f"Telegram error: {e}")
                trades_to_remove.append(trade)

            elif status == "trail_updated":
                new_stop = result["new_stop"]
                log(f"TRAIL: {trade.ticker} stop ${trade.stop_loss:.2f} -> ${new_stop:.2f}")
                try:
                    notify_stop_updated(
                        ticker=trade.ticker, old_stop=trade.stop_loss,
                        new_stop=new_stop, current_price=price,
                    )
                except Exception as e:
                    log(f"Telegram error: {e}")
                trade.stop_loss = new_stop

            else:
                tp_info = (
                    f"TP1 done -> TP2 @ ${trade.target_tp2:.2f}" if trade.tp1_hit
                    else f"waiting TP1 @ ${trade.target_tp1:.2f}"
                )
                log(f"OPEN: {trade.ticker} [{side.upper()}] ${price:.2f} | R={r:.2f} | {tp_info}")

        except Exception as e:
            log(f"Error monitoring {trade.ticker}: {e}")
            traceback.print_exc()

    for trade in trades_to_remove:
        open_trades.remove(trade)

    if trades_to_remove:
        if open_trades:
            _save_open_trades(open_trades)
        else:
            _delete_open_trades_sheets()


# -----------------------------------------
# البحث عن إشارات جديدة
# -----------------------------------------

def scan_for_signals():
    global open_trades, last_no_opp

    if len(open_trades) >= MAX_TOTAL:
        return

    try:
        account = get_account()
        if not account:
            log("Could not fetch account -- skipping scan")
            return

        current_positions = {t.ticker: (t.side, t.strategy) for t in open_trades}
        balance           = account["balance"]
        results           = run_selector(daily_stocks, current_positions=current_positions)
        found_signal      = False

        for signal in results.get("meanrev", []):
            if not risk_manager.can_trade():
                break
            # تحديد الاستراتيجية من الـ reason
            strategy = "momentum" if "MOM" in signal.reason else "meanrev"
            trade = open_meanrev_trade(signal, balance, strategy=strategy)
            if trade:
                open_trades.append(trade)
                _save_open_trades(open_trades)
                found_signal = True
                try:
                    notify_trade_open(
                        ticker=signal.ticker,
                        strategy="Mean Reversion",
                        side="BUY" if signal.side == "long" else "SELL SHORT",
                        price=signal.entry_price,
                        quantity=trade.quantity,
                        stop_loss=signal.stop_loss,
                        target=signal.target_tp2,
                        risk_amount=trade.risk_amount,
                    )
                except Exception as e:
                    log(f"Telegram error: {e}")

        if not found_signal:
            now  = get_ny_time()
            diff = (now - last_no_opp).total_seconds() / 60
            if diff >= NO_OPPORTUNITY_INTERVAL:
                try:
                    notify_no_opportunity()
                except Exception as e:
                    log(f"Telegram error: {e}")
                last_no_opp = now
                log("No opportunity -- notification sent")

    except Exception as e:
        log(f"Error in scan_for_signals: {e}")
        traceback.print_exc()


# -----------------------------------------
# روتين إغلاق السوق
# -----------------------------------------

def run_market_close():
    global open_trades, _close_done

    if _close_done:
        return

    log("=== MARKET CLOSE ROUTINE START ===")
    _close_done = True

    try:
        # ── الصفقات المفتوحة تنتقل لليوم التالي — لا نغلقها
        open_trades_summary = []
        if open_trades:
            log(f"{len(open_trades)} open trade(s) will carry over to next session:")
            for trade in open_trades:
                price = get_current_price(trade.ticker)
                if price > 0 and trade.stop_loss != trade.entry_price:
                    if trade.side == "long":
                        r = round((price - trade.entry_price) / abs(trade.entry_price - trade.stop_loss), 2)
                    else:
                        r = round((trade.entry_price - price) / abs(trade.entry_price - trade.stop_loss), 2)
                else:
                    r = 0.0
                open_trades_summary.append({
                    "ticker": trade.ticker,
                    "side":   trade.side,
                    "entry":  trade.entry_price,
                    "r":      r,
                })
                log(f"  → {trade.ticker} [{trade.side.upper()}] entry=${trade.entry_price:.2f} | R={r:+.2f}")

        account = get_account()
        balance = account.get("balance", 0) if account else 0
        send_daily_report(balance, open_trades=open_trades_summary)
        log("Daily report sent")

    except Exception as e:
        log(f"Error in run_market_close: {e}")
        traceback.print_exc()

    log("=== MARKET CLOSE ROUTINE END ===")


# -----------------------------------------
# الحلقة الرئيسية
# -----------------------------------------

def main():
    global _consecutive_errors, _error_notified

    log("=" * 55)
    log("BBAI Trading System -- Starting")
    log("Mean Reversion + SHORT Selling")
    log("Commands: /maintenance /resume /status /help")
    log("=" * 55)

    # الاتصال بـ Alpaca
    log("Connecting to Alpaca...")
    while True:
        try:
            account = get_account()
            if account and account.get("balance", 0) > 0:
                log(f"Connected | Balance: ${account['balance']:,.2f}")
                log(f"Next open: {get_next_market_open()}")
                log(f"Timezone: {TIMEZONE} | Loop: 30s")
                log("-" * 55)
                break
            else:
                log("Connection failed -- retrying in 60s...")
        except Exception as e:
            log(f"Connection error: {e} -- retrying in 60s...")
        time.sleep(60)

    # بدء الاستماع لأوامر Telegram في الخلفية
    start_command_listener(get_system_context)
    log("Telegram command listener started -- send /help for commands")
    log("-" * 55)

    # ── استعادة الصفقات المفتوحة (من Sheets أولاً، ثم Alpaca)
    log("Checking for open positions...")
    recovered = get_open_positions()
    if recovered:
        open_trades.extend(recovered)
        log(f"Recovered {len(recovered)} open position(s) -- will monitor them")
    log("-" * 55)

    # الحلقة الرئيسية
    while True:
        try:
            # إذا كان في وضع الصيانة
            if system_state.maintenance_mode:
                log("MAINTENANCE MODE -- trading paused")
                time.sleep(30)
                continue

            # التشغيل الطبيعي
            check_new_day()
            t = get_ny_time().strftime("%H:%M")

            if is_pre_market_alert_time() and not _pre_alert_done:
                run_pre_market_alert()

            elif is_pre_market_time() and not _pre_market_done:
                run_pre_market()

            elif is_market_hours():
                if not risk_manager.can_trade():
                    log("System paused -- daily loss limit reached")
                elif not daily_stocks and not _pre_market_done:
                    if system_state.maintenance_mode:
                        log("MAINTENANCE MODE -- waiting for /resume before pre-market")
                    else:
                        log("Started during market hours -- running pre-market now...")
                        run_pre_market()
                elif daily_stocks:
                    monitor_open_trades()
                    scan_for_signals()
                elif _pre_market_done and not daily_stocks:
                    # فشل تحميل الأسهم سابقاً — نعيد المحاولة كل 5 دقائق
                    log("⚠️ Universe فارغ — إعادة المحاولة...")
                    _pre_market_done = False  # يسمح بإعادة run_pre_market
                else:
                    log("No universe -- waiting for pre-market routine")

            elif is_close_time() and not _close_done:
                run_market_close()

            else:
                day_str = "Weekend" if not is_weekday() else "After hours"
                log(f"{day_str} | {t} {TIMEZONE} | Next open: {get_next_market_open()}")

            # إعادة ضبط عداد الأخطاء عند نجاح الدورة
            if _consecutive_errors > 0:
                _consecutive_errors = 0
                if _error_notified:
                    notify_error("", is_resolved=True)
                    _error_notified = False

        except KeyboardInterrupt:
            log("Manual shutdown")
            break

        except Exception as e:
            _consecutive_errors += 1
            log(f"Error #{_consecutive_errors}: {e}")
            traceback.print_exc()

            # إرسال إشعار Telegram بعد 3 أخطاء متتالية فقط
            if _consecutive_errors >= 3 and not _error_notified:
                notify_error(str(e))
                _error_notified = True

        time.sleep(30)


if __name__ == "__main__":
    main()
