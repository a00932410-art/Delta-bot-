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

# --- CONFIG & CREDENTIALS ---
DELTA_API_KEY = "nCXz95sPzB7UrjgOBMnFk62JZmbOOJ"
DELTA_API_SECRET = (
    "ht1GmKWtGJqrqvtynBbbcWfsF0xC7R0wsi0xbz8bJJH4eqDOqauuQEbLCmyD"
)
DELTA_BASE_URL = "https://testnet-api.delta.exchange"

PRODUCT_ID = 27  # ADAUSDT
TRADE_VALUE_USD = 5.0  # $5 Capital Trade
SL_POINTS = 0.00010  # Exact 10 Points SL (0.00010 USDT)
SYMBOL = "adausdt"
WS_URL = f"wss://stream.binance.com:9443/ws/{SYMBOL}@aggTrade"

# Timezone (05:30:00 AM to 23:30:00 PM IST)
IST_OFFSET = timedelta(hours=5, minutes=30)
tz_ist = timezone(IST_OFFSET)

price_book = {}
TRADE_EXECUTED_TODAY = False
lock = threading.Lock()


def get_window_timestamps():
  now = datetime.now(tz=tz_ist)
  start_dt = datetime(now.year, now.month, now.day, 5, 30, 0, tzinfo=tz_ist)
  end_dt = datetime(now.year, now.month, now.day, 23, 30, 0, tzinfo=tz_ist)
  return int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)


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


def execute_algo_trade(side, exact_price, trigger_ts_ms):
  global TRADE_EXECUTED_TODAY
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

  print("\n" + "=" * 65)
  print(f"🎯 [EXACT TIME ENTRY TRIGGERED: {exec_time_str} IST]")
  print(
      f"⚡ Side: {side.upper()} | Price: {exact_price:.5f} USDT | SL:"
      f" {sl_price:.5f} USDT"
  )
  print(
      f"💰 Capital: ${TRADE_VALUE_USD} | Size: {contract_size} ADA Contracts"
  )
  print("=" * 65)

  # Entry Order Placement
  res_entry = send_delta_request("/v2/orders", {
      "product_id": PRODUCT_ID,
      "size": contract_size,
      "side": side,
      "order_type": "limit_order",
      "limit_price": f"{exact_price:.5f}",
  }).json()
  print(f"📌 ENTRY ORDER RESPONSE: {res_entry}")

  # 10-Point Stop Loss Order Placement
  res_sl = send_delta_request("/v2/orders", {
      "product_id": PRODUCT_ID,
      "size": contract_size,
      "side": sl_side,
      "order_type": "market_order",
      "stop_order_type": "stop_loss_order",
      "stop_price": f"{sl_price:.5f}",
  }).json()
  print(f"🛡️ 10-POINT STOP LOSS RESPONSE: {res_sl}")
  print("🔒 DAILY LOCK: Today's single trade quota completed successfully.")


def on_message(ws, message):
  global TRADE_EXECUTED_TODAY
  msg = json.loads(message)

  p = float(msg["p"])
  q = float(msg["q"])
  t = int(msg["T"])
  is_maker = bool(msg["m"])
  p_str = f"{p:.5f}"

  start_ts, end_ts = get_window_timestamps()

  if t < start_ts or t > end_ts or TRADE_EXECUTED_TODAY:
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

    if is_maker:  # Maker is Buyer (Whale Limit BUY)
      pb["limit_buy_count"] += 1
    else:  # Maker is Seller (Whale Limit SELL)
      pb["limit_sell_count"] += 1

    # Whale Limit BUY Trigger (>= 1 trade & 0 opposite trades)
    if (
        pb["limit_buy_count"] >= 1
        and pb["limit_sell_count"] == 0
        and not TRADE_EXECUTED_TODAY
    ):
      TRADE_EXECUTED_TODAY = True
      threading.Thread(
          target=execute_algo_trade, args=("buy", p, pb["first_ts"])
      ).start()

    # Whale Limit SELL Trigger (>= 1 trade & 0 opposite trades)
    elif (
        pb["limit_sell_count"] >= 1
        and pb["limit_buy_count"] == 0
        and not TRADE_EXECUTED_TODAY
    ):
      TRADE_EXECUTED_TODAY = True
      threading.Thread(
          target=execute_algo_trade, args=("sell", p, pb["first_ts"])
      ).start()


def start_ws():
  while True:
    try:
      ws = websocket.WebSocketApp(WS_URL, on_message=on_message)
      ws.run_forever(
          sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False}
      )
    except Exception:
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
  print(f"Server live on port {PORT}")
  httpd.serve_forever()
    
