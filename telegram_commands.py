# =============================================================
# telegram_commands.py -- استقبال الأوامر من Telegram
# الأوامر:
#   /maintenance  -- إيقاف النظام كلياً + انتظار إعادة التشغيل
#   /resume       -- إعادة تشغيل النظام بعد الصيانة
#   /status       -- حالة النظام الآن
#   /help         -- قائمة الأوامر
# =============================================================

import requests
import threading
import os
import time
from datetime import datetime
import pytz

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TIMEZONE

TZ = pytz.timezone(TIMEZONE)

# ملف الـ flag على Render Disk
# وجوده = النظام في صيانة، غيابه = النظام يعمل
DISK_PATH         = os.getenv("RENDER_DISK_PATH", "logs")
MAINTENANCE_FLAG  = os.path.join(DISK_PATH, ".maintenance")


# -----------------------------------------
# حالة النظام
# -----------------------------------------

class SystemState:
    def __init__(self):
        # قراءة الـ flag عند البداية
        # لو كان موجوداً من جلسة سابقة نتجاهله ونبدأ نظيف
        self.maintenance_mode : bool = False
        self._last_update_id  : int  = 0

    def enter_maintenance(self):
        """يُفعّل وضع الصيانة ويكتب الـ flag على الـ disk."""
        self.maintenance_mode = True
        try:
            os.makedirs(DISK_PATH, exist_ok=True)
            with open(MAINTENANCE_FLAG, "w") as f:
                f.write(datetime.now(TZ).isoformat())
        except Exception as e:
            print(f"Warning: could not write maintenance flag: {e}", flush=True)

    def exit_maintenance(self):
        """يُلغي وضع الصيانة ويحذف الـ flag."""
        self.maintenance_mode = False
        try:
            if os.path.exists(MAINTENANCE_FLAG):
                os.remove(MAINTENANCE_FLAG)
        except Exception as e:
            print(f"Warning: could not remove maintenance flag: {e}", flush=True)

    def is_running(self) -> bool:
        return not self.maintenance_mode


system_state = SystemState()


# -----------------------------------------
# إرسال Telegram
# -----------------------------------------

def _send(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "HTML",
        }
        requests.post(url, json=payload, timeout=10)
        return True
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)
        return False


def _now() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M %Z")


# -----------------------------------------
# جلب الأوامر
# -----------------------------------------

def _get_updates() -> list:
    try:
        url    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        params = {
            "offset":  system_state._last_update_id + 1,
            "timeout": 5,
            "limit":   10,
        }
        response = requests.get(url, params=params, timeout=10)
        return response.json().get("result", [])
    except Exception:
        return []


# -----------------------------------------
# معالجة الأوامر
# -----------------------------------------

def _handle_command(command: str, context: dict):
    command = command.strip().lower().split("@")[0]
    now_str = _now()

    # ── /maintenance : إيقاف النظام كلياً
    if command == "/maintenance":
        if system_state.maintenance_mode:
            _send(
                "⚠️ <b>النظام في وضع الصيانة بالفعل</b>\n"
                f"🕐 {now_str}\n"
                "ارسل /resume عند الانتهاء."
            )
            return

        open_trades = context.get("open_trades", [])
        system_state.enter_maintenance()

        _send(
            "🔧 <b>MAINTENANCE MODE | وضع الصيانة</b>\n"
            f"🕐 {now_str}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "⛔ <b>النظام متوقف كلياً</b>\n"
            f"📊 الصفقات المفتوحة: {len(open_trades)}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "✅ يمكنك الآن تعديل الملفات ورفعها على GitHub\n"
            "✅ ثم اضغط Manual Deploy في Render\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "⚠️ النظام سينتظر أمر /resume قبل التداول\n"
            "ارسل /resume عند الانتهاء من الصيانة."
        )
        print(f"[{now_str}]  MAINTENANCE MODE ON -- System paused", flush=True)

    # ── /resume : استئناف النظام بعد الصيانة
    elif command == "/resume":
        if not system_state.maintenance_mode:
            _send(
                "ℹ️ <b>النظام يعمل بشكل طبيعي</b>\n"
                f"🕐 {now_str}\n"
                "لا يوجد وضع صيانة نشط حالياً."
            )
            return

        system_state.exit_maintenance()

        _send(
            "✅ <b>System Resumed | تم استئناف النظام</b>\n"
            f"🕐 {now_str}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🚀 النظام نشط ويعمل بشكل طبيعي\n"
            "📈 جاري استئناف التداول...\n"
            "ارسل /status للتحقق من الحالة."
        )
        print(f"[{now_str}]  MAINTENANCE MODE OFF -- Resumed", flush=True)

    # ── /status : حالة النظام
    elif command == "/status":
        open_trades  = context.get("open_trades", [])
        risk_manager = context.get("risk_manager")
        daily_stocks = context.get("daily_stocks", {})
        pre_done     = context.get("pre_market_done", False)
        close_done   = context.get("close_done", False)

        if system_state.maintenance_mode:
            mode_str = "🔧 Maintenance / صيانة"
        else:
            mode_str = "✅ Active / نشط"

        trades_info = ""
        for t in open_trades:
            emoji = "🟢" if t.side == "long" else "🔴"
            trades_info += f"\n    {emoji} {t.ticker} entry=${t.entry_price:.2f}"

        risk_info = ""
        if risk_manager:
            can = "✅ Yes" if risk_manager.can_trade() else "⛔ No (limit reached)"
            risk_info = (
                f"\n📉 Daily losses : {risk_manager.daily_losses}/2"
                f"\n📈 Daily wins   : {risk_manager.daily_wins}"
                f"\n🔓 Can trade    : {can}"
            )

        _send(
            f"📊 <b>System Status | حالة النظام</b>\n"
            f"🕐 {now_str}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"⚙️  Mode        : {mode_str}\n"
            f"📋 Universe    : {len(daily_stocks)} stocks\n"
            f"📂 Open trades : {len(open_trades)}"
            f"{trades_info}\n"
            f"🌅 Pre-market  : {'Done ✅' if pre_done else 'Pending ⏳'}\n"
            f"🔔 Day closed  : {'Yes ✅' if close_done else 'No'}"
            f"{risk_info}"
        )

    # ── /closeall : إغلاق كل المراكز فوراً
    elif command == "/closeall":
        context_data = context() if callable(context) else context
        open_trades  = context_data.get("open_trades", [])

        if not open_trades:
            _send("ℹ️ لا توجد صفقات مفتوحة حالياً.")
        else:
            _send(
                f"⚠️ <b>تحذير</b> — سيتم إغلاق {len(open_trades)} صفقة مفتوحة فوراً!\n"
                "أرسل /confirmclose للتأكيد."
            )
            system_state._pending_closeall = True

    # ── /confirmclose : تأكيد إغلاق كل المراكز
    elif command == "/confirmclose":
        if not getattr(system_state, "_pending_closeall", False):
            _send("❓ لا يوجد أمر إغلاق معلق. أرسل /closeall أولاً.")
        else:
            system_state._pending_closeall = False
            try:
                from executor import close_all_positions, get_current_price, _delete_open_trades_sheets
                from notifier import notify_trade_win, notify_trade_loss

                context_data = context() if callable(context) else context
                open_trades  = context_data.get("open_trades", [])
                risk_manager = context_data.get("risk_manager")

                # ── حساب P&L لكل صفقة قبل الإغلاق
                for trade in list(open_trades):
                    try:
                        current_price = get_current_price(trade.ticker)
                        if current_price <= 0:
                            current_price = trade.entry_price

                        if trade.side == "long":
                            pnl = (current_price - trade.entry_price) * trade.quantity
                        else:
                            pnl = (trade.entry_price - current_price) * trade.quantity

                        pnl = round(pnl, 2)
                        risk = getattr(trade, "risk_amount", 0)
                        r_multiple = round(pnl / risk, 2) if risk > 0 else 0.0

                        if pnl >= 0:
                            if risk_manager:
                                risk_manager.daily_wins += 1
                            notify_trade_win(
                                ticker=trade.ticker,
                                side=trade.side,
                                entry=trade.entry_price,
                                exit_price=current_price,
                                pnl=pnl,
                                r_multiple=r_multiple,
                                exit_reason="Manual /closeall",
                            )
                        else:
                            if risk_manager:
                                risk_manager.record_loss()
                            notify_trade_loss(
                                ticker=trade.ticker,
                                side=trade.side,
                                entry=trade.entry_price,
                                exit_price=current_price,
                                pnl=pnl,
                                r_multiple=r_multiple,
                                exit_reason="Manual /closeall",
                            )
                    except Exception as e:
                        _send(f"⚠️ خطأ في حساب P&L لـ {trade.ticker}: {e}")

                # ── إغلاق كل المراكز في Alpaca
                success = close_all_positions()

                # ── مسح الذاكرة و Sheets
                open_trades.clear()
                try:
                    _delete_open_trades_sheets()
                except Exception:
                    pass

                if success:
                    _send(
                        "✅ <b>تم إغلاق كل المراكز</b>\n"
                        "━━━━━━━━━━━━━━━━━━\n"
                        "🇬🇧 All positions closed successfully.\n\n"
                        "🇦🇪 تم إغلاق جميع الصفقات بنجاح."
                    )
                else:
                    _send("⚠️ فشل إغلاق بعض المراكز — تحقق من Alpaca يدوياً.")
            except Exception as e:
                _send(f"❌ خطأ في إغلاق المراكز: {e}")

    # ── /help
    elif command == "/help":
        _send(
            "📖 <b>BBAI Trading System Commands</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🔧 /maintenance\n"
            "    إيقاف النظام كلياً للصيانة\n\n"
            "✅ /resume\n"
            "    استئناف النظام بعد الصيانة\n\n"
            "📊 /status\n"
            "    عرض حالة النظام الآن\n\n"
            "🚨 /closeall\n"
            "    إغلاق كل الصفقات المفتوحة فوراً\n\n"
            "📖 /help\n"
            "    هذه القائمة"
        )

    else:
        _send(
            f"❓ أمر غير معروف: <code>{command}</code>\n"
            "ارسل /help لقائمة الأوامر."
        )


# -----------------------------------------
# حلقة الاستماع في الخلفية
# -----------------------------------------

def _polling_loop(get_context_fn):
    print("Telegram command listener started.", flush=True)

    while True:
        try:
            updates = _get_updates()
            for update in updates:
                update_id = update.get("update_id", 0)
                system_state._last_update_id = update_id

                message = update.get("message", {})
                text    = message.get("text", "").strip()
                chat_id = str(message.get("chat", {}).get("id", ""))

                if chat_id != str(TELEGRAM_CHAT_ID):
                    continue

                if text.startswith("/"):
                    print(f"Command: {text}", flush=True)
                    _handle_command(text, get_context_fn())

        except Exception as e:
            print(f"Listener error: {e}", flush=True)

        time.sleep(3)


def start_command_listener(get_context_fn):
    """يبدأ الاستماع في خيط خلفي. يُستدعى مرة واحدة من main.py."""
    thread = threading.Thread(
        target=_polling_loop,
        args=(get_context_fn,),
        daemon=True,
        name="TelegramListener",
    )
    thread.start()


# -----------------------------------------
# إشعارات الأخطاء
# -----------------------------------------

def notify_error(error_msg: str, is_resolved: bool = False):
    if is_resolved:
        _send(
            "✅ <b>Issue Resolved | تم حل المشكلة</b>\n"
            f"🕐 {_now()}\n"
            "النظام يعمل بشكل طبيعي الآن."
        )
    else:
        short_err = str(error_msg)[:200]
        _send(
            "⚠️ <b>System Issue | مشكلة في النظام</b>\n"
            f"🕐 {_now()}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"<code>{short_err}</code>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🔄 ما زالت المشكلة قائمة — جاري حلها...\n"
            "ارسل /status لمعرفة الحالة."
        )
