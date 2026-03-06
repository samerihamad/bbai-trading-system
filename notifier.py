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
) -> bool:
    emoji       = "🟩" if "BUY" in side else "🟥"
    side_ar     = "شراء" if "BUY" in side else "بيع على المكشوف"
    r_ratio     = round(abs(target - price) / abs(price - stop_loss), 2) if price != stop_loss else 0
    total_value = round(price * quantity, 2)
    msg = (
        f"{emoji} <b>New Trade -- {ticker}</b>\n"
        f"📅 {_now()}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇬🇧 <b>English</b>\n"
        f"📊 Strategy    : {strategy}\n"
        f"▶️  Direction    : {side}\n"
        f"💰 Entry        : ${price:.2f}\n"
        f"🔢 Qty          : {quantity} shares\n"
        f"💵 Total Value  : ${total_value:,.2f}\n"
        f"🔴 Stop Loss    : ${stop_loss:.2f}\n"
        f"🎯 Target       : ${target:.2f}\n"
        f"📈 R Ratio      : {r_ratio}R\n"
        f"⚠️  Risk         : ${risk_amount:.2f}\n"
        "\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇦🇪 <b>العربية</b>\n"
        f"📊 الاستراتيجية  : {strategy}\n"
        f"▶️  الاتجاه       : {side_ar}\n"
        f"💰 الدخول        : ${price:.2f}\n"
        f"🔢 الكمية        : {quantity} سهم\n"
        f"💵 إجمالي المبلغ : ${total_value:,.2f}\n"
        f"🔴 وقف الخسارة   : ${stop_loss:.2f}\n"
        f"🎯 الهدف         : ${target:.2f}\n"
        f"📈 نسبة R        : {r_ratio}R\n"
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


def notify_trade_win(
    ticker: str, entry_price: float, exit_price: float,
    quantity: int, profit: float, r_achieved: float,
) -> bool:
    msg = (
        f"✅ <b>WIN -- {ticker}</b>\n"
        f"🕐 {_now()}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇬🇧 <b>English</b>\n"
        f"💰 Entry   : ${entry_price:.2f}\n"
        f"💰 Exit    : ${exit_price:.2f}\n"
        f"🔢 Qty     : {quantity} shares\n"
        f"📈 Profit  : <b>+${profit:.2f}</b>\n"
        f"🎯 R       : {r_achieved:.2f}R\n"
        "\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🇦🇪 <b>العربية</b>\n"
        f"💰 الدخول  : ${entry_price:.2f}\n"
        f"💰 الخروج  : ${exit_price:.2f}\n"
        f"🔢 الكمية  : {quantity} سهم\n"
        f"📈 الربح   : <b>+${profit:.2f}</b>\n"
        f"🎯 النسبة  : {r_achieved:.2f}R"
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
