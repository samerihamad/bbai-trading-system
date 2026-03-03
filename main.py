# =============================================================
# main.py -- المحرك الرئيسي للنظام
# Loop كل 30 ثانية + pytz لقراءة وقت نيويورك (EST/EDT تلقائياً)
# =============================================================

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import time
import pytz
from datetime import datetime, timedelta

from config import (
    TIMEZONE,
    MARKET_OPEN,
    MARKET_CLOSE,
    PRE_MARKET_ALERT,
    NO_OPPORTUNITY_INTERVAL,
    MAX_TOTAL,
)
from universe    import get_daily_universe
from selector    import run_selector
from executor    import (
    get_account,
    get_next_market_open,
    open_meanrev_trade,
    monitor_trade,
    place_market_sell,
    close_all_positions,
    OpenTrade,
)
from strategy_meanrev import refresh_allowed_tickers
from risk        import DailyRiskManager
from reporter    import record_trade, send_daily_report
from notifier    import (
    notify_pre_market,
    notify_no_opportunity,
    notify_trade_open,
    notify_trade_win,
    notify_trade_loss,
    notify_stop_updated,
    notify_system_stopped,
)

TZ = pytz.timezone(TIMEZONE)

# -----------------------------------------
# الحالة العامة للنظام
# -----------------------------------------

risk_manager : DailyRiskManager = DailyRiskManager()
open_trades  : list[OpenTrade]  = []
daily_stocks : dict             = {}
last_no_opp  : datetime         = datetime.now(TZ) - timedelta(hours=2)

# أعلام التتبع اليومي -- تمنع تكرار المهام
_pre_market_done : bool = False
_close_done      : bool = False
_current_day     : str  = ""   # تاريخ اليوم الحالي بصيغة YYYY-MM-DD


def log(msg: str):
    """طباعة الرسالة مع الوقت بتوقيت نيويورك."""
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"[{now}]  {msg}")


# -----------------------------------------
# 1. قراءة الوقت وحالة السوق
# -----------------------------------------

def get_ny_time() -> datetime:
    """يُرجع الوقت الحالي بتوقيت نيويورك. pytz يتعامل مع EST/EDT تلقائياً."""
    return datetime.now(TZ)


def is_market_hours() -> bool:
    """يتحقق إذا كنا في ساعات تداول نشطة."""
    now = get_ny_time()
    if now.weekday() >= 5:
        return False
    t = now.strftime("%H:%M")
    return MARKET_OPEN <= t <= MARKET_CLOSE


def is_pre_market_time() -> bool:
    """يتحقق إذا كنا في وقت ما قبل الافتتاح (09:00 - MARKET_OPEN)."""
    now = get_ny_time()
    if now.weekday() >= 5:
        return False
    t = now.strftime("%H:%M")
    return "09:00" <= t < MARKET_OPEN


def is_close_time() -> bool:
    """يتحقق إذا تجاوزنا وقت إغلاق السوق."""
    now = get_ny_time()
    if now.weekday() >= 5:
        return False
    return now.strftime("%H:%M") > MARKET_CLOSE


def check_new_day():
    """
    يتحقق إذا بدأ يوم تداول جديد ويُعيد ضبط الأعلام.
    الفرق عن النسخة القديمة: يُحدّث _current_day هنا مباشرة
    حتى لا يتكرر الضبط كل 30 ثانية.
    """
    global _pre_market_done, _close_done, _current_day

    today = get_ny_time().strftime("%Y-%m-%d")
    if today != _current_day:
        _current_day     = today
        _pre_market_done = False
        _close_done      = False
        log(f"📅 يوم تداول جديد: {today} -- تم إعادة ضبط الأعلام")


# -----------------------------------------
# 2. روتين ما قبل الافتتاح
# -----------------------------------------

def run_pre_market():
    """يُنفَّذ مرة واحدة يومياً عند 09:00. يُعيد ضبط المخاطرة ويختار أسهم اليوم."""
    global daily_stocks, _pre_market_done

    log("بدء روتين ما قبل الافتتاح...")

    risk_manager.reset()
    log("تم إعادة ضبط مدير المخاطرة")

    daily_stocks = get_daily_universe()
    refresh_allowed_tickers(candidate_tickers=list(daily_stocks.keys()))
    log("تم تحديث فلترة الأسهم الديناميكية")

    if daily_stocks:
        notify_pre_market(list(daily_stocks.keys()))
        log(f"تم اختيار {len(daily_stocks)} سهم وإرسال التنبيه")
    else:
        log("لم يتم اختيار أي أسهم اليوم")

    _pre_market_done = True


# -----------------------------------------
# 3. روتين مراقبة الصفقات المفتوحة
# -----------------------------------------

def monitor_open_trades():
    """يراقب كل الصفقات المفتوحة ويتخذ قرارات الخروج."""
    global open_trades

    if not open_trades:
        return

    log(f"مراقبة {len(open_trades)} صفقة مفتوحة...")
    trades_to_remove = []

    for trade in open_trades:
        result = monitor_trade(trade)
        status = result["status"]
        price  = result["price"]
        r      = result["r"]
        side   = trade.side

        if status == "stopped":
            exit_qty = result.get("exit_qty", trade.quantity)
            log(f"{trade.ticker} [{side.upper()}] -- وقف الخسارة @ ${price:.2f} | كمية: {exit_qty}")
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
            notify_trade_loss(
                ticker=trade.ticker, entry_price=trade.entry_price,
                exit_price=price, quantity=exit_qty,
                loss=abs(pnl), daily_losses=risk_manager.daily_losses,
            )
            if stopped:
                notify_system_stopped()
                log("النظام متوقف بعد خسارتين")
            trades_to_remove.append(trade)

        elif status == "tp1_hit":
            tp1_qty  = result.get("exit_qty", trade.quantity // 2)
            new_stop = result["new_stop"]
            log(f"{trade.ticker} [{side.upper()}] -- TP1 @ ${price:.2f} | R={r:.1f} | خروج {tp1_qty} سهم")
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
            notify_trade_win(
                ticker=trade.ticker, entry_price=trade.entry_price,
                exit_price=price, quantity=tp1_qty,
                profit=pnl_tp1, r_achieved=r,
            )
            trade.tp1_hit   = True
            old_stop        = trade.stop_loss
            trade.stop_loss = new_stop
            log(f"{trade.ticker} -- نقل الوقف الى التعادل: ${old_stop:.2f} -> ${new_stop:.2f}")
            notify_stop_updated(
                ticker=trade.ticker, old_stop=old_stop,
                new_stop=new_stop, current_price=price,
            )

        elif status == "target":
            exit_qty = result.get("exit_qty", trade.quantity_remaining if trade.tp1_hit else trade.quantity)
            log(f"{trade.ticker} [{side.upper()}] -- {'TP2' if trade.tp1_hit else 'الهدف'} @ ${price:.2f} | R={r:.1f}")
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
            notify_trade_win(
                ticker=trade.ticker, entry_price=trade.entry_price,
                exit_price=price, quantity=exit_qty,
                profit=pnl, r_achieved=r,
            )
            trades_to_remove.append(trade)

        elif status == "trail_updated":
            new_stop = result["new_stop"]
            log(f"{trade.ticker} -- وقف متحرك: ${trade.stop_loss:.2f} -> ${new_stop:.2f}")
            notify_stop_updated(
                ticker=trade.ticker, old_stop=trade.stop_loss,
                new_stop=new_stop, current_price=price,
            )
            trade.stop_loss = new_stop

        else:
            tp_status = (
                f"TP1 done -> TP2 @ ${trade.target_tp2:.2f}" if trade.tp1_hit
                else f"TP1 @ ${trade.target_tp1:.2f}"
            )
            log(f"{trade.ticker} [{side.upper()}] -- ${price:.2f} | R={r:.2f} | {tp_status}")

    for trade in trades_to_remove:
        open_trades.remove(trade)


# -----------------------------------------
# 4. روتين البحث عن إشارات جديدة
# -----------------------------------------

def scan_for_signals():
    """يبحث عن إشارات جديدة ويفتح الصفقات المناسبة."""
    global open_trades, last_no_opp

    if len(open_trades) >= MAX_TOTAL:
        return

    account = get_account()
    if not account:
        log("فشل جلب معلومات الحساب")
        return

    current_positions = {t.ticker: t.side for t in open_trades}
    balance           = account["balance"]
    results           = run_selector(daily_stocks, current_positions=current_positions)
    found_signal      = False

    for signal in results["meanrev"]:
        if not risk_manager.can_trade():
            break
        trade = open_meanrev_trade(signal, balance)
        if trade:
            open_trades.append(trade)
            found_signal = True
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

    if not found_signal:
        now  = get_ny_time()
        diff = (now - last_no_opp).total_seconds() / 60
        if diff >= NO_OPPORTUNITY_INTERVAL:
            notify_no_opportunity()
            last_no_opp = now
            log("لا توجد فرصة -- تم ارسال الاشعار")


# -----------------------------------------
# 5. روتين إغلاق السوق
# -----------------------------------------

def run_market_close():
    """
    يُنفَّذ مرة واحدة فقط يومياً بعد 15:45.
    يُغلق كل الصفقات ويُرسل التقرير اليومي.
    """
    global open_trades, _close_done

    # الحماية المزدوجة -- لا تُنفَّذ مرتين ابداً
    if _close_done:
        return

    log("اغلاق السوق -- بدء روتين النهاية...")

    if open_trades:
        log(f"اغلاق {len(open_trades)} صفقة مفتوحة...")
        for trade in open_trades:
            exit_qty = trade.quantity_remaining if trade.tp1_hit else trade.quantity
            place_market_sell(trade.ticker, exit_qty, side=trade.side)
            record_trade(
                ticker=trade.ticker, strategy=trade.strategy,
                entry_price=trade.entry_price, exit_price=trade.entry_price,
                quantity=exit_qty, stop_loss=trade.stop_loss,
                target=trade.target, risk_amount=trade.risk_amount,
                exit_reason="eod", opened_at=trade.opened_at, side=trade.side,
            )
        close_all_positions()
        open_trades.clear()

    account = get_account()
    balance = account.get("balance", 0)

    # ضبط العلم قبل الارسال -- يمنع أي تكرار حتى لو حدث خطأ
    _close_done = True

    send_daily_report(balance)
    log("تم ارسال التقرير اليومي")


# -----------------------------------------
# 6. الحلقة الرئيسية
# -----------------------------------------

def main():
    log("=" * 55)
    log("بدء تشغيل نظام التداول الآلي -- Mean Reversion + SHORT")
    log("=" * 55)

    account = get_account()
    if not account:
        log("فشل الاتصال بـ Alpaca -- تحقق من المفاتيح في .env")
        return

    log(f"متصل بـ Alpaca | الرصيد: ${account['balance']:,.2f}")
    log(f"وقت الافتتاح القادم: {get_next_market_open()}")
    log(f"Loop كل 30 ثانية | Timezone: {TIMEZONE}")
    log("-" * 55)

    while True:
        try:
            # فحص يوم جديد واعادة ضبط الاعلام
            check_new_day()

            now = get_ny_time()

            # روتين ما قبل الافتتاح (09:00 - MARKET_OPEN)
            if is_pre_market_time() and not _pre_market_done:
                run_pre_market()

            # ساعات التداول النشطة
            elif is_market_hours():
                if not risk_manager.can_trade():
                    log("النظام متوقف -- تم الوصول لحد الخسارتين")
                elif daily_stocks:
                    monitor_open_trades()
                    scan_for_signals()
                else:
                    log("لا توجد اسهم مختارة -- في انتظار روتين ما قبل الافتتاح")

            # وقت الاغلاق -- مرة واحدة فقط
            elif is_close_time() and not _close_done:
                run_market_close()

            # السوق مغلق
            else:
                log(f"السوق مغلق | {now.strftime('%H:%M %Z')} | الافتتاح: {get_next_market_open()}")

        except KeyboardInterrupt:
            log("ايقاف يدوي من المستخدم")
            break
        except Exception as e:
            log(f"خطأ في الحلقة الرئيسية: {e}")
            import traceback
            traceback.print_exc()

        time.sleep(30)


if __name__ == "__main__":
    main()
