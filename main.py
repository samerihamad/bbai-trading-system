import os
import time
from alpaca.trading.client import TradingClient

print("🚀 Trading system is starting...", flush=True)

# قراءة المفاتيح من البيئة
API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
BASE_URL = os.getenv("ALPACA_BASE_URL")

if not API_KEY or not SECRET_KEY:
    print("❌ API keys not found!", flush=True)
    exit()

try:
    # إنشاء اتصال مع Alpaca (Paper)
    trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)

    account = trading_client.get_account()
    
    print("✅ Connected to Alpaca!", flush=True)
    print(f"Account Status: {account.status}", flush=True)
    print(f"Account Equity: ${account.equity}", flush=True)

except Exception as e:
    print(f"❌ Connection failed: {e}", flush=True)

while True:
    print("System is running...", flush=True)
    time.sleep(60)
