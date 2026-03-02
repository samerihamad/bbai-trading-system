# =============================================================
# main.py — المحرك الرئيسي للنظام
# يشغّل كل شيء تلقائياً بدون مكتبة schedule
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

# ─────────────────────────────────────────
# الحالة العامة للنظام
# ─────────────────────────────────────────

risk_manager  : DailyRiskManager = DailyRiskManager()
open_trades   : list[OpenTrade]  = []
daily_stocks  : dict             = {}
last_no_opp   : datetime         = datetime.now(TZ) - timedelta(hours=2)

# أعلام التتبع اليومي — تمنع تكرار المهام
_pre_market_done  : bool = False
_close_done       : bool = False
_universe_date    : str  = ""


def log(msg: str):
    """طباعة الرسالة مع الوقت بتوقيت نيويورك."""
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"[{now}]  {msg}")


# ─────────────────────────────────────────
# 1. قراءة الوقت وحالة السوق
# ─────────────────────────────────────────

def get_ny_time() -> datetime:
    """
    يُرجع الوقت الحالي بتوقيت نيويورك.
    pytz يتعامل مع التوقيت الصيفي (EDT) والشتوي (EST) تلقائياً.
    """
    return datetime.now(TZ)


def is_market_hours() -> bool:
    """يتحقق إذا كنا في ساعات تداول نشطة (09:35 - 15:45)."""
    now = get_ny_time()
    if now.weekday() >= 5:   # السبت والأحد
        return False
    t = now.strftime("%H:%M")
    return MARKET_OPEN <= t <= MARKET_CLOSE


def is_pre_market_time() -> bool:
    """يتحقق إذا كنا في وقت ما قبل الافتتاح (09:00 - 09:35)."""
    now = get_ny_time()
    if now.weekday() >= 5:
        return False
    t = now.strftime("%H:%M")
    return "09:00" <= t < MARKET_OPEN


def is_close_time() -> bool:
    """يتحقق إذا حان وقت إغلاق اليوم (15:45+)."""
    now = get_ny_time()
    if now.weekday() >= 5:
        return False
    return now.strftime("%H:%M") >= MARKET_CLOSE


def is_new_trading_day() -> bool:
    """يتحقق إذا بدأ يوم تداول جديد — يُعيد ضبط الأعلام."""
    global _universe_date
    today = get_ny_time().strftime("%Y-%m-%d")
    return today != _universe_date


# ─────────────────────────────────────────
# 2. روتين ما قبل الافتتاح
# ─────────────────────────────────────────

def run_pre_market():
    """
    يُنفَّذ مرة واحدة يومياً عند 09:00.
    يُعيد ضبط المخاطرة ويختار أسهم اليوم.
    """
    global daily_stocks, _pre_market_done, _close_done, _universe_date

    log("🌅 بدء روتين ما قبل الافتتاح...")

    # إعادة ضبط يومية
    risk_manager.reset()
    _close_done   = False
    _universe_date = get_ny_time().strftime("%Y-%m-%d")
    log("✅ تم إعادة ضبط مدير المخاطرة")

    # اختيار أسهم اليوم
    daily_stocks = get_daily_universe()

    # تحديث الفلترة الديناميكية
    refresh_allowed_tickers(candidate_tickers=list(daily_stocks.keys()))
    log("✅ تم تحديث فلترة الأسهم الديناميكية")

    if daily_stocks:
        notify_pre_market(list(daily_stocks.keys()))
        log(f"✅ تم اختيار {len(daily_stocks)} سهم وإرسال التنبيه")
    else:
        log("⚠️  لم يتم اختيار أي أسهم اليوم")

    _pre_market_done = True


# ─────────────────────────────────────────
# 3. روتين مراقبة الصفقات المفتوحة
# ─────────────────────────────────────────

def monitor_open_trades():
    """يراقب كل الصفقات المفتوحة ويتخذ قرارات الخروج."""
    global open_trades

    if not open_trades:
        return

    log(f"👁  مراقبة {len(open_trades)} صفقة مفتوحة...")
    trades_to_remove = []

    for trade in open_trades:
        result   = monitor_trade(trade)
        status   = result["status"]
        price    = result["price"]
        r        = result["r"]
        side     = trade.side

        if status == "stopped":
            # ── ضُرب وقف الخسارة
            exit_qty = result.get("exit_qty", trade.quantity)
            log(f"🛑 {trade.ticker} [{side.upper()}] — وقف الخسارة @ ${price:.2f} | كمية: {exit_qty}")
            place_market_sell(trade.ticker, exit_qty, side=side)
            pnl = round(
                (price - trade.entry_price) * exit_qty if side == "long"
                else (trade.entry_price - price) * exit_qty,
                2
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
                log("⛔️ النظام متوقف بعد خسارتين")
            trades_to_remove.append(trade)

        elif status == "tp1_hit":
            # ── تحقق TP1 — خروج جزئي 50%
            tp1_qty  = result.get("exit_qty", trade.quantity // 2)
            new_stop = result["new_stop"]
            log(f"🎯 {trade.ticker} [{side.upper()}] — TP1 @ ${price:.2f} | R={r:.1f} | خروج {tp1_qty} سهم")
            place_market_sell(trade.ticker, tp1_qty, side=side)
            pnl_tp1 = round(
                (price - trade.entry_price) * tp1_qty if side == "long"
                else (trade.entry_price - price) * tp1_qty,
                2
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
            # تحديث الصفقة للجزء المتبقي
            trade.tp1_hit   = True
            old_stop        = trade.stop_loss
            trade.stop_loss = new_stop
            log(f"🔄 {trade.ticker} — نقل الوقف إلى التعادل: ${old_stop:.2f} → ${new_stop:.2f}")
            notify_stop_updated(
                ticker=trade.ticker, old_stop=old_stop,
                new_stop=new_stop, current_price=price,
            )

        elif status == "target":
            # ── تحقق TP2 — خروج نهائي
            exit_qty = result.get("exit_qty", trade.quantity_remaining if trade.tp1_hit else trade.quantity)
            tp_label = "TP2 🎯🎯" if trade.tp1_hit else "الهدف 🎯"
            log(f"{tp_label} {trade.ticker} [{side.upper()}] — @ ${price:.2f} | R={r:.1f}")
            place_market_sell(trade.ticker, exit_qty, side=side)
            pnl = round(
                (price - trade.entry_price) * exit_qty if side == "long"
                else (trade.entry_price - price) * exit_qty,
                2
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
            # ── تحديث الوقف المتحرك
            new_stop = result["new_stop"]
            log(f"🔄 {trade.ticker} — وقف متحرك: ${trade.stop_loss:.2f} → ${new_stop:.2f}")
            notify_stop_updated(
                ticker=trade.ticker, old_stop=trade.stop_loss,
                new_stop=new_stop, current_price=price,
            )
            trade.stop_loss = new_stop

        else:
            # ── الصفقة مفتوحة — طباعة حالة
            tp_status = (
                f"TP1✅ → TP2 @ ${trade.target_tp2:.2f}" if trade.tp1_hit
                else f"TP1 @ ${trade.target_tp1:.2f}"
            )
            log(f"📊 {trade.ticker} [{side.upper()}] — ${price:.2f} | R={r:.2f} | {tp_status}")

    for trade in trades_to_remove:
        open_trades.remove(trade)


# ─────────────────────────────────────────
# 4. روتين البحث عن إشارات جديدة
# ─────────────────────────────────────────

def scan_for_signals():
    """يبحث عن إشارات جديدة ويفتح الصفقات المناسبة."""
    global open_trades, last_no_opp

    # أقصى MAX_TOTAL مراكز في نفس الوقت
    if len(open_trades) >= MAX_TOTAL:
        return

    account = get_account()
    if not account:
        log("❌ فشل جلب معلومات الحساب")
        return

    # تجهيز قائمة المراكز الحالية لتمريرها للـ selector
    current_positions = {
        t.ticker: t.side for t in open_trades
    }

    balance = account["balance"]
    results = run_selector(daily_stocks, current_positions=current_positions)
    found_signal = False

    for signal in results["meanrev"]:
        if not risk_manager.can_trade():
            break

        trade = open_meanrev_trade(signal, balance)
        if trade:
            open_trades.append(trade)
            found_signal = True
            notify_trade_open(
                ticker=signal.ticker,
                strategy="الارتداد",
                side="BUY" if signal.side == "long" else "SELL SHORT",
                price=signal.entry_price,
                quantity=trade.quantity,
                stop_loss=signal.stop_loss,
                target=signal.target_tp2,
                risk_amount=trade.risk_amount,
            )

    # إرسال "لا توجد فرصة" كل ساعة إذا لم تُفتح صفقات
    if not found_signal:
        now  = get_ny_time()
        diff = (now - last_no_opp).total_seconds() / 60
        if diff >= NO_OPPORTUNITY_INTERVAL:
            notify_no_opportunity()
            last_no_opp = now
            log("📭 لا توجد فرصة — تم إرسال الإشعار")


# ─────────────────────────────────────────
# 5. روتين إغلاق السوق
# ─────────────────────────────────────────

def run_market_close():
    """
    يُنفَّذ مرة واحدة يومياً عند 15:45.
    يُغلق كل الصفقات المفتوحة ويُرسل التقرير.
    """
    global open_trades, _close_done

    log("🔔 إغلاق السوق — بدء روتين النهاية...")

    if open_trades:
        log(f"📤 إغلاق {len(open_trades)} صفقة مفتوحة...")
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
    send_daily_report(balance)
    log("✅ تم إرسال التقرير اليومي")

    _close_done = True


# ─────────────────────────────────────────
# 6. الحلقة الرئيسية (بدون schedule)
# ─────────────────────────────────────────

def main():
    global _pre_market_done, _close_done

    log("=" * 55)
    log("🚀 بدء تشغيل نظام التداول الآلي — Mean Reversion + SHORT")
    log("=" * 55)

    # التحقق من الاتصال
    account = get_account()
    if not account:
        log("❌ فشل الاتصال بـ Alpaca — تحقق من المفاتيح في .env")
        return

    log(f"✅ متصل بـ Alpaca | الرصيد: ${account['balance']:,.2f}")
    log(f"   وقت الافتتاح القادم: {get_next_market_open()}")
    log(f"   Loop كل 30 ثانية | pytz={TIMEZONE}")
    log("─" * 55)

    while True:
        try:
            now = get_ny_time()

            # ── إعادة ضبط الأعلام في أيام تداول جديدة
            if is_new_trading_day():
                _pre_market_done = False
                _close_done      = False

            # ── روتين ما قبل الافتتاح (09:00 - 09:35)
            if is_pre_market_time() and not _pre_market_done:
                run_pre_market()

            # ── ساعات التداول (09:35 - 15:45)
            elif is_market_hours():
                if not risk_manager.can_trade():
                    log("⛔️ النظام متوقف — تم الوصول لحد الخسارتين")
                elif daily_stocks:
                    monitor_open_trades()
                    scan_for_signals()
                else:
                    log("⚠️  لا توجد أسهم مختارة — في انتظار روتين ما قبل الافتتاح")

            # ── وقت إغلاق السوق (15:45+)
            elif is_close_time() and not _close_done:
                run_market_close()

            # ── السوق مغلق
            else:
                log(f"💤 السوق مغلق | {now.strftime('%H:%M %Z')} | الافتتاح: {get_next_market_open()}")

        except KeyboardInterrupt:
            log("🛑 إيقاف يدوي من المستخدم")
            break
        except Exception as e:
            log(f"❌ خطأ في الحلقة الرئيسية: {e}")
            import traceback
            traceback.print_exc()

        # انتظر 30 ثانية قبل الدورة التالية
        time.sleep(30)


if __name__ == "__main__":
    main()
