# =============================================================
# executor.py — تنفيذ أوامر الشراء والبيع عبر Alpaca
# يتعامل مع: LONG و SHORT، فتح الصفقات، TP1/TP2، الوقف المتحرك
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
    ALPACA_DATA_URL,
)
from strategy_meanrev import MeanRevSignal, update_trailing_stop
from risk import calculate_position_size, calculate_r
from notifier import notify_short_not_allowed

# ── alpaca-py: تُستخدم فقط لأوامر SHORT وجلب الأسعار
try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import LimitOrderRequest, TakeProfitRequest, StopLossRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
    _ALPACA_PY_AVAILABLE = True
except ImportError:
    _ALPACA_PY_AVAILABLE = False
    print("⚠️  alpaca-py غير مثبتة — أوامر SHORT معطّلة")

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
    ticker:              str
    strategy:            str       # 'meanrev'
    side:                str       # 'long' أو 'short'
    order_id:            str
    entry_price:         float
    stop_loss:           float
    target:              float     # TP2 — الهدف النهائي
    target_tp1:          float     # TP1 = 1R (50% من الكمية)
    target_tp2:          float     # TP2 = 3R (50% من الكمية)
    trail_stop:          float
    trail_step:          float
    quantity:            int       # الكمية الكلية
    quantity_remaining:  int       # الكمية المتبقية بعد TP1
    tp1_hit:             bool      # هل تحقق TP1 بالفعل؟
    peak_price:          float     # أعلى سعر بعد الدخول (LONG) أو أدنى سعر (SHORT)
    risk_amount:         float
    opened_at:           datetime  = None

    def __post_init__(self):
        if self.opened_at is None:
            self.opened_at = datetime.utcnow()
        # تهيئة peak_price بسعر الدخول
        if self.peak_price == 0.0:
            self.peak_price = self.entry_price


# ─────────────────────────────────────────
# 1. جلب معلومات الحساب
# ─────────────────────────────────────────

def _get_alpaca_client() -> Optional["TradingClient"]:
    if not _ALPACA_PY_AVAILABLE:
        return None
    try:
        return TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
    except Exception as e:
        print(f"❌ خطأ في إنشاء Alpaca client: {e}")
        return None


def _place_short_order_alpaca_py(
    ticker: str, quantity: int, entry_price: float,
    stop_loss: float, target: float,
) -> Optional[str]:
    client = _get_alpaca_client()
    if not client:
        return None
    limit_price = round(entry_price * 0.999, 2)
    try:
        order_data = LimitOrderRequest(
            symbol=ticker, qty=quantity,
            side=OrderSide.SELL, time_in_force=TimeInForce.DAY,
            limit_price=limit_price, order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(target, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop_loss, 2)),
        )
        order    = client.submit_order(order_data=order_data)
        order_id = str(order.id)
        print(f"✅ أمر SHORT {ticker} تم عبر alpaca-py — ID: {order_id[:8]}...")
        return order_id
    except Exception as e:
        err_msg = str(e)
        print(f"❌ فشل SHORT {ticker}: {err_msg}")
        if "not allowed to short" in err_msg.lower() or "forbidden" in err_msg.lower():
            try:
                notify_short_not_allowed(
                    ticker=ticker,
                    reason="الحساب لا يدعم Short Selling — تأكد أن الرصيد فوق $2,000",
                )
            except Exception:
                pass
        return None


def get_open_positions() -> list:
    """
    يجلب المراكز المفتوحة من Alpaca ويحوّلها إلى OpenTrade.
    يُستدعى عند بدء التشغيل لتجنب فتح صفقات مكررة.
    """
    try:
        response = requests.get(
            f"{ALPACA_BASE_URL}/v2/positions",
            headers=HEADERS,
            timeout=10,
        )
        if response.status_code != 200:
            print(f"⚠️  فشل جلب المراكز: {response.status_code}")
            return []

        positions = response.json()
        trades    = []

        for pos in positions:
            symbol   = pos.get("symbol", "")
            side_raw = pos.get("side", "long")
            side     = "long" if side_raw == "long" else "short"
            qty      = abs(int(float(pos.get("qty", 1))))
            entry    = float(pos.get("avg_entry_price", 0))

            if not symbol or entry <= 0:
                continue

            # مستويات افتراضية
            if side == "long":
                stop = round(entry * 0.95, 2)
                tp1  = round(entry * 1.02, 2)
                tp2  = round(entry * 1.04, 2)
            else:
                stop = round(entry * 1.05, 2)
                tp1  = round(entry * 0.98, 2)
                tp2  = round(entry * 0.96, 2)

            # حساب الكميات بشكل صحيح
            tp1_qty            = max(1, qty // 2)
            quantity_remaining = qty - tp1_qty

            trade = OpenTrade(
                ticker=symbol, strategy="meanrev", side=side,
                order_id="recovered", entry_price=entry,
                stop_loss=stop, target=tp2,
                target_tp1=tp1, target_tp2=tp2,
                trail_stop=0.0, trail_step=0.0,
                quantity=qty, quantity_remaining=quantity_remaining,
                tp1_hit=False, peak_price=entry, risk_amount=0.0,
            )
            trades.append(trade)
            print(f"  ♻️  استعادة: {symbol} [{side.upper()}] qty={qty} (TP1={tp1_qty} | TP2={quantity_remaining}) entry=${entry:.2f}")

        if trades:
            print(f"✅ تم استعادة {len(trades)} مركز مفتوح من Alpaca")
        else:
            print("ℹ️  لا توجد مراكز مفتوحة في Alpaca")

        return trades

    except Exception as e:
        print(f"❌ خطأ في جلب المراكز المفتوحة: {e}")
        return []


def get_current_price(ticker: str) -> float:
    """يجلب آخر سعر للسهم — alpaca-py أولاً ثم snapshot كبديل."""
    if _ALPACA_PY_AVAILABLE:
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestQuoteRequest
            data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
            quote       = data_client.get_stock_latest_quote(
                            StockLatestQuoteRequest(symbol_or_symbols=ticker))
            q   = quote[ticker]
            bid = float(q.bid_price or 0)
            ask = float(q.ask_price or 0)
            if bid and ask:
                return round((bid + ask) / 2, 2)
        except Exception:
            pass

    try:
        response = requests.get(
            f"{ALPACA_DATA_URL}/v2/stocks/{ticker}/snapshot",
            headers=HEADERS,
            params={"feed": "iex"},
            timeout=10,
        )
        if response.status_code == 200:
            data  = response.json()
            price = float(
                (data.get("latestTrade") or {}).get("p") or
                (data.get("dailyBar")    or {}).get("c") or 0
            )
            if price > 0:
                return round(price, 2)
    except Exception as e:
        print(f"❌ خطأ في جلب سعر {ticker}: {e}")

    return 0.0


def get_account() -> dict:
    """يجلب معلومات حساب Alpaca."""
    try:
        response = requests.get(
            f"{ALPACA_BASE_URL}/v2/account",
            headers=HEADERS,
            timeout=10,
        )

        data = response.json()

        return {
            "balance":        float(data.get("equity", 0)),
            "buying_power":   float(data.get("buying_power", 0)),
            "cash":           float(data.get("cash", 0)),
            "shorting_enabled": data.get("shorting_enabled", False),
            "status":         data.get("status", "unknown"),
        }

    except Exception as e:
        print(f"❌ خطأ في جلب معلومات الحساب: {e}")
        return {}


def get_current_price(ticker: str) -> float:
    """يجلب آخر سعر للسهم (متوسط bid/ask)."""
    try:
        response = requests.get(
            f"{ALPACA_BASE_URL}/v2/stocks/{ticker}/quotes/latest",
            headers=HEADERS,
            timeout=10,
        )
        quote = response.json().get("quote", {})
        bid   = float(quote.get("bp", 0))
        ask   = float(quote.get("ap", 0))
        return round((bid + ask) / 2, 2) if bid and ask else 0.0
    except Exception as e:
        print(f"❌ خطأ في جلب سعر {ticker}: {e}")
        return 0.0


# ─────────────────────────────────────────
# 2. التحقق من السوق
# ─────────────────────────────────────────

def is_market_open() -> bool:
    """يتحقق إذا كان السوق مفتوحاً الآن عبر Alpaca Clock API."""
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
# 3. تنفيذ الأوامر
# ─────────────────────────────────────────

def place_bracket_order(
    ticker:      str,
    quantity:    int,
    entry_price: float,
    stop_loss:   float,
    target:      float,
    side:        str = "long",
) -> Optional[str]:
    """
    يُنفّذ Bracket Order — أمر دخول مع وقف خسارة وهدف.
    يدعم LONG (buy) وSHORT (sell).
    يُرجع order_id إذا نجح، None إذا فشل.
    """
    order_side = "buy" if side == "long" else "sell"

    # LONG:  limit_price أعلى قليلاً من السعر (نضمن التنفيذ)
    # SHORT: limit_price أقل قليلاً من السعر
    if side == "long":
        limit_price = round(entry_price * 1.001, 2)
    else:
        limit_price = round(entry_price * 0.999, 2)

    order = {
        "symbol":        ticker,
        "qty":           str(quantity),
        "side":          order_side,
        "type":          "limit",
        "limit_price":   str(limit_price),
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
            label    = "شراء" if side == "long" else "بيع على المكشوف"
            print(f"✅ أمر {label} {ticker} تم — ID: {order_id[:8]}...")
            return order_id
        else:
            print(f"❌ فشل أمر {ticker}: {data.get('message', 'خطأ غير معروف')}")
            return None

    except Exception as e:
        print(f"❌ خطأ في تنفيذ أمر {ticker}: {e}")
        return None


def place_market_sell(ticker: str, quantity: int, side: str = "long") -> Optional[str]:
    """
    يُغلق الصفقة بسعر السوق فوراً.
    LONG  → sell  (بيع الأسهم المحتفظ بها)
    SHORT → buy   (إعادة شراء الأسهم المقترضة)
    """
    close_side = "sell" if side == "long" else "buy"

    order = {
        "symbol":        ticker,
        "qty":           str(quantity),
        "side":          close_side,
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
            label    = "إغلاق LONG" if side == "long" else "تغطية SHORT"
            print(f"✅ أمر {label} {ticker} تم — ID: {order_id[:8]}...")
            return order_id
        else:
            print(f"❌ فشل إغلاق {ticker}: {data.get('message', 'خطأ غير معروف')}")
            return None

    except Exception as e:
        print(f"❌ خطأ في إغلاق {ticker}: {e}")
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

def open_meanrev_trade(
    signal:  MeanRevSignal,
    balance: float,
) -> Optional[OpenTrade]:
    """
    يفتح صفقة LONG أو SHORT مع خروج مزدوج TP1/TP2.
    - TP1 عند 1R: يخرج 50% من الكمية + ينقل الوقف إلى التعادل
    - TP2 عند 3R: يخرج الـ 50% المتبقية
    - Trailing Stop يُفعَّل بعد TP1
    - رافعة مالية × 2
    """
    sizing = calculate_position_size(
        balance=balance,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        use_leverage=True,
    )

    total_qty = sizing["quantity"]
    tp1_qty   = max(1, total_qty // 2)
    tp2_qty   = total_qty - tp1_qty

    side_label = "🟢 LONG" if signal.side == "long" else "🔴 SHORT"
    quality    = getattr(signal, "signal_quality", "standard").upper()

    print(f"\n📤 فتح صفقة ارتداد — {signal.ticker} {side_label} [{quality}]")
    print(f"   الكمية الكلية : {total_qty}")
    print(f"   TP1 ({tp1_qty} سهم) : ${signal.target_tp1:.2f} (1R)")
    print(f"   TP2 ({tp2_qty} سهم) : ${signal.target_tp2:.2f} (3R)")
    print(f"   وقف الخسارة   : ${signal.stop_loss:.2f}")
    print(f"   المخاطرة       : ${sizing['risk_amount']} | رافعة ×{sizing['leverage']}")

    # Bracket Order للـ TP1 فقط (نصف الكمية)
    order_id = place_bracket_order(
        ticker=signal.ticker,
        quantity=tp1_qty,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        target=signal.target_tp1,
        side=signal.side,
    )

    if not order_id:
        return None

    return OpenTrade(
        ticker=signal.ticker,
        strategy="meanrev",
        side=signal.side,
        order_id=order_id,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        target=signal.target_tp2,
        target_tp1=signal.target_tp1,
        target_tp2=signal.target_tp2,
        trail_stop=0.0,
        trail_step=signal.trail_step,
        quantity=total_qty,
        quantity_remaining=tp2_qty,
        tp1_hit=False,
        peak_price=signal.entry_price,
        risk_amount=sizing["risk_amount"],
    )


# ─────────────────────────────────────────
# 5. مراقبة الصفقات المفتوحة
# ─────────────────────────────────────────

def monitor_trade(trade: OpenTrade) -> dict:
    """
    يراقب الصفقة المفتوحة ويتحقق من:
    - هل ضُرب وقف الخسارة؟
    - هل تحقق TP1 (خروج جزئي 50%)?
    - هل تحقق TP2 (خروج نهائي 50%)?
    - هل يجب تحريك الوقف المتحرك؟

    يُرجع dict:
    - status   : 'open' | 'stopped' | 'tp1_hit' | 'target' | 'trail_updated'
    - price    : السعر الحالي
    - r        : نسبة R الحالية
    - new_stop : الوقف الجديد عند التحديث
    - exit_qty : الكمية المراد إغلاقها
    """
    current_price = get_current_price(trade.ticker)
    if current_price <= 0:
        return {"status": "open", "price": 0, "r": 0,
                "new_stop": trade.stop_loss, "exit_qty": 0}

    r_current = calculate_r(trade.entry_price, current_price, trade.stop_loss, trade.side)

    # تحديث peak_price
    if trade.side == "long":
        trade.peak_price = max(trade.peak_price, current_price)
    else:
        trade.peak_price = min(trade.peak_price, current_price)

    # ── ضُرب وقف الخسارة
    stop_hit = (
        (trade.side == "long"  and current_price <= trade.stop_loss) or
        (trade.side == "short" and current_price >= trade.stop_loss)
    )
    if stop_hit:
        exit_qty = trade.quantity_remaining if trade.tp1_hit else trade.quantity
        return {"status": "stopped", "price": current_price,
                "r": r_current, "new_stop": trade.stop_loss, "exit_qty": exit_qty}

    # ── TP1 (الهدف الأول = 1R) — خروج جزئي 50%
    tp1_hit = (
        (trade.side == "long"  and not trade.tp1_hit and current_price >= trade.target_tp1) or
        (trade.side == "short" and not trade.tp1_hit and current_price <= trade.target_tp1)
    )
    if tp1_hit:
        # tp1_qty = نصف الكمية الكلية دائماً
        # للمراكز الطبيعية:   quantity=100, quantity_remaining=50 → tp1_qty=50 ✅
        # للمراكز المستعادة:  quantity=103, quantity_remaining=103 → tp1_qty=51 ✅
        tp1_qty = trade.quantity - trade.quantity_remaining
        if tp1_qty <= 0:
            tp1_qty = max(1, trade.quantity // 2)
            # تحديث quantity_remaining للكمية المتبقية بعد TP1
            trade.quantity_remaining = trade.quantity - tp1_qty

        new_stop = trade.entry_price  # نقل الوقف إلى نقطة التعادل
        return {"status": "tp1_hit", "price": current_price,
                "r": r_current, "new_stop": new_stop, "exit_qty": tp1_qty}

    # ── TP2 (الهدف النهائي = 3R) — خروج كامل للكمية المتبقية
    tp2_hit = (
        (trade.side == "long"  and current_price >= trade.target_tp2) or
        (trade.side == "short" and current_price <= trade.target_tp2)
    )
    if tp2_hit:
        exit_qty = trade.quantity_remaining if trade.tp1_hit else trade.quantity
        return {"status": "target", "price": current_price,
                "r": r_current, "new_stop": trade.stop_loss, "exit_qty": exit_qty}

    # ── Trailing Stop (يُفعَّل بعد TP1 فقط للـ LONG)
    if trade.side == "long" and trade.tp1_hit and trade.trail_step > 0:
        new_stop = update_trailing_stop(current_price, trade.stop_loss, trade.trail_step)
        if new_stop > trade.stop_loss:
            return {"status": "trail_updated", "price": current_price,
                    "r": r_current, "new_stop": new_stop, "exit_qty": 0}

    return {"status": "open", "price": current_price,
            "r": r_current, "new_stop": trade.stop_loss, "exit_qty": 0}


def close_all_positions() -> bool:
    """يُغلق كل المراكز المفتوحة دفعة واحدة — يُستخدم عند نهاية الجلسة."""
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
