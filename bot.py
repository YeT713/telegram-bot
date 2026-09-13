import os
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


# ============================================================
# 基础配置
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "8289465171").strip()

BYBIT_URL = "https://api.bybit.com"
TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

PORT = int(os.getenv("PORT", "10000"))

# 每4小时同一币种最多发一次信号
SIGNAL_COOLDOWN = 4 * 60 * 60

# 扫描间隔
SCAN_INTERVAL = 60

# 监控币种
SYMBOLS = [
    # 核心45
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "TRXUSDT", "DOGEUSDT", "ADAUSDT", "HYPEUSDT", "ZECUSDT",
    "BCHUSDT", "LINKUSDT", "AVAXUSDT", "XLMUSDT", "SUIUSDT",
    "TONUSDT", "HBARUSDT", "SHIBUSDT", "LTCUSDT", "DOTUSDT",
    "UNIUSDT", "NEARUSDT", "ICPUSDT", "XMRUSDT", "APTUSDT",
    "TAOUSDT", "ATOMUSDT", "ARBUSDT", "CROUSDT", "VETUSDT",
    "FILUSDT", "ALGOUSDT", "RENDERUSDT", "INJUSDT", "OPUSDT",
    "IMXUSDT", "AAVEUSDT", "GRTUSDT", "SEIUSDT", "QNTUSDT",
    "THETAUSDT", "TIAUSDT", "MKRUSDT", "MNTUSDT", "PEPEUSDT",

    # 扩展10
    "WIFUSDT", "BONKUSDT", "FLOKIUSDT", "JUPUSDT", "ENAUSDT",
    "ONDOUSDT", "RUNEUSDT", "WLDUSDT", "STXUSDT", "CRVUSDT",

    # 扩展7
    "MAGMAUSDT", "FFUSDT", "LISKUSDT", "ARKUSDT",
    "EMBERUSDT", "PENGUUSDT", "JASMYUSDT",
]


# ============================================================
# 全局状态
# ============================================================

session = requests.Session()

last_signal_time = {}
state_lock = threading.Lock()

scanner_running = False
telegram_running = False

telegram_offset = 0


# ============================================================
# HTTP健康检查
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        body = b"Vegas Trading Bot is running."

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def start_web_server():
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthHandler)

        print(
            f"[WEB] HTTP health server started on port {PORT}",
            flush=True
        )

        server.serve_forever()

    except Exception as e:
        print(
            f"[WEB ERROR] {repr(e)}",
            flush=True
        )


# ============================================================
# Telegram
# ============================================================

def telegram_request(method, data=None, timeout=30):
    try:
        response = session.post(
            f"{TELEGRAM_URL}/{method}",
            data=data or {},
            timeout=timeout
        )

        return response.json()

    except Exception as e:
        print(
            f"[TELEGRAM ERROR] {method}: {repr(e)}",
            flush=True
        )

        return None


def send_message(text, chat_id=None):
    target = chat_id or CHAT_ID

    if not BOT_TOKEN:
        print(
            "[TELEGRAM ERROR] BOT_TOKEN is empty",
            flush=True
        )
        return False

    result = telegram_request(
        "sendMessage",
        {
            "chat_id": target,
            "text": text,
        },
        timeout=20
    )

    if not result:
        return False

    if not result.get("ok"):
        print(
            f"[TELEGRAM SEND ERROR] {result}",
            flush=True
        )
        return False

    return True


def telegram_startup_check():
    print("[TELEGRAM] Checking bot token...", flush=True)

    result = telegram_request(
        "getMe",
        timeout=20
    )

    if not result:
        print(
            "[TELEGRAM] getMe failed",
            flush=True
        )
        return False

    if not result.get("ok"):
        print(
            f"[TELEGRAM] getMe error: {result}",
            flush=True
        )
        return False

    bot = result["result"]

    print(
        f"[TELEGRAM] BOT OK: @{bot.get('username')}",
        flush=True
    )

    return True


def remove_webhook():
    print(
        "[TELEGRAM] Removing webhook...",
        flush=True
    )

    result = telegram_request(
        "deleteWebhook",
        {
            "drop_pending_updates": False
        },
        timeout=20
    )

    print(
        f"[TELEGRAM] deleteWebhook result: {result}",
        flush=True
    )


def handle_command(message):
    text = message.get("text", "").strip()

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", ""))

    username = chat.get("username", "")
    chat_type = chat.get("type", "")

    print(
        f"[TELEGRAM] Message received | "
        f"chat_id={chat_id} | "
        f"type={chat_type} | "
        f"user={username} | "
        f"text={text}",
        flush=True
    )

    # /start
    if text.startswith("/start"):

        reply = (
            "🤖 Vegas Trading Bot 已启动\n\n"
            "📊 策略：Vegas EMA 12 / 144 / 169 / 576 / 676\n"
            "⏱ 周期：1H 定方向 + 15M 找入场\n"
            "💰 数据源：Bybit\n"
            f"📈 监控币种：{len(SYMBOLS)} 个\n\n"
            "可用命令：\n"
            "/status\n"
            "/debug BTCUSDT"
        )

        send_message(
            reply,
            chat_id
        )

        return

    # /status
    if text.startswith("/status"):

        with state_lock:
            count = len(last_signal_time)

        reply = (
            "🤖 Vegas Trading Bot\n\n"
            "运行状态：🟢 正常\n"
            f"Telegram：{'🟢' if telegram_running else '🔴'}\n"
            f"策略扫描：{'🟢' if scanner_running else '🔴'}\n"
            f"监控币种：{len(SYMBOLS)}\n"
            f"最近信号记录：{count}\n\n"
            "策略：Vegas EMA 12/144/169/576/676\n"
            "方向：1H\n"
            "入场：15M"
        )

        send_message(
            reply,
            chat_id
        )

        return

    # /debug
    if text.startswith("/debug"):

        parts = text.split()

        if len(parts) < 2:
            send_message(
                "用法：/debug BTCUSDT",
                chat_id
            )
            return

        symbol = parts[1].upper()

        if not symbol.endswith("USDT"):
            symbol += "USDT"

        debug_symbol(
            symbol,
            chat_id
        )

        return


def telegram_polling():

    global telegram_offset
    global telegram_running

    if not BOT_TOKEN:
        print(
            "[TELEGRAM ERROR] BOT_TOKEN is empty!",
            flush=True
        )
        return

    if not telegram_startup_check():
        return

    remove_webhook()

    telegram_running = True

    print(
        "[TELEGRAM] Polling started successfully.",
        flush=True
    )

    while True:

        try:

            result = telegram_request(
                "getUpdates",
                {
                    "offset": telegram_offset,
                    "timeout": 20,
                    "allowed_updates": '["message"]'
                },
                timeout=30
            )

            if not result:
                time.sleep(3)
                continue

            if not result.get("ok"):

                print(
                    f"[TELEGRAM GETUPDATES ERROR] {result}",
                    flush=True
                )

                time.sleep(5)
                continue

            updates = result.get("result", [])

            for update in updates:

                telegram_offset = update["update_id"] + 1

                message = update.get("message")

                if not message:
                    continue

                handle_command(message)

        except Exception as e:

            print(
                f"[TELEGRAM LOOP ERROR] {repr(e)}",
                flush=True
            )

            time.sleep(5)


# ============================================================
# Bybit
# ============================================================

def bybit_kline(symbol, interval, limit=250):

    try:

        response = session.get(
            f"{BYBIT_URL}/v5/market/kline",
            params={
                "category": "linear",
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            },
            timeout=15
        )

        data = response.json()

        if data.get("retCode") != 0:

            print(
                f"[BYBIT] {symbol} {interval} "
                f"retCode={data.get('retCode')} "
                f"msg={data.get('retMsg')}",
                flush=True
            )

            return None

        rows = data.get("result", {}).get("list", [])

        if not rows:
            return None

        # Bybit 返回通常为最新 -> 最旧
        rows.reverse()

        candles = []

        for row in rows:

            candles.append({
                "time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
            })

        return candles

    except Exception as e:

        print(
            f"[BYBIT ERROR] {symbol} {interval}: {repr(e)}",
            flush=True
        )

        return None


def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(values[:period]) / period

    for price in values[period:]:

        result = (
            price - result
        ) * multiplier + result

    return result


def calculate_ema_series(values, period):

    if len(values) < period:
        return [None] * len(values)

    result = [None] * (period - 1)

    current = sum(values[:period]) / period

    result.append(current)

    multiplier = 2 / (period + 1)

    for price in values[period:]:

        current = (
            price - current
        ) * multiplier + current

        result.append(current)

    return result


# ============================================================
# 1H方向
# ============================================================

def get_1h_direction(candles):

    if not candles or len(candles) < 700:
        return "NEUTRAL"

    # 最后一根可能是未完成K线
    closed = candles[:-1]

    closes = [
        x["close"]
        for x in closed
    ]

    ema12 = ema(closes, 12)
    ema144 = ema(closes, 144)
    ema169 = ema(closes, 169)
    ema576 = ema(closes, 576)
    ema676 = ema(closes, 676)

    if None in (
        ema12,
        ema144,
        ema169,
        ema576,
        ema676
    ):
        return "NEUTRAL"

    close = closes[-1]

    bullish = (
        ema12 > ema144
        and ema144 > ema169
        and ema169 > ema576
        and ema576 > ema676
        and close > ema144
    )

    bearish = (
        ema12 < ema144
        and ema144 < ema169
        and ema169 < ema576
        and ema576 < ema676
        and close < ema144
    )

    if bullish:
        return "LONG"

    if bearish:
        return "SHORT"

    return "NEUTRAL"


# ============================================================
# Vegas信号
# ============================================================

def vegas_signal(symbol):

    h1 = bybit_kline(
        symbol,
        "60",
        750
    )

    if not h1:
        return None

    direction = get_1h_direction(h1)

    if direction == "NEUTRAL":
        return None

    m15 = bybit_kline(
        symbol,
        "15",
        300
    )

    if not m15 or len(m15) < 200:
        return None

    closes = [
        x["close"]
        for x in m15
    ]

    ema12 = calculate_ema_series(
        closes,
        12
    )

    ema144 = calculate_ema_series(
        closes,
        144
    )

    ema169 = calculate_ema_series(
        closes,
        169
    )

    # --------------------------------------------------------
    # B1
    # 深破后 EMA12 穿越 EMA144
    # 优先级最高
    # --------------------------------------------------------

    if len(m15) >= 5:

        prev = -2
        curr = -1

        if direction == "LONG":

            price_below = (
                m15[prev]["close"] < ema144[prev]
                and
                m15[prev]["close"] < ema169[prev]
            )

            ema12_below = (
                ema12[prev] < ema144[prev]
                and
                ema12[prev] < ema169[prev]
            )

            cross_up = (
                ema12[prev] <= ema144[prev]
                and
                ema12[curr] > ema144[curr]
            )

            if price_below and ema12_below and cross_up:

                return create_signal(
                    symbol,
                    "LONG",
                    m15,
                    curr
                )

        else:

            price_above = (
                m15[prev]["close"] > ema144[prev]
                and
                m15[prev]["close"] > ema169[prev]
            )

            ema12_above = (
                ema12[prev] > ema144[prev]
                and
                ema12[prev] > ema169[prev]
            )

            cross_down = (
                ema12[prev] >= ema144[prev]
                and
                ema12[curr] < ema144[curr]
            )

            if price_above and ema12_above and cross_down:

                return create_signal(
                    symbol,
                    "SHORT",
                    m15,
                    curr
                )

    # --------------------------------------------------------
    # B2
    # 浅破后重新站回
    # --------------------------------------------------------

    i = len(m15) - 2

    if i >= 2:

        prev = i - 1

        if direction == "LONG":

            broke = (
                m15[prev]["close"] < ema144[prev]
                and
                m15[prev]["close"] < ema169[prev]
            )

            ema12_stayed = (
                ema12[prev] >= ema144[prev]
                or
                ema12[prev] >= ema169[prev]
            )

            reclaim = (
                m15[i]["low"] > ema144[i]
                and
                m15[i]["close"] > ema169[i]
            )

            if broke and ema12_stayed and reclaim:

                return create_signal(
                    symbol,
                    "LONG",
                    m15,
                    i
                )

        else:

            broke = (
                m15[prev]["close"] > ema144[prev]
                and
                m15[prev]["close"] > ema169[prev]
            )

            ema12_stayed = (
                ema12[prev] <= ema144[prev]
                or
                ema12[prev] <= ema169[prev]
            )

            reclaim = (
                m15[i]["high"] < ema144[i]
                and
                m15[i]["close"] < ema169[i]
            )

            if broke and ema12_stayed and reclaim:

                return create_signal(
                    symbol,
                    "SHORT",
                    m15,
                    i
                )

    # --------------------------------------------------------
    # A
    # 正常回踩144
    # --------------------------------------------------------

    i = len(m15) - 2

    price = m15[i]["close"]

    if direction == "LONG":

        near_144 = (
            abs(price - ema144[i])
            / ema144[i]
            <= 0.02
        )

        not_break_169 = (
            m15[i]["close"]
            >= ema169[i] * 0.995
        )

        reclaim_12 = (
            m15[i]["close"]
            > ema12[i]
        )

        if near_144 and not_break_169 and reclaim_12:

            return create_signal(
                symbol,
                "LONG",
                m15,
                i
            )

    else:

        near_144 = (
            abs(price - ema144[i])
            / ema144[i]
            <= 0.02
        )

        not_break_169 = (
            m15[i]["close"]
            <= ema169[i] * 1.005
        )

        reclaim_12 = (
            m15[i]["close"]
            < ema12[i]
        )

        if near_144 and not_break_169 and reclaim_12:

            return create_signal(
                symbol,
                "SHORT",
                m15,
                i
            )

    return None


# ============================================================
# 止损止盈
# ============================================================

def create_signal(symbol, direction, candles, signal_index):

    # 信号K线不参与止损计算
    start = max(
        0,
        signal_index - 10
    )

    previous = candles[start:signal_index]

    if len(previous) < 10:
        return None

    if direction == "LONG":

        entry = candles[signal_index]["close"]

        lowest = min(
            x["low"]
            for x in previous
        )

        stop = lowest * 0.995

        if stop >= entry:
            return None

        take_profit = (
            entry
            + 2 * (entry - stop)
        )

    else:

        entry = candles[signal_index]["close"]

        highest = max(
            x["high"]
            for x in previous
        )

        stop = highest * 1.005

        if stop <= entry:
            return None

        take_profit = (
            entry
            - 2 * (stop - entry)
        )

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "take_profit": take_profit,
        "time": candles[signal_index]["time"]
    }


# ============================================================
# 信号发送
# ============================================================

def format_price(price):

    if price >= 1000:
        return f"{price:.2f}"

    if price >= 1:
        return f"{price:.4f}"

    return f"{price:.8f}".rstrip("0").rstrip(".")


def signal_text(signal):

    direction = signal["direction"]

    if direction == "LONG":

        return (
            "🟢 多单信号\n\n"
            f"币种：{signal['symbol']}\n"
            f"📍 入场价：{format_price(signal['entry'])}\n"
            f"🛑 止损：{format_price(signal['stop'])}\n"
            f"🎯 止盈：{format_price(signal['take_profit'])}\n\n"
            f"⏱ 信号时间：{time.strftime('%Y-%m-%d %H:%M')}"
        )

    return (
        "🔴 空单信号\n\n"
        f"币种：{signal['symbol']}\n"
        f"📍 入场价：{format_price(signal['entry'])}\n"
        f"🛑 止损：{format_price(signal['stop'])}\n"
        f"🎯 止盈：{format_price(signal['take_profit'])}\n\n"
        f"⏱ 信号时间：{time.strftime('%Y-%m-%d %H:%M')}"
    )


def process_symbol(symbol):

    try:

        signal = vegas_signal(symbol)

        if not signal:
            return

        now = time.time()

        with state_lock:

            last = last_signal_time.get(symbol, 0)

            if now - last < SIGNAL_COOLDOWN:

                print(
                    f"[COOLDOWN] {symbol}",
                    flush=True
                )

                return

            last_signal_time[symbol] = now

        text = signal_text(signal)

        print(
            f"[SIGNAL] {symbol} {signal['direction']}",
            flush=True
        )

        success = send_message(text)

        if not success:

            # 如果Telegram发送失败，不应该把币种永久锁住
            with state_lock:
                last_signal_time.pop(
                    symbol,
                    None
                )

    except Exception as e:

        print(
            f"[SCANNER ERROR] {symbol}: {repr(e)}",
            flush=True
        )


# ============================================================
# 交易扫描
# ============================================================

def trading_scanner():

    global scanner_running

    scanner_running = True

    print(
        f"[SCANNER] Started. Monitoring {len(SYMBOLS)} symbols.",
        flush=True
    )

    # 先等待Telegram启动
    time.sleep(5)

    while True:

        scan_start = time.time()

        print(
            "[SCANNER] New scan started...",
            flush=True
        )

        with ThreadPoolExecutor(
            max_workers=8
        ) as executor:

            futures = [
                executor.submit(
                    process_symbol,
                    symbol
                )
                for symbol in SYMBOLS
            ]

            for future in as_completed(futures):

                try:
                    future.result()

                except Exception as e:

                    print(
                        f"[SCANNER FUTURE ERROR] {repr(e)}",
                        flush=True
                    )

        elapsed = time.time() - scan_start

        print(
            f"[SCANNER] Scan finished in {elapsed:.1f}s",
            flush=True
        )

        time.sleep(
            max(
                5,
                SCAN_INTERVAL - elapsed
            )
        )


# ============================================================
# DEBUG
# ============================================================

def debug_symbol(symbol, chat_id):

    print(
        f"[DEBUG] Checking {symbol}",
        flush=True
    )

    h1 = bybit_kline(
        symbol,
        "60",
        750
    )

    m15 = bybit_kline(
        symbol,
        "15",
        300
    )

    if not h1:

        send_message(
            f"❌ {symbol}\n\n1H 数据获取失败。",
            chat_id
        )

        return

    if not m15:

        send_message(
            f"❌ {symbol}\n\n15M 数据获取失败。",
            chat_id
        )

        return

    direction = get_1h_direction(h1)

    signal = vegas_signal(symbol)

    if signal:

        result = (
            f"🔎 {symbol} DEBUG\n\n"
            f"1H方向：{direction}\n"
            f"当前检测：有信号\n"
            f"方向：{signal['direction']}\n"
            f"入场：{format_price(signal['entry'])}\n"
            f"止损：{format_price(signal['stop'])}\n"
            f"止盈：{format_price(signal['take_profit'])}"
        )

    else:

        result = (
            f"🔎 {symbol} DEBUG\n\n"
            f"1H方向：{direction}\n"
            "15M：当前没有符合条件的信号\n\n"
            "这不是程序错误，只代表当前没有满足策略条件。"
        )

    send_message(
        result,
        chat_id
    )


# ============================================================
# 主程序
# ============================================================

def main():

    print(
        "==================================================",
        flush=True
    )

    print(
        "Vegas Trading Bot starting...",
        flush=True
    )

    print(
        f"Python process PID: {os.getpid()}",
        flush=True
    )

    print(
        f"Symbols: {len(SYMBOLS)}",
        flush=True
    )

    print(
        f"Chat ID: {CHAT_ID}",
        flush=True
    )

    if not BOT_TOKEN:

        print(
            "[FATAL] BOT_TOKEN environment variable is EMPTY!",
            flush=True
        )

        # HTTP服务仍然启动，方便Render显示服务
        start_web_server()
        return

    # --------------------------------------------------------
    # Web服务
    # --------------------------------------------------------

    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )

    web_thread.start()

    time.sleep(1)

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    telegram_thread = threading.Thread(
        target=telegram_polling,
        daemon=True
    )

    telegram_thread.start()

    # --------------------------------------------------------
    # 交易扫描
    # --------------------------------------------------------

    scanner_thread = threading.Thread(
        target=trading_scanner,
        daemon=True
    )

    scanner_thread.start()

    print(
        "==================================================",
        flush=True
    )

    print(
        "[MAIN] All services started.",
        flush=True
    )

    print(
        "[MAIN] Telegram polling thread started.",
        flush=True
    )

    print(
        "[MAIN] Trading scanner thread started.",
        flush=True
    )

    print(
        "==================================================",
        flush=True
    )

    # 主线程永远运行
    while True:

        time.sleep(60)

        print(
            f"[HEARTBEAT] Telegram={telegram_running} "
            f"Scanner={scanner_running}",
            flush=True
        )


if __name__ == "__main__":
    main()
