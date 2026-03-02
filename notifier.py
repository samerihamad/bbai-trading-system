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
        print("Telegram غير معد -- تحقق من .env")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       message,
        "parse_mode": "HTML",
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"خطا في ارسال Telegram: {e}")
        return False


def _now() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M")


# -----------------------------------------
# 1. تنبيه ما قبل الافتتاح
# -----------------------------------------

def notify_pre_market(stocks: list) -> bool:
    stocks_str = " | ".join(stocks) if stocks else "لم يتم الاختيار"
    message = (
        "<b>تنبيه ما قبل الافتتاح</b>\n"
        f"الوقت: {_now()}\n"
        "---\n"
        f"اسهم اليوم ({len(stocks)}):\n"
        f"{stocks_str}\n"
        "---\n"
        "السوق يفتح بعد 30 دقيقة"
    )
    return _send(message)


# -----------------------------------------
# 2. لا توجد فرصة
# -----------------------------------------

def notify_no_opportunity() -> bool:
    message = (
        "<b>لا توجد فرصة حاليا</b>\n"
        f"الوقت: {_now()}\n"
        "---\n"
        "النظام يعمل ويراقب السوق"
    )
    return _send(message)


# -----------------------------------------
# 3. فتح صفقة
# -----------------------------------------

def notify_trade_open(
    ticker:      str,
    strategy:    str,
    side:        str,
    price:       float,
    quantity:    int,
    stop_loss:   float,
    target:      float,
    risk_amount: float,
) -> bool:
    side_emoji = "BUY" if side == "BUY" else "SELL SHORT"
    r_ratio = round((target - price) / abs(price - stop_loss), 2) if price != stop_loss else 0

    message = (
        f"<b>صفقة جديدة -- {ticker}</b>\n"
        f"الوقت: {_now()}\n"
        "---\n"
        f"الاستراتيجية : {strategy}\n"
        f"الاتجاه      : {side_emoji}\n"
        f"سعر الدخول   : ${price:.2f}\n"
        f"الكمية       : {quantity} سهم\n"
        f"وقف الخسارة  : ${stop_loss:.2f}\n"
        f"الهدف        : ${target:.2f}\n"
        f"نسبة R       : {r_ratio}R\n"
        f"المخاطرة     : ${risk_amount:.2f}"
    )
    return _send(message)


# -----------------------------------------
# 4. تعديل وقف الخسارة
# -----------------------------------------

def notify_stop_updated(
    ticker:        str,
    old_stop:      float,
    new_stop:      float,
    current_price: float,
) -> bool:
    message = (
        f"<b>تعديل وقف الخسارة -- {ticker}</b>\n"
        f"الوقت: {_now()}\n"
        "---\n"
        f"السعر الحالي : ${current_price:.2f}\n"
        f"الوقف القديم : ${old_stop:.2f}\n"
        f"الوقف الجديد : ${new_stop:.2f}"
    )
    return _send(message)


# -----------------------------------------
# 5. اغلاق صفقة -- ربح
# -----------------------------------------

def notify_trade_win(
    ticker:      str,
    entry_price: float,
    exit_price:  float,
    quantity:    int,
    profit:      float,
    r_achieved:  float,
) -> bool:
    message = (
        f"<b>ربح -- {ticker}</b>\n"
        f"الوقت: {_now()}\n"
        "---\n"
        f"الدخول  : ${entry_price:.2f}\n"
        f"الخروج  : ${exit_price:.2f}\n"
        f"الكمية  : {quantity} سهم\n"
        f"الربح   : +${profit:.2f}\n"
        f"تحقق    : {r_achieved:.1f}R"
    )
    return _send(message)


# -----------------------------------------
# 6. اغلاق صفقة -- خسارة
# -----------------------------------------

def notify_trade_loss(
    ticker:       str,
    entry_price:  float,
    exit_price:   float,
    quantity:     int,
    loss:         float,
    daily_losses: int,
) -> bool:
    warning = ""
    if daily_losses >= 2:
        warning = "\n<b>تم الوصول لحد الخسائر اليومي -- النظام متوقف</b>"

    message = (
        f"<b>خسارة -- {ticker}</b>\n"
        f"الوقت: {_now()}\n"
        "---\n"
        f"الدخول      : ${entry_price:.2f}\n"
        f"الخروج      : ${exit_price:.2f}\n"
        f"الكمية      : {quantity} سهم\n"
        f"الخسارة     : -${loss:.2f}\n"
        f"خسائر اليوم : {daily_losses}/2"
        f"{warning}"
    )
    return _send(message)


# -----------------------------------------
# 7. ايقاف النظام
# -----------------------------------------

def notify_system_stopped() -> bool:
    message = (
        "<b>النظام متوقف</b>\n"
        f"الوقت: {_now()}\n"
        "---\n"
        "تم الوصول لحد الخسارتين اليوميتين\n"
        "سيعود النظام للعمل غدا عند افتتاح السوق"
    )
    return _send(message)


# -----------------------------------------
# 8. التقرير اليومي
# -----------------------------------------

def notify_daily_report(
    date:         str,
    total_trades: int,
    wins:         int,
    losses:       int,
    total_r:      float,
    total_pnl:    float,
    balance:      float,
) -> bool:
    win_rate  = (wins / total_trades * 100) if total_trades > 0 else 0
    pnl_sign  = "+" if total_pnl >= 0 else ""
    r_sign    = "+" if total_r >= 0 else ""

    message = (
        f"<b>التقرير اليومي -- {date}</b>\n"
        "---\n"
        f"الصفقات    : {total_trades}\n"
        f"ربح        : {wins}\n"
        f"خسارة      : {losses}\n"
        f"نسبة الفوز : {win_rate:.1f}%\n"
        "---\n"
        f"اجمالي R   : {r_sign}{total_r:.2f}R\n"
        f"اجمالي PnL : ${pnl_sign}{total_pnl:.2f}\n"
        "---\n"
        f"الرصيد الحالي: ${balance:,.2f}"
    )
    return _send(message)
