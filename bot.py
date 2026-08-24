from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import http.server
import json
import os
import socketserver
import ssl
import threading
import time
import requests
import websocket

# --- CONFIG & DELTA DEMO CREDENTIALS ---
DELTA_API_KEY = "nCXz95sPzB7UrjgOBMnFk62JZmbOOJ"
DELTA_API_SECRET = (
    "ht1GmKWtGJqrqvtynBbbcWfsF0xC7R0wsi0xbz8bJJH4eqDOqauuQEbLCmyD"
)
DELTA_BASE_URL = "https://testnet-api.delta.exchange"

TRADE_VALUE_USD = 5.0  # $5 USD Capital
SL_POINTS = 0.00010  # Exact 10 Points SL
SYMBOL = "adausdt"
WS_URL = f"wss://stream.binance.com:9443/ws/{SYMBOL}@aggTrade"

# IST Timezone
IST_OFFSET = timedelta(hours=5, minutes=30)
tz_ist = timezone(IST_OFFSET)

price_book = {}
TEST_TRADE_EXECUTED = False
lock = threading.Lock()
ACTIVE_PRODUCT_ID = None
LAST_LOG_TIME = 0


def fetch_delta_ada_product():
  global ACTIVE_PRODUCT_ID
  try:
    print("🔍 Fetching ADA Product ID from Delta Testnet...")
    res = requests.get(f"{DELTA_BASE_URL}/v2/products", timeout=10).json()
    if res.get("success"):
      for prod in res.get("result", []):
        sym = prod.get("symbol", "").upper()
        if (
            "ADA" in sym
            and "USD" in sym
            and prod.get("contract_type") == "perpetual_futures"
        ):
          ACTIVE_PRODUCT_ID = prod.get("id")
          print(f"✅ Found Delta Contract: {sym} | Product ID: {ACTIVE_PRODUCT_ID}")
          return ACTIVE_PRODUCT_ID
      # Fallback agar contract_type match na ho
      for prod in res.get("result", []):
        sym = prod.get("symbol", "").upper()
        if sym in ["ADAUSD", "ADAUSDT", "ADA-PERP"]:
          ACTIVE_PRODUCT_ID = prod.get("id")
          print(f"✅ Found Delta Contract: {sym} | Product ID: {ACTIVE_PRODUCT_ID}")
          return ACTIVE_PRODUCT_ID
  except Exception as e:
    print(f"❌ Product ID Fetch Error: {e}")

  ACTIVE_PRODUCT_ID = 27  # Default Fallback
  return ACTIVE_PRODUCT_ID


def send_delta_order(endpoint, payload):
  timestamp = str(int(time.time()))
  body_str = json.dumps(payload, separators=(",", ":"))
  signature_payload = "POST" + timestamp + endpoint + body_str
  signature = hmac.new(
      DELTA_API_SECRET.encode("utf-8"),
      signature_payload.encode("utf-8"),
      hashlib.sha256,
  ).hexdigest()

  headers = {
      "api-key": DELTA_API_KEY,
      "signature": signature,
      "timestamp": timestamp,
      "Content-Type": "application/json",
      "User-Agent": "python-trade-bot",
  }

  url = DELTA_BASE_URL + endpoint
  return requests.post(url, data=body_str, headers=headers, timeout=8)


def execute_test_trade(side, exact_price, trigger_ts_ms):
  global TEST_TRADE_EXECUTED, ACTIVE_PRODUCT_ID
  if not ACTIVE_PRODUCT_ID:
    fetch_delta_ada_product()

  contract_size = max(1, int(TRADE_VALUE_USD / exact_price))

  exec_time_str = datetime.fromtimestamp(
      trigger_ts_ms / 1000, tz=tz_ist
  ).strftime("%H:%M:%S.%f")[:-3]

  if side == "buy":
    sl_price = round(exact_price - SL_POINTS, 5)
    sl_side = "sell"
  else:
    sl_price = round(exact_price + SL_POINTS, 5)
    sl_side = "buy"

  print("\n" + "🔥" * 30)
  print(f"🚀 [LIVE TEST TRIGGERED AT: {exec_time_str} IST]")
  print(
      f"⚡ Target Side: {side.upper()} | Exact Price: {exact_price:.5f} USDT |"
      f" Stop Loss: {sl_price:.5f} USDT"
  )
  print(
      f"💰 Order Capital: ${TRADE_VALUE_USD} | Contract Size: {contract_size} |"
      f" Product ID: {ACTIVE_PRODUCT_ID}"
  )
  print("🔥" * 30 + "\n")

  # 1. SEND ENTRY ORDER
  try:
    entry_payload = {
        "product_id": ACTIVE_PRODUCT_ID,
        "size": contract_size,
        "side": side,
        "order_type": "limit_order",
        "limit_price": f"{exact_price:.5f}",
    }
    print(f"📤 Sending Entry Order to Delta: {entry_payload}")
    res_entry = send_delta_order("/v2/orders", entry_payload).json()
    print(f"📥 ENTRY ORDER RESPONSE: {json.dumps(res_entry, indent=2)}")

    if res_entry.get("success"):
      TEST_TRADE_EXECUTED = True
      print("🎉 SUCCESS: Entry Limit Order placed successfully on Delta Demo!")

      # 2. SEND 10-POINT STOP LOSS
      sl_payload = {
          "product_id": ACTIVE_PRODUCT_ID,
          "size": contract_size,
          "side": sl_side,
          "order_type": "market_order",
          "stop_order_type": "stop_loss_order",
          "stop_price": f"{sl_price:.5f}",
          "reduce_only": True,
      }
      print(f"📤 Sending Stop-Loss Order: {sl_payload}")
      res_sl = send_delta_order("/v2/orders", sl_payload).json()
      print(f"📥 STOP LOSS RESPONSE: {json.dumps(res_sl, indent=2)}")

      if res_sl.get("success"):
        print(
            "🛡️ 100% VERIFIED: Stop-Loss attached perfectly to the live"
            " position!"
        )
      else:
        print(
            "⚠️ Stop-Loss Placement Warning:"
            f" {res_sl.get('error', 'Unknown response')}"
        )
    else:
      print(
          f"❌ Entry Rejected by Delta: {res_entry.get('error', 'Unknown Error')}"
      )
  except Exception as e:
    print(f"❌ Execution Exception: {e}")


def on_message(ws, message):
  global TEST_TRADE_EXECUTED, LAST_LOG_TIME
  msg = json.loads(message)

  p = float(msg["p"])
  q = float(msg["q"])
  t = int(msg["T"])
  is_maker = bool(msg["m"])
  p_str = f"{p:.5f}"

  # Har 3 second me live heartbeat log
  now_sec = time.time()
  if now_sec - LAST_LOG_TIME >= 3:
    print(
        f"💓 [LIVE FEED ACTIVE] ADA Price: {p:.5f} USDT | Maker:"
        f" {'BUY (Whale Support)' if is_maker else 'SELL (Whale Resistance)'}"
    )
    LAST_LOG_TIME = now_sec

  if TEST_TRADE_EXECUTED:
    return

  with lock:
    if p_str not in price_book:
      price_book[p_str] = {
          "limit_buy_count": 0,
          "limit_sell_count": 0,
          "total_usdt": 0.0,
          "first_ts": t,
      }

    pb = price_book[p_str]
    pb["total_usdt"] += p * q

    if is_maker:
      pb["limit_buy_count"] += 1
    else:
      pb["limit_sell_count"] += 1

    # Whale Limit BUY Trigger
    if (
        pb["limit_buy_count"] >= 1
        and pb["limit_sell_count"] == 0
        and not TEST_TRADE_EXECUTED
    ):
      threading.Thread(
          target=execute_test_trade, args=("buy", p, pb["first_ts"])
      ).start()

    # Whale Limit SELL Trigger
    elif (
        pb["limit_sell_count"] >= 1
        and pb["limit_buy_count"] == 0
        and not TEST_TRADE_EXECUTED
    ):
      threading.Thread(
          target=execute_test_trade, args=("sell", p, pb["first_ts"])
      ).start()


def start_ws():
  fetch_delta_ada_product()
  while True:
    try:
      print("🔌 Connecting to Binance WebSocket Stream (adausdt@aggTrade)...")
      ws = websocket.WebSocketApp(WS_URL, on_message=on_message)
      ws.run_forever(
          sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False}
      )
    except Exception as e:
      print(f"WebSocket Reconnecting in 2s... Error: {e}")
      time.sleep(2)


def keep_awake():
  while True:
    time.sleep(300)
    try:
      requests.get("https://delta-bot-vuxl.onrender.com", timeout=5)
    except Exception:
      pass


threading.Thread(target=start_ws, daemon=True).start()
threading.Thread(target=keep_awake, daemon=True).start()

PORT = int(os.environ.get("PORT", 8080))
Handler = http.server.SimpleHTTPRequestHandler
with socketserver.TCPServer(("", PORT), Handler) as httpd:
  print(f"🚀 Render Web Server Live on Port {PORT}")
  httpd.serve_forever()
    
