# =============================================================
# notifier.py — كل رسائل Telegram في مكان واحد
# =============================================================

import requests
from datetime import datetime
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TIMEZONE

import pytz

TZ = pytz.timezone(TIMEZONE)


# ─────────────────────────────────────────
# الدالة الأساسية للإرسال
# ─────────────────────────────────────────

def _send(message: str) -> bool:
    """
    ترسل رسالة نصية إلى Telegram.
    تُرجع True إذا نجح الإرسال، False إذا فشل.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Telegram غير مُعدّ — تحقق من .env")
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
        print(f"❌ خطأ في إرسال Telegram: {e}")
        return False


def _now() -> str:
    """يُرجع الوقت الحالي بتوقيت نيويورك."""
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M")


# ─────────────────────────────────────────
# 1. تنبيه ما قبل افتتاح السوق
# ─────────────────────────────────────────

def notify_pre_market(stocks: list[str]) -> bool:
    """يُرسل قبل افتتاح السوق بـ 30 دقيقة."""
    stocks_str = " | ".join(stocks) if stocks else "لم يتم الاختيار بعد"
    message = (
        f"🌅 <b>تنبيه ما قبل الافتتاح</b>\n"
        f"🕐 {_now()}\n"
        f"──────────────────\n"
        f"📋 أسهم اليوم ({len(stocks)}):\n"
        f"{stocks_str}\n"
        f"──────────────────\n"
        f"⏳ السوق يفتح بعد 30 دقيقة"
    )
    return _send(message)


# ─────────────────────────────────────────
# 2. لا توجد فرصة
# ─────────────────────────────────────────

def notify_no_opportunity() -> bool:
    """يُرسل كل ساعة عند عدم توفر أي فرصة."""
    message = (
        f"🔍 <b>لا توجد فرصة حالياً</b>\n"
        f"🕐 {_now()}\n"
        f"──────────────────\n"
        f"النظام يعمل ويراقب السوق"
    )
    return _send(message)


# ─────────────────────────────────────────
# 3. فتح صفقة
# ─────────────────────────────────────────

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
    """يُرسل إشعار فتح صفقة بكل التفاصيل."""
    side_emoji = "🟢" if side == "BUY" else "🔴"
    r_ratio = round((target - price) / abs(price - stop_loss), 2) if price != stop_loss else 0

    message = (
        f"{side_emoji} <b>صفقة جديدة — {ticker}</b>\n"
        f"🕐 {_now()}\n"
        f"──────────────────\n"
        f"📌 الاستراتيجية : {strategy}\n"
        f"📊 الاتجاه       : {side}\n"
        f"💲 سعر الدخول   : ${price:.2f}\n"
        f"�
