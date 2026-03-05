# =============================================================
# executor.py — تنفيذ أوامر الشراء والبيع عبر Alpaca
# يتعامل مع: LONG و SHORT، فتح الصفقات، TP1/TP2، الوقف المتحرك
# =============================================================

import requests
import time
import os
from datetime import datetime
from dataclasses import dataclass
from typing import Optional

from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_BASE_URL,
)
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

# ─────────────────────────────────────────
# ملف حفظ الصفقات المفتوحة (يبقى بين الـ Deploys)
# ─────────────────────────────────────────
import json as _json

_DISK_PATH        = os.getenv("RENDER_DISK_PATH", "logs")
_OPEN_TRADES_FILE = os.path.join(_DISK_PATH, "open_trades.json")


def _save_open_trades(trades: list) -> None:
    """يحفظ الصفقات المفتوحة في ملف JSON دائم."""
    try:
        os.makedirs(_DISK_PATH, exist_ok=True)
        data = []
        for t in trades:
            data.append({
                "ticker":             t.ticker,
                "strategy":           t.strategy,
                "side":               t.side,
                "order_id":           t.order_id,
                "entry_price":        t.entry_price,
                "stop_loss":          t.stop_loss,
                "target":             t.target,
                "target_tp1":         t.target_tp1,
                "target_tp2":         t.target_tp2,
                "trail_stop":         t.trail_stop,
                "trail_step":         t.trail_step,
                "quantity":           t.quantity,
                "quantity_remaining": t.quantity_remaining,
                "tp1_hit":            t.tp1_hit,
                "peak_price":         t.peak_price,
                "risk_amount":        t.risk_amount,
                "opened_at":          t.opened_at.isoformat() if hasattr(t.opened_at, "isoformat") else str(t.opened_at),
            })
        with open(_OPEN_TRADES_FILE, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️  فشل حفظ open_trades.json: {e}")


def _load_open_trades_from_file() -> list:
    """يقرأ الصفقات المفتوحة من الملف المحفوظ."""
    if not os.path.exists(_OPEN_TRADES_FILE):
        return []
    try:
        with open(_OPEN_TRADES_FILE, "r", encoding="utf-8") as f:
            data = _json.load(f)

        import pytz
        from datetime import datetime
        TZ = pytz.timezone(os.getenv("TIMEZONE", "America/New_York"))

        trades = []
        for d in data:
            # تحويل opened_at من string إلى datetime
            try:
                opened_at = datetime.fromisoformat(d["opened_at"])
                if opened_at.tzinfo is None:
                    opened_at = TZ.localize(opened_at)
            except Exception:
                opened_at = datetime.now(TZ)

            trade = OpenTrade(
                ticker=d["ticker"],
                strategy=d.get("strategy", "meanrev"),
                side=d["side"],
                order_id=d.get("order_id", "recovered"),
                entry_price=d["entry_price"],
                stop_loss=d["stop_loss"],
                target=d["target"],
                target_tp1=d["target_tp1"],
                target_tp2=d["target_tp2"],
                trail_stop=d.get("trail_stop", 0.0),
                trail_step=d.get("trail_step", 0.0),
                quantity=d["quantity"],
                quantity_remaining=d["quantity_remaining"],
                tp1_hit=d.get("tp1_hit", False),
                peak_price=d.get("peak_price", d["entry_price"]),
                risk_amount=d.get("risk_amount", 0.0),
            )
            trades.append(trade)
            print(f"  📂 استعادة من ملف: {d['ticker']} [{d['side'].upper()}]"
                  f" entry=${d['entry_price']:.2f}"
                  f" | SL=${d['stop_loss']:.2f}"
                  f" | TP1=${d['target_tp1']:.2f}"
                  f" | TP2=${d['target_tp2']:.2f}")

        print(f"✅ تم استعادة {len(trades)} صفقة من open_trades.json")
        return trades

    except Exception as e:
        print(f"❌ خطأ في قراءة open_trades.json: {e}")
        return []


def _delete_open_trades_file() -> None:
    """يحذف الملف عند إغلاق كل الصفقات."""
    try:
        if os.path.exists(_OPEN_TRADES_FILE):
            os.remove(_OPEN_TRADES_FILE)
    except Exception as e:
        print(f"⚠️  فشل حذف open_trades.json: {e}")


def get_open_positions() -> list:
    """
    يجلب المراكز المفتوحة عند بدء التشغيل:
    1. أولاً من open_trades.json (بيانات حقيقية كاملة)
    2. إذا لم يوجد → من Alpaca API (بيانات تقريبية)
    3. يتحقق أن الصفقات في الملف لا تزال مفتوحة في Alpaca
    """
    # ── أولاً: جرب الملف المحفوظ
    file_trades = _load_open_trades_from_file()

    # ── جلب المراكز الفعلية من Alpaca للتحقق
    try:
        response = requests.get(
            f"{ALPACA_BASE_URL}/v2/positions",
            headers=HEADERS,
            timeout=10,
        )
        alpaca_symbols = set()
        if response.status_code == 200:
            for pos in response.json():
                alpaca_symbols.add(pos.get("symbol", ""))
    except Exception:
        alpaca_symbols = set()

    if file_trades:
        # تصفية: احتفظ فقط بالصفقات المفتوحة فعلاً في Alpaca
        valid = [t for t in file_trades if t.ticker in alpaca_symbols]
        skipped = [t.ticker for t in file_trades if t.ticker not in alpaca_symbols]
        if skipped:
            print(f"  ⚠️  صفقات في الملف لكن مغلقة في Alpaca: {skipped}")
        if valid:
            return valid

    # ── ثانياً: fallback من Alpaca API مباشرة
    print("ℹ️  لا يوجد ملف محفوظ — استعادة من Alpaca API...")
    if not alpaca_symbols:
        print("ℹ️  لا توجد مراكز مفتوحة في Alpaca")
        return []

    try:
        response = requests.get(
            f"{ALPACA_BASE_URL}/v2/positions",
            headers=HEADERS,
            timeout=10,
        )
        trades = []
        for pos in response.json():
            symbol   = pos.get("symbol", "")
            side_raw = pos.get("side", "long")
            side     = "long" if side_raw == "long" else "short"
            qty      = abs(int(float(pos.get("qty", 1))))
            entry    = float(pos.get("avg_entry_price", 0))
            if not symbol or entry <= 0:
                continue

            # مستويات تقريبية فقط عند غياب الملف
            if side == "long":
                stop = round(entry * 0.95, 2)
                tp1  = round(entry * 1.02, 2)
                tp2  = round(entry * 1.04, 2)
            else:
                stop = round(entry * 1.05, 2)
                tp1  = round(entry * 0.98, 2)
                tp2  = round(entry * 0.96, 2)

            tp1_qty = max(1, qty // 2)
            trade = OpenTrade(
                ticker=symbol, strategy="meanrev", side=side,
                order_id="recovered", entry_price=entry,
                stop_loss=stop, target=tp2, target_tp1=tp1, target_tp2=tp2,
                trail_stop=0.0, trail_step=0.0,
                quantity=qty, quantity_remaining=qty - tp1_qty,
                tp1_hit=False, peak_price=entry, risk_amount=0.0,
            )
            trades.append(trade)
            print(f"  ♻️  استعادة من Alpaca: {symbol} [{side.upper()}] qty={qty} entry=${entry:.2f} ⚠️ مستويات تقريبية")

        if trades:
            print(f"✅ تم استعادة {len(trades)} مركز من Alpaca")
        return trades

    except Exception as e:
        print(f"❌ خطأ في جلب المراكز: {e}")
        return []


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
        tp1_qty  = trade.quantity - trade.quantity_remaining
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
