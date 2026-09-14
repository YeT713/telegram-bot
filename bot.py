import os
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "8289465171").strip()

# OKX 官方推荐的 API 域名
OKX_URL = "https://openapi.okx.com"

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

PORT = int(os.getenv("PORT", "10000"))

SCAN_INTERVAL = 60

SIGNAL_COOLDOWN = 4 * 60 * 60

# 1H缓存5分钟
H1_CACHE_SECONDS = 300

# 15M缓存55秒
M15_CACHE_SECONDS = 55


# ============================================================
# SYMBOLS
# ============================================================

SYMBOLS = [
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

h1_cache = {}
m15_cache = {}

cache_lock = threading.Lock()
state_lock = threading.Lock()


# ============================================================
# HTTP HEALTH SERVER
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
            f"[WEB] Server started on port {PORT}",
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

def telegram_request(
    method,
    data=None,
    timeout=30
):

    try:

        response = session.post(
            f"{TELEGRAM_URL}/{method}",
            data=data or {},
            timeout=timeout
        )

        return response.json()

    except Exception as e:

        print(
            f"[TELEGRAM ERROR] "
            f"{method}: {repr(e)}",
            flush=True
        )

        return None


def send_message(
    text,
    chat_id=None
):

    target = chat_id or CHAT_ID

    if not BOT_TOKEN:

        print(
            "[TELEGRAM] BOT_TOKEN missing",
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
            f"[TELEGRAM SEND ERROR] "
            f"{result}",
            flush=True
        )

        return False

    return True


def telegram_check():

    result = telegram_request(
        "getMe",
        timeout=20
    )

    if not result:

        return False

    if not result.get("ok"):

        print(
            f"[TELEGRAM getMe ERROR] {result}",
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
            "[TELEGRAM] BOT_TOKEN missing",
            flush=True
        )

        return

    if not telegram_check():

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
                    f"[TELEGRAM UPDATE ERROR] "
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

                if message:

                    handle_message(
                        message
                    )

        except Exception as e:

            print(
                f"[TELEGRAM LOOP ERROR] "
                f"{repr(e)}",
                flush=True
            )

            time.sleep(5)


# ============================================================
# COMMANDS
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
            "⏱ 1H 定方向 + 15M 找入场\n"
            "💰 数据源：OKX\n"
            f"📈 监控币种：{len(SYMBOLS)} 个\n\n"
            "可用命令：\n"
            "/status\n"
            "/debug BTCUSDT\n"
            "/debug TRXUSDT",
            chat_id
        )

        return

    if text.startswith("/status"):

        with state_lock:

            signal_count = len(
                last_signal_time
            )

        send_message(
            "🤖 Vegas Trading Bot\n\n"
            "运行状态：🟢\n"
            f"Telegram："
            f"{'🟢' if telegram_running else '🔴'}\n"
            f"扫描器："
            f"{'🟢' if scanner_running else '🔴'}\n"
            f"监控币种：{len(SYMBOLS)}\n"
            f"冷却记录：{signal_count}\n\n"
            "数据源：OKX\n"
            "1H：定方向\n"
            "15M：找入场\n"
            "Vegas：12 / 144 / 169 / 576 / 676",
            chat_id
        )

        return

    if text.startswith("/debug"):

        parts = text.split()

        if len(parts) < 2:

            send_message(
                "用法：\n"
                "/debug BTCUSDT\n"
                "/debug TRXUSDT",
                chat_id
            )

            return

        symbol = parts[1].upper()

        debug_symbol(
            symbol,
            chat_id
        )


# ============================================================
# SYMBOL CONVERSION
# ============================================================

def to_okx_swap(symbol):

    base = symbol.upper()

    if base.endswith("USDT"):

        base = base[:-4]

    return f"{base}-USDT-SWAP"


# ============================================================
# OKX REQUEST
# ============================================================

def okx_get(
    endpoint,
    params
):

    url = (
        OKX_URL
        + endpoint
    )

    try:

        response = session.get(
            url,
            params=params,
            timeout=15
        )

    except requests.RequestException as e:

        return None, (
            f"NETWORK_ERROR: "
            f"{type(e).__name__}: {e}"
        )

    except Exception as e:

        return None, (
            f"REQUEST_ERROR: {repr(e)}"
        )

    try:

        data = response.json()

    except Exception:

        return None, (
            f"INVALID_JSON "
            f"HTTP={response.status_code} "
            f"BODY={response.text[:300]}"
        )

    if response.status_code != 200:

        return None, (
            f"HTTP {response.status_code}: "
            f"{data}"
        )

    if data.get("code") != "0":

        return None, (
            f"OKX code={data.get('code')}, "
            f"msg={data.get('msg')}"
        )

    return data, None


# ============================================================
# OKX KLINES
# ============================================================

def fetch_okx_klines(
    symbol,
    bar,
    required
):

    inst_id = to_okx_swap(
        symbol
    )

    all_rows = []

    after = None

    # OKX单次最多300根
    page_size = 300

    # 为了防止异常分页死循环
    previous_oldest = None

    max_pages = (
        required // page_size + 5
    )

    for _ in range(max_pages):

        params = {
            "instId": inst_id,
            "bar": bar,
            "limit": str(page_size)
        }

        if after:

            params["after"] = str(
                after
            )

        data, error = okx_get(
            "/api/v5/market/candles",
            params
        )

        if error:

            return None, (
                f"{inst_id}\n"
                f"{error}"
            )

        rows = data.get(
            "data",
            []
        )

        if not rows:

            break

        all_rows.extend(
            rows
        )

        try:

            oldest = min(
                int(row[0])
                for row in rows
            )

        except Exception as e:

            return None, (
                f"KLINE_PARSE_ERROR: "
                f"{repr(e)}"
            )

        if (
            previous_oldest is not None
            and oldest >= previous_oldest
        ):

            break

        previous_oldest = oldest

        if len(all_rows) >= required:

            break

        after = oldest

        time.sleep(0.08)

    if not all_rows:

        return None, (
            "EMPTY_KLINE_DATA"
        )

    # 去重
    unique = {}

    for row in all_rows:

        try:

            unique[int(row[0])] = row

        except Exception:

            continue

    rows = list(
        unique.values()
    )

    # 最旧 -> 最新
    rows.sort(
        key=lambda x: int(x[0])
    )

    candles = []

    try:

        for row in rows:

            candles.append(
                {
                    "time": int(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "confirm": str(
                        row[8]
                    ) if len(row) > 8 else ""
                }
            )

    except Exception as e:

        return None, (
            f"KLINE_PARSE_ERROR: "
            f"{repr(e)}"
        )

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

            if (
                now - timestamp
                < H1_CACHE_SECONDS
            ):

                return candles, None

    candles, error = fetch_okx_klines(
        symbol,
        "1H",
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

            if (
                now - timestamp
                < M15_CACHE_SECONDS
            ):

                return candles, None

    candles, error = fetch_okx_klines(
        symbol,
        "15m",
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

def ema(
    values,
    period
):

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


def ema_series(
    values,
    period
):

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

def get_1h_direction(
    candles
):

    if not candles:

        return "NEUTRAL"

    # OKX confirm=1表示已完成
    closed = [
        x for x in candles
        if x.get("confirm") == "1"
    ]

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
# STOP / TP
# ============================================================

def make_signal(
    symbol,
    direction,
    candles,
    signal_index
):

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


# ============================================================
# SIGNAL DETECTION
# ============================================================

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

    closed = [
        x for x in m15
        if x.get("confirm") == "1"
    ]

    if len(closed) < 200:

        return None

    closes = [
        x["close"]
        for x in closed
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

    i = len(closed) - 1

    # ========================================================
    # B1
    # 深破后 EMA12 穿越 EMA144
    # ========================================================

    if i >= 2:

        p = i - 1

        if direction == "LONG":

            price_deep = (
                closed[p]["close"]
                < e144[p]
                and
                closed[p]["close"]
                < e169[p]
            )

            ema12_below = (
                e12[p] < e144[p]
                and
                e12[p] < e169[p]
            )

            cross_up = (
                e12[p] <= e144[p]
                and
                e12[i] > e144[i]
            )

            if (
                price_deep
                and ema12_below
                and cross_up
            ):

                return make_signal(
                    symbol,
                    "LONG",
                    closed,
                    i
                )

        else:

            price_deep = (
                closed[p]["close"]
                > e144[p]
                and
                closed[p]["close"]
                > e169[p]
            )

            ema12_above = (
                e12[p] > e144[p]
                and
                e12[p] > e169[p]
            )

            cross_down = (
                e12[p] >= e144[p]
                and
                e12[i] < e144[i]
            )

            if (
                price_deep
                and ema12_above
                and cross_down
            ):

                return make_signal(
                    symbol,
                    "SHORT",
                    closed,
                    i
                )

    # ========================================================
    # B2
    # 浅破 + 收回
    # ========================================================

    if i >= 2:

        p = i - 1

        if direction == "LONG":

            broke = (
                closed[p]["close"]
                < e144[p]
                and
                closed[p]["close"]
                < e169[p]
            )

            ema12_stayed = (
                e12[p] >= e144[p]
                or
                e12[p] >= e169[p]
            )

            reclaim = (
                closed[i]["low"]
                > e144[i]
                and
                closed[i]["close"]
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
                    closed,
                    i
                )

        else:

            broke = (
                closed[p]["close"]
                > e144[p]
                and
                closed[p]["close"]
                > e169[p]
            )

            ema12_stayed = (
                e12[p] <= e144[p]
                or
                e12[p] <= e169[p]
            )

            reclaim = (
                closed[i]["high"]
                < e144[i]
                and
                closed[i]["close"]
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
                    closed,
                    i
                )

    # ========================================================
    # A
    # 正常回踩EMA144
    # ========================================================

    price = closed[i]["close"]

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
                closed,
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
                closed,
                i
            )

    return None


# ============================================================
# FORMAT
# ============================================================

def format_price(
    price
):

    if price >= 1000:

        return f"{price:.2f}"

    if price >= 1:

        return f"{price:.4f}"

    return (
        f"{price:.8f}"
        .rstrip("0")
        .rstrip(".")
    )


def signal_message(
    signal
):

    # 使用北京时间
    dt = datetime.fromtimestamp(
        signal["time"] / 1000,
        ZoneInfo("Asia/Shanghai")
    )

    time_text = dt.strftime(
        "%Y-%m-%d %H:%M"
    )

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
            f"⏱ 信号时间：{time_text}"
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
        f"⏱ 信号时间：{time_text}"
    )


# ============================================================
# PROCESS
# ============================================================

def process_symbol(
    symbol
):

    try:

        h1, h1_error = get_h1(
            symbol
        )

        if not h1:

            print(
                f"[SCAN] {symbol} "
                f"1H ERROR: "
                f"{h1_error}",
                flush=True
            )

            return

        if len(h1) < 677:

            print(
                f"[SCAN] {symbol} "
                f"1H only {len(h1)} candles",
                flush=True
            )

            return

        m15, m15_error = get_m15(
            symbol
        )

        if not m15:

            print(
                f"[SCAN] {symbol} "
                f"15M ERROR: "
                f"{m15_error}",
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

            last = last_signal_time.get(
                symbol,
                0
            )

            if (
                now - last
                < SIGNAL_COOLDOWN
            ):

                return

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
        f"[SCANNER] "
        f"Monitoring {len(SYMBOLS)} symbols.",
        flush=True
    )

    while True:

        start = time.time()

        print(
            "[SCANNER] Scan started...",
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

            for future in as_completed(
                futures
            ):

                try:

                    future.result()

                except Exception as e:

                    print(
                        f"[SCANNER ERROR] "
                        f"{repr(e)}",
                        flush=True
                    )

        elapsed = (
            time.time() - start
        )

        print(
            f"[SCANNER] "
            f"Finished in "
            f"{elapsed:.1f}s",
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

def debug_symbol(
    symbol,
    chat_id
):

    symbol = symbol.upper()

    if not symbol.endswith("USDT"):

        symbol += "USDT"

    inst_id = to_okx_swap(
        symbol
    )

    print(
        f"[DEBUG] "
        f"{symbol} -> {inst_id}",
        flush=True
    )

    # -------------------------
    # 1H
    # -------------------------

    h1, h1_error = fetch_okx_klines(
        symbol,
        "1H",
        750
    )

    if not h1:

        send_message(
            "❌ "
            f"{symbol}\n\n"
            "OKX 1H 数据获取失败。\n\n"
            f"产品：{inst_id}\n"
            f"错误：\n{h1_error}",
            chat_id
        )

        return

    h1_closed = [
        x for x in h1
        if x.get("confirm") == "1"
    ]

    if len(h1_closed) < 676:

        send_message(
            "⚠️ "
            f"{symbol}\n\n"
            f"OKX产品：{inst_id}\n"
            f"1H总K线：{len(h1)}\n"
            f"已完成：{len(h1_closed)}\n\n"
            "EMA676需要至少676根已完成1H K线。\n"
            "目前数据不足。",
            chat_id
        )

        return

    direction = get_1h_direction(
        h1
    )

    # -------------------------
    # 15M
    # -------------------------

    m15, m15_error = fetch_okx_klines(
        symbol,
        "15m",
        300
    )

    if not m15:

        send_message(
            "❌ "
            f"{symbol}\n\n"
            "OKX 15M 数据获取失败。\n\n"
            f"产品：{inst_id}\n"
            f"错误：\n{m15_error}",
            chat_id
        )

        return

    m15_closed = [
        x for x in m15
        if x.get("confirm") == "1"
    ]

    signal = detect_signal(
        symbol,
        h1,
        m15
    )

    if signal:

        send_message(
            "🔎 DEBUG\n\n"
            f"币种：{symbol}\n"
            f"OKX：{inst_id}\n\n"
            f"1H方向：{direction}\n"
            f"1H已完成："
            f"{len(h1_closed)}\n"
            f"15M已完成："
            f"{len(m15_closed)}\n\n"
            "🟢 当前符合策略\n\n"
            f"方向：{signal['direction']}\n"
            f"入场："
            f"{format_price(signal['entry'])}\n"
            f"止损："
            f"{format_price(signal['stop'])}\n"
            f"止盈："
            f"{format_price(signal['tp'])}",
            chat_id
        )

    else:

        send_message(
            "🔎 DEBUG\n\n"
            f"币种：{symbol}\n"
            f"OKX：{inst_id}\n\n"
            f"1H方向：{direction}\n"
            f"1H已完成："
            f"{len(h1_closed)}\n"
            f"15M已完成："
            f"{len(m15_closed)}\n\n"
            "目前没有符合Vegas策略的信号。\n\n"
            "✅ 数据接口正常\n"
            "✅ K线正常\n"
            "✅ EMA可以计算\n"
            "ℹ️ 只是当前没有触发条件。",
            chat_id
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "==========================================",
        flush=True
    )

    print(
        "Vegas Trading Bot STARTING",
        flush=True
    )

    print(
        f"Data source: OKX",
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
        "==========================================",
        flush=True
    )

    # Render HTTP
    threading.Thread(
        target=start_web_server,
        daemon=True
    ).start()

    time.sleep(1)

    # Telegram
    threading.Thread(
        target=telegram_polling,
        daemon=True
    ).start()

    # Scanner
    threading.Thread(
        target=trading_scanner,
        daemon=True
    ).start()

    print(
        "[MAIN] All threads started.",
        flush=True
    )

    while True:

        time.sleep(60)

        print(
            "[HEARTBEAT] "
            f"Telegram={telegram_running} "
            f"Scanner={scanner_running} "
            f"H1Cache={len(h1_cache)} "
            f"M15Cache={len(m15_cache)}",
            flush=True
        )


if __name__ == "__main__":

    main()
