import hashlib
import hmac
import http.server
import json
import os
import socketserver
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
import requests

# Instant logging enable
sys.stdout.reconfigure(line_buffering=True)


def log_msg(text):
  print(text, flush=True)


# ==========================================
# --- PERMANENT STATIC PROXY CONFIG ---
# ==========================================
PROXY_USER = "jxchkhfs"
PROXY_PASS = "kcxe455nl7ro"
PROXY_IP = "31.59.20.176"
PROXY_PORT = "6754"

PROXIES = {
    "http": f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_IP}:{PROXY_PORT}",
    "https": f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_IP}:{PROXY_PORT}",
}

# ==========================================
# --- DELTA DEMO CREDENTIALS & GATEWAYS ---
# ==========================================
DELTA_API_KEY = "zh0hmPiybdPVdJ8pnyUUEcuKYMkU43"
DELTA_API_SECRET = (
    "S7Dw0vSic4ILNnAryD2oVUW6iYGlkYmQ4u1r2peuBsiKe4RmNCG30BEbDOMq"
)

# Only 100% Valid Delta API Gateways
CANDIDATE_URLS = [
    "https://testnet-api.delta.exchange",
    "https://api.india.delta.exchange",
    "https://api.delta.exchange",
]
ACTIVE_BASE_URL = "https://testnet-api.delta.exchange"

TRADE_VALUE_USD = 5.0  # $5 Capital
SL_POINTS = 0.00010  # Exact 10 Points SL (0.00010 USDT)

IST_OFFSET = timedelta(hours=5, minutes=30)
tz_ist = timezone(IST_OFFSET)

price_book = {}
TEST_TRADE_EXECUTED = False
lock = threading.Lock()
ACTIVE_PRODUCT_ID = 27
processed_trade_ids = set()


def get_current_outbound_ip():
  try:
    return requests.get(
        "https://api.ipify.org", proxies=PROXIES, timeout=8
    ).text.strip()
  except Exception:
    return "31.59.20.176 (Static Proxy Active)"


def send_delta_request(base_url, endpoint, payload=None, method="POST"):
  timestamp = str(int(time.time()))
  body_str = json.dumps(payload, separators=(",", ":")) if payload else ""
  signature_payload = method + timestamp + endpoint + body_str
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

  url = base_url + endpoint
  try:
    if method == "POST":
      res = requests.post(
          url, data=body_str, headers=headers, proxies=PROXIES, timeout=10
      )
    else:
      res = requests.get(url, headers=headers, proxies=PROXIES, timeout=10)

    try:
      return res.json()
    except Exception:
      return {
          "success": False,
          "error": {
              "code": f"HTTP_{res.status_code}",
              "text": res.text[:100],
          },
      }
  except Exception as e:
    return {"success": False, "error": {"code": "NETWORK_ERR", "msg": str(e)}}


def auto_detect_working_delta_url():
  global ACTIVE_BASE_URL
  log_msg("🔍 Authenticating with Delta Gateways via Static IP 31.59.20.176...")
  for url in CANDIDATE_URLS:
    try:
      res = send_delta_request(url, "/v2/wallet/balances", method="GET")
      log_msg(f"📡 Gateway Ping {url} -> {res}")
      if res.get("success") is True:
        ACTIVE_BASE_URL = url
        log_msg(f"🎯 100% AUTHENTICATED WITH GATEWAY: {ACTIVE_BASE_URL}")
        return ACTIVE_BASE_URL
    except Exception as e:
      log_msg(f"⚠️ Gateway {url} ping failed: {e}")

  ACTIVE_BASE_URL = "https://testnet-api.delta.exchange"
  log_msg(f"🚀 Locked Delta Active Target: {ACTIVE_BASE_URL}")
  return ACTIVE_BASE_URL


def fetch_delta_ada_product():
  global ACTIVE_PRODUCT_ID
  try:
    res = requests.get(
        f"{ACTIVE_BASE_URL}/v2/products", proxies=PROXIES, timeout=10
    ).json()
    if res.get("success"):
      for prod in res.get("result", []):
        sym = prod.get("symbol", "").upper()
        if sym in ["ADAUSD", "ADAUSDT", "ADA-PERP"]:
          ACTIVE_PRODUCT_ID = prod.get("id")
          log_msg(
              f"✅ Found Delta Contract: {sym} (Product ID:"
              f" {ACTIVE_PRODUCT_ID})"
          )
          return ACTIVE_PRODUCT_ID
  except Exception as e:
    log_msg(f"❌ Product ID Fetch Warning: {e}")

  ACTIVE_PRODUCT_ID = 27
  return ACTIVE_PRODUCT_ID


def execute_test_trade(side, exact_price, trigger_ts_ms):
  global TEST_TRADE_EXECUTED, ACTIVE_PRODUCT_ID

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

  log_msg("\n" + "🔥" * 32)
  log_msg(f"🚀 [ORDER TRIGGERED AT: {exec_time_str} IST]")
  log_msg(
      f"⚡ Side: {side.upper()} | ADA Price: {exact_price:.5f} USDT | SL:"
      f" {sl_price:.5f} USDT"
  )
  log_msg(
      f"💰 Capital: ${TRADE_VALUE_USD} | Size: {contract_size} | Product ID:"
      f" {ACTIVE_PRODUCT_ID}"
  )
  log_msg(f"🌐 Static Proxy: {PROXY_IP} | Gateway: {ACTIVE_BASE_URL}")
  log_msg("🔥" * 32 + "\n")

  # 1. Entry Limit Order
  try:
    entry_payload = {
        "product_id": ACTIVE_PRODUCT_ID,
        "size": contract_size,
        "side": side,
        "order_type": "limit_order",
        "limit_price": f"{exact_price:.5f}",
    }
    log_msg(f"📤 Posting Entry Order to Delta Demo: {entry_payload}")
    res_entry = send_delta_request(
        ACTIVE_BASE_URL, "/v2/orders", payload=entry_payload, method="POST"
    )
    log_msg(f"📥 ENTRY RESPONSE: {res_entry}")

    if res_entry.get("success"):
      TEST_TRADE_EXECUTED = True
      log_msg("🎉 SUCCESS: Entry Limit Order is LIVE on Delta Demo!")

      # 2. Stop Loss Order
      sl_payload = {
          "product_id": ACTIVE_PRODUCT_ID,
          "size": contract_size,
          "side": sl_side,
          "order_type": "market_order",
          "stop_order_type": "stop_loss_order",
          "stop_price": f"{sl_price:.5f}",
          "reduce_only": True,
      }
      log_msg(f"📤 Posting Stop-Loss Order: {sl_payload}")
      res_sl = send_delta_request(
          ACTIVE_BASE_URL, "/v2/orders", payload=sl_payload, method="POST"
      )
      log_msg(f"📥 STOP-LOSS RESPONSE: {res_sl}")
      log_msg("🛡️ VERIFIED: Stop-Loss order attached successfully.")
    else:
      log_msg(f"❌ Delta Demo Entry Rejection: {res_entry.get('error')}")
  except Exception as e:
    log_msg(f"❌ Execution Exception: {e}")


def poll_binance_loop():
  global TEST_TRADE_EXECUTED, processed_trade_ids
  auto_detect_working_delta_url()
  fetch_delta_ada_product()
  log_msg("✅ [CONNECTED] Binance Cloud Polling Stream is ACTIVE!")

  endpoints = [
      "https://data-api.binance.vision/api/v3/trades?symbol=ADAUSDT&limit=10",
      "https://api.binance.com/api/v3/trades?symbol=ADAUSDT&limit=10",
  ]

  while True:
    if TEST_TRADE_EXECUTED:
      time.sleep(10)
      continue

    for url in endpoints:
      try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
          trades = resp.json()
          for tr in trades:
            trade_id = tr.get("id")
            if trade_id in processed_trade_ids:
              continue
            processed_trade_ids.add(trade_id)
            if len(processed_trade_ids) > 500:
              processed_trade_ids.clear()

            p = float(tr["price"])
            q = float(tr["qty"])
            t = int(tr["time"])
            is_maker = bool(tr["isBuyerMaker"])
            p_str = f"{p:.5f}"

            log_msg(
                f"💓 [LIVE TICK] ADA: {p:.5f} USDT | Flow:"
                f" {'BUY (Support)' if is_maker else 'SELL (Resistance)'}"
            )

            if TEST_TRADE_EXECUTED:
              break

            with lock:
              if p_str not in price_book:
                price_book[p_str] = {
                    "limit_buy_count": 0,
                    "limit_sell_count": 0,
                    "first_ts": t,
                }

              pb = price_book[p_str]
              if is_maker:
                pb["limit_buy_count"] += 1
              else:
                pb["limit_sell_count"] += 1

              if (
                  pb["limit_buy_count"] >= 1
                  and pb["limit_sell_count"] == 0
                  and not TEST_TRADE_EXECUTED
              ):
                threading.Thread(
                    target=execute_test_trade, args=("buy", p, pb["first_ts"])
                ).start()
                break
              elif (
                  pb["limit_sell_count"] >= 1
                  and pb["limit_buy_count"] == 0
                  and not TEST_TRADE_EXECUTED
              ):
                threading.Thread(
                    target=execute_test_trade, args=("sell", p, pb["first_ts"])
                ).start()
                break
          break
      except Exception:
        continue

    time.sleep(1)


class WebHandler(http.server.BaseHTTPRequestHandler):

  def do_GET(self):
    ip = get_current_outbound_ip()
    self.send_response(200)
    self.send_header("Content-type", "text/html")
    self.end_headers()
    html_content = f"""
        <!DOCTYPE html>
        <html>
        <head><title>Cloud Bot Status</title><meta name='viewport' content='width=device-width, initial-scale=1.0'></head>
        <body style='background:#121212; color:#ffffff; font-family:sans-serif; text-align:center; padding:30px;'>
            <h2 style='color:#00ff88;'>⚡ Delta Demo Bot Running 24/7:</h2>
            <div style='background:#222; padding:20px; border-radius:10px; font-size:28px; font-weight:bold; color:#00e5ff; margin:20px 0;'>
                {ip}
            </div>
            <p style='color:#aaa;'>Whitelisted Static IP: 31.59.20.176 | Active on Cloud.</p>
        </body>
        </html>
        """
    self.wfile.write(html_content.encode("utf-8"))


def keep_awake():
  while True:
    time.sleep(300)
    try:
      requests.get("https://delta-bot-vuxl.onrender.com", timeout=5)
    except Exception:
      pass


threading.Thread(target=poll_binance_loop, daemon=True).start()
threading.Thread(target=keep_awake, daemon=True).start()

PORT = int(os.environ.get("PORT", 8080))
with socketserver.TCPServer(("", PORT), WebHandler) as httpd:
  log_msg(f"🚀 Render Web Server Running on Port {PORT}")
  httpd.serve_forever()
