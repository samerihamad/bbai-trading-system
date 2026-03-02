# =============================================================
# risk.py — إدارة المخاطرة وتتبع الخسائر اليومية
# =============================================================

from config import (
    RISK_PER_TRADE,
    MAX_DAILY_LOSSES,
    STRATEGY2_LEVERAGE,
    PROFIT_FACTOR_CUT,
)


# ─────────────────────────────────────────
# 1. حساب حجم الصفقة
# ─────────────────────────────────────────

def calculate_position_size(
    balance:      float,
    entry_price:  float,
    stop_loss:    float,
    use_leverage: bool = False,
) -> dict:
    """
    يحسب عدد الأسهم بناءً على:
    - الرصيد الحالي
    - سعر الدخول
    - وقف الخسارة
    - نسبة المخاطرة 3% ديناميكية (Compounding)

    يُرجع dict يحتوي:
    - quantity    : عدد الأسهم
    - risk_amount : المبلغ المخاطر به بالدولار
    - risk_pct    : نسبة المخاطرة
    - leverage    : الرافعة المستخدمة
    """
    if entry_price <= 0 or stop_loss <= 0:
        raise ValueError("سعر الدخول ووقف الخسارة يجب أن يكونا أكبر من صفر")

    # LONG: وقف الخسارة يجب أن يكون أقل من سعر الدخول
    # SHORT: نقبل وقف الخسارة أعلى من سعر الدخول
    risk_per_share = abs(entry_price - stop_loss)
    if risk_per_share == 0:
        raise ValueError("سعر الدخول ووقف الخسارة متساويان — لا يمكن حساب الحجم")

    leverage     = STRATEGY2_LEVERAGE if use_leverage else 1.0
    risk_amount  = balance * RISK_PER_TRADE
    quantity     = int((risk_amount * leverage) / risk_per_share)

    if quantity <= 0:
        quantity = 1  # على الأقل سهم واحد

    return {
        "quantity":    quantity,
        "risk_amount": round(risk_amount, 2),
        "risk_pct":    RISK_PER_TRADE * 100,
        "leverage":    leverage,
    }


# ─────────────────────────────────────────
# 2. حساب نسبة R المحققة
# ─────────────────────────────────────────

def calculate_r(
    entry_price: float,
    exit_price:  float,
    stop_loss:   float,
    side:        str = "long",
) -> float:
    """
    يحسب كم R حققت الصفقة.
    مثال LONG:  دخلت 100، وقف 95، خرجت 110 → R = +2.0
    مثال SHORT: دخلت 100، وقف 105، خرجت 90  → R = +2.0
    """
    risk_per_share = abs(entry_price - stop_loss)
    if risk_per_share <= 0:
        return 0.0

    if side == "long":
        return round((exit_price - entry_price) / risk_per_share, 2)
    else:
        return round((entry_price - exit_price) / risk_per_share, 2)


# ─────────────────────────────────────────
# 3. فحص شرط قطع الخسارة (Profit Factor Cut)
# ─────────────────────────────────────────

def check_profit_factor_cut(
    entry_price:  float,
    peak_price:   float,
    current_price: float,
    side:         str = "long",
) -> bool:
    """
    يتحقق إذا كانت الصفقة تراجعت 20% من قمتها → يجب الخروج.

    LONG:  إذا انخفض السعر 20% من أعلى نقطة بعد الدخول
    SHORT: إذا ارتفع السعر 20% من أدنى نقطة بعد الدخول
    """
    if side == "long":
        peak_pnl    = peak_price - entry_price
        current_pnl = current_price - entry_price
    else:
        peak_pnl    = entry_price - peak_price
        current_pnl = entry_price - current_price

    if peak_pnl <= 0:
        return False

    drawdown = (peak_pnl - current_pnl) / peak_pnl
    return drawdown >= PROFIT_FACTOR_CUT


# ─────────────────────────────────────────
# 4. تتبع الخسائر اليومية
# ─────────────────────────────────────────

class DailyRiskManager:
    """
    يتتبع عدد الخسائر اليومية ويوقف النظام عند الحد.
    يتم إنشاء كائن جديد منه كل يوم في main.py
    """

    def __init__(self):
        self.daily_losses = 0
        self.daily_wins   = 0
        self.daily_pnl    = 0.0
        self.daily_r      = 0.0
        self.is_stopped   = False

    def record_win(self, pnl: float, r_achieved: float):
        """يسجّل صفقة رابحة."""
        self.daily_wins += 1
        self.daily_pnl  += pnl
        self.daily_r    += r_achieved

    def record_loss(self, pnl: float, r_achieved: float) -> bool:
        """
        يسجّل صفقة خاسرة.
        يُرجع True إذا وصلنا لحد الخسارتين → يجب إيقاف النظام.
        """
        self.daily_losses += 1
        self.daily_pnl    += pnl   # سيكون سالباً
        self.daily_r      += r_achieved

        if self.daily_losses >= MAX_DAILY_LOSSES:
            self.is_stopped = True
            return True

        return False

    def can_trade(self) -> bool:
        """يتحقق إذا كان مسموحاً بفتح صفقات جديدة."""
        return not self.is_stopped

    def get_summary(self) -> dict:
        """يُرجع ملخص اليوم للتقرير."""
        total    = self.daily_wins + self.daily_losses
        win_rate = (self.daily_wins / total * 100) if total > 0 else 0.0

        return {
            "total_trades": total,
            "wins":         self.daily_wins,
            "losses":       self.daily_losses,
            "win_rate":     round(win_rate, 1),
            "total_pnl":    round(self.daily_pnl, 2),
            "total_r":      round(self.daily_r, 2),
            "is_stopped":   self.is_stopped,
        }

    def reset(self):
        """يعيد ضبط كل شيء — يُستدعى في بداية كل يوم."""
        self.__init__()
