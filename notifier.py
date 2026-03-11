# =============================================================
# notifier.py -- كل رسائل Telegram في مكان واحد
# كل الرسائل بقسمين: 🇬🇧 English + 🇦🇪 العربية
# =============================================================

import requests
from datetime import datetime
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TIMEZONE
import pytz

TZ = pytz.timezone(TIMEZONE)


def _send(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured -- check .env")
        return False
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def _now() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M %Z")


def notify_pre_market(stocks: list) -> bool:
    stocks_str = " | ".join(stocks[:20]) if stocks else "None"
    msg = (
        "🌅 <b>Pre-Market Alert | تنبيه ما قبل الافتتاح</b>\n"
        f"🕐 {_now()}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇬🇧 <b>English</b>\n"
        f"📋 Watchlist ({len(stocks)} stocks):\n"
        f"<code>{stocks_str}</code>\n"
        "⏳ Market opens at 09:30 EST\n"
        "\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇦🇪 <b>العربية</b>\n"
        f"📋 قائمة المراقبة ({len(stocks)} سهم):\n"
        f"<code>{stocks_str}</code>\n"
        "⏳ السوق يفتح الساعة 09:30 EST"
    )
    return _send(msg)


def notify_no_opportunity() -> bool:
    msg = (
        "🔍 <b>No Opportunity | لا توجد فرصة</b>\n"
        f"🕐 {_now()}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇬🇧 System is running and monitoring the market.\n\n"
        "🇦🇪 النظام يعمل ويراقب السوق."
    )
    return _send(msg)


def notify_trade_open(
    ticker: str, strategy: str, side: str,
    price: float, quantity: int,
    stop_loss: float, target: float, risk_amount: float,
    target_tp1: float = 0.0, target_tp2: float = 0.0,
    qty_tp1: int = 0, qty_tp2: int = 0,
    trade_number: int = 0,
) -> bool:
    emoji       = "🟩" if "BUY" in side else "🟥"
    side_ar     = "شراء" if "BUY" in side else "بيع على المكشوف"

    # استخدام TP2 كـ R ratio إذا متوفر
    main_target = target_tp2 if target_tp2 > 0 else target
    r_ratio     = round(abs(main_target - price) / abs(price - stop_loss), 2) if price != stop_loss else 0
    total_value = round(price * quantity, 2)

    # رقم الصفقة
    trade_num_line = f"\n🔔 <b>NEW TRADE #{trade_number}</b> 🔔\n" if trade_number > 0 else ""
    trade_num_ar   = f"\n🔔 <b>صفقة جديدة #{trade_number}</b> 🔔\n" if trade_number > 0 else ""

    # سطور TP1 / TP2
    if target_tp1 > 0 and target_tp2 > 0:
        tp_en = (
            f"🎯 TP1 ({qty_tp1} shares) : ${target_tp1:.2f} (1R)\n"
            f"🏆 TP2 ({qty_tp2} shares) : ${target_tp2:.2f} ({r_ratio}R)\n"
        )
        tp_ar = (
            f"🎯 الهدف 1 ({qty_tp1} سهم) : ${target_tp1:.2f} (1R)\n"
            f"🏆 الهدف 2 ({qty_tp2} سهم) : ${target_tp2:.2f} ({r_ratio}R)\n"
        )
    else:
        tp_en = f"🎯 Target       : ${main_target:.2f}\n📈 R Ratio      : {r_ratio}R\n"
        tp_ar = f"🎯 الهدف         : ${main_target:.2f}\n📈 نسبة R        : {r_ratio}R\n"

    msg = (
        f"{emoji} <b>New Trade -- {ticker}</b>\n"
        f"{trade_num_line}"
        f"📅 {_now()}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇬🇧 <b>English</b>\n"
        f"📊 Strategy    : {strategy}\n"
        f"▶️  Direction    : {side}\n"
        f"💰 Entry        : ${price:.2f}\n"
        f"🔢 Qty          : {quantity} shares\n"
        f"💵 Total Value  : ${total_value:,.2f}\n"
        f"🔴 Stop Loss    : ${stop_loss:.2f}\n"
        f"{tp_en}"
        f"⚠️  Risk         : ${risk_amount:.2f}\n"
        "\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇦🇪 <b>العربية</b>\n"
        f"{trade_num_ar}"
        f"📊 الاستراتيجية  : {strategy}\n"
        f"▶️  الاتجاه       : {side_ar}\n"
        f"💰 الدخول        : ${price:.2f}\n"
        f"🔢 الكمية        : {quantity} سهم\n"
        f"💵 إجمالي المبلغ : ${total_value:,.2f}\n"
        f"🔴 وقف الخسارة   : ${stop_loss:.2f}\n"
        f"{tp_ar}"
        f"⚠️  المخاطرة      : ${risk_amount:.2f}"
    )
    return _send(msg)


def notify_stop_updated(
    ticker: str, old_stop: float,
    new_stop: float, current_price: float,
) -> bool:
    msg = (
        f"🔄 <b>Stop Updated -- {ticker}</b>\n"
        f"🕐 {_now()}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇬🇧 <b>English</b>\n"
        f"📈 Price     : ${current_price:.2f}\n"
        f"🔴 Old Stop  : ${old_stop:.2f}\n"
        f"🟢 New Stop  : ${new_stop:.2f}\n"
        "\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇦🇪 <b>العربية</b>\n"
        f"📈 السعر         : ${current_price:.2f}\n"
        f"🔴 الوقف القديم  : ${old_stop:.2f}\n"
        f"🟢 الوقف الجديد  : ${new_stop:.2f}\n"
        "تم تحريك وقف الخسارة"
    )
    return _send(msg)


def notify_tp1_hit(
    ticker: str, side: str,
    entry_price: float, tp1_price: float,
    qty_tp1: int, profit_tp1: float,
    r_achieved: float, qty_remaining: int,
    tp2_price: float,
) -> bool:
    """رسالة واضحة عند ضرب الهدف الأول — الصفقة لا تزال مفتوحة."""
    direction = "📈 BUY" if side == "long" else "📉 SHORT"
    direction_ar = "شراء" if side == "long" else "مكشوف"
    msg = (
        f"🎯 <b>TP1 HIT ✅ — {ticker}</b>\n"
        f"🕐 {_now()}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇬🇧 <b>English</b>\n\n"
        f"{direction}\n"
        f"💰 Entry        : ${entry_price:.2f}\n"
        f"🎯 TP1 Exit     : ${tp1_price:.2f}\n"
        f"🔢 Qty Closed   : {qty_tp1} shares\n"
        f"📈 Profit TP1   : <b>+${profit_tp1:.2f}</b>\n"
        f"🎯 R            : {r_achieved:.2f}R\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔓 Still Open   : {qty_remaining} shares → TP2 @ ${tp2_price:.2f}\n"
        f"🔒 Stop → moved to breakeven\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇦🇪 <b>العربية</b>\n\n"
        f"{direction_ar}\n"
        f"💰 الدخول         : ${entry_price:.2f}\n"
        f"🎯 خروج TP1       : ${tp1_price:.2f}\n"
        f"🔢 الكمية المغلقة : {qty_tp1} سهم\n"
        f"📈 ربح TP1        : <b>+${profit_tp1:.2f}</b>\n"
        f"🎯 النسبة         : {r_achieved:.2f}R\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔓 لا تزال مفتوحة : {qty_remaining} سهم → TP2 @ ${tp2_price:.2f}\n"
        f"🔒 الوقف → انتقل للتعادل"
    )
    return _send(msg)


def notify_tp2_hit(
    ticker: str, side: str,
    entry_price: float, tp2_price: float,
    qty_tp2: int, profit_tp2: float,
    r_achieved: float,
    profit_tp1: float, total_profit: float,
) -> bool:
    """رسالة واضحة عند ضرب الهدف الثاني — الصفقة مغلقة بالكامل."""
    direction = "📈 BUY" if side == "long" else "📉 SHORT"
    direction_ar = "شراء" if side == "long" else "مكشوف"
    msg = (
        f"🏆 <b>TP2 HIT ✅ — {ticker} — TRADE CLOSED</b>\n"
        f"🕐 {_now()}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇬🇧 <b>English</b>\n\n"
        f"{direction}\n"
        f"💰 Entry        : ${entry_price:.2f}\n"
        f"🏆 TP2 Exit     : ${tp2_price:.2f}\n"
        f"🔢 Qty Closed   : {qty_tp2} shares\n"
        f"📈 Profit TP2   : <b>+${profit_tp2:.2f}</b>\n"
        f"🎯 R            : {r_achieved:.2f}R\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 TP1 Profit   : +${profit_tp1:.2f}\n"
        f"📊 TP2 Profit   : +${profit_tp2:.2f}\n"
        f"💰 Total Profit : <b>+${total_profit:.2f}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇦🇪 <b>العربية</b>\n\n"
        f"{direction_ar}\n"
        f"💰 الدخول         : ${entry_price:.2f}\n"
        f"🏆 خروج TP2       : ${tp2_price:.2f}\n"
        f"🔢 الكمية المغلقة : {qty_tp2} سهم\n"
        f"📈 ربح TP2        : <b>+${profit_tp2:.2f}</b>\n"
        f"🎯 النسبة         : {r_achieved:.2f}R\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 ربح TP1        : +${profit_tp1:.2f}\n"
        f"📊 ربح TP2        : +${profit_tp2:.2f}\n"
        f"💰 إجمالي الربح   : <b>+${total_profit:.2f}</b>\n"
        "✅ <b>الصفقة مغلقة بالكامل</b>"
    )
    return _send(msg)


def notify_trade_closed(
    ticker: str, side: str,
    entry_price: float, exit_price: float,
    quantity: int, total_profit: float,
    r_achieved: float, exit_reason: str,
    tp1_profit: float = 0.0,
) -> bool:
    """
    رسالة الإغلاق النهائي للصفقة — تُستخدم عند:
    - Stop بعد TP1 (ربح أو خسارة)
    - Stop بدون TP1 (خسارة كاملة)
    - Manual Close (/closeall)
    """
    is_win   = total_profit > 0
    emoji    = "✅ WIN" if is_win else "❌ LOSS"
    pnl_sign = "+" if is_win else ""
    pnl_emoji = "📈" if is_win else "📉"
    direction_ar = "شراء" if side == "long" else "مكشوف"

    # هل مرّت بـ TP1 قبل الإغلاق؟
    tp1_line = f"📊 TP1 Profit  : +${tp1_profit:.2f}\n" if tp1_profit > 0 else ""
    tp1_line_ar = f"📊 ربح TP1     : +${tp1_profit:.2f}\n" if tp1_profit > 0 else ""

    msg = (
        f"{emoji} — <b>{ticker} — TRADE CLOSED</b>\n"
        f"🕐 {_now()}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇬🇧 <b>English</b>\n\n"
        f"{'📈 BUY' if side == 'long' else '📉 SHORT'}\n"
        f"💰 Entry        : ${entry_price:.2f}\n"
        f"💰 Exit         : ${exit_price:.2f}\n"
        f"🔢 Qty          : {quantity} shares\n"
        f"🚪 Reason       : {exit_reason}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{tp1_line}"
        f"{pnl_emoji} Total P&L    : <b>{pnl_sign}${total_profit:.2f}</b>\n"
        f"🎯 R            : {pnl_sign}{r_achieved:.2f}R\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇦🇪 <b>العربية</b>\n\n"
        f"{direction_ar}\n"
        f"💰 الدخول         : ${entry_price:.2f}\n"
        f"💰 الخروج         : ${exit_price:.2f}\n"
        f"🔢 الكمية         : {quantity} سهم\n"
        f"🚪 السبب          : {exit_reason}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{tp1_line_ar}"
        f"{pnl_emoji} إجمالي الربح  : <b>{pnl_sign}${total_profit:.2f}</b>\n"
        f"🎯 النسبة         : {pnl_sign}{r_achieved:.2f}R\n"
        f"{'✅ صفقة رابحة' if is_win else '❌ صفقة خاسرة'}"
    )
    return _send(msg)


def notify_trade_win(
    ticker: str, entry_price: float, exit_price: float,
    quantity: int, profit: float, r_achieved: float,
) -> bool:
    """دالة قديمة للتوافق — تستدعي notify_trade_closed داخلياً."""
    return notify_trade_closed(
        ticker=ticker, side="long",
        entry_price=entry_price, exit_price=exit_price,
        quantity=quantity, total_profit=profit,
        r_achieved=r_achieved, exit_reason="Target",
    )


def notify_trailing_update(
    ticker: str,
    side: str,
    old_stop: float,
    new_stop: float,
    current_price: float,
    r_moved: float,
    update_count: int,
) -> bool:
    """
    يُرسل إشعار Telegram عند تحريك Trailing Stop بشكل كبير (> 1R).
    لا يُرسل عند كل تحديث — فقط عند التحريكات المهمة.
    """
    direction = "📈" if side == "long" else "📉"
    side_ar   = "شراء" if side == "long" else "مكشوف"
    moved     = round(abs(new_stop - old_stop), 4)

    msg = (
        f"🔄 <b>Trailing Stop — {ticker}</b>\n"
        f"🕐 {_now()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🇬🇧 English\n\n"
        f"{direction} Side       : {side.upper()}\n"
        f"💰 Price      : ${current_price:.2f}\n"
        f"🔒 Old Stop   : ${old_stop:.2f}\n"
        f"🔒 New Stop   : ${new_stop:.2f}  (+${moved:.4f})\n"
        f"🎯 R Moved    : {r_moved:+.2f}R\n"
        f"🔢 Update #   : {update_count}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🇦🇪 العربية\n\n"
        f"{direction} الاتجاه    : {side_ar}\n"
        f"💰 السعر الحالي : ${current_price:.2f}\n"
        f"🔒 الوقف القديم : ${old_stop:.2f}\n"
        f"🔒 الوقف الجديد : ${new_stop:.2f}\n"
        f"🎯 المسافة      : {r_moved:+.2f}R\n"
    )
    return _send(msg)


def notify_trailing_max_reached(ticker: str, update_count: int) -> bool:
    """يُرسل إشعار عند بلوغ الحد الأقصى لتحديثات الـ Trailing."""
    msg = (
        f"⚠️ <b>Trailing Max Reached — {ticker}</b>\n"
        f"🕐 {_now()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"وصل عداد الـ Trailing لـ {update_count} تحديث.\n"
        f"تم إيقاف التحديث التلقائي — الـ Stop الحالي ثابت.\n"
        f"راقب الصفقة يدوياً إذا لزم."
    )
    return _send(msg)


def notify_trade_loss(
    ticker: str, entry_price: float, exit_price: float,
    quantity: int, loss: float, daily_losses: int,
) -> bool:
    warning_en = ""
    warning_ar = ""
    if daily_losses >= 2:
        warning_en = "\n⛔ <b>Daily loss limit reached -- System STOPPED</b>"
        warning_ar = "\n⛔ <b>تم الوصول لحد الخسائر -- النظام متوقف</b>"
    msg = (
        f"❌ <b>LOSS -- {ticker}</b>\n"
        f"🕐 {_now()}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇬🇧 <b>English</b>\n"
        f"💰 Entry       : ${entry_price:.2f}\n"
        f"💰 Exit        : ${exit_price:.2f}\n"
        f"🔢 Qty         : {quantity} shares\n"
        f"📉 Loss        : <b>-${loss:.2f}</b>\n"
        f"📉 Daily Loss  : {daily_losses}/2"
        f"{warning_en}\n"
        "\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇦🇪 <b>العربية</b>\n"
        f"💰 الدخول       : ${entry_price:.2f}\n"
        f"💰 الخروج       : ${exit_price:.2f}\n"
        f"🔢 الكمية       : {quantity} سهم\n"
        f"📉 الخسارة      : <b>-${loss:.2f}</b>\n"
        f"📉 خسائر اليوم : {daily_losses}/2"
        f"{warning_ar}"
    )
    return _send(msg)


def notify_system_started(balance: float, open_trades: int) -> bool:
    """تُرسَل فور اكتمال الـ Deploy وبدء النظام."""
    trades_line = f"📊 مراكز مفتوحة: {open_trades}" if open_trades > 0 else "📊 لا توجد مراكز مفتوحة"
    msg = (
        f"✅ <b>النظام انطلق بنجاح</b>\n"
        f"🕐 {_now()}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💰 الرصيد: <b>${balance:,.2f}</b>\n"
        f"{trades_line}\n"
        "⚙️ الـ Deploy اكتمل — النظام يعمل بشكل سليم ✅"
    )
    return _send(msg)


def notify_system_stopped() -> bool:
    msg = (
        "⛔ <b>SYSTEM STOPPED | النظام متوقف</b>\n"
        f"🕐 {_now()}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇬🇧 <b>English</b>\n"
        "Daily loss limit of 2 trades reached.\n"
        "System will resume tomorrow at market open.\n"
        "\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇦🇪 <b>العربية</b>\n"
        "تم الوصول لحد الخسارتين اليوميتين.\n"
        "سيعود النظام غداً عند افتتاح السوق."
    )
    return _send(msg)


def notify_daily_report(
    date: str,
    total_trades: int,
    wins: int,
    losses: int,
    total_r: float,
    total_pnl: float,
    balance: float,
    long_trades: int = 0,
    short_trades: int = 0,
    best_trade: float = 0.0,
    worst_trade: float = 0.0,
    avg_win: float = 0.0,
    avg_loss: float = 0.0,
    open_trades: list = None,
) -> bool:

    win_rate    = (wins / total_trades * 100) if total_trades > 0 else 0
    pnl_emoji   = "📈" if total_pnl >= 0 else "📉"
    pnl_sign    = "+" if total_pnl >= 0 else ""
    r_emoji     = "✅" if total_r >= 0 else "❌"
    r_sign      = "+" if total_r >= 0 else ""
    wrate_emoji = "🟢" if win_rate >= 50 else "🔴"

    msg = (
        f"📊 <b>Daily Report | التقرير اليومي</b>\n"
        f"🗓 {date}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇬🇧 <b>English</b>\n\n"
        f"🔢 Total Trades : <b>{total_trades}</b>\n"
        f"🟢 Wins : {wins}   🔴 Losses : {losses}\n"
        f"{wrate_emoji} Win Rate : {win_rate:.1f}%\n"
        f"📊 Long : {long_trades}   |   Short : {short_trades}\n"
        f"{r_emoji} Total R : {r_sign}{total_r:.2f}R\n"
        f"{pnl_emoji} Total PnL : ${pnl_sign}{total_pnl:.2f}\n"
    )

    if total_trades > 0:
        msg += (
            f"\n🚀 Best Trade  : +${best_trade:.2f}\n"
            f"💥 Worst Trade : ${worst_trade:.2f}\n"
            f"📈 Avg Win     : +${avg_win:.2f}\n"
            f"📉 Avg Loss    : ${avg_loss:.2f}\n"
        )

    msg += f"\n💰 <b>Balance</b> : ${balance:,.2f}\n"

    if open_trades:
        msg += "\n📂 <b>Open Positions (carry over)</b>\n"
        for t in open_trades:
            side_emoji = "🟢" if t["side"] == "long" else "🔴"
            msg += f"  {side_emoji} {t['ticker']} | entry=${t['entry']:.2f} | R={t['r']:+.2f}\n"

    msg += (
        "\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇦🇪 <b>العربية</b>\n\n"
        f"🔢 إجمالي الصفقات : <b>{total_trades}</b>\n"
        f"🟢 أرباح : {wins}   🔴 خسائر : {losses}\n"
        f"{wrate_emoji} نسبة الفوز : {win_rate:.1f}%\n"
        f"📊 شراء : {long_trades}   |   مكشوف : {short_trades}\n"
        f"{r_emoji} إجمالي R : {r_sign}{total_r:.2f}R\n"
        f"{pnl_emoji} إجمالي الربح : ${pnl_sign}{total_pnl:.2f}\n"
    )

    if total_trades > 0:
        msg += (
            f"\n🚀 أفضل صفقة : +${best_trade:.2f}\n"
            f"💥 أسوأ صفقة  : ${worst_trade:.2f}\n"
        )

    msg += f"\n💰 <b>الرصيد</b> : ${balance:,.2f}"

    if open_trades:
        msg += "\n\n📂 <b>صفقات مفتوحة (تنتقل لغد)</b>\n"
        for t in open_trades:
            side_ar    = "شراء" if t["side"] == "long" else "مكشوف"
            side_emoji = "🟢" if t["side"] == "long" else "🔴"
            msg += f"  {side_emoji} {t['ticker']} | {side_ar} | دخول=${t['entry']:.2f} | R={t['r']:+.2f}\n"

    return _send(msg)
