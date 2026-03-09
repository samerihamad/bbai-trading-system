# =============================================================
# reporter.py -- التقارير اليومية وتتبع الأداء
# التخزين: Google Sheets (دائم — لا يُمسح عند إعادة التشغيل)
# =============================================================

import os
from datetime import datetime, date, timedelta
from dataclasses import dataclass, asdict
import pytz

from config import TIMEZONE
from notifier import notify_daily_report

TZ = pytz.timezone(TIMEZONE)


# -----------------------------------------
# نموذج سجل الصفقة
# -----------------------------------------

@dataclass
class TradeRecord:
    ticker:      str
    strategy:    str
    side:        str
    entry_price: float
    exit_price:  float
    quantity:    int
    stop_loss:   float
    target:      float
    risk_amount: float
    pnl:         float
    r_achieved:  float
    outcome:     str
    exit_reason: str
    opened_at:   str
    closed_at:   str


# -----------------------------------------
# 1. حفظ وتحميل السجلات — Google Sheets
# -----------------------------------------

def save_trade(record: TradeRecord):
    """يحفظ الصفقة في Google Sheets."""
    try:
        from executor import save_closed_trade_sheets
        save_closed_trade_sheets(asdict(record))
    except Exception as e:
        print(f"⚠️  فشل حفظ الصفقة في Sheets: {e}")


def load_today_trades() -> list[dict]:
    return load_trades_by_date(date.today().isoformat())


def load_trades_by_date(target_date: str) -> list[dict]:
    """يجلب صفقات يوم معين من Google Sheets."""
    try:
        from executor import load_closed_trades_by_date_sheets
        return load_closed_trades_by_date_sheets(target_date)
    except Exception as e:
        print(f"⚠️  فشل جلب الصفقات من Sheets: {e}")
        return []


def get_all_trade_dates() -> list[str]:
    """يجلب كل التواريخ المتاحة من Sheets."""
    try:
        from executor import _get_closed_trades_ws
        ws = _get_closed_trades_ws()
        if not ws:
            return []
        rows = ws.get_all_records()
        dates = list({str(r.get("date", "")) for r in rows if r.get("date")})
        return sorted(dates, reverse=True)
    except Exception as e:
        print(f"⚠️  فشل جلب التواريخ: {e}")
        return []


# -----------------------------------------
# 2. تسجيل الصفقات
# -----------------------------------------

def record_trade(
    ticker:      str,
    strategy:    str,
    entry_price: float,
    exit_price:  float,
    quantity:    int,
    stop_loss:   float,
    target:      float,
    risk_amount: float,
    exit_reason: str,
    opened_at:   datetime,
    side:        str = "long",
) -> TradeRecord:
    """يسجل صفقة مكتملة ويحسب النتائج تلقائياً."""

    if side == "long":
        pnl        = round((exit_price - entry_price) * quantity, 2)
        r_achieved = round((exit_price - entry_price) / abs(entry_price - stop_loss), 2) if stop_loss != entry_price else 0.0
    else:
        pnl        = round((entry_price - exit_price) * quantity, 2)
        r_achieved = round((entry_price - exit_price) / abs(entry_price - stop_loss), 2) if stop_loss != entry_price else 0.0

    outcome = "win" if pnl > 0 else "loss"

    record = TradeRecord(
        ticker=ticker,
        strategy=strategy,
        side=side,
        entry_price=entry_price,
        exit_price=exit_price,
        quantity=quantity,
        stop_loss=stop_loss,
        target=target,
        risk_amount=risk_amount,
        pnl=pnl,
        r_achieved=r_achieved,
        outcome=outcome,
        exit_reason=exit_reason,
        opened_at=opened_at.isoformat(),
        closed_at=datetime.now(TZ).isoformat(),
    )
    save_trade(record)
    return record


# -----------------------------------------
# 3. حساب الاحصائيات
# -----------------------------------------

def calculate_daily_stats(trades: list[dict]) -> dict:
    if not trades:
        return {
            "total_trades": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0,   "total_pnl": 0.0, "total_r": 0.0,
            "avg_win": 0.0,    "avg_loss": 0.0,
            "best_trade": 0.0, "worst_trade": 0.0,
            "long_trades": 0,  "short_trades": 0,
        }

    wins   = [t for t in trades if t["outcome"] == "win"]
    losses = [t for t in trades if t["outcome"] == "loss"]
    pnls   = [t["pnl"] for t in trades]

    return {
        "total_trades":  len(trades),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      round(len(wins) / len(trades) * 100, 1),
        "total_pnl":     round(sum(pnls), 2),
        "total_r":       round(sum(t["r_achieved"] for t in trades), 2),
        "avg_win":       round(sum(t["pnl"] for t in wins)   / len(wins)   if wins   else 0, 2),
        "avg_loss":      round(sum(t["pnl"] for t in losses) / len(losses) if losses else 0, 2),
        "best_trade":    round(max(pnls), 2),
        "worst_trade":   round(min(pnls), 2),
        "long_trades":   len([t for t in trades if t.get("side") == "long"]),
        "short_trades":  len([t for t in trades if t.get("side") == "short"]),
    }


# -----------------------------------------
# 4. حفظ الملخص اليومي في Sheets
# -----------------------------------------

DAILY_SUMMARY_SHEET  = "Daily Summary"
DAILY_SUMMARY_HEADERS = [
    "date", "total_trades", "wins", "losses", "win_rate",
    "long_trades", "short_trades",
    "total_r", "total_pnl",
    "best_trade", "worst_trade", "avg_win", "avg_loss",
    "balance",
]

def _save_daily_summary_sheets(stats: dict, balance: float, today: str) -> None:
    """يحفظ ملخص اليوم في تبويب Daily Summary في Google Sheets."""
    try:
        from executor import _get_sheets_client
        import os
        gc = _get_sheets_client()
        if not gc:
            return
        SHEET_ID = "1C1kcOrXAbZ36_0lgC5awNgd1wqpIs3diZO-FXcGXJLo"
        ss = gc.open_by_key(SHEET_ID)

        # إنشاء التبويب إذا لم يكن موجوداً
        try:
            ws = ss.worksheet(DAILY_SUMMARY_SHEET)
        except Exception:
            ws = ss.add_worksheet(title=DAILY_SUMMARY_SHEET, rows=500, cols=len(DAILY_SUMMARY_HEADERS))
            ws.append_row(DAILY_SUMMARY_HEADERS)

        # تحقق إذا اليوم مسجّل مسبقاً — تحديث بدلاً من إضافة مكرر
        existing = ws.get_all_records()
        for idx, row in enumerate(existing, start=2):  # start=2 لأن صف 1 هو الـ headers
            if str(row.get("date", "")) == today:
                # تحديث الصف الموجود
                ws.update(f"A{idx}:{chr(65 + len(DAILY_SUMMARY_HEADERS) - 1)}{idx}", [[
                    today,
                    stats["total_trades"], stats["wins"], stats["losses"],
                    stats["win_rate"], stats["long_trades"], stats["short_trades"],
                    stats["total_r"], stats["total_pnl"],
                    stats["best_trade"], stats["worst_trade"],
                    stats["avg_win"], stats["avg_loss"],
                    round(balance, 2),
                ]])
                print(f"✅ Daily Summary لـ {today} تم تحديثه في Google Sheets")
                return

        # إضافة صف جديد
        ws.append_row([
            today,
            stats["total_trades"], stats["wins"], stats["losses"],
            stats["win_rate"], stats["long_trades"], stats["short_trades"],
            stats["total_r"], stats["total_pnl"],
            stats["best_trade"], stats["worst_trade"],
            stats["avg_win"], stats["avg_loss"],
            round(balance, 2),
        ], value_input_option="RAW")
        print(f"✅ Daily Summary لـ {today} تم حفظه في Google Sheets")

    except Exception as e:
        print(f"⚠️  فشل حفظ Daily Summary: {e}")


# -----------------------------------------
# 5. ارسال التقرير اليومي
# -----------------------------------------

def send_daily_report(balance: float, open_trades: list = None) -> bool:
    """
    يجمع صفقات اليوم ويرسل تقريراً عبر Telegram ويحفظ الملخص في Sheets.
    يُستدعى مرة واحدة فقط من main.py عند اغلاق السوق.
    """
    trades = load_today_trades()
    stats  = calculate_daily_stats(trades)
    today  = date.today().isoformat()

    notify_daily_report(
        date=today,
        total_trades=stats["total_trades"],
        wins=stats["wins"],
        losses=stats["losses"],
        total_r=stats["total_r"],
        total_pnl=stats["total_pnl"],
        balance=balance,
        long_trades=stats["long_trades"],
        short_trades=stats["short_trades"],
        best_trade=stats["best_trade"],
        worst_trade=stats["worst_trade"],
        avg_win=stats["avg_win"],
        avg_loss=stats["avg_loss"],
        open_trades=open_trades or [],
    )

    # ── حفظ الملخص اليومي في Google Sheets
    _save_daily_summary_sheets(stats, balance, today)

    # طباعة في الـ logs
    print("\n" + "=" * 55)
    print(f"Daily Report -- {today}")
    print("=" * 55)
    print(f"  Trades        : {stats['total_trades']}")
    print(f"  Win / Loss    : {stats['wins']} / {stats['losses']}")
    print(f"  Win Rate      : {stats['win_rate']}%")
    print(f"  Total R       : {stats['total_r']:+.2f}R")
    print(f"  Total PnL     : ${stats['total_pnl']:+.2f}")
    print(f"  Long / Short  : {stats['long_trades']} / {stats['short_trades']}")
    if trades:
        print(f"  Best Trade    : ${stats['best_trade']:+.2f}")
        print(f"  Worst Trade   : ${stats['worst_trade']:+.2f}")
    print(f"  Balance       : ${balance:,.2f}")
    print("=" * 55)

    return True


# -----------------------------------------
# 5. تقرير الاداء الاسبوعي
# -----------------------------------------

def get_weekly_stats() -> dict:
    all_trades = []
    for i in range(5):
        day = (date.today() - timedelta(days=i)).isoformat()
        all_trades.extend(load_trades_by_date(day))
    stats = calculate_daily_stats(all_trades)
    stats["period"] = "Last 5 trading days"
    return stats
