# =============================================================
# reporter.py — التقارير اليومية وتتبع الأداء
# التخزين: Render Disk (مجلد دائم لا يُمسح عند إعادة التشغيل)
# =============================================================

import json
import os
from datetime import datetime, date, timedelta
from dataclasses import dataclass, asdict
from typing import Optional
import pytz

from config import TIMEZONE
from notifier import notify_daily_report

TZ = pytz.timezone(TIMEZONE)

# ─────────────────────────────────────────
# مسار التخزين على Render Disk
# ─────────────────────────────────────────
# على Render: اربط الـ Disk على المسار /data
# على جهازك المحلي: سيُنشئ مجلد logs/ تلقائياً

DISK_PATH = os.getenv("RENDER_DISK_PATH", "logs")
LOGS_DIR  = os.path.join(DISK_PATH, "trades")


def _ensure_logs_dir():
    """ينشئ مجلد التخزين إذا لم يكن موجوداً."""
    os.makedirs(LOGS_DIR, exist_ok=True)


def _log_file(target_date: str) -> str:
    """يُرجع مسار ملف JSON ليوم معين."""
    return os.path.join(LOGS_DIR, f"trades_{target_date}.json")


# ─────────────────────────────────────────
# نموذج سجل الصفقة
# ─────────────────────────────────────────

@dataclass
class TradeRecord:
    ticker:       str
    strategy:     str           # 'conservative' أو 'meanrev'
    entry_price:  float
    exit_price:   float
    quantity:     int
    stop_loss:    float
    target:       float
    risk_amount:  float
    pnl:          float
    r_achieved:   float
    outcome:      str           # 'win' أو 'loss'
    exit_reason:  str           # 'target' | 'stopped' | 'trail' | 'eod'
    opened_at:    str
    closed_at:    str


# ─────────────────────────────────────────
# 1. حفظ وتحميل السجلات
# ─────────────────────────────────────────

def save_trade(record: TradeRecord):
    """
    يحفظ الصفقة في Render Disk.
    المسار: /data/trades/trades_YYYY-MM-DD.json
    """
    _ensure_logs_dir()

    today     = date.today().isoformat()
    log_path  = _log_file(today)
    trades    = load_trades_by_date(today)
    trades.append(asdict(record))

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2)

    print(f"💾 تم حفظ الصفقة في: {log_path}")


def load_today_trades() -> list[dict]:
    """يحمّل صفقات اليوم."""
    return load_trades_by_date(date.today().isoformat())


def load_trades_by_date(target_date: str) -> list[dict]:
    """
    يحمّل صفقات يوم معين من Render Disk.
    target_date: بصيغة 'YYYY-MM-DD'
    """
    log_path = _log_file(target_date)
    if not os.path.exists(log_path):
        return []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ خطأ في تحميل سجلات {target_date}: {e}")
        return []


def get_all_trade_dates() -> list[str]:
    """
    يُرجع قائمة بكل أيام التداول المحفوظة.
    مفيد لمراجعة الأداء التاريخي.
    """
    _ensure_logs_dir()
    files = os.listdir(LOGS_DIR)
    dates = [
        f.replace("trades_", "").replace(".json", "")
        for f in files
        if f.startswith("trades_") and f.endswith(".json")
    ]
    return sorted(dates, reverse=True)


# ─────────────────────────────────────────
# 2. تسجيل الصفقات
# ─────────────────────────────────────────

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
) -> TradeRecord:
    """
    يُسجّل صفقة مكتملة ويحسب النتائج تلقائياً.
    exit_reason: 'target' | 'stopped' | 'trail' | 'eod'
    """
    pnl        = round((exit_price - entry_price) * quantity, 2)
    risk_share = entry_price - stop_loss
    r_achieved = round((exit_price - entry_price) / risk_share, 2) if risk_share > 0 else 0.0
    outcome    = "win" if pnl > 0 else "loss"

    record = TradeRecord(
        ticker=ticker,
        strategy=strategy,
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


# ─────────────────────────────────────────
# 3. حساب الإحصائيات
# ─────────────────────────────────────────

def calculate_daily_stats(trades: list[dict]) -> dict:
    """يحسب إحصائيات شاملة لقائمة صفقات."""
    if not trades:
        return {
            "total_trades": 0,   "wins": 0,
            "losses": 0,         "win_rate": 0.0,
            "total_pnl": 0.0,   "total_r": 0.0,
            "avg_win": 0.0,      "avg_loss": 0.0,
            "best_trade": 0.0,   "worst_trade": 0.0,
            "conservative": 0,   "meanrev": 0,
        }

    wins   = [t for t in trades if t["outcome"] == "win"]
    losses = [t for t in trades if t["outcome"] == "loss"]
    pnls   = [t["pnl"] for t in trades]

    return {
        "total_trades": len(trades),
        "wins":         len(wins),
        "losses":       len(losses),
        "win_rate":     round(len(wins) / len(trades) * 100, 1),
        "total_pnl":    round(sum(pnls), 2),
        "total_r":      round(sum(t["r_achieved"] for t in trades), 2),
        "avg_win":      round(sum(t["pnl"] for t in wins)   / len(wins)   if wins   else 0, 2),
        "avg_loss":     round(sum(t["pnl"] for t in losses) / len(losses) if losses else 0, 2),
        "best_trade":   round(max(pnls), 2),
        "worst_trade":  round(min(pnls), 2),
        "conservative": len([t for t in trades if t["strategy"] == "conservative"]),
        "meanrev":      len([t for t in trades if t["strategy"] == "meanrev"]),
    }


# ─────────────────────────────────────────
# 4. إرسال التقرير اليومي
# ─────────────────────────────────────────

def send_daily_report(balance: float) -> bool:
    """
    يجمع صفقات اليوم ويرسل تقريراً عبر Telegram.
    يُستدعى تلقائياً من main.py عند إغلاق السوق.
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
    )

    print("\n" + "=" * 55)
    print(f"📊 تقرير يوم {today}")
    print("=" * 55)
    print(f"  الصفقات       : {stats['total_trades']}")
    print(f"  ربح / خسارة   : {stats['wins']} / {stats['losses']}")
    print(f"  نسبة الفوز    : {stats['win_rate']}%")
    print(f"  إجمالي R      : {stats['total_r']:+.2f}R")
    print(f"  إجمالي P&L    : ${stats['total_pnl']:+.2f}")
    print(f"  أفضل صفقة     : ${stats['best_trade']:+.2f}")
    print(f"  أسوأ صفقة     : ${stats['worst_trade']:+.2f}")
    print(f"  الرصيد الحالي : ${balance:,.2f}")
    print("=" * 55)

    return True


# ─────────────────────────────────────────
# 5. تقرير الأداء الأسبوعي
# ─────────────────────────────────────────

def get_weekly_stats() -> dict:
    """يحسب إحصائيات آخر 5 أيام تداول."""
    all_trades = []
    for i in range(5):
        day = (date.today() - timedelta(days=i)).isoformat()
        all_trades.extend(load_trades_by_date(day))
    stats = calculate_daily_stats(all_trades)
    stats["period"] = "آخر 5 أيام تداول"
    return stats
