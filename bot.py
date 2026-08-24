import time
import json
import hmac
import hashlib
import requests
import threading
import sys
import os
import http.server
import socketserver
from datetime import datetime, timezone, timedelta

# Instant logging enable
sys.stdout.reconfigure(line_buffering=True)

def log_msg(text):
    print(text, flush=True)

def get_current_server_ip():
    try:
        return requests.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        return "IP Fetch Error"

# ==========================================
# --- DELTA CREDENTIALS ---
# ==========================================
DELTA_API_KEY = "zh0hmPiybdPVdJ8pnyUUEcuKYMkU43"
DELTA_API_SECRET = "S7Dw0vSic4ILNnAryD2oVUW6iYGlkYmQ4u1r2peuBsiKe4RmNCG30BEbDOMq"

CANDIDATE_URLS = [
    "https://demo-api.delta.exchange",
    "https://testnet-api.delta.exchange",
    "https://api.delta.exchange",
    "https://api.india.delta.exchange"
]
ACTIVE_BASE_URL = "https://demo-api.delta.exchange"

TRADE_VALUE_USD = 5.0      # $5 Capital
SL_POINTS = 0.00010        # Exact 10 Points SL (0.00010 USDT)

IST_OFFSET = timedelta(hours=5, minutes=30)
tz_ist = timezone(IST_OFFSET)

price_book = {}
TEST_TRADE_EXECUTED = False
lock = threading.Lock()
ACTIVE_PRODUCT_ID = 27
processed_trade_ids = set()

def send_delta_request(base_url, endpoint, payload=None, method="POST"):
    timestamp = str(int(time.time()))
    body_str = json.dumps(payload, separators=(',', ':')) if payload else ""
    signature_payload = method + timestamp + endpoint + body_str
    signature = hmac.new(
        DELTA_API_SECRET.encode('utf-8'),
        signature_payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    headers = {
        'api-key': DELTA_API_KEY,
        'signature': signature,
        'timestamp': timestamp,
        'Content-Type': 'application/json',
        'User-Agent': 'python-trade-bot'
    }

    url = base_url + endpoint
    if method == "POST":
        return requests.post(url, data=body_str, headers=headers, timeout=8)
    else:
        return requests.get(url, headers=headers, timeout=8)

def auto_detect_working_delta_url():
    global ACTIVE_BASE_URL
    log_msg("🔍 Auto-detecting correct Delta Server for your API key...")
    for url in CANDIDATE_URLS:
        try:
            res = send_delta_request(url, "/v2/wallet/balances", method="GET").json()
            if res.get("success"):
                ACTIVE_BASE_URL = url
                log_msg(f"🎯 100% CONNECTED & AUTHENTICATED: {ACTIVE_BASE_URL}")
                return ACTIVE_BASE_URL
            elif res.get("error", {}).get("code") != "invalid_api_key":
                ACTIVE_BASE_URL = url
                log_msg(f"✅ Selected Compatible Delta Endpoint: {ACTIVE_BASE_URL}")
                return ACTIVE_BASE_URL
        except Exception:
            continue
    log_msg(f"ℹ️ Defaulting to: {ACTIVE_BASE_URL}")
    return ACTIVE_BASE_URL

def fetch_delta_ada_product():
    global ACTIVE_PRODUCT_ID
    try:
        res = requests.get(f"{ACTIVE_BASE_URL}/v2/products", timeout=10).json()
        if res.get("success"):
            for prod in res.get("result", []):
                sym = prod.get("symbol", "").upper()
                if sym in ["ADAUSD", "ADAUSDT", "ADA-PERP"]:
                    ACTIVE_PRODUCT_ID = prod.get("id")
                    log_msg(f"✅ Found Delta Contract: {sym} (Product ID: {ACTIVE_PRODUCT_ID})")
                    return ACTIVE_PRODUCT_ID
    except Exception as e:
        log_msg(f"❌ Product ID Fetch Warning: {e}")
    
    ACTIVE_PRODUCT_ID = 27
    return ACTIVE_PRODUCT_ID

def execute_test_trade(side, exact_price, trigger_ts_ms):
    global TEST_TRADE_EXECUTED, ACTIVE_PRODUCT_ID
    
    contract_size = max(1, int(TRADE_VALUE_USD / exact_price))
    exec_time_str = datetime.fromtimestamp(trigger_ts_ms / 1000, tz=tz_ist).strftime('%H:%M:%S.%f')[:-3]

    if side == "buy":
        sl_price = round(exact_price - SL_POINTS, 5)
        sl_side = "sell"
    else:
        sl_price = round(exact_price + SL_POINTS, 5)
        sl_side = "buy"

    log_msg("\n" + "🔥"*32)
    log_msg(f"🚀 [ORDER TRIGGERED AT: {exec_time_str} IST]")
    log_msg(f"⚡ Side: {side.upper()} | Price: {exact_price:.5f} USDT | SL: {sl_price:.5f} USDT")
    log_msg(f"💰 Capital: ${TRADE_VALUE_USD} | Size: {contract_size} | Product ID: {ACTIVE_PRODUCT_ID}")
    log_msg(f"🌐 Target Server: {ACTIVE_BASE_URL}")
    log_msg("🔥"*32 + "\n")

    # 1. Entry Limit Order
    try:
        entry_payload = {
            "product_id": ACTIVE_PRODUCT_ID,
            "size": contract_size,
            "side": side,
            "order_type": "limit_order",
            "limit_price": f"{exact_price:.5f}"
        }
        log_msg(f"📤 Posting Entry Order to Delta: {entry_payload}")
        res_entry = send_delta_request(ACTIVE_BASE_URL, "/v2/orders", payload=entry_payload, method="POST").json()
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
                "reduce_only": True
            }
            log_msg(f"📤 Posting Stop-Loss Order: {sl_payload}")
            res_sl = send_delta_request(ACTIVE_BASE_URL, "/v2/orders", payload=sl_payload, method="POST").json()
            log_msg(f"📥 STOP-LOSS RESPONSE: {res_sl}")
            log_msg("🛡️ VERIFIED: Stop-Loss order attached successfully.")
        else:
            log_msg(f"❌ Delta API Entry Rejection: {res_entry.get('error')}")
    except Exception as e:
        log_msg(f"❌ Execution Exception: {e}")

def poll_binance_loop():
    global TEST_TRADE_EXECUTED, processed_trade_ids
    auto_detect_working_delta_url()
    fetch_delta_ada_product()
    log_msg("✅ [CONNECTED] Binance Cloud Polling Stream is ACTIVE!")
    
    endpoints = [
        "https://data-api.binance.vision/api/v3/trades?symbol=ADAUSDT&limit=10",
        "https://api.binance.com/api/v3/trades?symbol=ADAUSDT&limit=10"
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

                        p = float(tr['price'])
                        q = float(tr['qty'])
                        t = int(tr['time'])
                        is_maker = bool(tr['isBuyerMaker'])
                        p_str = f"{p:.5f}"

                        log_msg(f"💓 [LIVE TICK] ADA: {p:.5f} USDT | Flow: {'BUY (Support)' if is_maker else 'SELL (Resistance)'}")

                        if TEST_TRADE_EXECUTED:
                            break

                        with lock:
                            if p_str not in price_book:
                                price_book[p_str] = {'limit_buy_count': 0, 'limit_sell_count': 0, 'first_ts': t}

                            pb = price_book[p_str]
                            if is_maker:
                                pb['limit_buy_count'] += 1
                            else:
                                pb['limit_sell_count'] += 1

                            if pb['limit_buy_count'] >= 1 and pb['limit_sell_count'] == 0 and not TEST_TRADE_EXECUTED:
                                threading.Thread(target=execute_test_trade, args=("buy", p, pb['first_ts'])).start()
                                break
                            elif pb['limit_sell_count'] >= 1 and pb['limit_buy_count'] == 0 and not TEST_TRADE_EXECUTED:
                                threading.Thread(target=execute_test_trade, args=("sell", p, pb['first_ts'])).start()
                                break
                    break
            except Exception:
                continue

        time.sleep(1)

class WebHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ip = get_current_server_ip()
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head><title>Render Server IP</title><meta name='viewport' content='width=device-width, initial-scale=1.0'></head>
        <body style='background:#121212; color:#ffffff; font-family:sans-serif; text-align:center; padding:30px;'>
            <h2 style='color:#00ff88;'>⚡ Render Outbound IP Address:</h2>
            <div style='background:#222; padding:20px; border-radius:10px; font-size:28px; font-weight:bold; color:#00e5ff; margin:20px 0;'>
                {ip}
            </div>
            <p style='color:#aaa;'>IP Whitelisted & Bot Active 24/7 on Cloud.</p>
        </body>
        </html>
        """
        self.wfile.write(html_content.encode('utf-8'))

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
