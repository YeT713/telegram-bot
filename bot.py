import os
import time
import asyncio
import threading
from datetime import datetime

import requests
from http.server import BaseHTTPRequestHandler, HTTPServer


# =========================================================
# 配置
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = "8289465171"

BYBIT_URL = "https://api.bybit.com"

SCAN_INTERVAL = 30
COOLDOWN = 4 * 60 * 60

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "TRXUSDT", "DOGEUSDT", "ADAUSDT", "HYPEUSDT", "ZECUSDT",
    "BCHUSDT", "LINKUSDT", "AVAXUSDT", "XLMUSDT", "SUIUSDT",
    "TONUSDT", "HBARUSDT", "SHIBUSDT", "LTCUSDT", "DOTUSDT",
    "UNIUSDT", "NEARUSDT", "ICPUSDT", "XMRUSDT", "APTUSDT",
    "TAOUSDT", "ATOMUSDT", "ARBUSDT", "CROUSDT", "VETUSDT",
    "FILUSDT", "ALGOUSDT", "RENDERUSDT", "INJUSDT", "OPUSDT",
    "IMXUSDT", "AAVEUSDT", "GRTUSDT", "SEIUSDT", "QNTUSDT",
    "THETAUSDT", "TIAUSDT", "MKRUSDT", "MNTUSDT", "PEPEUSDT",
    "WIFUSDT", "BONKUSDT", "FLOKIUSDT", "JUPUSDT", "ENAUSDT",
    "ONDOUSDT", "RUNEUSDT", "WLDUSDT", "STXUSDT", "CRVUSDT",
    "MAGMAUSDT", "FFUSDT", "LISKUSDT", "ARKUSDT", "EMBERUSDT",
    "PENGUUSDT", "JASMYUSDT",
]

EMA_PERIODS = [12, 144, 169, 576, 676]

last_signal = {}
directions = {}

stats = {
    "scans": 0,
    "signals": 0,
    "api_errors": 0,
}


# =========================================================
# Render 健康检查
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.end_headers()
        self.wfile.write(
            b"YeTrading Vegas Bot is running."
        )

    def log_message(self, format, *args):
        pass


def health_server():
    port = int(os.getenv("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(f"[HEALTH] port={port}")

    server.serve_forever()


# =========================================================
# Bybit K线
# =========================================================

def get_klines(symbol, interval, limit=800):

    try:

        response = requests.get(
            f"{BYBIT_URL}/v5/market/kline",
            params={
                "category": "linear",
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            },
            timeout=15,
        )

        if response.status_code != 200:
            stats["api_errors"] += 1
            return []

        data = response.json()

        if data.get("retCode") != 0:
            stats["api_errors"] += 1
            return []

        rows = data.get(
            "result", {}
        ).get(
            "list", []
        )

        candles = []

        for row in rows:

            candles.append({
                "time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
            })

        candles.sort(
            key=lambda x: x["time"]
        )

        return candles

    except Exception as e:

        stats["api_errors"] += 1

        print(
            f"[API ERROR] {symbol} {interval}: {e}"
        )

        return []


# =========================================================
# EMA
# =========================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    value = sum(
        values[:period]
    ) / period

    for price in values[period:]:
        value = (
            price - value
        ) * multiplier + value

    return value


def add_ema(candles):

    closes = [
        x["close"]
        for x in candles
    ]

    result = []

    for i in range(len(candles)):

        item = candles[i].copy()

        values = closes[:i + 1]

        item["ema12"] = ema(values, 12)
        item["ema144"] = ema(values, 144)
        item["ema169"] = ema(values, 169)
        item["ema576"] = ema(values, 576)
        item["ema676"] = ema(values, 676)

        result.append(item)

    return result


# =========================================================
# 1H Vegas 方向
#
# 多头：
# EMA12 > EMA144 > EMA169 > EMA576 > EMA676
# 且收盘价 > EMA144
#
# 空头反过来
# =========================================================

def get_direction(symbol):

    candles = get_klines(
        symbol,
        "60",
        800
    )

    if len(candles) < 700:
        return None

    # 排除正在形成的 1H K线
    closed = candles[:-1]

    data = add_ema(closed)

    c = data[-1]

    if c["ema676"] is None:
        return None

    bullish = (
        c["ema12"] >
        c["ema144"] >
        c["ema169"] >
        c["ema576"] >
        c["ema676"]
        and
        c["close"] > c["ema144"]
    )

    bearish = (
        c["ema12"] <
        c["ema144"] <
        c["ema169"] <
        c["ema576"] <
        c["ema676"]
        and
        c["close"] < c["ema144"]
    )

    if bullish:
        return "LONG"

    if bearish:
        return "SHORT"

    return None


# =========================================================
# A 策略
#
# 正常回踩 EMA144 ±2%
#
# 15M 收盘不能有效突破 EMA169
#
# 然后收盘重新站上/跌破 EMA12
# =========================================================

def strategy_a(data, direction):

    if len(data) < 3:
        return None

    c = data[-2]

    if c["ema169"] is None:
        return None

    ema144 = c["ema144"]
    ema169 = c["ema169"]
    ema12 = c["ema12"]

    zone_low = ema144 * 0.98
    zone_high = ema144 * 1.02

    entered = (
        c["low"] <= zone_high
        and
        c["high"] >= zone_low
    )

    if direction == "LONG":

        structure_ok = (
            c["close"] >= ema169
        )

        confirm = (
            c["close"] > ema12
        )

        if entered and structure_ok and confirm:

            return {
                "strategy": "A",
                "side": "LONG",
                "index": len(data) - 2,
                "entry": c["close"],
            }

    if direction == "SHORT":

        structure_ok = (
            c["close"] <= ema169
        )

        confirm = (
            c["close"] < ema12
        )

        if entered and structure_ok and confirm:

            return {
                "strategy": "A",
                "side": "SHORT",
                "index": len(data) - 2,
                "entry": c["close"],
            }

    return None


# =========================================================
# B2
#
# 价格突破144/169
# 但 EMA12 没有跟着突破
# 然后价格重新收回
# =========================================================

def strategy_b2(data, direction):

    if len(data) < 4:
        return None

    previous = data[-3]
    current = data[-2]

    if current["ema169"] is None:
        return None

    if direction == "LONG":

        broke = (
            previous["low"] < previous["ema144"]
            and
            previous["low"] < previous["ema169"]
        )

        ema12_hold = (
            previous["ema12"] >= previous["ema144"]
            and
            previous["ema12"] >= previous["ema169"]
        )

        candle_above = (
            current["low"] > current["ema144"]
        )

        close_above = (
            current["close"] > current["ema169"]
        )

        if (
            broke
            and ema12_hold
            and candle_above
            and close_above
        ):

            return {
                "strategy": "B2",
                "side": "LONG",
                "index": len(data) - 2,
                "entry": current["close"],
            }

    if direction == "SHORT":

        broke = (
            previous["high"] > previous["ema144"]
            and
            previous["high"] > previous["ema169"]
        )

        ema12_hold = (
            previous["ema12"] <= previous["ema144"]
            and
            previous["ema12"] <= previous["ema169"]
        )

        candle_below = (
            current["high"] < current["ema144"]
        )

        close_below = (
            current["close"] < current["ema169"]
        )

        if (
            broke
            and
            ema12_hold
            and
            candle_below
            and
            close_below
        ):

            return {
                "strategy": "B2",
                "side": "SHORT",
                "index": len(data) - 2,
                "entry": current["close"],
            }

    return None


# =========================================================
# B1
#
# 深破：
# 价格和 EMA12 一起突破
#
# EMA12 重新穿越 EMA144
#
# 当前15M K线盘中即可触发
# =========================================================

def strategy_b1(data, direction):

    if len(data) < 4:
        return None

    previous = data[-2]
    current = data[-1]

    if current["ema144"] is None:
        return None

    if direction == "LONG":

        deep_break = (
            previous["low"] < previous["ema144"]
            and
            previous["low"] < previous["ema169"]
            and
            previous["ema12"] < previous["ema144"]
            and
            previous["ema12"] < previous["ema169"]
        )

        cross = (
            previous["ema12"] <= previous["ema144"]
            and
            current["ema12"] > current["ema144"]
        )

        if deep_break and cross:

            return {
                "strategy": "B1",
                "side": "LONG",
                "index": len(data) - 1,
                "entry": current["close"],
            }

    if direction == "SHORT":

        deep_break = (
            previous["high"] > previous["ema144"]
            and
            previous["high"] > previous["ema169"]
            and
            previous["ema12"] > previous["ema144"]
            and
            previous["ema12"] > previous["ema169"]
        )

        cross = (
            previous["ema12"] >= previous["ema144"]
            and
            current["ema12"] < current["ema144"]
        )

        if deep_break and cross:

            return {
                "strategy": "B1",
                "side": "SHORT",
                "index": len(data) - 1,
                "entry": current["close"],
            }

    return None


# =========================================================
# 计算止损止盈
#
# 信号K线不参与计算
# 使用信号之前10根已收盘15M K线
# =========================================================

def calculate_sl_tp(data, signal):

    index = signal["index"]

    if index < 10:
        return None

    previous_10 = data[
        index - 10:index
    ]

    if len(previous_10) != 10:
        return None

    entry = signal["entry"]

    if signal["side"] == "LONG":

        lowest = min(
            x["low"]
            for x in previous_10
        )

        sl = lowest * 0.995

        risk = entry - sl

        if risk <= 0:
            return None

        tp = entry + risk * 2

    else:

        highest = max(
            x["high"]
            for x in previous_10
        )

        sl = highest * 1.005

        risk = sl - entry

        if risk <= 0:
            return None

        tp = entry - risk * 2

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
    }


# =========================================================
# 价格格式
# =========================================================

def price_text(price):

    if price >= 1000:
        return f"{price:.0f}"

    if price >= 100:
        return f"{price:.2f}"

    if price >= 1:
        return f"{price:.4f}"

    if price >= 0.01:
        return f"{price:.6f}"

    return f"{price:.8f}"


# =========================================================
# Telegram发送
# =========================================================

def send_message(chat_id, text):

    if not BOT_TOKEN:
        print("[ERROR] BOT_TOKEN不存在")
        return False

    try:

        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
            },
            timeout=15,
        )

        if response.status_code != 200:

            print(
                "[TELEGRAM ERROR]",
                response.text
            )

            return False

        return True

    except Exception as e:

        print(
            "[TELEGRAM ERROR]",
            e
        )

        return False


# =========================================================
# 发送交易信号
# =========================================================

def send_signal(symbol, signal, data):

    now = time.time()

    last = last_signal.get(
        symbol,
        0
    )

    # 每个币4小时冷却
    if now - last < COOLDOWN:
        return

    risk = calculate_sl_tp(
        data,
        signal
    )

    if not risk:
        return

    if signal["side"] == "LONG":

        text = (
            "🟢 多单信号\n\n"
            f"币种：{symbol}\n"
            f"📍 入场价：{price_text(risk['entry'])}\n"
            f"🛑 止损：{price_text(risk['sl'])}\n"
            f"🎯 止盈：{price_text(risk['tp'])}\n\n"
            f"⏱ 信号时间："
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )

    else:

        text = (
            "🔴 空单信号\n\n"
            f"币种：{symbol}\n"
            f"📍 入场价：{price_text(risk['entry'])}\n"
            f"🛑 止损：{price_text(risk['sl'])}\n"
            f"🎯 止盈：{price_text(risk['tp'])}\n\n"
            f"⏱ 信号时间："
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )

    if send_message(CHAT_ID, text):

        last_signal[symbol] = now

        stats["signals"] += 1

        print(
            f"[SIGNAL] {symbol} "
            f"{signal['side']} "
            f"{signal['strategy']}"
        )


# =========================================================
# 扫描单个币
# =========================================================

def scan_symbol(symbol):

    try:

        direction = get_direction(
            symbol
        )

        directions[symbol] = direction

        if direction is None:
            return

        candles = get_klines(
            symbol,
            "15",
            800
        )

        if len(candles) < 700:
            return

        data = add_ema(candles)

        # =================================================
        # B1优先
        # =================================================

        signal = strategy_b1(
            data,
            direction
        )

        # =================================================
        # B2第二
        # =================================================

        if signal is None:

            signal = strategy_b2(
                data,
                direction
            )

        # =================================================
        # A最后
        # =================================================

        if signal is None:

            signal = strategy_a(
                data,
                direction
            )

        if signal:

            send_signal(
                symbol,
                signal,
                data
            )

    except Exception as e:

        print(
            f"[SCAN ERROR] {symbol}: {e}"
        )


# =========================================================
# 交易扫描
# =========================================================

async def trading_loop():

    print("[TRADING] started")

    while True:

        started = time.time()

        print(
            "\n========== 开始扫描 =========="
        )

        tasks = [
            asyncio.to_thread(
                scan_symbol,
                symbol
            )
            for symbol in SYMBOLS
        ]

        await asyncio.gather(
            *tasks,
            return_exceptions=True
        )

        stats["scans"] += 1

        longs = sum(
            1
            for x in directions.values()
            if x == "LONG"
        )

        shorts = sum(
            1
            for x in directions.values()
            if x == "SHORT"
        )

        neutral = sum(
            1
            for x in directions.values()
            if x is None
        )

        elapsed = time.time() - started

        print(
            f"扫描完成：{elapsed:.1f}s"
        )

        print(
            f"1H多头：{longs}"
        )

        print(
            f"1H空头：{shorts}"
        )

        print(
            f"1H无方向：{neutral}"
        )

        print(
            f"累计信号：{stats['signals']}"
        )

        print(
            f"API错误：{stats['api_errors']}"
        )

        print(
            "=============================="
        )

        await asyncio.sleep(
            SCAN_INTERVAL
        )


# =========================================================
# Telegram机器人
# =========================================================

def telegram_api(method, payload):

    try:

        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=payload,
            timeout=30,
        )

        if response.status_code != 200:
            return None

        return response.json()

    except Exception:
        return None


async def telegram_loop():

    print("[TELEGRAM] polling started")

    offset = 0

    while True:

        try:

            result = await asyncio.to_thread(
                telegram_api,
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": [
                        "message"
                    ],
                }
            )

            if not result:
                await asyncio.sleep(1)
                continue

            updates = result.get(
                "result",
                []
            )

            for update in updates:

                offset = (
                    update["update_id"] + 1
                )

                message = update.get(
                    "message"
                )

                if not message:
                    continue

                text = message.get(
                    "text",
                    ""
                ).strip()

                chat_id = str(
                    message["chat"]["id"]
                )

                # /start
                if text == "/start":

                    reply = (
                        "🤖 YeTrading Vegas Bot\n\n"
                        "机器人运行中。\n\n"
                        f"监控币种：{len(SYMBOLS)}\n"
                        "交易所：Bybit\n"
                        "方向：1H\n"
                        "入场：15M\n"
                        "策略：Vegas A / B1 / B2\n"
                        "信号冷却：每币种4小时"
                    )

                    await asyncio.to_thread(
                        send_message,
                        chat_id,
                        reply
                    )

                # /status
                elif text == "/status":

                    longs = sum(
                        1
                        for x in directions.values()
                        if x == "LONG"
                    )

                    shorts = sum(
                        1
                        for x in directions.values()
                        if x == "SHORT"
                    )

                    neutral = sum(
                        1
                        for x in directions.values()
                        if x is None
                    )

                    reply = (
                        "🤖 机器人状态：运行中\n\n"
                        f"监控币种：{len(SYMBOLS)}\n"
                        f"1H多头：{longs}\n"
                        f"1H空头：{shorts}\n"
                        f"1H无方向：{neutral}\n"
                        "Bybit：正常\n\n"
                        "策略：Vegas A / B1 / B2\n"
                        "信号冷却：每币种4小时"
                    )

                    await asyncio.to_thread(
                        send_message,
                        chat_id,
                        reply
                    )

        except Exception as e:

            print(
                f"[TELEGRAM ERROR] {e}"
            )

            await asyncio.sleep(3)


# =========================================================
# 主程序
# =========================================================

async def main_async():

    await asyncio.gather(
        telegram_loop(),
        trading_loop()
    )


def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN 环境变量不存在"
        )

    thread = threading.Thread(
        target=health_server,
        daemon=True
    )

    thread.start()

    print(
        "================================"
    )

    print(
        "YeTrading Vegas Bot"
    )

    print(
        f"监控币种：{len(SYMBOLS)}"
    )

    print(
        "交易所：Bybit"
    )

    print(
        "策略：Vegas A / B1 / B2"
    )

    print(
        "================================"
    )

    # Python 3.14 正确启动方式
    asyncio.run(
        main_async()
    )


if __name__ == "__main__":
    main()import os
import time
import asyncio
import threading
from datetime import datetime

import requests
from http.server import BaseHTTPRequestHandler, HTTPServer


# =========================================================
# 配置
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = "8289465171"

BYBIT_URL = "https://api.bybit.com"

SCAN_INTERVAL = 30
COOLDOWN = 4 * 60 * 60

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "TRXUSDT", "DOGEUSDT", "ADAUSDT", "HYPEUSDT", "ZECUSDT",
    "BCHUSDT", "LINKUSDT", "AVAXUSDT", "XLMUSDT", "SUIUSDT",
    "TONUSDT", "HBARUSDT", "SHIBUSDT", "LTCUSDT", "DOTUSDT",
    "UNIUSDT", "NEARUSDT", "ICPUSDT", "XMRUSDT", "APTUSDT",
    "TAOUSDT", "ATOMUSDT", "ARBUSDT", "CROUSDT", "VETUSDT",
    "FILUSDT", "ALGOUSDT", "RENDERUSDT", "INJUSDT", "OPUSDT",
    "IMXUSDT", "AAVEUSDT", "GRTUSDT", "SEIUSDT", "QNTUSDT",
    "THETAUSDT", "TIAUSDT", "MKRUSDT", "MNTUSDT", "PEPEUSDT",
    "WIFUSDT", "BONKUSDT", "FLOKIUSDT", "JUPUSDT", "ENAUSDT",
    "ONDOUSDT", "RUNEUSDT", "WLDUSDT", "STXUSDT", "CRVUSDT",
    "MAGMAUSDT", "FFUSDT", "LISKUSDT", "ARKUSDT", "EMBERUSDT",
    "PENGUUSDT", "JASMYUSDT",
]

EMA_PERIODS = [12, 144, 169, 576, 676]

last_signal = {}
directions = {}

stats = {
    "scans": 0,
    "signals": 0,
    "api_errors": 0,
}


# =========================================================
# Render 健康检查
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.end_headers()
        self.wfile.write(
            b"YeTrading Vegas Bot is running."
        )

    def log_message(self, format, *args):
        pass


def health_server():
    port = int(os.getenv("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(f"[HEALTH] port={port}")

    server.serve_forever()


# =========================================================
# Bybit K线
# =========================================================

def get_klines(symbol, interval, limit=800):

    try:

        response = requests.get(
            f"{BYBIT_URL}/v5/market/kline",
            params={
                "category": "linear",
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            },
            timeout=15,
        )

        if response.status_code != 200:
            stats["api_errors"] += 1
            return []

        data = response.json()

        if data.get("retCode") != 0:
            stats["api_errors"] += 1
            return []

        rows = data.get(
            "result", {}
        ).get(
            "list", []
        )

        candles = []

        for row in rows:

            candles.append({
                "time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
            })

        candles.sort(
            key=lambda x: x["time"]
        )

        return candles

    except Exception as e:

        stats["api_errors"] += 1

        print(
            f"[API ERROR] {symbol} {interval}: {e}"
        )

        return []


# =========================================================
# EMA
# =========================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    value = sum(
        values[:period]
    ) / period

    for price in values[period:]:
        value = (
            price - value
        ) * multiplier + value

    return value


def add_ema(candles):

    closes = [
        x["close"]
        for x in candles
    ]

    result = []

    for i in range(len(candles)):

        item = candles[i].copy()

        values = closes[:i + 1]

        item["ema12"] = ema(values, 12)
        item["ema144"] = ema(values, 144)
        item["ema169"] = ema(values, 169)
        item["ema576"] = ema(values, 576)
        item["ema676"] = ema(values, 676)

        result.append(item)

    return result


# =========================================================
# 1H Vegas 方向
#
# 多头：
# EMA12 > EMA144 > EMA169 > EMA576 > EMA676
# 且收盘价 > EMA144
#
# 空头反过来
# =========================================================

def get_direction(symbol):

    candles = get_klines(
        symbol,
        "60",
        800
    )

    if len(candles) < 700:
        return None

    # 排除正在形成的 1H K线
    closed = candles[:-1]

    data = add_ema(closed)

    c = data[-1]

    if c["ema676"] is None:
        return None

    bullish = (
        c["ema12"] >
        c["ema144"] >
        c["ema169"] >
        c["ema576"] >
        c["ema676"]
        and
        c["close"] > c["ema144"]
    )

    bearish = (
        c["ema12"] <
        c["ema144"] <
        c["ema169"] <
        c["ema576"] <
        c["ema676"]
        and
        c["close"] < c["ema144"]
    )

    if bullish:
        return "LONG"

    if bearish:
        return "SHORT"

    return None


# =========================================================
# A 策略
#
# 正常回踩 EMA144 ±2%
#
# 15M 收盘不能有效突破 EMA169
#
# 然后收盘重新站上/跌破 EMA12
# =========================================================

def strategy_a(data, direction):

    if len(data) < 3:
        return None

    c = data[-2]

    if c["ema169"] is None:
        return None

    ema144 = c["ema144"]
    ema169 = c["ema169"]
    ema12 = c["ema12"]

    zone_low = ema144 * 0.98
    zone_high = ema144 * 1.02

    entered = (
        c["low"] <= zone_high
        and
        c["high"] >= zone_low
    )

    if direction == "LONG":

        structure_ok = (
            c["close"] >= ema169
        )

        confirm = (
            c["close"] > ema12
        )

        if entered and structure_ok and confirm:

            return {
                "strategy": "A",
                "side": "LONG",
                "index": len(data) - 2,
                "entry": c["close"],
            }

    if direction == "SHORT":

        structure_ok = (
            c["close"] <= ema169
        )

        confirm = (
            c["close"] < ema12
        )

        if entered and structure_ok and confirm:

            return {
                "strategy": "A",
                "side": "SHORT",
                "index": len(data) - 2,
                "entry": c["close"],
            }

    return None


# =========================================================
# B2
#
# 价格突破144/169
# 但 EMA12 没有跟着突破
# 然后价格重新收回
# =========================================================

def strategy_b2(data, direction):

    if len(data) < 4:
        return None

    previous = data[-3]
    current = data[-2]

    if current["ema169"] is None:
        return None

    if direction == "LONG":

        broke = (
            previous["low"] < previous["ema144"]
            and
            previous["low"] < previous["ema169"]
        )

        ema12_hold = (
            previous["ema12"] >= previous["ema144"]
            and
            previous["ema12"] >= previous["ema169"]
        )

        candle_above = (
            current["low"] > current["ema144"]
        )

        close_above = (
            current["close"] > current["ema169"]
        )

        if (
            broke
            and ema12_hold
            and candle_above
            and close_above
        ):

            return {
                "strategy": "B2",
                "side": "LONG",
                "index": len(data) - 2,
                "entry": current["close"],
            }

    if direction == "SHORT":

        broke = (
            previous["high"] > previous["ema144"]
            and
            previous["high"] > previous["ema169"]
        )

        ema12_hold = (
            previous["ema12"] <= previous["ema144"]
            and
            previous["ema12"] <= previous["ema169"]
        )

        candle_below = (
            current["high"] < current["ema144"]
        )

        close_below = (
            current["close"] < current["ema169"]
        )

        if (
            broke
            and
            ema12_hold
            and
            candle_below
            and
            close_below
        ):

            return {
                "strategy": "B2",
                "side": "SHORT",
                "index": len(data) - 2,
                "entry": current["close"],
            }

    return None


# =========================================================
# B1
#
# 深破：
# 价格和 EMA12 一起突破
#
# EMA12 重新穿越 EMA144
#
# 当前15M K线盘中即可触发
# =========================================================

def strategy_b1(data, direction):

    if len(data) < 4:
        return None

    previous = data[-2]
    current = data[-1]

    if current["ema144"] is None:
        return None

    if direction == "LONG":

        deep_break = (
            previous["low"] < previous["ema144"]
            and
            previous["low"] < previous["ema169"]
            and
            previous["ema12"] < previous["ema144"]
            and
            previous["ema12"] < previous["ema169"]
        )

        cross = (
            previous["ema12"] <= previous["ema144"]
            and
            current["ema12"] > current["ema144"]
        )

        if deep_break and cross:

            return {
                "strategy": "B1",
                "side": "LONG",
                "index": len(data) - 1,
                "entry": current["close"],
            }

    if direction == "SHORT":

        deep_break = (
            previous["high"] > previous["ema144"]
            and
            previous["high"] > previous["ema169"]
            and
            previous["ema12"] > previous["ema144"]
            and
            previous["ema12"] > previous["ema169"]
        )

        cross = (
            previous["ema12"] >= previous["ema144"]
            and
            current["ema12"] < current["ema144"]
        )

        if deep_break and cross:

            return {
                "strategy": "B1",
                "side": "SHORT",
                "index": len(data) - 1,
                "entry": current["close"],
            }

    return None


# =========================================================
# 计算止损止盈
#
# 信号K线不参与计算
# 使用信号之前10根已收盘15M K线
# =========================================================

def calculate_sl_tp(data, signal):

    index = signal["index"]

    if index < 10:
        return None

    previous_10 = data[
        index - 10:index
    ]

    if len(previous_10) != 10:
        return None

    entry = signal["entry"]

    if signal["side"] == "LONG":

        lowest = min(
            x["low"]
            for x in previous_10
        )

        sl = lowest * 0.995

        risk = entry - sl

        if risk <= 0:
            return None

        tp = entry + risk * 2

    else:

        highest = max(
            x["high"]
            for x in previous_10
        )

        sl = highest * 1.005

        risk = sl - entry

        if risk <= 0:
            return None

        tp = entry - risk * 2

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
    }


# =========================================================
# 价格格式
# =========================================================

def price_text(price):

    if price >= 1000:
        return f"{price:.0f}"

    if price >= 100:
        return f"{price:.2f}"

    if price >= 1:
        return f"{price:.4f}"

    if price >= 0.01:
        return f"{price:.6f}"

    return f"{price:.8f}"


# =========================================================
# Telegram发送
# =========================================================

def send_message(chat_id, text):

    if not BOT_TOKEN:
        print("[ERROR] BOT_TOKEN不存在")
        return False

    try:

        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
            },
            timeout=15,
        )

        if response.status_code != 200:

            print(
                "[TELEGRAM ERROR]",
                response.text
            )

            return False

        return True

    except Exception as e:

        print(
            "[TELEGRAM ERROR]",
            e
        )

        return False


# =========================================================
# 发送交易信号
# =========================================================

def send_signal(symbol, signal, data):

    now = time.time()

    last = last_signal.get(
        symbol,
        0
    )

    # 每个币4小时冷却
    if now - last < COOLDOWN:
        return

    risk = calculate_sl_tp(
        data,
        signal
    )

    if not risk:
        return

    if signal["side"] == "LONG":

        text = (
            "🟢 多单信号\n\n"
            f"币种：{symbol}\n"
            f"📍 入场价：{price_text(risk['entry'])}\n"
            f"🛑 止损：{price_text(risk['sl'])}\n"
            f"🎯 止盈：{price_text(risk['tp'])}\n\n"
            f"⏱ 信号时间："
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )

    else:

        text = (
            "🔴 空单信号\n\n"
            f"币种：{symbol}\n"
            f"📍 入场价：{price_text(risk['entry'])}\n"
            f"🛑 止损：{price_text(risk['sl'])}\n"
            f"🎯 止盈：{price_text(risk['tp'])}\n\n"
            f"⏱ 信号时间："
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )

    if send_message(CHAT_ID, text):

        last_signal[symbol] = now

        stats["signals"] += 1

        print(
            f"[SIGNAL] {symbol} "
            f"{signal['side']} "
            f"{signal['strategy']}"
        )


# =========================================================
# 扫描单个币
# =========================================================

def scan_symbol(symbol):

    try:

        direction = get_direction(
            symbol
        )

        directions[symbol] = direction

        if direction is None:
            return

        candles = get_klines(
            symbol,
            "15",
            800
        )

        if len(candles) < 700:
            return

        data = add_ema(candles)

        # =================================================
        # B1优先
        # =================================================

        signal = strategy_b1(
            data,
            direction
        )

        # =================================================
        # B2第二
        # =================================================

        if signal is None:

            signal = strategy_b2(
                data,
                direction
            )

        # =================================================
        # A最后
        # =================================================

        if signal is None:

            signal = strategy_a(
                data,
                direction
            )

        if signal:

            send_signal(
                symbol,
                signal,
                data
            )

    except Exception as e:

        print(
            f"[SCAN ERROR] {symbol}: {e}"
        )


# =========================================================
# 交易扫描
# =========================================================

async def trading_loop():

    print("[TRADING] started")

    while True:

        started = time.time()

        print(
            "\n========== 开始扫描 =========="
        )

        tasks = [
            asyncio.to_thread(
                scan_symbol,
                symbol
            )
            for symbol in SYMBOLS
        ]

        await asyncio.gather(
            *tasks,
            return_exceptions=True
        )

        stats["scans"] += 1

        longs = sum(
            1
            for x in directions.values()
            if x == "LONG"
        )

        shorts = sum(
            1
            for x in directions.values()
            if x == "SHORT"
        )

        neutral = sum(
            1
            for x in directions.values()
            if x is None
        )

        elapsed = time.time() - started

        print(
            f"扫描完成：{elapsed:.1f}s"
        )

        print(
            f"1H多头：{longs}"
        )

        print(
            f"1H空头：{shorts}"
        )

        print(
            f"1H无方向：{neutral}"
        )

        print(
            f"累计信号：{stats['signals']}"
        )

        print(
            f"API错误：{stats['api_errors']}"
        )

        print(
            "=============================="
        )

        await asyncio.sleep(
            SCAN_INTERVAL
        )


# =========================================================
# Telegram机器人
# =========================================================

def telegram_api(method, payload):

    try:

        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=payload,
            timeout=30,
        )

        if response.status_code != 200:
            return None

        return response.json()

    except Exception:
        return None


async def telegram_loop():

    print("[TELEGRAM] polling started")

    offset = 0

    while True:

        try:

            result = await asyncio.to_thread(
                telegram_api,
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": [
                        "message"
                    ],
                }
            )

            if not result:
                await asyncio.sleep(1)
                continue

            updates = result.get(
                "result",
                []
            )

            for update in updates:

                offset = (
                    update["update_id"] + 1
                )

                message = update.get(
                    "message"
                )

                if not message:
                    continue

                text = message.get(
                    "text",
                    ""
                ).strip()

                chat_id = str(
                    message["chat"]["id"]
                )

                # /start
                if text == "/start":

                    reply = (
                        "🤖 YeTrading Vegas Bot\n\n"
                        "机器人运行中。\n\n"
                        f"监控币种：{len(SYMBOLS)}\n"
                        "交易所：Bybit\n"
                        "方向：1H\n"
                        "入场：15M\n"
                        "策略：Vegas A / B1 / B2\n"
                        "信号冷却：每币种4小时"
                    )

                    await asyncio.to_thread(
                        send_message,
                        chat_id,
                        reply
                    )

                # /status
                elif text == "/status":

                    longs = sum(
                        1
                        for x in directions.values()
                        if x == "LONG"
                    )

                    shorts = sum(
                        1
                        for x in directions.values()
                        if x == "SHORT"
                    )

                    neutral = sum(
                        1
                        for x in directions.values()
                        if x is None
                    )

                    reply = (
                        "🤖 机器人状态：运行中\n\n"
                        f"监控币种：{len(SYMBOLS)}\n"
                        f"1H多头：{longs}\n"
                        f"1H空头：{shorts}\n"
                        f"1H无方向：{neutral}\n"
                        "Bybit：正常\n\n"
                        "策略：Vegas A / B1 / B2\n"
                        "信号冷却：每币种4小时"
                    )

                    await asyncio.to_thread(
                        send_message,
                        chat_id,
                        reply
                    )

        except Exception as e:

            print(
                f"[TELEGRAM ERROR] {e}"
            )

            await asyncio.sleep(3)


# =========================================================
# 主程序
# =========================================================

async def main_async():

    await asyncio.gather(
        telegram_loop(),
        trading_loop()
    )


def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN 环境变量不存在"
        )

    thread = threading.Thread(
        target=health_server,
        daemon=True
    )

    thread.start()

    print(
        "================================"
    )

    print(
        "YeTrading Vegas Bot"
    )

    print(
        f"监控币种：{len(SYMBOLS)}"
    )

    print(
        "交易所：Bybit"
    )

    print(
        "策略：Vegas A / B1 / B2"
    )

    print(
        "================================"
    )

    # Python 3.14 正确启动方式
    asyncio.run(
        main_async()
    )


if __name__ == "__main__":
    main()
