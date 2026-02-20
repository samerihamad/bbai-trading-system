# =============================================================
# executor.py — تنفيذ أوامر الشراء والبيع عبر Alpaca
# يتعامل مع: فتح الصفقات، وقف الخسارة، الهدف، الوقف المتحرك
# =============================================================

import requests
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Optional

from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_BASE_URL,
)
from strategy_conservative import ConservativeSignal
from strategy_meanrev import MeanRevSignal, update_trailing_stop
from risk import calculate_position_size, calculate_r

HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    "Content-Type":        "application/json",
}


# ─────────────────────────────────────────
# نموذج الصفقة المفتوحة
# ─────────────────────────────────────────

@dataclass
class OpenTrade:
    ticker:        str
    strategy:      str          # 'conservative' أو 'meanrev'
    order_id:      str          # معرف أمر Alpaca
    entry_price:   float
    stop_loss:     float
    target:        float
    trail_stop:    float        # للاستراتيجية المحافظة
    trail_step:    float        # للاستراتيجية الثانية
    quantity:      int
    risk_amount:   float
    opened_at:     datetime = None

    def __post_init__(self):
        if self.opened_at is None:
            self.opened_at = datetime.utcnow()


# ─────────────────────────────────────────
# 1. جلب معلومات الحساب
# ─────────────────────────────────────────

def get_account() -> dict:
    """
    يجلب معلومات حساب Alpaca.
    يُرجع: الرصيد، القوة الشرائية، وضع الحساب.
    """
    try:
        response = requests.get(
            f"{ALPACA_BASE_URL}/v2/account",
            headers=HEADERS,
            timeout=10,
        )
        data = response.json()
        return {
            "balance":          float(data.get("equity", 0)),
            "buying_power":     float(data.get("buying_power", 0)),
            "cash":             float(data.get("cash", 0)),
            "status":           data.get("status", "unknown"),
        }
    except Exception as e:
        print(f"❌ خطأ في جلب معلومات الحساب: {e}")
        return {}


def get_current_price(ticker: str) -> float:
    """يجلب آخر سعر للسهم."""
    try:
        response = requests.get(
            f"{ALPACA_BASE_URL}/v2/stocks/{ticker}/quotes/latest",
            headers=HEADERS,
            timeout=10,
        )
        data  = response.json()
        quote = data.get("quote", {})
        # متوسط bid و ask كسعر حالي
        bid = float(quote.get("bp", 0))
        ask = float(quote.get("ap", 0))
        return round((bid + ask) / 2, 2) if bid and ask else 0.0
    except Exception as e:
        print(f"❌ خطأ في جلب سعر {ticker}: {e}")
        return 0.0


# ─────────────────────────────────────────
# 2. التحقق من السوق
# ─────────────────────────────────────────

def is_market_open() -> bool:
    """يتحقق إذا كان السوق مفتوحاً الآن."""
    try:
        response = requests.get(
            f"{ALPACA_BASE_URL}/v2/clock",
            headers=HEADERS,
            timeout=10,
        )
        return response.json().get("is_open", False)
    except Exception as e:
        print(f"❌ خطأ في التحقق من السوق: {e}")
        return False


def get_next_market_open() -> str:
    """يُرجع وقت افتتاح السوق القادم."""
    try:
        response = requests.get(
            f"{ALPACA_BASE_URL}/v2/clock",
            headers=HEADERS,
            timeout=10,
        )
        return response.json().get("next_open", "غير متاح")
    except Exception:
        return "غير متاح"


# ─────────────────────────────────────────
# 3. تنفيذ أوامر الشراء
# ─────────────────────────────────────────

def place_bracket_order(
    ticker:     str,
    quantity:   int,
    entry_price: float,
    stop_loss:  float,
    target:     float,
) -> Optional[str]:
    """
    يُنفّذ Bracket Order — أمر شراء مع وقف خسارة وهدف في نفس الوقت.
    Bracket Order = أمر رئيسي + أمر وقف + أمر هدف (OCO)
    يُرجع order_id إذا نجح، None إذا فشل.
    """
    order = {
        "symbol":        ticker,
        "qty":           str(quantity),
        "side":          "buy",
        "type":          "limit",
        "limit_price":   str(round(entry_price * 1.001, 2)),  # هامش 0.1% للتنفيذ
        "time_in_force": "day",
        "order_class":   "bracket",
        "stop_loss": {
            "stop_price": str(round(stop_loss, 2)),
        },
        "take_profit": {
            "limit_price": str(round(target, 2)),
        },
    }

    try:
        response = requests.post(
            f"{ALPACA_BASE_URL}/v2/orders",
            headers=HEADERS,
            json=order,
            timeout=15,
        )
        data = response.json()

        if response.status_code in (200, 201):
            order_id = data.get("id", "")
            print(f"✅ أمر شراء {ticker} تم — ID: {order_id[:8]}...")
            return order_id
        else:
            print(f"❌ فشل أمر {ticker}: {data.get('message', 'خطأ غير معروف')}")
            return None

    except Exception as e:
        print(f"❌ خطأ في تنفيذ أمر {ticker}: {e}")
        return None


def place_market_sell(ticker: str, quantity: int) -> Optional[str]:
    """
    يُغلق الصفقة بسعر السوق فوراً.
    يُستخدم عند ضرب الوقف المتحرك أو نهاية الجلسة.
    """
    order = {
        "symbol":        ticker,
        "qty":           str(quantity),
        "side":          "sell",
        "type":          "market",
        "time_in_force": "day",
    }

    try:
        response = requests.post(
            f"{ALPACA_BASE_URL}/v2/orders",
            headers=HEADERS,
            json=order,
            timeout=15,
        )
        data = response.json()

        if response.status_code in (200, 201):
            order_id = data.get("id", "")
            print(f"✅ أمر بيع {ticker} تم — ID: {order_id[:8]}...")
            return order_id
        else:
            print(f"❌ فشل بيع {ticker}: {data.get('message', 'خطأ غير معروف')}")
            return None

    except Exception as e:
        print(f"❌ خطأ في بيع {ticker}: {e}")
        return None


def cancel_order(order_id: str) -> bool:
    """يلغي أمراً معلقاً."""
    try:
        response = requests.delete(
            f"{ALPACA_BASE_URL}/v2/orders/{order_id}",
            headers=HEADERS,
            timeout=10,
        )
        return response.status_code in (200, 204)
    except Exception as e:
        print(f"❌ خطأ في إلغاء الأمر: {e}")
        return False


# ─────────────────────────────────────────
# 4. فتح الصفقات
# ─────────────────────────────────────────

def open_conservative_trade(
    signal:  ConservativeSignal,
    balance: float,
) -> Optional[OpenTrade]:
    """
    يفتح صفقة للاستراتيجية المحافظة.
    - Bracket Order مع وقف ثابت وهدف R2
    - بدون رافعة مالية
    """
    # حساب حجم الصفقة
    sizing = calculate_position_size(
        balance=balance,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        use_leverage=False,
    )

    print(f"\n📤 فتح صفقة محافظة — {signal.ticker}")
    print(f"   الكمية: {sizing['quantity']} | المخاطرة: ${sizing['risk_amount']}")

    order_id = place_bracket_order(
        ticker=signal.ticker,
        quantity=sizing["quantity"],
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        target=signal.target,
    )

    if not order_id:
        return None

    return OpenTrade(
        ticker=signal.ticker,
        strategy="conservative",
        order_id=order_id,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        target=signal.target,
        trail_stop=signal.trail_stop,
        trail_step=0.0,
        quantity=sizing["quantity"],
        risk_amount=sizing["risk_amount"],
    )


def open_meanrev_trade(
    signal:  MeanRevSignal,
    balance: float,
) -> Optional[OpenTrade]:
    """
    يفتح صفقة لاستراتيجية الارتداد.
    - Bracket Order مع وقف ضيق وهدف VWAP
    - مع رافعة مالية × 2
    """
    sizing = calculate_position_size(
        balance=balance,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        use_leverage=True,
    )

    print(f"\n📤 فتح صفقة ارتداد — {signal.ticker}")
    print(f"   الكمية: {sizing['quantity']} | المخاطرة: ${sizing['risk_amount']} | رافعة ×{sizing['leverage']}")

    order_id = place_bracket_order(
        ticker=signal.ticker,
        quantity=sizing["quantity"],
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        target=signal.target,
    )

    if not order_id:
        return None

    return OpenTrade(
        ticker=signal.ticker,
        strategy="meanrev",
        order_id=order_id,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        target=signal.target,
        trail_stop=0.0,
        trail_step=signal.trail_step,
        quantity=signal.entry_price,
        risk_amount=sizing["risk_amount"],
    )


# ─────────────────────────────────────────
# 5. مراقبة الصفقات المفتوحة
# ─────────────────────────────────────────

def monitor_trade(trade: OpenTrade) -> dict:
    """
    يراقب الصفقة المفتوحة ويتحقق من:
    - هل ضُرب وقف الخسارة؟
    - هل تحقق الهدف؟
    - هل يجب تحريك الوقف المتحرك؟

    يُرجع dict يحتوي:
    - status : 'open' | 'stopped' | 'target' | 'trail_updated'
    - price  : السعر الحالي
    - r      : نسبة R الحالية
    - new_stop: الوقف الجديد (عند التحديث)
    """
    current_price = get_current_price(trade.ticker)
    if current_price <= 0:
        return {"status": "open", "price": 0, "r": 0, "new_stop": trade.stop_loss}

    r_current = calculate_r(trade.entry_price, current_price, trade.stop_loss)

    # ── ضُرب وقف الخسارة
    if current_price <= trade.stop_loss:
        return {
            "status":   "stopped",
            "price":    current_price,
            "r":        r_current,
            "new_stop": trade.stop_loss,
        }

    # ── تحقق الهدف
    if current_price >= trade.target:
        return {
            "status":   "target",
            "price":    current_price,
            "r":        r_current,
            "new_stop": trade.stop_loss,
        }

    # ── الاستراتيجية المحافظة: Trailing Stop عند R1
    if trade.strategy == "conservative" and r_current >= 1.0:
        if current_price > trade.trail_stop:
            return {
                "status":   "trail_updated",
                "price":    current_price,
                "r":        r_current,
                "new_stop": trade.trail_stop,
            }

    # ── استراتيجية الارتداد: Trailing Stop متحرك دائماً
    if trade.strategy == "meanrev" and trade.trail_step > 0:
        new_stop = update_trailing_stop(current_price, trade.stop_loss, trade.trail_step)
        if new_stop > trade.stop_loss:
            return {
                "status":   "trail_updated",
                "price":    current_price,
                "r":        r_current,
                "new_stop": new_stop,
            }

    return {
        "status":   "open",
        "price":    current_price,
        "r":        r_current,
        "new_stop": trade.stop_loss,
    }


def close_all_positions() -> bool:
    """
    يُغلق كل المراكز المفتوحة دفعة واحدة.
    يُستخدم عند نهاية الجلسة أو إيقاف النظام.
    """
    try:
        response = requests.delete(
            f"{ALPACA_BASE_URL}/v2/positions",
            headers=HEADERS,
            timeout=15,
        )
        success = response.status_code in (200, 204, 207)
        if success:
            print("✅ تم إغلاق كل المراكز المفتوحة")
        return success
    except Exception as e:
        print(f"❌ خطأ في إغلاق المراكز: {e}")
        return False
