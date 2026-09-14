import os
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "8289465171").strip()

BYBIT_URL = "https://api.bybit.com"
TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

PORT = int(os.getenv("PORT", "10000"))

# 同一币种4小时内最多发送一次信号
SIGNAL_COOLDOWN = 4 * 60 * 60

# 扫描周期
SCAN_INTERVAL = 60

# 1H缓存5分钟
H1_CACHE_SECONDS = 300

# 15M缓存55秒
M15_CACHE_SECONDS = 55


# ============================================================
# 62 SYMBOLS
# ============================================================

SYMBOLS = [
    # 核心45
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "TRXUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "HYPEUSDT",
    "ZECUSDT",
    "BCHUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "XLMUSDT",
    "SUIUSDT",
    "TONUSDT",
    "HBARUSDT",
    "SHIBUSDT",
    "LTCUSDT",
    "DOTUSDT",
    "UNIUSDT",
    "NEARUSDT",
    "ICPUSDT",
    "XMRUSDT",
    "APTUSDT",
    "TAOUSDT",
    "ATOMUSDT",
    "ARBUSDT",
    "CROUSDT",
    "VETUSDT",
    "FILUSDT",
    "ALGOUSDT",
    "RENDERUSDT",
    "INJUSDT",
    "OPUSDT",
    "IMXUSDT",
    "AAVEUSDT",
    "GRTUSDT",
    "SEIUSDT",
    "QNTUSDT",
    "THETAUSDT",
    "TIAUSDT",
    "MKRUSDT",
    "MNTUSDT",
    "PEPEUSDT",

    # 扩展10
    "WIFUSDT",
    "BONKUSDT",
    "FLOKIUSDT",
    "JUPUSDT",
    "ENAUSDT",
    "ONDOUSDT",
    "RUNEUSDT",
    "WLDUSDT",
    "STXUSDT",
    "CRVUSDT",

    # 扩展7
    "MAGMAUSDT",
    "FFUSDT",
    "LISKUSDT",
    "ARKUSDT",
    "EMBERUSDT",
    "PENGUUSDT",
    "JASMYUSDT",
]


# ============================================================
# GLOBAL
# ============================================================

session = requests.Session()

telegram_offset = 0

telegram_running = False
scanner_running = False

last_signal_time = {}

cache_lock = threading.Lock()

h1_cache = {}
m15_cache = {}

state_lock = threading.Lock()


# ============================================================
# RENDER HTTP SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        body = b"Vegas Trading Bot is running."

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.send_header(
            "Content-Length",
            str(len(body))
        )
        self.end_headers()

        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def start_web_server():

    try:
        server = HTTPServer(
            ("0.0.0.0", PORT),
            HealthHandler
        )

        print(
            f"[WEB] HTTP server started on port {PORT}",
            flush=True
        )

        server.serve_forever()

    except Exception as e:

        print(
            f"[WEB ERROR] {repr(e)}",
            flush=True
        )


# ============================================================
# TELEGRAM
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
            f"[TELEGRAM REQUEST ERROR] "
            f"{method}: {repr(e)}",
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
            "text": text
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


def telegram_check():

    print(
        "[TELEGRAM] Checking bot...",
        flush=True
    )

    result = telegram_request(
        "getMe",
        timeout=20
    )

    if not result:

        return False

    if not result.get("ok"):

        print(
            f"[TELEGRAM] getMe failed: {result}",
            flush=True
        )

        return False

    bot = result["result"]

    print(
        f"[TELEGRAM] Bot OK: "
        f"@{bot.get('username')}",
        flush=True
    )

    return True


def delete_webhook():

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
        f"[TELEGRAM] deleteWebhook: {result}",
        flush=True
    )


def telegram_polling():

    global telegram_offset
    global telegram_running

    if not BOT_TOKEN:

        print(
            "[TELEGRAM FATAL] BOT_TOKEN missing",
            flush=True
        )

        return

    if not telegram_check():

        print(
            "[TELEGRAM FATAL] Bot token check failed",
            flush=True
        )

        return

    delete_webhook()

    telegram_running = True

    print(
        "[TELEGRAM] Polling started.",
        flush=True
    )

    while True:

        try:

            result = telegram_request(
                "getUpdates",
                {
                    "offset": telegram_offset,
                    "timeout": 20,
                    "allowed_updates":
                        '["message"]'
                },
                timeout=30
            )

            if not result:

                time.sleep(3)
                continue

            if not result.get("ok"):

                print(
                    f"[TELEGRAM GETUPDATES ERROR] "
                    f"{result}",
                    flush=True
                )

                time.sleep(5)
                continue

            updates = result.get(
                "result",
                []
            )

            for update in updates:

                telegram_offset = (
                    update["update_id"] + 1
                )

                message = update.get(
                    "message"
                )

                if not message:
                    continue

                handle_message(message)

        except Exception as e:

            print(
                f"[TELEGRAM LOOP ERROR] "
                f"{repr(e)}",
                flush=True
            )

            time.sleep(5)


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

def handle_message(message):

    text = message.get(
        "text",
        ""
    ).strip()

    chat = message.get(
        "chat",
        {}
    )

    chat_id = str(
        chat.get("id", "")
    )

    print(
        f"[TELEGRAM] "
        f"chat={chat_id} "
        f"text={text}",
        flush=True
    )

    if text.startswith("/start"):

        send_message(
            "🤖 Vegas Trading Bot 已启动\n\n"
            "📊 策略：Vegas EMA 12 / 144 / 169 / 576 / 676\n"
            "⏱ 周期：1H 定方向 + 15M 找入场\n"
            "💰 数据源：Bybit\n"
            f"📈 监控币种：{len(SYMBOLS)} 个\n\n"
            "可用命令：\n"
            "/status\n"
            "/debug BTCUSDT",
            chat_id
        )

        return

    if text.startswith("/status"):

        with state_lock:

            signals = len(
                last_signal_time
            )

        send_message(
            "🤖 Vegas Trading Bot\n\n"
            "运行状态：🟢\n"
            f"Telegram："
            f"{'🟢' if telegram_running else '🔴'}\n"
            f"策略扫描："
            f"{'🟢' if scanner_running else '🔴'}\n"
            f"监控币种：{len(SYMBOLS)}\n"
            f"信号冷却记录：{signals}\n\n"
            "策略：Vegas EMA 12/144/169/576/676\n"
            "方向：1H\n"
            "入场：15M",
            chat_id
        )

        return

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


# ============================================================
# BYBIT API
# ============================================================

def fetch_kline(
    symbol,
    interval,
    limit
):

    url = (
        f"{BYBIT_URL}"
        "/v5/market/kline"
    )

    try:

        response = session.get(
            url,
            params={
                "category": "linear",
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            },
            timeout=15
        )

    except requests.RequestException as e:

        error = (
            f"NETWORK_ERROR: {type(e).__name__}: {e}"
        )

        print(
            f"[BYBIT ERROR] "
            f"{symbol} {interval} "
            f"{error}",
            flush=True
        )

        return None, error

    except Exception as e:

        error = (
            f"REQUEST_ERROR: {repr(e)}"
        )

        print(
            f"[BYBIT ERROR] "
            f"{symbol} {interval} "
            f"{error}",
            flush=True
        )

        return None, error

    try:

        data = response.json()

    except Exception:

        error = (
            f"INVALID_JSON "
            f"HTTP={response.status_code} "
            f"BODY={response.text[:300]}"
        )

        print(
            f"[BYBIT ERROR] "
            f"{symbol} {interval} "
            f"{error}",
            flush=True
        )

        return None, error

    if response.status_code != 200:

        error = (
            f"HTTP {response.status_code} "
            f"{data}"
        )

        print(
            f"[BYBIT ERROR] "
            f"{symbol} {interval} "
            f"{error}",
            flush=True
        )

        return None, error

    ret_code = data.get(
        "retCode"
    )

    if ret_code != 0:

        error = (
            f"retCode={ret_code}, "
            f"retMsg={data.get('retMsg')}"
        )

        print(
            f"[BYBIT ERROR] "
            f"{symbol} {interval} "
            f"{error}",
            flush=True
        )

        return None, error

    rows = (
        data
        .get("result", {})
        .get("list", [])
    )

    if not rows:

        error = "EMPTY_KLINE_LIST"

        print(
            f"[BYBIT ERROR] "
            f"{symbol} {interval} "
            f"{error}",
            flush=True
        )

        return None, error

    candles = []

    # Bybit返回：最新 -> 最旧
    # 我们转换成：最旧 -> 最新
    rows.reverse()

    try:

        for row in rows:

            candles.append(
                {
                    "time": int(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4])
                }
            )

    except Exception as e:

        error = (
            f"PARSE_ERROR: {repr(e)}"
        )

        return None, error

    return candles, None


# ============================================================
# CACHE
# ============================================================

def get_h1(symbol):

    now = time.time()

    with cache_lock:

        cached = h1_cache.get(
            symbol
        )

        if cached:

            candles, timestamp = cached

            if now - timestamp < H1_CACHE_SECONDS:

                return candles, None

    candles, error = fetch_kline(
        symbol,
        "60",
        750
    )

    if candles:

        with cache_lock:

            h1_cache[symbol] = (
                candles,
                now
            )

    return candles, error


def get_m15(symbol):

    now = time.time()

    with cache_lock:

        cached = m15_cache.get(
            symbol
        )

        if cached:

            candles, timestamp = cached

            if now - timestamp < M15_CACHE_SECONDS:

                return candles, None

    candles, error = fetch_kline(
        symbol,
        "15",
        300
    )

    if candles:

        with cache_lock:

            m15_cache[symbol] = (
                candles,
                now
            )

    return candles, error


# ============================================================
# EMA
# ============================================================

def ema(values, period):

    if len(values) < period:

        return None

    current = (
        sum(values[:period])
        / period
    )

    multiplier = (
        2 / (period + 1)
    )

    for price in values[period:]:

        current = (
            (price - current)
            * multiplier
            + current
        )

    return current


def ema_series(values, period):

    result = [
        None
    ] * len(values)

    if len(values) < period:

        return result

    current = (
        sum(values[:period])
        / period
    )

    result[period - 1] = current

    multiplier = (
        2 / (period + 1)
    )

    for i in range(
        period,
        len(values)
    ):

        current = (
            (values[i] - current)
            * multiplier
            + current
        )

        result[i] = current

    return result


# ============================================================
# 1H DIRECTION
# ============================================================

def get_1h_direction(candles):

    if not candles:

        return "NEUTRAL"

    # 排除最后一根未完成K线
    closed = candles[:-1]

    if len(closed) < 676:

        return "NEUTRAL"

    closes = [
        x["close"]
        for x in closed
    ]

    e12 = ema(
        closes,
        12
    )

    e144 = ema(
        closes,
        144
    )

    e169 = ema(
        closes,
        169
    )

    e576 = ema(
        closes,
        576
    )

    e676 = ema(
        closes,
        676
    )

    if None in (
        e12,
        e144,
        e169,
        e576,
        e676
    ):

        return "NEUTRAL"

    close = closes[-1]

    bullish = (
        e12 > e144
        and e144 > e169
        and e169 > e576
        and e576 > e676
        and close > e144
    )

    bearish = (
        e12 < e144
        and e144 < e169
        and e169 < e576
        and e576 < e676
        and close < e144
    )

    if bullish:

        return "LONG"

    if bearish:

        return "SHORT"

    return "NEUTRAL"


# ============================================================
# SIGNAL
# ============================================================

def make_signal(
    symbol,
    direction,
    candles,
    signal_index
):

    # signal_index之前必须至少有10根完整K线
    if signal_index < 10:

        return None

    previous = candles[
        signal_index - 10:
        signal_index
    ]

    entry = candles[
        signal_index
    ]["close"]

    if direction == "LONG":

        lowest = min(
            x["low"]
            for x in previous
        )

        stop = lowest * 0.995

        if stop >= entry:

            return None

        tp = (
            entry
            + 2 * (entry - stop)
        )

    else:

        highest = max(
            x["high"]
            for x in previous
        )

        stop = highest * 1.005

        if stop <= entry:

            return None

        tp = (
            entry
            - 2 * (stop - entry)
        )

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "tp": tp,
        "time": candles[
            signal_index
        ]["time"]
    }


def detect_signal(
    symbol,
    h1,
    m15
):

    direction = get_1h_direction(
        h1
    )

    if direction == "NEUTRAL":

        return None

    if len(m15) < 200:

        return None

    closes = [
        x["close"]
        for x in m15
    ]

    e12 = ema_series(
        closes,
        12
    )

    e144 = ema_series(
        closes,
        144
    )

    e169 = ema_series(
        closes,
        169
    )

    # ========================================================
    # B1
    # EMA12 穿越 EMA144
    #
    # 使用当前未完成K线检查“立即信号”
    # 当前K线只用于发现穿越
    # 止损仍然只使用之前10根已完成K线
    # ========================================================

    current = len(m15) - 1
    previous = current - 1

    if previous >= 1:

        if direction == "LONG":

            price_deep_break = (
                m15[previous]["close"]
                < e144[previous]
                and
                m15[previous]["close"]
                < e169[previous]
            )

            ema12_below = (
                e12[previous]
                < e144[previous]
                and
                e12[previous]
                < e169[previous]
            )

            cross_up = (
                e12[previous]
                <= e144[previous]
                and
                e12[current]
                > e144[current]
            )

            if (
                price_deep_break
                and ema12_below
                and cross_up
            ):

                return make_signal(
                    symbol,
                    "LONG",
                    m15,
                    current
                )

        else:

            price_deep_break = (
                m15[previous]["close"]
                > e144[previous]
                and
                m15[previous]["close"]
                > e169[previous]
            )

            ema12_above = (
                e12[previous]
                > e144[previous]
                and
                e12[previous]
                > e169[previous]
            )

            cross_down = (
                e12[previous]
                >= e144[previous]
                and
                e12[current]
                < e144[current]
            )

            if (
                price_deep_break
                and ema12_above
                and cross_down
            ):

                return make_signal(
                    symbol,
                    "SHORT",
                    m15,
                    current
                )

    # ========================================================
    # B2
    # 浅破 + 收回
    # 使用最后一根已完成15M K线
    # ========================================================

    i = len(m15) - 2
    p = i - 1

    if p >= 1:

        if direction == "LONG":

            broke = (
                m15[p]["close"]
                < e144[p]
                and
                m15[p]["close"]
                < e169[p]
            )

            ema12_stayed = (
                e12[p] >= e144[p]
                or
                e12[p] >= e169[p]
            )

            reclaim = (
                m15[i]["low"]
                > e144[i]
                and
                m15[i]["close"]
                > e169[i]
            )

            if (
                broke
                and ema12_stayed
                and reclaim
            ):

                return make_signal(
                    symbol,
                    "LONG",
                    m15,
                    i
                )

        else:

            broke = (
                m15[p]["close"]
                > e144[p]
                and
                m15[p]["close"]
                > e169[p]
            )

            ema12_stayed = (
                e12[p] <= e144[p]
                or
                e12[p] <= e169[p]
            )

            reclaim = (
                m15[i]["high"]
                < e144[i]
                and
                m15[i]["close"]
                < e169[i]
            )

            if (
                broke
                and ema12_stayed
                and reclaim
            ):

                return make_signal(
                    symbol,
                    "SHORT",
                    m15,
                    i
                )

    # ========================================================
    # A
    # 正常回踩EMA144
    # ========================================================

    i = len(m15) - 2

    price = m15[i]["close"]

    if direction == "LONG":

        near_144 = (
            abs(price - e144[i])
            / e144[i]
            <= 0.02
        )

        not_break_169 = (
            price
            >= e169[i] * 0.995
        )

        above_12 = (
            price > e12[i]
        )

        if (
            near_144
            and not_break_169
            and above_12
        ):

            return make_signal(
                symbol,
                "LONG",
                m15,
                i
            )

    else:

        near_144 = (
            abs(price - e144[i])
            / e144[i]
            <= 0.02
        )

        not_break_169 = (
            price
            <= e169[i] * 1.005
        )

        below_12 = (
            price < e12[i]
        )

        if (
            near_144
            and not_break_169
            and below_12
        ):

            return make_signal(
                symbol,
                "SHORT",
                m15,
                i
            )

    return None


# ============================================================
# FORMAT
# ============================================================

def format_price(price):

    if price >= 1000:

        return f"{price:.2f}"

    if price >= 1:

        return f"{price:.4f}"

    return (
        f"{price:.8f}"
        .rstrip("0")
        .rstrip(".")
    )


def signal_message(signal):

    if signal["direction"] == "LONG":

        return (
            "🟢 多单信号\n\n"
            f"币种：{signal['symbol']}\n"
            f"📍 入场价："
            f"{format_price(signal['entry'])}\n"
            f"🛑 止损："
            f"{format_price(signal['stop'])}\n"
            f"🎯 止盈："
            f"{format_price(signal['tp'])}\n\n"
            f"⏱ 信号时间："
            f"{time.strftime('%Y-%m-%d %H:%M')}"
        )

    return (
        "🔴 空单信号\n\n"
        f"币种：{signal['symbol']}\n"
        f"📍 入场价："
        f"{format_price(signal['entry'])}\n"
        f"🛑 止损："
        f"{format_price(signal['stop'])}\n"
        f"🎯 止盈："
        f"{format_price(signal['tp'])}\n\n"
        f"⏱ 信号时间："
        f"{time.strftime('%Y-%m-%d %H:%M')}"
    )


# ============================================================
# PROCESS ONE SYMBOL
# ============================================================

def process_symbol(symbol):

    try:

        h1, h1_error = get_h1(
            symbol
        )

        if not h1:

            print(
                f"[SCAN] {symbol} "
                f"1H failed: {h1_error}",
                flush=True
            )

            return

        # EMA676至少需要676根已完成K线
        if len(h1) < 677:

            print(
                f"[SCAN] {symbol} "
                f"1H candles={len(h1)}, "
                f"not enough for EMA676",
                flush=True
            )

            return

        m15, m15_error = get_m15(
            symbol
        )

        if not m15:

            print(
                f"[SCAN] {symbol} "
                f"15M failed: {m15_error}",
                flush=True
            )

            return

        signal = detect_signal(
            symbol,
            h1,
            m15
        )

        if not signal:

            return

        now = time.time()

        with state_lock:

            previous = last_signal_time.get(
                symbol,
                0
            )

            if (
                now - previous
                < SIGNAL_COOLDOWN
            ):

                return

            # 先记录
            last_signal_time[
                symbol
            ] = now

        text = signal_message(
            signal
        )

        print(
            f"[SIGNAL] "
            f"{symbol} "
            f"{signal['direction']}",
            flush=True
        )

        success = send_message(
            text
        )

        if not success:

            with state_lock:

                last_signal_time.pop(
                    symbol,
                    None
                )

    except Exception as e:

        print(
            f"[PROCESS ERROR] "
            f"{symbol}: {repr(e)}",
            flush=True
        )


# ============================================================
# SCANNER
# ============================================================

def trading_scanner():

    global scanner_running

    scanner_running = True

    print(
        f"[SCANNER] Started. "
        f"Monitoring {len(SYMBOLS)} symbols.",
        flush=True
    )

    while True:

        started = time.time()

        print(
            "[SCANNER] Scan started...",
            flush=True
        )

        with ThreadPoolExecutor(
            max_workers=8
        ) as executor:

            futures = []

            for symbol in SYMBOLS:

                futures.append(
                    executor.submit(
                        process_symbol,
                        symbol
                    )
                )

            for future in as_completed(
                futures
            ):

                try:

                    future.result()

                except Exception as e:

                    print(
                        f"[SCANNER FUTURE ERROR] "
                        f"{repr(e)}",
                        flush=True
                    )

        elapsed = (
            time.time() - started
        )

        print(
            f"[SCANNER] "
            f"Scan finished: "
            f"{elapsed:.1f}s",
            flush=True
        )

        sleep_time = max(
            5,
            SCAN_INTERVAL - elapsed
        )

        time.sleep(
            sleep_time
        )


# ============================================================
# DEBUG
# ============================================================

def debug_symbol(
    symbol,
    chat_id
):

    symbol = symbol.upper()

    if not symbol.endswith("USDT"):

        symbol += "USDT"

    print(
        f"[DEBUG] {symbol}",
        flush=True
    )

    # 直接请求，不走缓存
    h1, h1_error = fetch_kline(
        symbol,
        "60",
        750
    )

    if not h1:

        send_message(
            "❌ "
            f"{symbol}\n\n"
            "1H 数据获取失败。\n\n"
            f"Bybit错误：\n{h1_error}",
            chat_id
        )

        return

    h1_count = len(h1)

    if h1_count < 677:

        send_message(
            "⚠️ "
            f"{symbol}\n\n"
            f"Bybit返回1H K线："
            f"{h1_count} 根\n"
            "EMA676需要至少677根数据。\n\n"
            "因此目前无法判断1H方向。",
            chat_id
        )

        return

    direction = get_1h_direction(
        h1
    )

    m15, m15_error = fetch_kline(
        symbol,
        "15",
        300
    )

    if not m15:

        send_message(
            "❌ "
            f"{symbol}\n\n"
            "15M 数据获取失败。\n\n"
            f"Bybit错误：\n{m15_error}",
            chat_id
        )

        return

    signal = detect_signal(
        symbol,
        h1,
        m15
    )

    if signal:

        send_message(
            "🔎 DEBUG\n\n"
            f"币种：{symbol}\n"
            f"1H方向：{direction}\n"
            "15M：🟢 当前符合策略\n\n"
            f"方向：{signal['direction']}\n"
            f"入场：{format_price(signal['entry'])}\n"
            f"止损：{format_price(signal['stop'])}\n"
            f"止盈：{format_price(signal['tp'])}",
            chat_id
        )

    else:

        send_message(
            "🔎 DEBUG\n\n"
            f"币种：{symbol}\n"
            f"1H方向：{direction}\n"
            f"1H K线：{h1_count} 根\n"
            f"15M K线：{len(m15)} 根\n\n"
            "15M：目前没有符合条件的信号。\n\n"
            "这属于正常情况，不代表机器人故障。",
            chat_id
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "================================================",
        flush=True
    )

    print(
        "Vegas Trading Bot STARTING",
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

    print(
        f"Port: {PORT}",
        flush=True
    )

    print(
        "================================================",
        flush=True
    )

    # Render Web Server
    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )

    web_thread.start()

    time.sleep(1)

    # Telegram
    telegram_thread = threading.Thread(
        target=telegram_polling,
        daemon=True
    )

    telegram_thread.start()

    # Scanner
    scanner_thread = threading.Thread(
        target=trading_scanner,
        daemon=True
    )

    scanner_thread.start()

    print(
        "[MAIN] Telegram thread started.",
        flush=True
    )

    print(
        "[MAIN] Scanner thread started.",
        flush=True
    )

    print(
        "================================================",
        flush=True
    )

    # Keep process alive
    while True:

        time.sleep(60)

        print(
            "[HEARTBEAT] "
            f"Telegram={telegram_running} "
            f"Scanner={scanner_running} "
            f"Cache1H={len(h1_cache)} "
            f"Cache15M={len(m15_cache)}",
            flush=True
        )


if __name__ == "__main__":

    main()
