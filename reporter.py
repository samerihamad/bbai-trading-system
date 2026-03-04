# =============================================================
# reporter.py -- التقارير اليومية وتتبع الأداء
# التخزين: Google Sheets (دائم) + logs محلية (مؤقتة)
# =============================================================

import json
import os
import time
from datetime import datetime, date, timedelta
from dataclasses import dataclass, asdict
import pytz

from config import TIMEZONE
from notifier import notify_daily_report

TZ = pytz.timezone(TIMEZONE)

# -----------------------------------------
# إعداد Google Sheets
# -----------------------------------------
SHEET_ID        = "1C1kcOrXAbZ36_0lgC5awNgd1wqpIs3diZO-FXcGXJLo"
SHEET_NAME      = "Trades"
SUMMARY_SHEET   = "Daily Summary"
CREDENTIALS_ENV = "GOOGLE_CREDENTIALS_JSON"  # متغير بيئة في Render

# -----------------------------------------
# مسار التخزين المحلي (احتياطي)
# -----------------------------------------
DISK_PATH = os.getenv("RENDER_DISK_PATH", "logs")
LOGS_DIR  = os.path.join(DISK_PATH, "trades")


def _ensure_logs_dir():
    os.makedirs(LOGS_DIR, exist_ok=True)


def _log_file(target_date: str) -> str:
    return os.path.join(LOGS_DIR, f"trades_{target_date}.json")


# -----------------------------------------
# تهيئة Google Sheets Client
# -----------------------------------------

def _get_sheets_client():
    """يُنشئ Google Sheets client من credentials."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]

        # أولاً: من Environment Variable (Render)
        creds_json = os.getenv(CREDENTIALS_ENV)
        if creds_json:
            import json as json_mod
            creds_dict = json_mod.loads(creds_json)
            creds      = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        else:
            # ثانياً: من ملف محلي (للتطوير)
            creds = Credentials.from_service_account_file(
                "bbai-489218-bca75c7e5d12.json", scopes=scopes
            )

        return gspread.authorize(creds)

    except ImportError:
        print("⚠️  gspread غير مثبت — pip install gspread google-auth")
        return None
    except Exception as e:
        print(f"⚠️  فشل الاتصال بـ Google Sheets: {e}")
        return None


def _get_or_create_worksheet(gc, sheet_id: str, title: str, headers: list) -> object:
    """يجلب أو ينشئ worksheet بالعنوان المطلوب."""
    try:
        spreadsheet = gc.open_by_key(sheet_id)
        try:
            ws = spreadsheet.worksheet(title)
        except Exception:
            ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(headers))
            ws.append_row(headers, value_input_option="RAW")
            # تنسيق الصف الأول
            ws.format("1:1", {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
            })
        return ws
    except Exception as e:
        print(f"⚠️  خطأ في Google Sheets worksheet: {e}")
        return None


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
# 1. حفظ الصفقة — محلياً + Google Sheets
# -----------------------------------------

def save_trade(record: TradeRecord):
    """يحفظ الصفقة محلياً (احتياطي) وفي Google Sheets."""
    # ── حفظ محلي
    _ensure_logs_dir()
    today    = date.today().isoformat()
    log_path = _log_file(today)
    trades   = load_trades_by_date(today)
    trades.append(asdict(record))
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2)
    print(f"Trade saved locally: {log_path}")

    # ── حفظ في Google Sheets
    _save_to_sheets(record)


def _save_to_sheets(record: TradeRecord):
    """يُضيف صف جديد في Google Sheets لكل صفقة مكتملة."""
    gc = _get_sheets_client()
    if not gc:
        return

    headers = [
        "Date", "Time (EST)", "Ticker", "Side", "Strategy",
        "Entry", "Exit", "Qty", "Stop Loss", "Target",
        "P&L ($)", "R", "Outcome", "Exit Reason", "Risk ($)"
    ]

    ws = _get_or_create_worksheet(gc, SHEET_ID, SHEET_NAME, headers)
    if not ws:
        return

    try:
        closed_dt = datetime.fromisoformat(record.closed_at)
        row = [
            closed_dt.strftime("%Y-%m-%d"),
            closed_dt.strftime("%H:%M EST"),
            record.ticker,
            record.side.upper(),
            record.strategy,
            record.entry_price,
            record.exit_price,
            record.quantity,
            record.stop_loss,
            record.target,
            record.pnl,
            record.r_achieved,
            record.outcome.upper(),
            record.exit_reason,
            record.risk_amount,
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")

        # تلوين الصف بناءً على النتيجة
        last_row = len(ws.get_all_values())
        if record.outcome == "win":
            ws.format(f"A{last_row}:O{last_row}", {
                "backgroundColor": {"red": 0.85, "green": 0.95, "blue": 0.85}
            })
        else:
            ws.format(f"A{last_row}:O{last_row}", {
                "backgroundColor": {"red": 0.95, "green": 0.85, "blue": 0.85}
            })

        print(f"✅ صفقة {record.ticker} حُفظت في Google Sheets")

    except Exception as e:
        print(f"⚠️  فشل حفظ الصفقة في Google Sheets: {e}")


# -----------------------------------------
# 2. تحميل السجلات المحلية
# -----------------------------------------

def load_today_trades() -> list:
    return load_trades_by_date(date.today().isoformat())


def load_trades_by_date(target_date: str) -> list:
    log_path = _log_file(target_date)
    if not os.path.exists(log_path):
        return []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading trades {target_date}: {e}")
        return []


def get_all_trade_dates() -> list:
    _ensure_logs_dir()
    files = os.listdir(LOGS_DIR)
    dates = [
        f.replace("trades_", "").replace(".json", "")
        for f in files
        if f.startswith("trades_") and f.endswith(".json")
    ]
    return sorted(dates, reverse=True)


# -----------------------------------------
# 3. تسجيل الصفقات
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
# 4. حساب الاحصائيات
# -----------------------------------------

def calculate_daily_stats(trades: list) -> dict:
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
# 5. إرسال التقرير اليومي + حفظه في Sheets
# -----------------------------------------

def send_daily_report(balance: float) -> bool:
    """يجمع صفقات اليوم ويرسل تقريراً عبر Telegram ويحفظه في Sheets."""
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
    )

    # ── حفظ ملخص يومي في Sheets
    _save_daily_summary_to_sheets(today, stats, balance)

    # ── طباعة في الـ logs
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


def _save_daily_summary_to_sheets(today: str, stats: dict, balance: float):
    """يحفظ ملخص اليوم في sheet منفصل."""
    gc = _get_sheets_client()
    if not gc:
        return

    headers = [
        "Date", "Trades", "Wins", "Losses", "Win Rate (%)",
        "Total P&L ($)", "Total R", "Long", "Short",
        "Best ($)", "Worst ($)", "Balance ($)"
    ]

    ws = _get_or_create_worksheet(gc, SHEET_ID, SUMMARY_SHEET, headers)
    if not ws:
        return

    try:
        row = [
            today,
            stats["total_trades"],
            stats["wins"],
            stats["losses"],
            stats["win_rate"],
            stats["total_pnl"],
            stats["total_r"],
            stats["long_trades"],
            stats["short_trades"],
            stats["best_trade"],
            stats["worst_trade"],
            balance,
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")
        print(f"✅ ملخص اليوم {today} حُفظ في Google Sheets")
    except Exception as e:
        print(f"⚠️  فشل حفظ ملخص اليوم في Google Sheets: {e}")


# -----------------------------------------
# 6. تقرير الأداء الأسبوعي
# -----------------------------------------

def get_weekly_stats() -> dict:
    all_trades = []
    for i in range(5):
        day = (date.today() - timedelta(days=i)).isoformat()
        all_trades.extend(load_trades_by_date(day))
    stats = calculate_daily_stats(all_trades)
    stats["period"] = "Last 5 trading days"
    return stats
