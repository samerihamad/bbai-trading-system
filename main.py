# =============================================================
# main.py — المحرك الرئيسي للنظام
# يشغّل كل شيء تلقائياً 24/7 بدون تدخل
# استراتيجية وحيدة: Mean Reversion
# =============================================================

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import time
import schedule
import pytz
from datetime import datetime, timedelta

from config import (
    TIMEZONE,
    PRE_MARKET_ALERT,
    NO_OPPORTUNITY_INTERVAL,
    MARKET_OPEN,
    MARKET_CLOSE,
)
from universe    import get_daily_universe
from selector    import run_selector
from executor    import (
    get_account,
    is_market_open,
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

# ─────────────────────────────────────────
# الحالة العامة للنظام
# ─────────────────────────────────────────

risk_manager : DailyRiskManager = DailyRiskManager()
open_trades  : list[OpenTrade]  = []
daily_stocks : list[str]        = []
last_no_opp  : datetime         = datetime.now(TZ) - timedelta(hours=2)


def log(msg: str):
    """طباعة مع الوقت."""
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}]  {msg}")


# ─────────────────────────────────────────
# 1. روتين ما قبل الافتتاح (9:00 AM)
# ─────────────────────────────────────────

def pre_market_routine():
    global daily_stocks, risk_manager

    log("🌅 بدء روتين ما قبل الافتتاح...")
    risk_manager.reset()
    log("✅ تم إعادة ضبط مدير المخاطرة")

    daily_stocks = get_daily_universe()

    # ⑥ تحديث فلترة الأسهم الديناميكية بناءً على آخر 30 يوم
    refresh_allowed_tickers(candidate_tickers=daily_stocks)
    log("✅ تم تحديث فلترة الأسهم الديناميكية")

    if daily_stocks:
        notify_pre_market(daily_stocks)
        log(f"✅ تم اختيار {len(daily_stocks)} سهم وإرسال التنبيه")
    else:
        log("⚠️ لم يتم اختيار أي أسهم اليوم")


# ─────────────────────────────────────────
# 2. روتين الفحص (كل 5 دقائق)
# ─────────────────────────────────────────

def scan_routine():
    global open_trades, last_no_opp

    if not is_market_open():
        log("💤 السوق مغلق — في انتظار الافتتاح")
        return

    if not risk_manager.can_trade():
        log("⛔️ النظام متوقف — تم الوصول لحد الخسارتين")
        return

    if not daily_stocks:
        log("⚠️ لا توجد أسهم مختارة")
        return

    log(f"🔍 بدء فحص {len(daily_stocks)} سهم...")

    _monitor_open_trades()
    _scan_for_signals()


def _monitor_open_trades():
    global open_trades

    if not open_trades:
        return

    log(f"👁  مراقبة {len(open_trades)} صفقة مفتوحة...")
    trades_to_remove = []

    for trade in open_trades:
        result = monitor_trade(trade)
        status = result["status"]
        price  = result["price"]
        r      = result["r"]

        if status == "stopped":
            exit_qty = result.get("exit_qty", trade.quantity)
            log(f"🛑 {trade.ticker} — ضُرب وقف الخسارة عند ${price:.2f} | كمية: {exit_qty}")
            place_market_sell(trade.ticker, exit_qty)
            pnl = round((price - trade.entry_price) * exit_qty, 2)
            record_trade(
                ticker=trade.ticker, strategy=trade.strategy,
                entry_price=trade.entry_price, exit_price=price,
                quantity=exit_qty, stop_loss=trade.stop_loss,
                target=trade.target, risk_amount=trade.risk_amount,
                exit_reason="stopped", opened_at=trade.opened_at,
            )
            stopped = risk_manager.record_loss(pnl, r)
            notify_trade_loss(
                ticker=trade.ticker, entry_price=trade.entry_price,
                exit_price=price, quantity=exit_qty,
                loss=abs(pnl), daily_losses=risk_manager.daily_losses,
            )
            if stopped:
                notify_system_stopped()
                log("⛔️ النظام متوقف بعد خسارتين")
            trades_to_remove.append(trade)

        elif status == "tp1_hit":
            # خروج جزئي 50% + نقل الوقف إلى نقطة التعادل
            tp1_qty  = result.get("exit_qty", trade.quantity // 2)
            new_stop = result["new_stop"]
            log(f"🎯 {trade.ticker} — تحقق TP1 @ ${price:.2f} | R={r:.1f} | خروج {tp1_qty} سهم")
            place_market_sell(trade.ticker, tp1_qty)
            pnl_tp1 = round((price - trade.entry_price) * tp1_qty, 2)
            record_trade(
                ticker=trade.ticker, strategy=trade.strategy,
                entry_price=trade.entry_price, exit_price=price,
                quantity=tp1_qty, stop_loss=trade.stop_loss,
                target=trade.target_tp1, risk_amount=trade.risk_amount / 2,
                exit_reason="tp1", opened_at=trade.opened_at,
            )
            risk_manager.record_win(pnl_tp1, r)
            notify_trade_win(
                ticker=trade.ticker, entry_price=trade.entry_price,
                exit_price=price, quantity=tp1_qty,
                profit=pnl_tp1, r_achieved=r,
            )
            # تحديث الصفقة للجزء المتبقي
            trade.tp1_hit   = True
            trade.stop_loss = new_stop
            log(f"🔄 {trade.ticker} — نقل الوقف إلى التعادل: ${new_stop:.2f}")
            notify_stop_updated(
                ticker=trade.ticker, old_stop=trade.stop_loss,
                new_stop=new_stop, current_price=price,
            )

        elif status == "target":
            exit_qty = result.get("exit_qty", trade.quantity_remaining if trade.tp1_hit else trade.quantity)
            tp_label = "TP2 🎯🎯" if trade.tp1_hit else "الهدف 🎯"
            log(f"{tp_label} {trade.ticker} — @ ${price:.2f} | R={r:.1f} | خروج {exit_qty} سهم")
            place_market_sell(trade.ticker, exit_qty)
            pnl = round((price - trade.entry_price) * exit_qty, 2)
            record_trade(
                ticker=trade.ticker, strategy=trade.strategy,
                entry_price=trade.entry_price, exit_price=price,
                quantity=exit_qty, stop_loss=trade.stop_loss,
                target=trade.target, risk_amount=trade.risk_amount,
                exit_reason="target", opened_at=trade.opened_at,
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
            log(f"🔄 {trade.ticker} — تحديث الوقف: ${trade.stop_loss:.2f} → ${new_stop:.2f}")
            notify_stop_updated(
                ticker=trade.ticker, old_stop=trade.stop_loss,
                new_stop=new_stop, current_price=price,
            )
            trade.stop_loss = new_stop

        else:
            tp_status = f"TP1✅ → TP2 @ ${trade.target_tp2:.2f}" if trade.tp1_hit else f"TP1 @ ${trade.target_tp1:.2f}"
            log(f"📊 {trade.ticker} — مفتوحة | ${price:.2f} | R={r:.2f} | {tp_status}")

    for trade in trades_to_remove:
        open_trades.remove(trade)


def _scan_for_signals():
    global open_trades, last_no_opp

    # أقصى 3 صفقات مفتوحة في نفس الوقت
    if len(open_trades) >= 3:
        return

    account = get_account()
    if not account:
        log("❌ فشل جلب معلومات الحساب")
        return

    balance  = account["balance"]
    results  = run_selector(daily_stocks)
    found_signal = False

    available_slots = 3 - len(open_trades)
    for signal in results["meanrev"][:available_slots]:
        if not risk_manager.can_trade():
            break
        trade = open_meanrev_trade(signal, balance)
        if trade:
            open_trades.append(trade)
            found_signal = True
            notify_trade_open(
                ticker=signal.ticker, strategy="الارتداد",
                side="BUY", price=signal.entry_price,
                quantity=trade.quantity, stop_loss=signal.stop_loss,
                target=signal.target_tp2, risk_amount=trade.risk_amount,
            )

    if not found_signal:
        now  = datetime.now(TZ)
        diff = (now - last_no_opp).total_seconds() / 60
        if diff >= NO_OPPORTUNITY_INTERVAL:
            notify_no_opportunity()
            last_no_opp = now
            log("📭 لا توجد فرصة — تم إرسال الإشعار")


# ─────────────────────────────────────────
# 3. روتين إغلاق السوق (4:05 PM)
# ─────────────────────────────────────────

def market_close_routine():
    log("🔔 إغلاق السوق — بدء روتين النهاية...")

    if open_trades:
        log(f"📤 إغلاق {len(open_trades)} صفقة مفتوحة...")
        for trade in open_trades:
            exit_qty = trade.quantity_remaining if trade.tp1_hit else trade.quantity
            place_market_sell(trade.ticker, exit_qty)
            record_trade(
                ticker=trade.ticker, strategy=trade.strategy,
                entry_price=trade.entry_price, exit_price=trade.entry_price,
                quantity=exit_qty, stop_loss=trade.stop_loss,
                target=trade.target, risk_amount=trade.risk_amount,
                exit_reason="eod", opened_at=trade.opened_at,
            )
        close_all_positions()
        open_trades.clear()

    account = get_account()
    balance = account.get("balance", 0)
    send_daily_report(balance)
    log("✅ تم إرسال التقرير اليومي")


# ─────────────────────────────────────────
# 4. جدولة المهام
# ─────────────────────────────────────────

def setup_schedule():
    for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
        getattr(schedule.every(), day).at("09:00").do(pre_market_routine)
        getattr(schedule.every(), day).at("16:05").do(market_close_routine)

    schedule.every(5).minutes.do(scan_routine)

    log("✅ تم إعداد الجدول الزمني")
    log("   09:00 AM — اختيار الأسهم + تحديث فلترة الأداء")
    log("   كل 5 دقائق — فحص الأسهم ومراقبة الصفقات")
    log("   04:05 PM — إغلاق الصفقات والتقرير اليومي")


# ─────────────────────────────────────────
# 5. نقطة البداية
# ─────────────────────────────────────────

def main():
    log("=" * 55)
    log("🚀 بدء تشغيل نظام التداول الآلي — Mean Reversion")
    log("=" * 55)

    account = get_account()
    if not account:
        log("❌ فشل الاتصال بـ Alpaca — تحقق من المفاتيح في .env")
        return

    log(f"✅ متصل بـ Alpaca | الرصيد: ${account['balance']:,.2f}")
    log(f"   وقت الافتتاح القادم: {get_next_market_open()}")

    setup_schedule()

    log("⏳ النظام يعمل — في انتظار المهام المجدولة...")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
