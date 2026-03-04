# =============================================================
# test_short.py — اختبار Short Selling عبر alpaca-py
# شغّل هذا الملف مرة واحدة فقط للتحقق من إمكانية Short
# سيحاول فتح Short على TSLA بكمية 1 سهم فقط
# وسيلغي الأمر فوراً بعد التأكد
# =============================================================

import os
from dotenv import load_dotenv
load_dotenv()

ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")

if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
    print("❌ لم يتم العثور على ALPACA_API_KEY أو ALPACA_SECRET_KEY في ملف .env")
    exit(1)

print("=" * 55)
print("اختبار Short Selling عبر alpaca-py")
print("=" * 55)

# ─────────────────────────────────────────
# 1. التحقق من نوع الحساب
# ─────────────────────────────────────────
try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    print("✅ alpaca-py مثبتة بنجاح")
except ImportError:
    print("❌ alpaca-py غير مثبتة — شغّل: pip install alpaca-py")
    exit(1)

client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)

# جلب معلومات الحساب
print("\n📋 معلومات الحساب:")
print("-" * 40)
try:
    account = client.get_account()
    print(f"  النوع            : {account.account_type if hasattr(account, 'account_type') else 'غير متاح'}")
    print(f"  الرصيد           : ${float(account.equity):,.2f}")
    print(f"  Shorting Enabled : {account.shorting_enabled}")
    print(f"  Status           : {account.status}")

    if not account.shorting_enabled:
        print("\n⛔ الحساب لا يدعم Short Selling")
        print("   السبب: shorting_enabled = False")
        print("   الحل : تواصل مع دعم Alpaca لتحويل الحساب إلى Margin")
        exit(1)

    print("\n✅ shorting_enabled = True — الحساب يدعم Short")

except Exception as e:
    print(f"❌ خطأ في جلب معلومات الحساب: {e}")
    exit(1)

# ─────────────────────────────────────────
# 2. إرسال أمر Short تجريبي (1 سهم TSLA)
# ─────────────────────────────────────────
print("\n📤 محاولة إرسال أمر Short تجريبي...")
print("   السهم   : TSLA")
print("   الكمية  : 1 سهم فقط")
print("   النوع   : Market Order")
print("-" * 40)

order_id = None
try:
    order_data = MarketOrderRequest(
        symbol="TSLA",
        qty=1,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )

    order = client.submit_order(order_data=order_data)
    order_id = str(order.id)

    print(f"✅ نجح الأمر! ID: {order_id[:8]}...")
    print(f"   Status : {order.status}")
    print(f"   Side   : {order.side}")
    print(f"   Qty    : {order.qty}")

except Exception as e:
    err = str(e)
    print(f"❌ فشل الأمر: {err}")

    if "not allowed to short" in err.lower():
        print("\n⛔ التشخيص: الحساب Cash — لا يسمح بـ Short حتى مع alpaca-py")
        print("   الحل الوحيد: تواصل مع Alpaca لتغيير نوع الحساب إلى Margin")
    elif "forbidden" in err.lower() or "403" in err:
        print("\n⛔ التشخيص: خطأ في الصلاحيات — تحقق من API Key")
    else:
        print(f"\n⚠️  خطأ غير متوقع — راجع الرسالة أعلاه")
    exit(1)

# ─────────────────────────────────────────
# 3. إلغاء الأمر فوراً
# ─────────────────────────────────────────
if order_id:
    print("\n🔄 إلغاء الأمر التجريبي فوراً...")
    try:
        client.cancel_order_by_id(order_id)
        print("✅ تم إلغاء الأمر بنجاح — لا توجد صفقة مفتوحة")
    except Exception as e:
        print(f"⚠️  لم يتم إلغاء الأمر تلقائياً: {e}")
        print(f"   يرجى إلغاؤه يدوياً من Alpaca Dashboard → Orders")

# ─────────────────────────────────────────
# النتيجة النهائية
# ─────────────────────────────────────────
print("\n" + "=" * 55)
print("✅ النتيجة: الحساب يدعم Short Selling عبر alpaca-py")
print("   النظام جاهز لتنفيذ أوامر SHORT عند ظهور الفرصة")
print("=" * 55)
