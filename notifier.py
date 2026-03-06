# =============================================================
# notifier.py -- كل رسائل Telegram في مكان واحد
# =============================================================

import requests
from datetime import datetime
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TIMEZONE
import pytz

TZ = pytz.timezone(TIMEZONE)


# -----------------------------------------
# الدالة الاساسية للارسال
# -----------------------------------------

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


# -----------------------------------------
# 1. تنبيه ما قبل الافتتاح
# -----------------------------------------

def notify_pre_market(stocks: list) -> bool:
    stocks_str = " | ".join(stocks[:20]) if stocks else "None"
    msg = (
        "&#127774; <b>Pre-Market Alert</b>\n"
        "&#x062a;&#x0646;&#x0628;&#x064a;&#x0647; &#x0645;&#x0627; &#x0642;&#x0628;&#x0644; &#x0627;&#x0644;&#x0627;&#x0641;&#x062a;&#x062a;&#x0627;&#x062d;\n"
        f"&#128336; {_now()}\n"
        "&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;\n"
        f"&#128203; Watchlist ({len(stocks)} stocks):\n"
        f"<code>{stocks_str}</code>\n"
        "&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;\n"
        "&#9203; Market opens in 30 minutes\n"
        "&#x627;&#x644;&#x633;&#x648;&#x642; &#x064a;&#x0641;&#x062a;&#x062d; &#x062e;&#x0644;&#x0627;&#x0644; 30 &#x062f;&#x0642;&#x064a;&#x0642;&#x0629;"
    )
    return _send(msg)


# -----------------------------------------
# 2. لا توجد فرصة
# -----------------------------------------

def notify_no_opportunity() -> bool:
    msg = (
        "&#128269; <b>No Opportunity / &#x644;&#x0627; &#x062a;&#x0648;&#x062c;&#x062f; &#x0641;&#x0631;&#x0635;&#x0629;</b>\n"
        f"&#128336; {_now()}\n"
        "&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;\n"
        "System is running and monitoring the market.\n"
        "&#x627;&#x644;&#x646;&#x638;&#x627;&#x645; &#x064a;&#x0639;&#x0645;&#x0644; &#x0648;&#x064a;&#x0631;&#x0627;&#x0642;&#x0628; &#x0627;&#x0644;&#x0633;&#x0648;&#x0642;."
    )
    return _send(msg)


# -----------------------------------------
# 3. فتح صفقة
# -----------------------------------------

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


# -----------------------------------------
# 4. تعديل وقف الخسارة
# -----------------------------------------

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


# -----------------------------------------
# 5. اغلاق صفقة -- ربح
# -----------------------------------------

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
        f"🎯 النسبة  : {r_achieved:.2f}R\n"
    )
    return _send(msg)


# -----------------------------------------
# 6. اغلاق صفقة -- خسارة
# -----------------------------------------

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


# -----------------------------------------
# 7. ايقاف النظام
# -----------------------------------------

def notify_system_stopped() -> bool:
    msg = (
        "&#9940; <b>SYSTEM STOPPED</b>\n"
        f"&#128336; {_now()}\n"
        "&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;\n"
        "Daily loss limit of 2 trades reached.\n"
        "System will resume tomorrow at market open.\n"
        "&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;\n"
        "&#x62a;&#x645; &#x627;&#x644;&#x648;&#x635;&#x648;&#x644; &#x644;&#x62d;&#x62f; &#x627;&#x644;&#x62e;&#x633;&#x627;&#x631;&#x62a;&#x64a;&#x646; &#x627;&#x644;&#x64a;&#x648;&#x645;&#x64a;&#x62a;&#x64a;&#x646;.\n"
        "&#x633;&#x64a;&#x639;&#x648;&#x62f; &#x627;&#x644;&#x646;&#x638;&#x627;&#x645; &#x63a;&#x62f;&#x627;&#x64b; &#x639;&#x646;&#x62f; &#x627;&#x641;&#x62a;&#x62a;&#x627;&#x62d; &#x627;&#x644;&#x633;&#x648;&#x642;."
    )
    return _send(msg)


# -----------------------------------------
# 8. التقرير اليومي -- عربي + انجليزي مع emoji
# -----------------------------------------
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

    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    pnl_emoji   = "📈" if total_pnl >= 0 else "📉"
    pnl_sign    = "+" if total_pnl >= 0 else ""
    r_emoji     = "✅" if total_r >= 0 else "❌"
    r_sign      = "+" if total_r >= 0 else ""
    wrate_emoji = "🟢" if win_rate >= 50 else "🔴"
    bal_emoji   = "💰"

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
            "\n"
            f"🚀 <b>Best Trade</b> : +${best_trade:.2f}\n"
            f"💥 <b>Worst Trade</b> : ${worst_trade:.2f}\n"
            f"📈 Avg Win : +${avg_win:.2f}\n"
            f"📉 Avg Loss : ${avg_loss:.2f}\n"
        )

    msg += f"\n{bal_emoji} <b>Balance</b> : ${balance:,.2f}\n"

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
            "\n"
            f"🚀 <b>أفضل صفقة</b> : +${best_trade:.2f}\n"
            f"💥 <b>أسوأ صفقة</b> : ${worst_trade:.2f}\n"
        )

    msg += f"\n{bal_emoji} <b>الرصيد</b> : ${balance:,.2f}"

    if open_trades:
        msg += "\n\n📂 <b>صفقات مفتوحة (تنتقل لغد)</b>\n"
        for t in open_trades:
            side_ar    = "شراء" if t["side"] == "long" else "مكشوف"
            side_emoji = "🟢" if t["side"] == "long" else "🔴"
            msg += f"  {side_emoji} {t['ticker']} | {side_ar} | دخول=${t['entry']:.2f} | R={t['r']:+.2f}\n"

    return _send(msg)
    
#def notify_daily_report(
 #   date: str,
 #   total_trades: int,
 #   losses: int,
 #   total_r: float,
 #   total_pnl: float,
 #   balance: float,
 #   long_trades: int = 0,
 #   short_trades: int = 0,
 #   best_trade: float = 0.0,
 #   worst_trade: float = 0.0,
 #   avg_win: float = 0.0,
 #   avg_loss: float = 0.0,
#) -> bool:
 #   win_rate    = (wins / total_trades * 100) if total_trades > 0 else 0
 #   pnl_emoji   = "&#128200;" if total_pnl >= 0 else "&#128201;"
 #   pnl_sign    = "+" if total_pnl >= 0 else ""
 #   r_emoji     = "&#9989;" if total_r >= 0 else "&#10060;"
 #   r_sign      = "+" if total_r >= 0 else ""
 #   wrate_emoji = "&#129001;" if win_rate >= 50 else "&#128997;"
 #   bal_emoji   = "&#128176;"

#    msg = (
#        f"&#128202; <b>Daily Report -- {date}</b>\n"
#        "&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;\n"

        # القسم الانجليزي
#        "&#127468;&#127463; <b>English</b>\n"
#        f"&#128290; Total Trades : {total_trades}  "
#        f"(&#129001; {wins} Win / &#128997; {losses} Loss)\n"
#        f"{wrate_emoji} Win Rate    : {win_rate:.1f}%\n"
#        f"&#129001; Long        : {long_trades}  |  "
#        f"&#128997; Short       : {short_trades}\n"
#        f"{r_emoji} Total R     : {r_sign}{total_r:.2f}R\n"
#        f"{pnl_emoji} Total PnL  : ${pnl_sign}{total_pnl:.2f}\n"
#    )

#    if total_trades > 0:
#        msg += (
#            f"&#127919; Best Trade  : +${best_trade:.2f}\n"
#            f"&#128308; Worst Trade : ${worst_trade:.2f}\n"
#            f"&#128200; Avg Win     : +${avg_win:.2f}\n"
#            f"&#128201; Avg Loss    : ${avg_loss:.2f}\n"
#        )

#    msg += (
#        f"{bal_emoji} Balance    : ${balance:,.2f}\n"
#        "&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;\n"

#        # القسم العربي
#        "&#127462;&#127466; <b>&#x627;&#x644;&#x639;&#x631;&#x628;&#x64a;&#x629;</b>\n"
#        f"&#128290; &#x625;&#x62c;&#x645;&#x627;&#x644;&#x64a; &#x627;&#x644;&#x635;&#x641;&#x642;&#x627;&#x62a; : {total_trades}  "
#        f"(&#129001; {wins} &#x631;&#x628;&#x62d; / &#128997; {losses} &#x62e;&#x633;&#x627;&#x631;&#x629;)\n"
#        f"{wrate_emoji} &#x646;&#x633;&#x628;&#x629; &#x627;&#x644;&#x641;&#x648;&#x632;  : {win_rate:.1f}%\n"
#        f"&#129001; &#x634;&#x631;&#x627;&#x621; : {long_trades}  |  "
#        f"&#128997; &#x645;&#x643;&#x634;&#x648;&#x641; : {short_trades}\n"
#        f"{r_emoji} &#x625;&#x62c;&#x645;&#x627;&#x644;&#x64a; R : {r_sign}{total_r:.2f}R\n"
#        f"{pnl_emoji} &#x625;&#x62c;&#x645;&#x627;&#x644;&#x64a; &#x627;&#x644;&#x631;&#x628;&#x62d; : ${pnl_sign}{total_pnl:.2f}\n"
#    )

#    if total_trades > 0:
#        msg += (
#            f"&#127919; &#x623;&#x641;&#x636;&#x644; &#x635;&#x641;&#x642;&#x629; : +${best_trade:.2f}\n"
#            f"&#128308; &#x623;&#x633;&#x648;&#x623; &#x635;&#x641;&#x642;&#x629;  : ${worst_trade:.2f}\n"
#        )

#    msg += f"{bal_emoji} &#x627;&#x644;&#x631;&#x635;&#x64a;&#x62f; : ${balance:,.2f}"

#    return _send(msg)
