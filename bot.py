import hashlib
import hmac
import http.server
import json
import os
import socketserver
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
import requests
import websocket

# --- CONFIG & DELTA DEMO CREDENTIALS ---
DELTA_API_KEY = "nCXz95sPzB7UrjgOBMnFk62JZmbOOJ"
DELTA_API_SECRET = (
    "ht1GmKWtGJqrqvtynBbbcWfsF0xC7R0wsi0xbz8bJJH4eqDOqauuQEbLCmyD"
)
DELTA_BASE_URL = "https://testnet-api.delta.exchange"

PRODUCT_ID = 27  # ADAUSDT
TRADE_VALUE_USD = 5.0
MIN_USDT_TRIGGER = 7000.0  # $7,000+ Whale Order
SL_POINTS = 0.00100  # 10 Points SL
SYMBOL = "adausdt"
WS_URL = f"wss://stream.binance.com:9443/ws/{SYMBOL}@aggTrade"

IST_OFFSET = timedelta(hours=5, minutes=30)
tz_ist = timezone(IST_OFFSET)
START_TIME_IST = datetime(2026, 8, 23, 5, 30, 0, tzinfo=tz_ist)
START_TS_MS = int(START_TIME_IST.timestamp() * 1000)

price_data = {}
lock = threading.Lock()
TRADE_EXECUTED_TODAY = False


def send_delta_request(endpoint, payload):
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
  }
  return requests.post(
      DELTA_BASE_URL + endpoint, data=body_str, headers=headers, timeout=5
  )


def execute_algo_trade(side, exact_price):
  global TRADE_EXECUTED_TODAY
  contract_size = max(1, int(TRADE_VALUE_USD / exact_price))
  sl_price = (
      round(exact_price - SL_POINTS, 5)
      if side == "buy"
      else round(exact_price + SL_POINTS, 5)
  )
  sl_side = "sell" if side == "buy" else "buy"

  print(
      f"\n🎯 [TRIGGERED] {side.upper()} @ {exact_price:.5f} USDT | SL:"
      f" {sl_price:.5f}"
  )

  # Entry Order
  res_entry = send_delta_request("/v2/orders", {
      "product_id": PRODUCT_ID,
      "size": contract_size,
      "side": side,
      "order_type": "limit_order",
      "limit_price": f"{exact_price:.5f}",
  }).json()
  print(f"📌 LIMIT ENTRY RESULT: {res_entry}")

  # 10-Point SL Order
  res_sl = send_delta_request("/v2/orders", {
      "product_id": PRODUCT_ID,
      "size": contract_size,
      "side": sl_side,
      "order_type": "market_order",
      "stop_order_type": "stop_loss_order",
      "stop_price": f"{sl_price:.5f}",
  }).json()
  print(f"🛡️ 10-POINT SL RESULT: {res_sl}")
  print("🔒 LOCKED: 1 Trade Executed.")


def on_message(ws, message):
  global TRADE_EXECUTED_TODAY
  msg = json.loads(message)
  price, qty, t_ms, is_maker = (
      float(msg["p"]),
      float(msg["q"]),
      int(msg["T"]),
      bool(msg["m"]),
  )
  usdt_val = price * qty
  p_str = f"{price:.5f}"

  if t_ms < START_TS_MS or TRADE_EXECUTED_TODAY:
    return

  with lock:
    if p_str not in price_data:
      price_data[p_str] = {
          "buy_u": 0.0,
          "buy_c": 0,
          "sell_u": 0.0,
          "sell_c": 0,
      }
    d = price_data[p_str]

    if is_maker:
      d["buy_u"] += usdt_val
      d["buy_c"] += 1
    else:
      d["sell_u"] += usdt_val
      d["sell_c"] += 1

    if (
        d["buy_c"] > 0
        and d["sell_c"] == 0
        and d["buy_u"] >= MIN_USDT_TRIGGER
        and not TRADE_EXECUTED_TODAY
    ):
      TRADE_EXECUTED_TODAY = True
      threading.Thread(
          target=execute_algo_trade, args=("buy", price)
      ).start()
    elif (
        d["sell_c"] > 0
        and d["buy_c"] == 0
        and d["sell_u"] >= MIN_USDT_TRIGGER
        and not TRADE_EXECUTED_TODAY
    ):
      TRADE_EXECUTED_TODAY = True
      threading.Thread(
          target=execute_algo_trade, args=("sell", price)
      ).start()


def start_ws():
  ws = websocket.WebSocketApp(WS_URL, on_message=on_message)
  ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False})


# Render Port Listener for 24/7 Cloud Uptime
threading.Thread(target=start_ws, daemon=True).start()
PORT = int(os.environ.get("PORT", 8080))
Handler = http.server.SimpleHTTPRequestHandler
with socketserver.TCPServer(("", PORT), Handler) as httpd:
  print(f"Server live on port {PORT}")
  httpd.serve_forever()
  
