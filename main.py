# =============================================================
# main.py — المحرك الرئيسي للنظام
# يشغّل كل شيء تلقائياً 24/7 بدون تدخل
# =============================================================

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
    open_conservative_trade,
    open_meanrev_trade,
    monitor_trade,
    place_market_sell,
    close_all_positions,
    OpenTrade,
)
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
    """
    يعمل في 9:00 AM بتوقيت نيويورك.
    - يختار أسهم اليوم
    - يُرسل تنبيه Telegram
    - يُعيد ضبط مدير المخاطرة
    """
    global daily_stocks, risk_manager

    log("🌅 بدء روتين ما قبل الافتتاح...")

    # إعادة ضبط الخسائر اليومية
    risk_manager.reset()
    log("✅ تم إعادة ضبط مدير المخاطرة")

    # اختيار أسهم اليوم
    daily_stocks = get_daily_universe()

    # إرسال تنبيه Telegram
    if daily_stocks:
        notify_pre_market(daily_stocks)
        log(f"✅ تم اختيار {len(daily_stocks)} سهم وإرسال التنبيه")
    else:
        log("⚠️ لم يتم اختيار أي أسهم اليوم")


# ─────────────────────────────────────────
# 2. روتين الفحص (كل 5 دقائق أثناء السوق)
# ─────────────────────────────────────────

def scan_routine():
    """
    يعمل كل 5 دقائق أثناء ساعات التداول.
    - يفحص الأسهم المختارة
    - يُنفّذ الإشارات إذا توفرت
    - يراقب الصفقات المفتوحة
    """
    global open_trades, last_no_opp

    # ── تحقق من حالة السوق
    if not is_market_open():
        log("💤 السوق مغلق — في انتظار الافتتاح")
        return

    # ── تحقق من حالة النظام
    if not risk_manager.can_trade():
        log("⛔️ النظام متوقف — تم الوصول لحد الخسارتين")
        return

    if not daily_stocks:
        log("⚠️ لا توجد أسهم مختارة — تحقق من pre_market_routine")
        return

    log(f"🔍 بدء فحص {len(daily_stocks)} سهم...")

    # ── 2A. مراقبة الصفقات المفتوحة أولاً
    _monitor_open_trades()

    # ── 2B. البحث عن فرص جديدة
    _scan_for_signals()


def _monitor_open_trades():
    """يراقب كل الصفقات المفتوحة ويتخذ الإجراء المناسب."""
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

        # ── ضُرب وقف الخسارة
        if status == "stopped":
            log(f"🛑 {trade.ticker} — ضُرب وقف الخسارة عند ${price:.2f}")
            place_market_sell(trade.ticker, trade.quantity)

            pnl = round((price - trade.entry_price) * trade.quantity, 2)

            record_trade(
                ticker=trade.ticker, strategy=trade.strategy,
                entry_price=trade.entry_price, exit_price=price,
                quantity=trade.quantity, stop_loss=trade.stop_loss,
                target=trade.target, risk_amount=trade.risk_amount,
                exit_reason="stopped", opened_at=trade.opened_at,
            )

            stopped = risk_manager.record_loss(pnl, r)
            notify_trade_loss(
                ticker=trade.ticker,
                entry_price=trade.entry_price,
                exit_price=price,
                quantity=trade.quantity,
                loss=abs(pnl),
                daily_losses=risk_manager.daily_losses,
            )

            if stopped:
                notify_system_stopped()
                log("⛔️ النظام متوقف بعد خسارتين")

            trades_to_remove.append(trade)

        # ── تحقق الهدف
        elif status == "target":
            log(f"🎯 {trade.ticker} — تحقق الهدف عند ${price:.2f} | R={r:.1f}")
            place_market_sell(trade.ticker, trade.quantity)

            pnl = round((price - trade.entry_price) * trade.quantity, 2)

            record_trade(
                ticker=trade.ticker, strategy=trade.strategy,
                entry_price=trade.entry_price, exit_price=price,
                quantity=trade.quantity, stop_loss=trade.stop_loss,
                target=trade.target, risk_amount=trade.risk_amount,
                exit_reason="target", opened_at=trade.opened_at,
            )

            risk_manager.record_win(pnl, r)
            notify_trade_win(
                ticker=trade.ticker,
                entry_price=trade.entry_price,
                exit_price=price,
                quantity=trade.quantity,
                profit=pnl,
                r_achieved=r,
            )

            trades_to_remove.append(trade)

        # ── تحديث الوقف المتحرك
        elif status == "trail_updated":
            new_stop = result["new_stop"]
            log(f"🔄 {trade.ticker} — تحديث الوقف: ${trade.stop_loss:.2f} → ${new_stop:.2f}")
            notify_stop_updated(
                ticker=trade.ticker,
                old_stop=trade.stop_loss,
                new_stop=new_stop,
                current_price=price,
            )
            trade.stop_loss = new_stop

        else:
            log(f"📊 {trade.ticker} — مفتوحة | السعر: ${price:.2f} | R={r:.2f}")

    # إزالة الصفقات المغلقة
    for trade in trades_to_remove:
        open_trades.remove(trade)


def _scan_for_signals():
    """يفحص الأسهم ويُنفّذ الإشارات المتاحة."""
    global open_trades, last_no_opp

    # الاستراتيجية المحافظة: صفقة واحدة فقط
    conservative_open = [t for t in open_trades if t.strategy == "conservative"]
    if conservative_open:
        log("ℹ️  الاستراتيجية المحافظة: صفقة مفتوحة بالفعل")

    # الاستراتيجية الثانية: حتى 3 صفقات
    meanrev_open = [t for t in open_trades if t.strategy == "meanrev"]
    if len(meanrev_open) >= 3:
        log("ℹ️  استراتيجية الارتداد: وصلنا للحد الأقصى (3 صفقات)")
        return

    # تشغيل المحلل
    account = get_account()
    if not account:
        log("❌ فشل جلب معلومات الحساب")
        return

    balance = account["balance"]
    results = run_selector(daily_stocks)

    found_signal = False

    # ── معالجة إشارات الاستراتيجية المحافظة
    if not conservative_open:
        for signal in results["conservative"]:
            if not risk_manager.can_trade():
                break
            trade = open_conservative_trade(signal, balance)
            if trade:
                open_trades.append(trade)
                found_signal = True
                notify_trade_open(
                    ticker=signal.ticker,
                    strategy="المحافظة",
                    side="BUY",
                    price=signal.entry_price,
                    quantity=trade.quantity,
                    stop_loss=signal.stop_loss,
                    target=signal.target,
                    risk_amount=trade.risk_amount,
                )
                break  # صفقة واحدة فقط للمحافظة

    # ── معالجة إشارات استراتيجية الارتداد
    available_slots = 3 - len(meanrev_open)
    for signal in results["meanrev"][:available_slots]:
        if not risk_manager.can_trade():
            break
        trade = open_meanrev_trade(signal, balance)
        if trade:
            open_trades.append(trade)
            found_signal = True
            notify_trade_open(
                ticker=signal.ticker,
                strategy="الارتداد",
                side="BUY",
                price=signal.entry_price,
                quantity=trade.quantity,
                stop_loss=signal.stop_loss,
                target=signal.target,
                risk_amount=trade.risk_amount,
            )

    # ── إرسال "لا توجد فرصة" كل ساعة
    if not found_signal:
        now = datetime.now(TZ)
        diff = (now - last_no_opp).total_seconds() / 60
        if diff >= NO_OPPORTUNITY_INTERVAL:
            notify_no_opportunity()
            last_no_opp = now
            log("📭 لا توجد فرصة — تم إرسال الإشعار")


# ─────────────────────────────────────────
# 3. روتين إغلاق السوق (4:00 PM)
# ─────────────────────────────────────────

def market_close_routine():
    """
    يعمل عند إغلاق السوق (4:00 PM).
    - يُغلق كل الصفقات المفتوحة
    - يُرسل التقرير اليومي
    """
    log("🔔 إغلاق السوق — بدء روتين النهاية...")

    # إغلاق كل الصفقات المفتوحة
    if open_trades:
        log(f"📤 إغلاق {len(open_trades)} صفقة مفتوحة...")
        for trade in open_trades:
            price = 0.0
            place_market_sell(trade.ticker, trade.quantity)
            record_trade(
                ticker=trade.ticker, strategy=trade.strategy,
                entry_price=trade.entry_price, exit_price=trade.entry_price,
                quantity=trade.quantity, stop_loss=trade.stop_loss,
                target=trade.target, risk_amount=trade.risk_amount,
                exit_reason="eod", opened_at=trade.opened_at,
            )
        close_all_positions()
        open_trades.clear()

    # إرسال التقرير اليومي
    account = get_account()
    balance = account.get("balance", 0)
    send_daily_report(balance)
    log("✅ تم إرسال التقرير اليومي")


# ─────────────────────────────────────────
# 4. جدولة المهام
# ─────────────────────────────────────────

def setup_schedule():
    """يُعدّ جدول المهام اليومية."""

    # ما قبل الافتتاح — 9:00 AM بتوقيت نيويورك
    schedule.every().monday.at("09:00").do(pre_market_routine)
    schedule.every().tuesday.at("09:00").do(pre_market_routine)
    schedule.every().wednesday.at("09:00").do(pre_market_routine)
    schedule.every().thursday.at("09:00").do(pre_market_routine)
    schedule.every().friday.at("09:00").do(pre_market_routine)

    # فحص السوق — كل 5 دقائق
    schedule.every(5).minutes.do(scan_routine)

    # إغلاق السوق — 4:05 PM (5 دقائق بعد الإغلاق للتأكد)
    schedule.every().monday.at("16:05").do(market_close_routine)
    schedule.every().tuesday.at("16:05").do(market_close_routine)
    schedule.every().wednesday.at("16:05").do(market_close_routine)
    schedule.every().thursday.at("16:05").do(market_close_routine)
    schedule.every().friday.at("16:05").do(market_close_routine)

    log("✅ تم إعداد الجدول الزمني")
    log("   09:00 AM — اختيار الأسهم وتنبيه ما قبل الافتتاح")
    log("   كل 5 دقائق — فحص الأسهم ومراقبة الصفقات")
    log("   04:05 PM — إغلاق الصفقات والتقرير اليومي")


# ─────────────────────────────────────────
# 5. نقطة البداية
# ─────────────────────────────────────────

def main():
    log("=" * 55)
    log("🚀 بدء تشغيل نظام التداول الآلي")
    log("=" * 55)

    # التحقق من الاتصال بـ Alpaca
    account = get_account()
    if not account:
        log("❌ فشل الاتصال بـ Alpaca — تحقق من المفاتيح في .env")
        return

    log(f"✅ متصل بـ Alpaca | الرصيد: ${account['balance']:,.2f}")
    log(f"   وقت الافتتاح القادم: {get_next_market_open()}")

    # إعداد الجدول
    setup_schedule()

    # الحلقة الرئيسية — تعمل 24/7
    log("⏳ النظام يعمل — في انتظار المهام المجدولة...")
    while True:
        schedule.run_pending()
        time.sleep(30)  # فحص كل 30 ثانية


if __name__ == "__main__":
    main()
