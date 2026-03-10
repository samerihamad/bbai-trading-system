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
from risk import calculate_position_size, calculate_r

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
    يكتشف الصفقات المغلقة في Alpaca (تلقائياً عبر Bracket أو يدوياً)
    ويرسل إشعار Telegram مع PnL ويسجّلها في Sheets.
    يتحقق أيضاً من الكميات ويُحدّثها إذا اختلفت.
    """
    if not open_trades:
        return open_trades
    try:
        r = requests.get(f"{ALPACA_BASE_URL}/v2/positions", headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return open_trades

        # بناء خريطة: ticker → position data
        alpaca_positions = {pos.get("symbol", ""): pos for pos in r.json()}
        alpaca_symbols   = set(alpaca_positions.keys())
        removed = []

        for trade in open_trades[:]:
            if trade.ticker not in alpaca_symbols:
                # ── الصفقة مغلقة في Alpaca
                open_trades.remove(trade)
                removed.append(trade.ticker)

                # محاولة جلب آخر سعر تنفيذ من Alpaca Orders
                exit_price  = trade.entry_price
                exit_reason = "closed_alpaca"
                try:
                    ord_r = requests.get(
                        f"{ALPACA_BASE_URL}/v2/orders",
                        headers=HEADERS,
                        params={"symbols": trade.ticker, "status": "closed", "limit": 5, "direction": "desc"},
                        timeout=10,
                    )
                    if ord_r.status_code == 200:
                        for o in ord_r.json():
                            if o.get("status") == "filled" and o.get("filled_avg_price"):
                                exit_price  = float(o["filled_avg_price"])
                                otype = o.get("order_class", "")
                                if "bracket" in otype or o.get("type") in ("stop", "stop_limit"):
                                    exit_reason = "stop_loss_alpaca"
                                elif o.get("type") == "limit":
                                    exit_reason = "tp_alpaca"
                                break
                except Exception:
                    pass

                # حساب PnL
                qty = getattr(trade, "quantity_remaining", trade.quantity)
                if trade.side == "long":
                    pnl = round((exit_price - trade.entry_price) * qty, 2)
                else:
                    pnl = round((trade.entry_price - exit_price) * qty, 2)

                risk  = abs(trade.entry_price - trade.stop_loss)
                r_val = round(pnl / (risk * qty), 2) if risk > 0 and qty > 0 else 0.0

                print(f"  🔔 {trade.ticker} أُغلقت | exit=${exit_price:.2f} | PnL=${pnl:+.2f} | {r_val:+.2f}R")

                # تسجيل في Sheets
                try:
                    from reporter import record_trade
                    record_trade(
                        ticker=trade.ticker,
                        strategy=getattr(trade, "strategy", "meanrev"),
                        entry_price=trade.entry_price,
                        exit_price=exit_price,
                        quantity=qty,
                        stop_loss=trade.stop_loss,
                        target=trade.target_tp2,
                        risk_amount=getattr(trade, "risk_amount", 0),
                        exit_reason=exit_reason,
                        opened_at=getattr(trade, "opened_at", None) or __import__("datetime").datetime.now(),
                        side=trade.side,
                    )
                except Exception as e:
                    print(f"  ⚠️  فشل تسجيل {trade.ticker} في Sheets: {e}")

                # إشعار Telegram
                try:
                    from notifier import notify_trade_win, notify_trade_loss
                    duration = ""
                    if hasattr(trade, "opened_at") and trade.opened_at:
                        import datetime as _dt
                        import pytz as _pytz
                        TZ_ = _pytz.timezone("America/New_York")
                        now_ = _dt.datetime.now(_pytz.utc).astimezone(TZ_)
                        oa   = trade.opened_at if hasattr(trade.opened_at, "tzinfo") else TZ_.localize(trade.opened_at)
                        mins = int((now_ - oa).total_seconds() / 60)
                        duration = f"{mins}d" if mins >= 1440 else f"{mins}m"
                    if pnl >= 0:
                        notify_trade_win(
                            ticker=trade.ticker, side=trade.side,
                            entry=trade.entry_price, exit_p=exit_price,
                            qty=qty, pnl=pnl, r=r_val,
                            reason=exit_reason, duration=duration,
                        )
                    else:
                        notify_trade_loss(
                            ticker=trade.ticker, side=trade.side,
                            entry=trade.entry_price, exit_p=exit_price,
                            qty=qty, pnl=pnl, r=r_val,
                            reason=exit_reason, duration=duration,
                        )
                except Exception as e:
                    print(f"  ⚠️  فشل إشعار Telegram لـ {trade.ticker}: {e}")

            else:
                # ── الصفقة لا تزال مفتوحة — تحقق من الكمية
                pos        = alpaca_positions[trade.ticker]
                alpaca_qty = abs(int(float(pos.get("qty", trade.quantity))))
                system_qty = trade.quantity

                if alpaca_qty != system_qty and not trade.tp1_hit:
                    if alpaca_qty < system_qty:
                        # TP1 نُفِّذ جزئياً — الكمية المتبقية هي ما في Alpaca
                        diff = system_qty - alpaca_qty
                        print(f"  🔄 {trade.ticker}: Alpaca={alpaca_qty} < النظام={system_qty} (diff={diff}) → TP1 جزئي")
                        trade.quantity           = system_qty   # نُبقي الكمية الأصلية
                        trade.quantity_remaining = alpaca_qty   # المتبقي = ما في Alpaca فعلاً
                        trade.tp1_hit            = True         # نعتبر TP1 تحقق
                    elif alpaca_qty > system_qty:
                        print(f"  ⚠️  {trade.ticker}: Alpaca={alpaca_qty} > النظام={system_qty} — نُبقي قيمة النظام")

        if removed:
            print(f"🔄 Sync: أُغلق {len(removed)} صفقة بواسطة Alpaca: {removed}")

    except Exception as e:
        print(f"⚠️  فشل Sync مع Alpaca: {e}")
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

            # ── إشعار Telegram عند خطأ PDT
            if "pattern day trading" in error_msg.lower():
                try:
                    from notifier import _send
                    side_ar  = "شراء 🟢" if side == "long" else "بيع 🔴"
                    _send(
                        f"⛔ <b>تعذّر فتح الصفقة — قيود PDT</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n\n"
                        f"🇬🇧 <b>English</b>\n"
                        f"🔒 Trade blocked by Pattern Day Trading rule.\n"
                        f"📌 {ticker} | {side_ar} | entry=${entry_price:.2f}\n"
                        f"💡 Solution: Raise account balance above $25,000.\n\n"
                        f"🇦🇪 <b>العربية</b>\n"
                        f"🔒 تم حجب الصفقة بسبب قاعدة PDT.\n"
                        f"📌 {ticker} | {side_ar} | دخول=${entry_price:.2f}\n"
                        f"💡 الحل: ارفع رصيد الحساب فوق $25,000."
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


def update_stop_loss_alpaca(trade: "OpenTrade", new_stop: float) -> bool:
    """
    يحدّث وقف الخسارة الفعلي في Alpaca للصفقة المفتوحة.

    الآلية:
    1. يجلب الأوامر المعلقة للسهم
    2. يلغي أوامر الـ Stop الموجودة
    3. يضع أمر Stop جديد بالسعر المحدّث

    يُستخدم عند:
    - نقل الوقف للتعادل بعد TP1
    - تحريك الـ Trailing Stop
    """
    ticker = trade.ticker
    side   = trade.side
    qty    = trade.quantity_remaining

    try:
        # ── جلب كل الأوامر المعلقة للسهم
        r = requests.get(
            f"{ALPACA_BASE_URL}/v2/orders",
            headers=HEADERS,
            params={"symbols": ticker, "status": "open", "limit": 20},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"⚠️  فشل جلب أوامر {ticker}: {r.status_code}")
            return False

        orders = r.json()

        # ── إلغاء كل الأوامر المعلقة للسهم (bracket + stop + limit)
        # ضروري لتحرير qty_available قبل وضع Stop جديد
        cancelled = 0
        for o in orders:
            if o.get("symbol") != ticker:
                continue
            oid   = o.get("id", "")
            otype = o.get("type", "")
            oprice = float(o.get("stop_price") or o.get("limit_price") or 0)
            if cancel_order(oid):
                cancelled += 1
                print(f"  🗑️  إلغاء أمر [{otype}] {oid[:8]}... @ ${oprice:.2f}")

        if cancelled == 0:
            print(f"  ℹ️  لا توجد أوامر معلقة لـ {ticker}")

        # ── انتظر لحظة لتحرير qty_available في Alpaca
        time.sleep(1)

        # ── إنشاء أمر Stop جديد
        close_side = "sell" if side == "long" else "buy"
        new_order = {
            "symbol":        ticker,
            "qty":           str(qty),
            "side":          close_side,
            "type":          "stop",
            "stop_price":    str(round(new_stop, 2)),
            "time_in_force": "gtc",
        }

        resp = requests.post(
            f"{ALPACA_BASE_URL}/v2/orders",
            headers=HEADERS,
            json=new_order,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            oid = resp.json().get("id", "")[:8]
            print(f"  ✅ Stop جديد لـ {ticker} @ ${new_stop:.2f} | qty={qty} | ID:{oid}...")
            return True
        else:
            msg = resp.json().get("message", "خطأ")
            print(f"  ❌ فشل إنشاء Stop جديد لـ {ticker}: {msg}")
            return False

    except Exception as e:
        print(f"❌ خطأ في تحديث Stop لـ {ticker}: {e}")
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

    # ── حساب نسبة المخاطرة الديناميكية من الـ Score
    from risk import dynamic_risk_pct
    sig_score    = getattr(signal, "score", 0)
    risk_pct     = dynamic_risk_pct(sig_score)

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

    # ── فحص مبكر: نحتاج كمية ≥ 2 لتقسيم TP1/TP2
    # إذا كانت buying_power لا تكفي حتى لسهمين — نرفض الصفقة
    if total_qty < 2:
        print(f"⛔ رُفضت الصفقة — الكمية {total_qty} < 2 (buying_power غير كافٍ لتقسيم TP1/TP2)")
        return None

    side_label     = "🟢 LONG" if signal.side == "long" else "🔴 SHORT"
    quality        = getattr(signal, "signal_quality", "standard").upper()
    strategy_label = "زخم" if strategy == "momentum" else "ارتداد"

    print(f"\n📤 فتح صفقة {strategy_label} — {signal.ticker} {side_label} [{quality}]")
    print(f"   Score          : {sig_score:.0f} → مخاطرة {sizing['risk_pct']}%")
    print(f"   الكمية الكلية : {total_qty}")
    print(f"   TP1 ({tp1_qty} سهم) : ${signal.target_tp1:.2f} (1R)")
    print(f"   TP2 ({tp2_qty} سهم) : ${signal.target_tp2:.2f} (3R)")
    print(f"   وقف الخسارة   : ${signal.stop_loss:.2f}")
    print(f"   المخاطرة       : ${sizing['risk_amount']} | رافعة ×{sizing['leverage']}")

    # ── أمر 1: tp1_qty مع هدف TP1
    order_id_1 = place_bracket_order(
        ticker=signal.ticker,
        quantity=tp1_qty,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        target=signal.target_tp1,
        side=signal.side,
    )
    if not order_id_1:
        return None

    # ── أمر 2: tp2_qty مع هدف TP2 — يضمن حماية النصف الثاني حتى لو توقف السيرفر
    order_id_2 = place_bracket_order(
        ticker=signal.ticker,
        quantity=tp2_qty,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        target=signal.target_tp2,
        side=signal.side,
    )
    if not order_id_2:
        # ── فشل TP2 → إلغاء TP1 وإغلاق البوزيشن كاملاً
        print(f"❌ فشل أمر TP2 لـ {signal.ticker} — إلغاء الصفقة بالكامل")
        # إلغاء أمر TP1
        if order_id_1:
            try:
                requests.delete(
                    f"{ALPACA_BASE_URL}/v2/orders/{order_id_1}",
                    headers=HEADERS, timeout=10,
                )
                print(f"  🗑️  تم إلغاء أمر TP1: {order_id_1[:8]}...")
            except Exception as e:
                print(f"  ⚠️  فشل إلغاء TP1: {e}")
        # إغلاق البوزيشن
        try:
            requests.delete(
                f"{ALPACA_BASE_URL}/v2/positions/{signal.ticker}",
                headers=HEADERS, timeout=10,
            )
            print(f"  🗑️  تم إغلاق بوزيشن {signal.ticker}")
        except Exception as e:
            print(f"  ⚠️  فشل إغلاق البوزيشن: {e}")
        return None

    print(f"   ✅ أمر TP1: {order_id_1[:8] if order_id_1 else 'N/A'}...")
    print(f"   ✅ أمر TP2: {order_id_2[:8] if order_id_2 else 'فشل'}...")

    # ── انتظر حتى 5 ثوان للتأكد من تنفيذ الأمر، ثم اقرأ الكمية الفعلية من Positions
    # positions API أدق من filled_qty لأنه يعكس ما يحتفظ به الحساب فعلياً
    actual_total = total_qty  # افتراضي
    for attempt in range(3):
        time.sleep(2)
        try:
            pos_r = requests.get(
                f"{ALPACA_BASE_URL}/v2/positions/{signal.ticker}",
                headers=HEADERS, timeout=10,
            )
            if pos_r.status_code == 200:
                held_qty = abs(int(float(pos_r.json().get("qty", 0))))
                if held_qty > 0:
                    actual_total = held_qty
                    break
            elif pos_r.status_code == 404:
                # لم يُنفَّذ الأمر بعد — انتظر أكثر
                pass
        except Exception:
            pass

    # توزيع الكمية الفعلية بين TP1 وTP2 بنسبة 50/50
    actual_tp1_qty = max(1, actual_total // 2)
    actual_tp2_qty = actual_total - actual_tp1_qty

    if actual_total != total_qty:
        print(f"   ℹ️  كمية فعلية من Alpaca: {actual_total} (طُلب: {total_qty}) — tp1={actual_tp1_qty} tp2={actual_tp2_qty}")

    return OpenTrade(
        ticker=signal.ticker,
        strategy=strategy,
        side=signal.side,
        order_id=order_id_1,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        target=signal.target_tp2,
        target_tp1=signal.target_tp1,
        target_tp2=signal.target_tp2,
        trail_stop=0.0,
        trail_step=signal.trail_step,
        quantity=actual_total,
        quantity_remaining=actual_tp2_qty,
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

    # ── Trailing Stop بعد TP1
    if trade.tp1_hit and trade.trail_step > 0:
        if trade.side == "long":
            new_stop = update_trailing_stop(current_price, trade.stop_loss, trade.trail_step)
            if new_stop > trade.stop_loss:
                return {"status": "trail_updated", "price": current_price,
                        "r": r_current, "new_stop": new_stop, "exit_qty": 0}
        elif trade.side == "short":
            new_stop = trade.stop_loss - trade.trail_step
            if current_price < trade.stop_loss - trade.trail_step:
                new_stop = current_price + trade.trail_step
                if new_stop < trade.stop_loss:
                    return {"status": "trail_updated", "price": current_price,
                            "r": r_current, "new_stop": new_stop, "exit_qty": 0}

    # ── تحريك الوقف للتعادل قبل TP1 إذا تجاوز السعر 1.5R
    # يحمي الصفقة من الانعكاس قبل الوصول لـ TP1
    if not trade.tp1_hit and r_current >= 1.5:
        risk    = abs(trade.entry_price - trade.stop_loss)
        if trade.side == "long":
            breakeven_stop = trade.entry_price + (risk * 0.2)  # +0.2R فوق التعادل
            if breakeven_stop > trade.stop_loss:
                return {"status": "trail_updated", "price": current_price,
                        "r": r_current, "new_stop": round(breakeven_stop, 2), "exit_qty": 0}
        else:
            breakeven_stop = trade.entry_price - (risk * 0.2)  # -0.2R تحت التعادل (SHORT)
            if breakeven_stop < trade.stop_loss:
                return {"status": "trail_updated", "price": current_price,
                        "r": r_current, "new_stop": round(breakeven_stop, 2), "exit_qty": 0}

    return {"status": "open", "price": current_price,
            "r": r_current, "new_stop": trade.stop_loss, "exit_qty": 0}


def get_position_qty_available(ticker: str) -> int:
    """يجلب الكمية المتاحة للبيع من Alpaca للسهم المحدد."""
    try:
        r = requests.get(
            f"{ALPACA_BASE_URL}/v2/positions/{ticker}",
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code == 200:
            return abs(int(float(r.json().get("qty_available", 0))))
    except Exception as e:
        print(f"⚠️  فشل جلب qty_available لـ {ticker}: {e}")
    return 0


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
