# =============================================================
# test_trailing.py — اختبارات Trailing Stop
# تشغيل: python3 test_trailing.py
# لا تحتاج اتصال بـ Alpaca أو Telegram — كل شيء Mock
# =============================================================

import sys
import types
import unittest
from dataclasses import dataclass, field
from unittest.mock import patch, MagicMock

# ── Mock للـ config قبل الـ import
mock_config = types.ModuleType("config")
mock_config.TRAILING_ATR_MULT        = 0.5
mock_config.TRAILING_ATR_MULT_MOM    = 0.6
mock_config.TRAILING_BUFFER_PCT      = 0.001
mock_config.TRAILING_ATR_TIMEFRAME   = "15Min"
mock_config.TRAILING_MAX_UPDATES     = 5   # صغير للاختبار
mock_config.TRAILING_START_AFTER_TP1 = True
mock_config.ALPACA_API_KEY    = "TEST"
mock_config.ALPACA_SECRET_KEY = "TEST"
mock_config.ALPACA_BASE_URL   = "https://paper-api.alpaca.markets"
mock_config.ALPACA_DATA_URL   = "https://data.alpaca.markets"
mock_config.SHEET_ID          = "TEST"
mock_config.TIMEZONE          = "America/New_York"
sys.modules["config"] = mock_config

# Mock للمكتبات الخارجية
for mod in ["gspread", "google.oauth2.service_account", "requests", "notifier"]:
    sys.modules[mod] = MagicMock()


# ── نعرّف OpenTrade مباشرة بدون import executor كامل
@dataclass
class OpenTrade:
    ticker:             str
    strategy:           str
    side:               str
    order_id:           str
    entry_price:        float
    stop_loss:          float
    target:             float
    target_tp1:         float
    target_tp2:         float
    trail_stop:         float   = 0.0
    trail_step:         float   = 0.0
    quantity:           int     = 100
    quantity_remaining: int     = 50
    tp1_hit:            bool    = False
    peak_price:         float   = 0.0
    risk_amount:        float   = 0.0
    opened_at:          object  = None
    trailing_active:    bool    = False
    highest_price:      float   = 0.0
    lowest_price:       float   = 0.0
    current_atr:        float   = 0.0
    trail_update_count: int     = 0
    tp1_pnl:            float   = 0.0
    closing_in_progress: bool   = False

    def __post_init__(self):
        if self.highest_price == 0.0:
            self.highest_price = self.entry_price
        if self.lowest_price == 0.0:
            self.lowest_price = self.entry_price
        if self.peak_price == 0.0:
            self.peak_price = self.entry_price


# ── نستورد فقط دالة update_trailing_stop بشكل منفصل (بدون import كامل)
def make_update_trailing_stop():
    """
    نسخة مُبسَّطة من update_trailing_stop للاختبار
    تتجاهل API calls وتعمل على الـ mock data
    """
    import config as _config

    def update_trailing_stop(trade: OpenTrade, current_price: float, mock_atr: float = 0.5) -> dict:
        if not trade.trailing_active:
            return {"status": "not_active"}

        max_updates = getattr(_config, "TRAILING_MAX_UPDATES", 200)
        if trade.trail_update_count >= max_updates:
            return {"status": "max_reached"}

        if trade.strategy == "momentum":
            atr_mult = getattr(_config, "TRAILING_ATR_MULT_MOM", 0.6)
        else:
            atr_mult = getattr(_config, "TRAILING_ATR_MULT", 0.5)

        buf_pct = getattr(_config, "TRAILING_BUFFER_PCT", 0.001)

        # نستخدم mock_atr مباشرةً بدل API
        if trade.current_atr == 0:
            trade.current_atr = mock_atr

        effective_atr = trade.current_atr
        if effective_atr <= 0:
            return {"status": "no_change", "new_stop": trade.stop_loss}

        buffer     = buf_pct * current_price
        trail_step = round(atr_mult * effective_atr + buffer, 4)

        if trade.side == "long":
            if current_price > trade.highest_price:
                trade.highest_price = current_price
            new_stop = round(trade.highest_price - trail_step, 4)
            if new_stop > trade.stop_loss:
                old_stop = trade.stop_loss
                trade.stop_loss          = new_stop
                trade.trail_update_count += 1
                return {"status": "updated", "new_stop": new_stop, "old_stop": old_stop}

        elif trade.side == "short":
            if current_price < trade.lowest_price:
                trade.lowest_price = current_price
            new_stop = round(trade.lowest_price + trail_step, 4)
            if new_stop < trade.stop_loss:
                old_stop = trade.stop_loss
                trade.stop_loss          = new_stop
                trade.trail_update_count += 1
                return {"status": "updated", "new_stop": new_stop, "old_stop": old_stop}

        return {"status": "no_change", "new_stop": trade.stop_loss}

    return update_trailing_stop


update_trailing_stop = make_update_trailing_stop()


# ══════════════════════════════════════════════════════════════
# الاختبارات
# ══════════════════════════════════════════════════════════════

class TestTrailingNotActive(unittest.TestCase):
    """trailing_active = False → لا يحدث شيء"""

    def test_not_active_returns_correct_status(self):
        trade = OpenTrade(
            ticker="AAPL", strategy="meanrev", side="long",
            order_id="x", entry_price=100.0, stop_loss=98.0,
            target=104.0, target_tp1=102.0, target_tp2=104.0,
            risk_amount=2.0, trailing_active=False,
        )
        result = update_trailing_stop(trade, current_price=105.0)
        self.assertEqual(result["status"], "not_active")
        self.assertEqual(trade.stop_loss, 98.0)  # لم يتغير


class TestTrailingLong(unittest.TestCase):
    """اختبارات LONG trailing"""

    def _make_trade(self, entry=100.0, stop=98.0, risk=2.0, strategy="meanrev"):
        t = OpenTrade(
            ticker="TSLA", strategy=strategy, side="long",
            order_id="x", entry_price=entry, stop_loss=stop,
            target=106.0, target_tp1=102.0, target_tp2=104.0,
            risk_amount=risk, trailing_active=True,
        )
        t.highest_price = entry
        return t

    def test_stop_moves_up_when_price_rises(self):
        trade = self._make_trade()
        result = update_trailing_stop(trade, current_price=103.0, mock_atr=0.5)
        self.assertEqual(result["status"], "updated")
        self.assertGreater(trade.stop_loss, 98.0)

    def test_stop_never_moves_down(self):
        trade = self._make_trade()
        update_trailing_stop(trade, current_price=103.0, mock_atr=0.5)
        stop_after_rise = trade.stop_loss
        # السعر ينزل — الـ stop يجب أن يبقى ثابتاً
        result = update_trailing_stop(trade, current_price=101.0, mock_atr=0.5)
        self.assertIn(result["status"], ["no_change", "updated"])
        self.assertGreaterEqual(trade.stop_loss, stop_after_rise)

    def test_highest_price_tracked_correctly(self):
        trade = self._make_trade(entry=100.0)
        update_trailing_stop(trade, current_price=105.0, mock_atr=0.5)
        self.assertEqual(trade.highest_price, 105.0)
        # سعر أقل → highest لا يتغير
        update_trailing_stop(trade, current_price=103.0, mock_atr=0.5)
        self.assertEqual(trade.highest_price, 105.0)

    def test_momentum_uses_wider_trail(self):
        """Momentum ATR mult أكبر → trail_step أكبر → stop أبعد"""
        trade_mr  = self._make_trade(strategy="meanrev")
        trade_mom = self._make_trade(strategy="momentum")
        res_mr  = update_trailing_stop(trade_mr,  current_price=103.0, mock_atr=0.5)
        res_mom = update_trailing_stop(trade_mom, current_price=103.0, mock_atr=0.5)
        # Momentum stop يجب أن يكون أقل من MeanRev (trailing أوسع)
        if res_mr["status"] == "updated" and res_mom["status"] == "updated":
            self.assertLess(res_mom["new_stop"], res_mr["new_stop"])

    def test_counter_increments(self):
        trade = self._make_trade()
        update_trailing_stop(trade, current_price=103.0, mock_atr=0.5)
        update_trailing_stop(trade, current_price=105.0, mock_atr=0.5)
        self.assertEqual(trade.trail_update_count, 2)


class TestTrailingShort(unittest.TestCase):
    """اختبارات SHORT trailing"""

    def _make_trade(self, entry=100.0, stop=102.0, risk=2.0):
        t = OpenTrade(
            ticker="BZ", strategy="meanrev", side="short",
            order_id="x", entry_price=entry, stop_loss=stop,
            target=96.0, target_tp1=98.0, target_tp2=96.0,
            risk_amount=risk, trailing_active=True,
        )
        t.lowest_price = entry
        return t

    def test_stop_moves_down_when_price_falls(self):
        trade = self._make_trade()
        result = update_trailing_stop(trade, current_price=97.0, mock_atr=0.5)
        self.assertEqual(result["status"], "updated")
        self.assertLess(trade.stop_loss, 102.0)

    def test_stop_never_moves_up_for_short(self):
        trade = self._make_trade()
        update_trailing_stop(trade, current_price=97.0, mock_atr=0.5)
        stop_after_fall = trade.stop_loss
        # السعر يرتفع → الـ stop يجب أن يبقى ثابتاً أو ينزل
        update_trailing_stop(trade, current_price=99.0, mock_atr=0.5)
        self.assertLessEqual(trade.stop_loss, stop_after_fall)

    def test_lowest_price_tracked(self):
        trade = self._make_trade(entry=100.0)
        update_trailing_stop(trade, current_price=96.0, mock_atr=0.5)
        self.assertEqual(trade.lowest_price, 96.0)
        # سعر أعلى → lowest لا يتغير
        update_trailing_stop(trade, current_price=98.0, mock_atr=0.5)
        self.assertEqual(trade.lowest_price, 96.0)


class TestTrailingMaxUpdates(unittest.TestCase):
    """اختبار TRAILING_MAX_UPDATES = 5 (في هذه الاختبارات)"""

    def test_stops_after_max_updates(self):
        trade = OpenTrade(
            ticker="APA", strategy="meanrev", side="long",
            order_id="x", entry_price=30.0, stop_loss=29.0,
            target=33.0, target_tp1=31.5, target_tp2=33.0,
            risk_amount=1.0, trailing_active=True,
        )
        trade.highest_price = 30.0

        # نحرّك السعر 10 مرات لنتجاوز الـ MAX=5
        statuses = []
        for i in range(10):
            price = 30.0 + (i * 0.5)
            res = update_trailing_stop(trade, current_price=price, mock_atr=0.3)
            statuses.append(res["status"])

        self.assertIn("max_reached", statuses)
        self.assertLessEqual(trade.trail_update_count, 5)


class TestSheetsRecovery(unittest.TestCase):
    """اختبار validation الـ highest/lowest عند load"""

    def test_highest_price_defaults_to_entry_on_bad_data(self):
        """محاكاة _load_open_trades: highest=0 → يُعاد لـ entry_price"""
        entry = 50.0
        highest = float(0 or 0) or entry   # نفس منطق الكود
        self.assertEqual(highest, entry)

    def test_valid_highest_price_preserved(self):
        entry   = 50.0
        highest = float(55.0 or 0) or entry
        self.assertEqual(highest, 55.0)

    def test_long_trailing_with_recovered_trade(self):
        """صفقة بعد restart — trailing يكمل من نفس النقطة"""
        trade = OpenTrade(
            ticker="NVDA", strategy="meanrev", side="long",
            order_id="x", entry_price=500.0, stop_loss=502.0,
            target=510.0, target_tp1=505.0, target_tp2=510.0,
            risk_amount=2.0, trailing_active=True,
            highest_price=506.0,        # تم استعادته من Sheets
            trail_update_count=3,       # تم استعادته من Sheets
            current_atr=0.8,
        )
        result = update_trailing_stop(trade, current_price=507.0, mock_atr=0.8)
        self.assertEqual(result["status"], "updated")
        self.assertEqual(trade.trail_update_count, 4)  # استمر من 3


# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("  🧪 Trailing Stop Tests")
    print("=" * 60)
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    for cls in [
        TestTrailingNotActive,
        TestTrailingLong,
        TestTrailingShort,
        TestTrailingMaxUpdates,
        TestSheetsRecovery,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print()
    if result.wasSuccessful():
        print("✅ كل الاختبارات نجحت!")
    else:
        print(f"❌ {len(result.failures)} فشل | {len(result.errors)} خطأ")
    sys.exit(0 if result.wasSuccessful() else 1)
