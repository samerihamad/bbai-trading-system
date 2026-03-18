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
    ALPACA_DATA_URL,
)
from strategy_meanrev import MeanRevSignal, update_trailing_stop
from risk import calculate_position_size, calculate_r, dynamic_risk_pct

# ─────────────────────────────────────────
# Google Sheets — حفظ الصفقات المفتوحة
# ─────────────────────────────────────────
SHEET_ID           = "1C1kcOrXAbZ36_0lgC5awNgd1wqpIs3diZO-FXcGXJLo"
OPEN_TRADES_SHEET  = "Open Trades"
CLOSED_TRADES_SHEET = "Closed Trades"
CREDENTIALS_ENV   = "GOOGLE_CREDENTIALS_JSON"


def _get_sheets_client():
    try:
        import gspread, json as _j
        from google.oauth2.service_account import Credentials
        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds_json = os.getenv(CREDENTIALS_ENV)
        if creds_json:
            creds = Credentials.from_service_account_info(_j.loads(creds_json), scopes=scopes)
        else:
            creds = Credentials.from_service_account_file("bbai-489218-bca75c7e5d12.json", scopes=scopes)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"⚠️  Google Sheets غير متاح: {e}")
        return None


def _get_open_trades_ws():
    gc = _get_sheets_client()
    if not gc:
        return None
    try:
        ss = gc.open_by_key(SHEET_ID)
        try:
            return ss.worksheet(OPEN_TRADES_SHEET)
        except Exception:
            headers = [
                "ticker","strategy","side","order_id",
                "entry_price","stop_loss","target",
                "target_tp1","target_tp2","trail_stop","trail_step",
                "quantity","quantity_remaining","tp1_hit",
                "peak_price","risk_amount","opened_at"
            ]
            ws = ss.add_worksheet(title=OPEN_TRADES_SHEET, rows=100, cols=len(headers))
            ws.append_row(headers)
            return ws
    except Exception as e:
        print(f"⚠️  فشل جلب Open Trades worksheet: {e}")
        return None


def _save_open_trades(trades: list) -> None:
    ws = _get_open_trades_ws()
    if not ws:
        return
    try:
        ws.resize(rows=1)
        ws.resize(rows=100)
        for t in trades:
            opened_at = t.opened_at.isoformat() if hasattr(t.opened_at, "isoformat") else str(t.opened_at)
            ws.append_row([
                t.ticker, t.strategy, t.side, t.order_id,
                t.entry_price, t.stop_loss, t.target,
                t.target_tp1, t.target_tp2,
                t.trail_stop, t.trail_step,
                t.quantity, t.quantity_remaining,
                str(t.tp1_hit), t.peak_price, t.risk_amount, opened_at
            ], value_input_option="RAW")
        print(f"✅ حُفظت {len(trades)} صفقة مفتوحة في Google Sheets")
    except Exception as e:
        print(f"⚠️  فشل حفظ الصفقات المفتوحة: {e}")


def _delete_open_trades_sheets() -> None:
    ws = _get_open_trades_ws()
    if not ws:
        return
    try:
        ws.resize(rows=1)
        ws.resize(rows=100)
        print("✅ تم مسح Open Trades من Google Sheets")
    except Exception as e:
        print(f"⚠️  فشل مسح Open Trades: {e}")


# ─────────────────────────────────────────
# Closed Trades — Google Sheets
# ─────────────────────────────────────────

CLOSED_HEADERS = [
    "date", "ticker", "strategy", "side",
    "entry_price", "exit_price", "quantity",
    "stop_loss", "target", "risk_amount",
    "pnl", "r_achieved", "outcome",
    "exit_reason", "opened_at", "closed_at",
]

def _get_closed_trades_ws():
    gc = _get_sheets_client()
    if not gc:
        return None
    try:
        ss = gc.open_by_key(SHEET_ID)
        try:
            return ss.worksheet(CLOSED_TRADES_SHEET)
        except Exception:
            ws = ss.add_worksheet(title=CLOSED_TRADES_SHEET, rows=1000, cols=len(CLOSED_HEADERS))
            ws.append_row(CLOSED_HEADERS)
            return ws
    except Exception as e:
        print(f"⚠️  فشل جلب Closed Trades worksheet: {e}")
        return None


def save_closed_trade_sheets(record: dict) -> None:
    """يحفظ صفقة مكتملة في Google Sheets — يُستدعى من reporter.py."""
    ws = _get_closed_trades_ws()
    if not ws:
        return
    try:
        from datetime import date
        row = [
            date.today().isoformat(),
            record.get("ticker", ""),
            record.get("strategy", ""),
            record.get("side", ""),
            record.get("entry_price", 0),
            record.get("exit_price", 0),
            record.get("quantity", 0),
            record.get("stop_loss", 0),
            record.get("target", 0),
            record.get("risk_amount", 0),
            record.get("pnl", 0),
            record.get("r_achieved", 0),
            record.get("outcome", ""),
            record.get("exit_reason", ""),
            record.get("opened_at", ""),
            record.get("closed_at", ""),
        ]
        ws.append_row(row, value_input_option="RAW")
        print(f"✅ صفقة {record.get('ticker')} حُفظت في Closed Trades Sheets")
    except Exception as e:
        print(f"⚠️  فشل حفظ Closed Trade في Sheets: {e}")


def load_closed_trades_by_date_sheets(target_date: str) -> list[dict]:
    """يجلب صفقات يوم معين من Google Sheets."""
    ws = _get_closed_trades_ws()
    if not ws:
        return []
    try:
        rows = ws.get_all_records()
        return [r for r in rows if str(r.get("date", "")) == target_date]
    except Exception as e:
        print(f"⚠️  فشل جلب Closed Trades من Sheets: {e}")
        return []


def _load_open_trades_from_sheets() -> list:
    ws = _get_open_trades_ws()
    if not ws:
        return []
    try:
        rows = ws.get_all_records()
        if not rows:
            return []
        import pytz
        TZ     = pytz.timezone(os.getenv("TIMEZONE", "America/New_York"))
        trades = []
        for d in rows:
            try:
                opened_at = datetime.fromisoformat(str(d["opened_at"]))
                if opened_at.tzinfo is None:
                    opened_at = TZ.localize(opened_at)
            except Exception:
                opened_at = datetime.now(TZ)
            trade = OpenTrade(
                ticker=str(d["ticker"]), strategy=str(d.get("strategy","meanrev")),
                side=str(d["side"]), order_id=str(d.get("order_id","recovered")),
                entry_price=float(d["entry_price"]), stop_loss=float(d["stop_loss"]),
                target=float(d["target"]), target_tp1=float(d["target_tp1"]),
                target_tp2=float(d["target_tp2"]), trail_stop=float(d.get("trail_stop",0)),
                trail_step=float(d.get("trail_step",0)), quantity=int(d["quantity"]),
                quantity_remaining=int(d["quantity_remaining"]),
                tp1_hit=str(d.get("tp1_hit","False"))=="True",
                peak_price=float(d.get("peak_price", d["entry_price"])),
                risk_amount=float(d.get("risk_amount",0)),
            )
            trades.append(trade)
            print(f"  📂 استعادة من Sheets: {d['ticker']} [{d['side'].upper()}]"
                  f" entry=${float(d['entry_price']):.2f}"
                  f" | SL=${float(d['stop_loss']):.2f}"
                  f" | TP1=${float(d['target_tp1']):.2f}"
                  f" | TP2=${float(d['target_tp2']):.2f}")
        print(f"✅ تم استعادة {len(trades)} صفقة من Google Sheets")
        return trades
    except Exception as e:
        print(f"❌ خطأ في قراءة Open Trades: {e}")
        return []


def get_open_positions() -> list:
    """
    يجلب المراكز المفتوحة عند بدء التشغيل:
    1. أولاً من Google Sheets (بيانات حقيقية)
    2. إذا فارغ → من Alpaca API (بيانات تقريبية)
    """
    alpaca_symbols = set()
    try:
        r = requests.get(f"{ALPACA_BASE_URL}/v2/positions", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            for pos in r.json():
                alpaca_symbols.add(pos.get("symbol",""))
    except Exception:
        pass

    # أولاً: Google Sheets
    sheets_trades = _load_open_trades_from_sheets()
    if sheets_trades:
        valid   = [t for t in sheets_trades if t.ticker in alpaca_symbols]
        skipped = [t.ticker for t in sheets_trades if t.ticker not in alpaca_symbols]
        if skipped:
            print(f"  ⚠️  في Sheets لكن مغلقة في Alpaca: {skipped}")
        if valid:
            return valid

    # ثانياً: Alpaca fallback
    if not alpaca_symbols:
        print("ℹ️  لا توجد مراكز مفتوحة في Alpaca")
        return []

    print("ℹ️  لا يوجد بيانات في Sheets — استعادة من Alpaca (مستويات تقريبية)...")
    try:
        r      = requests.get(f"{ALPACA_BASE_URL}/v2/positions", headers=HEADERS, timeout=10)
        trades = []
        for pos in r.json():
            symbol = pos.get("symbol","")
            side   = "long" if pos.get("side","long") == "long" else "short"
            qty    = abs(int(float(pos.get("qty",1))))
            entry  = float(pos.get("avg_entry_price",0))
            if not symbol or entry <= 0:
                continue
            if side == "long":
                stop,tp1,tp2 = round(entry*.95,2), round(entry*1.02,2), round(entry*1.04,2)
            else:
                stop,tp1,tp2 = round(entry*1.05,2), round(entry*.98,2), round(entry*.96,2)
            tp1_qty = max(1, qty//2)
            trades.append(OpenTrade(
                ticker=symbol, strategy="meanrev", side=side,
                order_id="recovered", entry_price=entry,
                stop_loss=stop, target=tp2, target_tp1=tp1, target_tp2=tp2,
                trail_stop=0.0, trail_step=0.0, quantity=qty,
                quantity_remaining=qty-tp1_qty, tp1_hit=False,
                peak_price=entry, risk_amount=0.0,
            ))
            print(f"  ♻️  {symbol} [{side.upper()}] qty={qty} entry=${entry:.2f} ⚠️ تقريبي")
        if trades:
            print(f"✅ تم استعادة {len(trades)} مركز من Alpaca")
        return trades
    except Exception as e:
        print(f"❌ خطأ في جلب المراكز: {e}")
        return []


def sync_with_alpaca(open_trades: list) -> list:
    """
    يكتشف الصفقات المغلقة يدوياً في Alpaca ويحذفها من القائمة.

    حماية من الحذف الخاطئ:
    1. Grace period: أي صفقة فُتحت منذ أقل من 3 دقائق لا تُلمس
       (bracket order قد يظل pending قبل أن يُنفَّذ ويظهر في positions)
    2. Retry ×2 قبل الحكم بالإغلاق
    3. تحقق مزدوج: أي صفقة غائبة تُتحقق منها مباشرة قبل الحذف
    4. رفض قائمة فارغة [] إذا كان عندنا صفقات مفتوحة
    """
    if not open_trades:
        return open_trades

    import pytz
    from datetime import datetime, timezone, timedelta
    GRACE_SECONDS = 180  # 3 دقائق — وقت كافٍ لأي bracket order أن يُنفَّذ

    # ── جلب positions من Alpaca مع retry ×2
    alpaca_symbols = None
    for attempt in range(1, 3):
        try:
            r = requests.get(
                f"{ALPACA_BASE_URL}/v2/positions",
                headers=HEADERS, timeout=10,
            )
            if r.status_code != 200:
                print(f"  ⚠️  sync_with_alpaca: HTTP {r.status_code} (محاولة {attempt}/2) — تخطي")
                time.sleep(1)
                continue

            positions = r.json()

            # حماية: Alpaca أرجع [] لكن عندنا صفقات — شك في الرد
            if len(positions) == 0 and len(open_trades) > 0:
                print(f"  ⚠️  sync_with_alpaca: Alpaca أرجع [] لكن عندنا {len(open_trades)} صفقة — تجاهل (محاولة {attempt}/2)")
                time.sleep(1.5)
                continue

            alpaca_symbols = {pos.get("symbol", "") for pos in positions}
            break

        except Exception as e:
            print(f"  ⚠️  sync_with_alpaca محاولة {attempt}/2: {e}")
            time.sleep(1)

    if alpaca_symbols is None:
        print("  ⚠️  sync_with_alpaca: تعذّر الحصول على positions موثوق — لا تغيير")
        return open_trades

    # ── فحص كل صفقة
    now_utc = datetime.now(timezone.utc)
    removed = []
    for trade in open_trades[:]:
        if trade.ticker in alpaca_symbols:
            continue  # موجودة — لا شيء

        # ── Grace period: تجاهل الصفقات الجديدة جداً
        try:
            opened = trade.opened_at
            if opened is not None:
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=timezone.utc)
                age_seconds = (now_utc - opened).total_seconds()
                if age_seconds < GRACE_SECONDS:
                    print(f"  ⏳ {trade.ticker}: غائبة عن Alpaca لكن فُتحت منذ {age_seconds:.0f}s — grace period ({GRACE_SECONDS}s)")
                    continue
        except Exception:
            pass  # إذا فشل حساب الوقت — لا نحذف

        # ── تحقق مزدوج: هل هي مغلقة فعلاً؟
        try:
            r2 = requests.get(
                f"{ALPACA_BASE_URL}/v2/positions/{trade.ticker}",
                headers=HEADERS, timeout=8,
            )
            if r2.status_code == 200:
                print(f"  ✅ {trade.ticker}: موجودة في Alpaca (تحقق مزدوج) — لا حذف")
                continue
            elif r2.status_code == 404:
                open_trades.remove(trade)
                removed.append(trade.ticker)
                print(f"  ⚠️  {trade.ticker}: مغلقة في Alpaca (404 مؤكد) — حُذفت من المراقبة")
            else:
                print(f"  ⚠️  {trade.ticker}: HTTP {r2.status_code} في التحقق المزدوج — لا حذف")
        except Exception as e2:
            print(f"  ⚠️  {trade.ticker}: فشل التحقق المزدوج ({e2}) — لا حذف")

    if removed:
        print(f"🔄 Sync: حُذف {len(removed)} صفقة مؤكدة: {removed}")

    return open_trades


def get_current_price(ticker: str) -> float:
    """يجلب آخر سعر للسهم — snapshot API."""
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
                (data.get("latestQuote") or {}).get("ap") or
                (data.get("dailyBar")    or {}).get("c") or 0
            )
            if price > 0:
                return round(price, 2)
    except Exception as e:
        print(f"❌ خطأ في جلب سعر {ticker}: {e}")
    return 0.0

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

    # ── تحقق من عدم وجود مركز مفتوح مسبقاً (يمنع: bracket orders must be entry orders)
    try:
        pos_r = requests.get(
            f"{ALPACA_BASE_URL}/v2/positions/{ticker}",
            headers=HEADERS, timeout=8,
        )
        if pos_r.status_code == 200:
            existing_qty = abs(int(float(pos_r.json().get("qty", 0))))
            if existing_qty > 0:
                print(f"⛔ {ticker}: مركز مفتوح مسبقاً ({existing_qty} سهم) — تخطي")
                try:
                    from notifier import _send
                    side_ar = "شراء 🟢" if side == "long" else "بيع 🔴"
                    _send(
                        f"⚠️ <b>تخطي {ticker}</b>\n"
                        f"يوجد مركز مفتوح مسبقاً ({existing_qty} سهم).\n"
                        f"النظام لا يفتح صفقة مكررة على نفس السهم."
                    )
                except Exception:
                    pass
                return None
    except Exception:
        pass  # فشل التحقق — نكمل

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
            error_msg = data.get("message", "خطأ غير معروف")
            print(f"❌ فشل أمر {ticker}: {error_msg}")
            try:
                from notifier import _send
                side_ar = "شراء 🟢" if side == "long" else "بيع 🔴"
                if "pattern day trading" in error_msg.lower():
                    _send(
                        f"⛔ <b>تعذّر فتح الصفقة — قيود PDT</b>\n"
                        f"📌 {ticker} | {side_ar} | entry=${entry_price:.2f}\n"
                        f"💡 الحل: ارفع رصيد الحساب فوق $25,000."
                    )
                elif "bracket" in error_msg.lower() and "entry" in error_msg.lower():
                    _send(
                        f"⚠️ <b>فشل فتح {ticker} — مركز موجود في Alpaca</b>\n"
                        f"📌 {ticker} | {side_ar} | entry=${entry_price:.2f}\n"
                        f"❗ {error_msg}\n"
                        f"⚠️ تحقق من Positions وأغلق أي مركز بلا stop يدوياً."
                    )
                else:
                    _send(
                        f"❌ <b>فشل فتح صفقة {ticker}</b>\n"
                        f"📌 {side_ar} | entry=${entry_price:.2f}\n"
                        f"السبب: {error_msg}"
                    )
            except Exception:
                pass
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


def update_stop_in_alpaca(ticker: str, new_stop: float, side: str, max_retries: int = 2) -> bool:
    """
    يحدّث وقف الخسارة الحقيقي في Alpaca:
    1. يلغي كل الأوامر المعلّقة للسهم (stops + take_profits + bracket legs)
    2. يجلب الكمية الفعلية من position
    3. يضع stop جديد بالكمية الصحيحة

    ملاحظة: إلغاء كل الأوامر آمن لأن:
    - TP2 يُراقب يدوياً بالكود في monitor_trade()
    - Stop الجديد يُوضع بالكمية الفعلية (يحل مشكلة bracket legs بكمية خاطئة)
    """
    for attempt in range(1, max_retries + 1):
        try:
            # ── 1. جلب كل الأوامر المفتوحة لهذا السهم
            r = requests.get(
                f"{ALPACA_BASE_URL}/v2/orders",
                headers=HEADERS,
                params={"status": "open", "symbols": ticker, "limit": 50},
                timeout=10,
            )
            if r.status_code != 200:
                raise RuntimeError(f"GET orders failed: {r.status_code}")

            # ── 2. إلغاء كل الأوامر المعلّقة (نظيف — بدلاً من محاولة حذف legs فردية)
            cancelled_count = 0
            for o in r.json():
                order_id = o.get("id", "")
                if not order_id:
                    continue
                del_r = requests.delete(
                    f"{ALPACA_BASE_URL}/v2/orders/{order_id}",
                    headers=HEADERS, timeout=10,
                )
                if del_r.status_code in (200, 204):
                    cancelled_count += 1
                elif del_r.status_code == 422:
                    # 422 = الأمر غير قابل للإلغاء (ربما نُفّذ أو أُلغي مسبقاً) — تخطي
                    pass
                else:
                    print(f"  ⚠️  فشل إلغاء أمر {order_id[:8]}: HTTP {del_r.status_code}")

            if cancelled_count > 0:
                print(f"  🔄 {ticker}: أُلغي {cancelled_count} أمر معلّق")
                time.sleep(0.5)  # انتظر حتى تُعالَج الإلغاءات

            # ── 3. جلب الكمية الفعلية من Alpaca
            r2 = requests.get(
                f"{ALPACA_BASE_URL}/v2/positions/{ticker}",
                headers=HEADERS, timeout=10,
            )
            if r2.status_code != 200:
                raise RuntimeError(f"GET position failed: {r2.status_code}")
            actual_qty = abs(int(float(r2.json().get("qty", 0))))
            if actual_qty == 0:
                print(f"  ℹ️  {ticker}: لا يوجد مركز مفتوح — تخطي تحديث الـ stop")
                return False

            # ── 4. وضع stop جديد بالكمية الصحيحة
            stop_side = "sell" if side == "long" else "buy"
            order = {
                "symbol":        ticker,
                "qty":           str(actual_qty),
                "side":          stop_side,
                "type":          "stop",
                "stop_price":    str(round(new_stop, 2)),
                "time_in_force": "day",
            }
            resp = requests.post(
                f"{ALPACA_BASE_URL}/v2/orders",
                headers=HEADERS, json=order, timeout=15,
            )
            if resp.status_code in (200, 201):
                print(f"  ✅ Stop حقيقي في Alpaca: {ticker} @ ${new_stop:.2f} (qty={actual_qty})")
                return True
            else:
                raise RuntimeError(f"POST order failed: {resp.json().get('message', resp.status_code)}")

        except Exception as e:
            print(f"  ⚠️  update_stop_in_alpaca محاولة {attempt}/{max_retries} — {ticker}: {e}")
            if attempt < max_retries:
                time.sleep(1.5)

    # ── فشل نهائي بعد كل المحاولات — إشعار Telegram
    print(f"  ❌ فشل تحديث Stop في Alpaca نهائياً: {ticker} @ ${new_stop:.2f}")
    try:
        from notifier import _send
        _send(
            f"⚠️ <b>فشل تحديث Stop — {ticker}</b>\n"
            f"🕐 تعذّر تحريك الوقف إلى ${new_stop:.2f} بعد {max_retries} محاولات.\n"
            f"راجع الصفقة يدوياً في Alpaca."
        )
    except Exception:
        pass
    return False


def _check_stop_not_at_breakeven(ticker: str, breakeven: float, side: str) -> bool:
    """
    يتحقق إذا كان الـ stop الحالي في Alpaca لم يصل للـ breakeven بعد.
    يُرجع True إذا يحتاج تحديث، False إذا هو بالفعل عند breakeven أو أفضل.
    """
    try:
        r = requests.get(
            f"{ALPACA_BASE_URL}/v2/orders",
            headers=HEADERS,
            params={"status": "open", "symbols": ticker, "limit": 50},
            timeout=10,
        )
        if r.status_code != 200:
            return False  # لا نعرف — لا تتدخل
        for o in r.json():
            # فحص standalone stop orders
            if o.get("type") == "stop":
                current_stop = float(o.get("stop_price") or 0)
                if side == "long" and current_stop < breakeven - 0.01:
                    return True   # الـ stop لا يزال أقل من breakeven
                if side == "short" and current_stop > breakeven + 0.01:
                    return True
            # فحص legs داخل bracket
            for leg in (o.get("legs") or []):
                if leg.get("type") == "stop":
                    current_stop = float(leg.get("stop_price") or 0)
                    if side == "long" and current_stop < breakeven - 0.01:
                        return True
                    if side == "short" and current_stop > breakeven + 0.01:
                        return True
        return False  # كل الـ stops عند breakeven أو أفضل
    except Exception as e:
        print(f"  ⚠️  _check_stop_not_at_breakeven {ticker}: {e}")
        return False


def sync_trade_state_with_alpaca(trade) -> bool:
    """
    أهم دالة — تُشغَّل قبل كل دورة مراقبة:
    - تكتشف TP1 تلقائياً إذا أغلق Alpaca النصف بنفسه
    - تحدّث quantity_remaining
    - تحرّك SL إلى breakeven في Alpaca فعلياً

    Grace period: إذا الصفقة فُتحت منذ أقل من 3 دقائق ولم تظهر بعد
    في positions (bracket pending) — لا نحذفها.
    """
    from datetime import datetime, timezone

    GRACE_SECONDS = 180  # 3 دقائق

    try:
        r = requests.get(f"{ALPACA_BASE_URL}/v2/positions/{trade.ticker}", headers=HEADERS, timeout=10)
        if r.status_code == 404:
            # ── تحقق من grace period قبل الحكم بالإغلاق
            try:
                opened = trade.opened_at
                if opened is not None:
                    if opened.tzinfo is None:
                        opened = opened.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - opened).total_seconds()
                    if age < GRACE_SECONDS:
                        print(f"  ⏳ {trade.ticker}: 404 لكن فُتحت منذ {age:.0f}s — grace period، لا حذف")
                        return True  # treat as still open
            except Exception:
                pass
            return False  # الصفقة مغلقة بالكامل
        if r.status_code != 200:
            return True   # خطأ مؤقت — لا تغيّر شيء

        actual_qty = abs(int(float(r.json().get("qty", 0))))

        if actual_qty == 0:
            return False  # مغلقة

        # ── اكتشاف TP1 تلقائي (Alpaca أغلق النصف)
        expected_full = trade.quantity
        half_qty      = max(1, expected_full // 2)

        if actual_qty <= half_qty and not trade.tp1_hit:
            price = get_current_price(trade.ticker)
            closed_qty = expected_full - actual_qty
            trade.tp1_hit            = True
            trade.quantity_remaining = actual_qty
            old_stop                 = trade.stop_loss
            trade.stop_loss          = trade.entry_price  # breakeven

            # تحريك الـ Stop الحقيقي في Alpaca
            update_stop_in_alpaca(trade.ticker, trade.entry_price, trade.side)

            profit = round(
                (price - trade.entry_price) * closed_qty if trade.side == "long"
                else (trade.entry_price - price) * closed_qty, 2
            )
            r_ach = round(
                abs(price - trade.entry_price) / max(abs(trade.entry_price - old_stop), 0.01), 2
            )
            print(f"  ✅ TP1 AUTO: {trade.ticker} | qty أغلق={closed_qty} | profit=${profit:.2f} | SL→breakeven")

            # ── تسجيل TP1 في Closed Trades (كان مفقوداً — يمنع ضياع السجل)
            try:
                from reporter import record_trade as _record_trade
                _record_trade(
                    ticker=trade.ticker, strategy=trade.strategy,
                    entry_price=trade.entry_price, exit_price=price,
                    quantity=closed_qty, stop_loss=old_stop,
                    target=trade.target_tp1, risk_amount=trade.risk_amount / 2,
                    exit_reason="tp1_auto", opened_at=trade.opened_at,
                    side=trade.side,
                )
            except Exception as e:
                print(f"  ⚠️  فشل تسجيل TP1 AUTO: {e}")

            try:
                from notifier import notify_tp1_hit, notify_stop_updated
                notify_tp1_hit(
                    ticker=trade.ticker, side=trade.side,
                    entry_price=trade.entry_price, tp1_price=price,
                    qty_tp1=closed_qty, profit_tp1=profit,
                    r_achieved=r_ach, qty_remaining=actual_qty,
                    tp2_price=trade.target_tp2,
                )
                notify_stop_updated(
                    ticker=trade.ticker, old_stop=old_stop,
                    new_stop=trade.entry_price, current_price=price,
                )
            except Exception as e:
                print(f"  Telegram error: {e}")
            _save_open_trades([trade])
            return True

        # ── تحديث الكمية إذا تغيرت بدون TP1 (مثلاً إغلاق جزئي يدوي)
        if trade.tp1_hit and actual_qty != trade.quantity_remaining:
            trade.quantity_remaining = actual_qty
            _save_open_trades([trade])

        # ── تحقق: إذا tp1_hit لكن الـ stop في Alpaca لم يتحرك بعد للـ breakeven
        # يحدث لما main.py اكتشف TP1 لكن update_stop_in_alpaca فشلت أو السيرفر restart
        if trade.tp1_hit and trade.stop_loss == trade.entry_price:
            stop_needs_update = _check_stop_not_at_breakeven(trade.ticker, trade.entry_price, trade.side)
            if stop_needs_update:
                print(f"  🔄 sync: {trade.ticker} tp1_hit لكن stop لم يتحرك في Alpaca — إعادة التحديث")
                update_stop_in_alpaca(trade.ticker, trade.entry_price, trade.side)

        return True
    except Exception as e:
        print(f"  ⚠️  sync error {trade.ticker}: {e}")
        return True  # لا توقف المراقبة بسبب خطأ مؤقت


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
    signal:   MeanRevSignal,
    balance:  float,
    strategy: str = "meanrev",
) -> Optional[OpenTrade]:
    """
    يفتح صفقة LONG أو SHORT مع خروج مزدوج TP1/TP2.
    strategy: 'meanrev' أو 'momentum'

    يفتح أمرين منفصلين في Alpaca:
    - أمر 1 (tp1_qty): bracket مع TP1 و SL
    - أمر 2 (tp2_qty): bracket مع TP2 و SL
    هذا يضمن حماية الـ 50% الثانية حتى لو توقف السيرفر.
    """
    account      = get_account()
    balance      = account.get("balance", balance) if account else balance
    buying_power = account.get("buying_power", 0) if account else 0

    # ── Dynamic Risk: نسبة المخاطرة بناءً على قوة الإشارة (Score)
    signal_score = getattr(signal, "score", 0.0)
    risk_pct     = dynamic_risk_pct(signal_score)

    sizing = calculate_position_size(
        balance=balance,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        use_leverage=True,
        buying_power=buying_power,
        risk_override=risk_pct,
    )

    total_qty = sizing["quantity"]
    tp1_qty   = max(1, total_qty // 2)
    tp2_qty   = total_qty - tp1_qty

    side_label     = "🟢 LONG" if signal.side == "long" else "🔴 SHORT"
    quality        = getattr(signal, "signal_quality", "standard").upper()
    strategy_label = "زخم" if strategy == "momentum" else "ارتداد"

    print(f"\n📤 فتح صفقة {strategy_label} — {signal.ticker} {side_label} [{quality}]")
    print(f"   الكمية الكلية : {total_qty}")
    print(f"   TP1 ({tp1_qty} سهم) : ${signal.target_tp1:.2f} (1R) — يدوي")
    print(f"   TP2 ({tp2_qty} سهم) : ${signal.target_tp2:.2f} (3R) — Alpaca")
    print(f"   وقف الخسارة   : ${signal.stop_loss:.2f}")
    print(f"   المخاطرة       : ${sizing['risk_amount']} ({risk_pct*100:.0f}% | Score={signal_score:.0f}) | رافعة ×{sizing['leverage']}")

    # ── براكيت واحد للكامل (TP2 + SL) — TP1 يُكتشف تلقائياً
    order_id = place_bracket_order(
        ticker=signal.ticker,
        quantity=total_qty,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        target=signal.target_tp2,
        side=signal.side,
    )
    if not order_id:
        return None

    print(f"   ✅ Bracket (qty={total_qty} | TP2=${signal.target_tp2:.2f} | SL=${signal.stop_loss:.2f}) — ID: {order_id[:8]}...")

    return OpenTrade(
        ticker=signal.ticker,
        strategy=strategy,
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
        quantity_remaining=total_qty,  # كامل — يتحدث عند اكتشاف TP1
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
        tp1_qty  = trade.quantity // 2  # نصف الكمية الأصلية دائماً
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

    # ── Trailing Stop (يُفعَّل بعد TP1 للـ LONG والـ SHORT)
    if trade.tp1_hit and trade.trail_step > 0:
        if trade.side == "long":
            new_stop = update_trailing_stop(current_price, trade.stop_loss, trade.trail_step)
            if new_stop > trade.stop_loss:
                return {"status": "trail_updated", "price": current_price,
                        "r": r_current, "new_stop": new_stop, "exit_qty": 0}
        elif trade.side == "short":
            # SHORT: الوقف فوق السعر — يتحرك للأسفل مع انخفاض السعر (أضيق = أفضل)
            new_stop = round(current_price + trade.trail_step, 4)
            if new_stop < trade.stop_loss:
                return {"status": "trail_updated", "price": current_price,
                        "r": r_current, "new_stop": new_stop, "exit_qty": 0}

    return {"status": "open", "price": current_price,
            "r": r_current, "new_stop": trade.stop_loss, "exit_qty": 0}


def close_all_positions() -> bool:
    """
    يُغلق كل المراكز المفتوحة بثلاث خطوات:
    1. إلغاء كل الأوامر المعلّقة (bracket child orders: stop + take_profit)
    2. إغلاق كل الـ positions بـ market order
    3. تحقق نهائي للتأكيد
    """
    print("🔄 close_all_positions: بدء تسلسل الإغلاق...")

    # ── الخطوة 1: إلغاء كل الأوامر المعلّقة
    try:
        r1 = requests.delete(
            f"{ALPACA_BASE_URL}/v2/orders",
            headers=HEADERS, timeout=15,
        )
        if r1.status_code in (200, 204, 207):
            cancelled = r1.json() if r1.text and r1.text != "null" else []
            print(f"  ✅ الخطوة 1: أُلغي {len(cancelled) if isinstance(cancelled, list) else '?'} أمر معلّق")
        else:
            print(f"  ⚠️  الخطوة 1: HTTP {r1.status_code} — {r1.text[:100]}")
    except Exception as e:
        print(f"  ⚠️  الخطوة 1 فشلت: {e}")

    time.sleep(1.5)  # انتظر حتى تُعالَج إلغاءات الأوامر

    # ── الخطوة 2: إغلاق كل الـ positions
    try:
        r2 = requests.delete(
            f"{ALPACA_BASE_URL}/v2/positions",
            headers=HEADERS, timeout=15,
        )
        if r2.status_code in (200, 204, 207):
            print(f"  ✅ الخطوة 2: طلب إغلاق positions أُرسل (HTTP {r2.status_code})")
        else:
            print(f"  ⚠️  الخطوة 2: HTTP {r2.status_code} — {r2.text[:100]}")
    except Exception as e:
        print(f"  ⚠️  الخطوة 2 فشلت: {e}")

    time.sleep(2.0)  # انتظر تنفيذ الـ market orders

    # ── الخطوة 3: تحقق نهائي
    try:
        r3 = requests.get(
            f"{ALPACA_BASE_URL}/v2/positions",
            headers=HEADERS, timeout=10,
        )
        if r3.status_code == 200:
            remaining = r3.json()
            if len(remaining) == 0:
                print("  ✅ الخطوة 3: تأكيد — لا توجد positions مفتوحة")
                return True
            else:
                # محاولة إغلاق ما تبقى بشكل فردي
                print(f"  ⚠️  الخطوة 3: لا يزال {len(remaining)} مركز — محاولة إغلاق فردي...")
                all_closed = True
                for pos in remaining:
                    sym = pos.get("symbol", "")
                    try:
                        r_single = requests.delete(
                            f"{ALPACA_BASE_URL}/v2/positions/{sym}",
                            headers=HEADERS, timeout=10,
                        )
                        if r_single.status_code in (200, 204):
                            print(f"    ✅ {sym}: أُغلق")
                        else:
                            print(f"    ❌ {sym}: HTTP {r_single.status_code}")
                            all_closed = False
                    except Exception as e_s:
                        print(f"    ❌ {sym}: {e_s}")
                        all_closed = False
                return all_closed
        else:
            print(f"  ⚠️  الخطوة 3: تعذّر التحقق — HTTP {r3.status_code}")
            return True  # افترض نجاح الخطوتين السابقتين
    except Exception as e:
        print(f"  ⚠️  الخطوة 3 فشلت: {e}")
        return True
