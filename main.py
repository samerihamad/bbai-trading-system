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
    update_stop_loss_alpaca,
    _save_open_trades,
    _delete_open_trades_sheets,
    save_flags_to_sheets,
    load_flags_from_sheets,
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
    notify_tp1_hit,
    notify_tp2_hit,
    notify_trade_closed,
    notify_universe_refresh,
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
last_scan    : datetime          = datetime.now(TZ) - timedelta(hours=2)
last_universe_refresh : datetime = datetime.now(TZ) - timedelta(hours=2)
SCAN_INTERVAL_MIN     : int      = 5    # فحص الإشارات كل 5 دقائق
UNIVERSE_REFRESH_MIN  : int      = 60   # تحديث قائمة الأسهم كل ساعة
_startup_time         : datetime = datetime.now(TZ)  # وقت بدء التشغيل

_pre_market_done  : bool = False
_pre_alert_done   : bool = False
_close_done      : bool = False

def _save_flags_to_disk():
    """يحفظ الـ flags في Google Sheets — يبقى بعد كل Deploy."""
    try:
        save_flags_to_sheets(_flags)
    except Exception as e:
        print(f"⚠️  فشل حفظ الـ flags: {e}", flush=True)

# ── تحميل الـ flags من Sheets عند Startup
_disk_flags = load_flags_from_sheets()

# ── تهيئة _current_day من الـ Sheets لمنع reset وهمي عند restart في نفس اليوم
_current_day: str = _disk_flags.get("date", "")

# ── flags كـ dict واحد
_flags = {
    "pre_market_done": _disk_flags.get("pre_market_done", False),
    "pre_alert_done":  _disk_flags.get("pre_alert_done",  False),
    "watchlist_sent":  _disk_flags.get("watchlist_sent",  False),  # رسالة قائمة الأسهم
    "close_done":      _disk_flags.get("close_done",      False),
    "daily_trade_num": _disk_flags.get("daily_trade_num", 0),
}
if any([_flags["pre_alert_done"], _flags["pre_market_done"], _flags["close_done"]]):
    print(f"📂 Flags restored: pre_alert={_flags['pre_alert_done']} | "
          f"pre_market={_flags['pre_market_done']} | close={_flags['close_done']} | "
          f"trades={_flags['daily_trade_num']}", flush=True)

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
    global _current_day
    today = get_ny_time().strftime("%Y-%m-%d")
    if today != _current_day:
        _current_day     = today
        _flags["pre_market_done"]  = False
        _flags["pre_alert_done"]   = False
        _flags["watchlist_sent"]   = False
        _flags["close_done"]       = False
        _flags["daily_trade_num"]  = 0
        # مسح الـ flags القديمة من Sheets
        try:
            save_flags_to_sheets(_flags)
        except Exception:
            pass
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
        "pre_market_done": _flags["pre_market_done"],
        "close_done": _flags["close_done"],
    }


# -----------------------------------------
# روتين ما قبل الافتتاح
# -----------------------------------------

def run_pre_market_alert():
    """09:00 — رسالة تنبيه فقط بدون اختيار أسهم."""
    
    if _flags["pre_alert_done"]:
        return
    _flags["pre_alert_done"] = True
    _save_flags_to_disk()
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
    global daily_stocks

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
            # نُرسل رسالة Watchlist فقط إذا لم تُرسل اليوم (تجنب التكرار عند restart)
            if not _flags["watchlist_sent"]:
                notify_pre_market(daily_stocks)
                _flags["watchlist_sent"] = True
                _save_flags_to_disk()
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

    _flags["pre_market_done"] = True
    _save_flags_to_disk()
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
                trade.closing_in_progress = True   # ← يمنع sync من تسجيل خسارة ثانية

                # ── تحقق من qty_available قبل البيع
                # إذا = 0 فـ Alpaca نفّذ الـ Bracket Stop تلقائياً — لا نبيع مرة ثانية
                from executor import get_position_qty_available
                available = get_position_qty_available(trade.ticker)

                if available == 0:
                    log(f"  ℹ️  {trade.ticker}: Alpaca نفّذ الـ Stop تلقائياً — تسجيل فقط بدون بيع")
                elif available < exit_qty:
                    log(f"  ⚠️  {trade.ticker}: متاح={available} < مطلوب={exit_qty} → بيع ما هو متاح")
                    place_market_sell(trade.ticker, available, side=side)
                    exit_qty = available
                else:
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
                # ── تحديد WIN/LOSS — اقتراح صديق: total_pnl = tp1_pnl + remaining_pnl
                if trade.tp1_hit:
                    total_pnl = round(trade.tp1_pnl + pnl, 2)
                    outcome   = "win" if total_pnl > 0 else "loss"
                    log(f"  📊 Total PnL: tp1=${trade.tp1_pnl:+.2f} + remaining=${pnl:+.2f} = ${total_pnl:+.2f} → {outcome}")
                    if outcome == "win":
                        risk_manager.record_win(total_pnl, r)
                        try:
                            notify_trade_closed(
                                ticker=trade.ticker, side=side,
                                entry_price=trade.entry_price, exit_price=price,
                                quantity=exit_qty, total_profit=total_pnl,
                                r_achieved=r, exit_reason="Stop (after TP1)",
                                tp1_profit=trade.tp1_pnl,
                            )
                        except Exception as e:
                            log(f"Telegram error: {e}")
                    else:
                        stopped = risk_manager.record_loss(total_pnl, r)
                        try:
                            notify_trade_closed(
                                ticker=trade.ticker, side=side,
                                entry_price=trade.entry_price, exit_price=price,
                                quantity=exit_qty, total_profit=total_pnl,
                                r_achieved=r, exit_reason="Stop (after TP1)",
                                tp1_profit=trade.tp1_pnl,
                            )
                            if stopped:
                                notify_system_stopped()
                        except Exception as e:
                            log(f"Telegram error: {e}")
                else:
                    # Stop بدون TP1 → خسارة كاملة
                    stopped = risk_manager.record_loss(pnl, r)
                    try:
                        notify_trade_closed(
                            ticker=trade.ticker, side=side,
                            entry_price=trade.entry_price, exit_price=price,
                            quantity=exit_qty, total_profit=pnl,
                            r_achieved=r, exit_reason="Stop Loss",
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
                trade.closing_in_progress = True
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
                    notify_tp1_hit(
                        ticker=trade.ticker, side=side,
                        entry_price=trade.entry_price, tp1_price=price,
                        qty_tp1=tp1_qty, profit_tp1=pnl_tp1,
                        r_achieved=r,
                        qty_remaining=trade.quantity_remaining - tp1_qty,
                        tp2_price=trade.target_tp2,
                    )
                except Exception as e:
                    log(f"Telegram error: {e}")
                trade.tp1_hit              = True
                trade.tp1_pnl              = pnl_tp1
                # ── تفعيل Trailing Stop المتقدم
                trade.trailing_active      = True
                trade.highest_price        = price   # نبدأ من سعر TP1
                trade.lowest_price         = price
                trade.closing_in_progress  = False   # ← الصفقة لا تزال مفتوحة
                trade.stop_loss = new_stop
                # ── تحديث الوقف الفعلي في Alpaca (نقل للتعادل)
                try:
                    update_stop_loss_alpaca(trade, new_stop)
                except Exception as e:
                    log(f"⚠️ فشل تحديث Stop في Alpaca لـ {trade.ticker}: {e}")

            elif status == "target":
                exit_qty = result.get("exit_qty", trade.quantity_remaining if trade.tp1_hit else trade.quantity)
                label    = "TP2" if trade.tp1_hit else "TARGET"
                log(f"{label}: {trade.ticker} [{side.upper()}] @ ${price:.2f} | R={r:.1f}")
                trade.closing_in_progress = True
                place_market_sell(trade.ticker, exit_qty, side=side)
                pnl = round(
                    (price - trade.entry_price) * exit_qty if side == "long"
                    else (trade.entry_price - price) * exit_qty, 2
                )
                # ── حساب total_pnl الصريح (اقتراح صديق)
                if trade.tp1_hit:
                    total_pnl = round(trade.tp1_pnl + pnl, 2)
                    log(f"  📊 Total PnL: tp1=${trade.tp1_pnl:+.2f} + tp2=${pnl:+.2f} = ${total_pnl:+.2f}")
                else:
                    total_pnl = pnl
                record_trade(
                    ticker=trade.ticker, strategy=trade.strategy,
                    entry_price=trade.entry_price, exit_price=price,
                    quantity=exit_qty, stop_loss=trade.stop_loss,
                    target=trade.target, risk_amount=trade.risk_amount,
                    exit_reason="target", opened_at=trade.opened_at, side=side,
                )
                risk_manager.record_win(total_pnl, r)
                try:
                    if trade.tp1_hit:
                        notify_tp2_hit(
                            ticker=trade.ticker, side=side,
                            entry_price=trade.entry_price, tp2_price=price,
                            qty_tp2=exit_qty, profit_tp2=pnl,
                            r_achieved=r,
                            profit_tp1=trade.tp1_pnl, total_profit=total_pnl,
                        )
                    else:
                        notify_trade_closed(
                            ticker=trade.ticker, side=side,
                            entry_price=trade.entry_price, exit_price=price,
                            quantity=exit_qty, total_profit=total_pnl,
                            r_achieved=r, exit_reason="Target",
                        )
                except Exception as e:
                    log(f"Telegram error: {e}")
                trades_to_remove.append(trade)

            elif status == "trail_updated":
                new_stop = result["new_stop"]
                log(f"TRAIL: {trade.ticker} stop ${trade.stop_loss:.2f} -> ${new_stop:.2f}")
                # ── تحديث الوقف الفعلي في Alpaca
                try:
                    update_stop_loss_alpaca(trade, new_stop)
                except Exception as e:
                    log(f"⚠️ فشل تحديث Trailing Stop في Alpaca لـ {trade.ticker}: {e}")
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
# تحديث Universe كل ساعة أثناء السوق
# -----------------------------------------

def refresh_universe_if_needed():
    global daily_stocks, last_universe_refresh
    now  = get_ny_time()
    mins = (now - last_universe_refresh).total_seconds() / 60
    if mins < UNIVERSE_REFRESH_MIN:
        return
    try:
        log("🔄 تحديث قائمة الأسهم (كل ساعة)...")
        new_stocks = get_daily_universe()
        if new_stocks:
            daily_stocks = new_stocks
            refresh_allowed_tickers(candidate_tickers=list(daily_stocks.keys()))
            last_universe_refresh = now
            log(f"✅ تم تحديث القائمة: {len(daily_stocks)} سهم")
            try:
                notify_universe_refresh(daily_stocks)
            except Exception as e:
                log(f"Telegram error (refresh): {e}")
        else:
            log("⚠️ فشل تحديث القائمة — نبقى على القائمة الحالية")
    except Exception as e:
        log(f"❌ خطأ في تحديث Universe: {e}")


# -----------------------------------------
# البحث عن إشارات جديدة
# -----------------------------------------

def scan_for_signals():
    global open_trades, last_no_opp, last_scan

    if len(open_trades) >= MAX_TOTAL:
        return

    # ── تحقق من الفترة الزمنية — لا تفحص أكثر من مرة كل 5 دقائق
    now  = get_ny_time()
    mins = (now - last_scan).total_seconds() / 60
    if mins < SCAN_INTERVAL_MIN:
        return

    try:
        last_scan = now  # ← تحديث وقت آخر فحص
        account = get_account()
        if not account:
            log("Could not fetch account -- skipping scan")
            return

        # ── طبقة حماية: جلب المراكز الفعلية من Alpaca الآن
        # يمنع فتح صفقة على سهم موجود حتى لو open_trades فارغة
        try:
            import requests as _req
            from config import ALPACA_BASE_URL, ALPACA_API_KEY, ALPACA_SECRET_KEY
            _headers = {
                "APCA-API-KEY-ID":     ALPACA_API_KEY,
                "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
            }
            r_pos = _req.get(f"{ALPACA_BASE_URL}/v2/positions", headers=_headers, timeout=8)
            alpaca_live_tickers = set()
            if r_pos.status_code == 200:
                alpaca_live_tickers = {p.get("symbol", "") for p in r_pos.json()}

            open_tickers_system = {t.ticker for t in open_trades}
            ghost_tickers = alpaca_live_tickers - open_tickers_system
            if ghost_tickers:
                log(f"⚠️  مراكز في Alpaca لكن غير موجودة في النظام: {ghost_tickers} — سيُحظر فتح أي منها")
        except Exception:
            alpaca_live_tickers = {t.ticker for t in open_trades}
            ghost_tickers = set()

        current_positions = {t.ticker: (t.side, t.strategy) for t in open_trades}
        # أضف الـ ghost tickers لـ current_positions لمنع selector من اختيارها
        for gt in ghost_tickers:
            current_positions[gt] = ("unknown", "unknown")

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
                _flags["daily_trade_num"] += 1
                _save_flags_to_disk()
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
                        target_tp1=signal.target_tp1,
                        target_tp2=signal.target_tp2,
                        qty_tp1=trade.quantity - trade.quantity_remaining,
                        qty_tp2=trade.quantity_remaining,
                        trade_number=_flags["daily_trade_num"],
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
    global open_trades

    if _flags["close_done"]:
        return

    log("=== MARKET CLOSE ROUTINE START ===")
    _flags["close_done"] = True
    _save_flags_to_disk()

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

    # ── إشعار Telegram: النظام انطلق بنجاح
    try:
        from notifier import notify_system_started
        balance = account.get("balance", 0) if account else 0
        notify_system_started(balance=balance, open_trades=len(open_trades))
    except Exception as e:
        log(f"Startup notification error: {e}")

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

            if is_pre_market_alert_time() and not _flags["pre_alert_done"]:
                run_pre_market_alert()

            elif is_pre_market_time() and not _flags["pre_market_done"]:
                run_pre_market()

            elif is_market_hours():
                if not risk_manager.can_trade():
                    log("System paused -- daily loss limit reached")
                elif not daily_stocks and not _flags["pre_market_done"]:
                    if system_state.maintenance_mode:
                        log("MAINTENANCE MODE -- waiting for /resume before pre-market")
                    else:
                        # ── انتظر دقيقتين بعد الـ startup لاستقرار IEX feed
                        elapsed = (datetime.now(TZ) - _startup_time).total_seconds()
                        if elapsed < 120:
                            remaining = int(120 - elapsed)
                            log(f"⏳ تهيئة السيرفر — انتظار {remaining}s لاستقرار البيانات...")
                        else:
                            log("Started during market hours -- running pre-market now...")
                            run_pre_market()
                elif daily_stocks:
                    monitor_open_trades()
                    refresh_universe_if_needed()
                    scan_for_signals()
                elif _flags["pre_market_done"] and not daily_stocks:
                    # فشل تحميل الأسهم سابقاً — نعيد المحاولة كل 5 دقائق
                    log("⚠️ Universe فارغ — إعادة المحاولة...")
                    _flags["pre_market_done"] = False  # يسمح بإعادة run_pre_market
                else:
                    log("No universe -- waiting for pre-market routine")

            elif is_close_time() and not _flags["close_done"]:
                run_market_close()

            else:
                if is_weekday():
                    t_now = get_ny_time().strftime("%H:%M")
                    if "09:30" <= t_now < "09:35":
                        log(f"⏳ السوق فتح — ننتظر 09:35 لتشغيل Pre-Market | {t_now}")
                    else:
                        log(f"After hours | {t} {TIMEZONE} | Next open: {get_next_market_open()}")
                else:
                    log(f"Weekend | {t} {TIMEZONE} | Next open: {get_next_market_open()}")

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
