import os
import time
import asyncio
import threading
from datetime import datetime

import requests
from http.server import BaseHTTPRequestHandler, HTTPServer


# =========================================================
# 基础配置
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = "8289465171"

BYBIT_URL = "https://api.bybit.com"

SCAN_INTERVAL = 30
COOLDOWN_SECONDS = 4 * 60 * 60

EMA_FAST = 12
EMA_144 = 144
EMA_169 = 169
EMA_576 = 576
EMA_676 = 676


# =========================================================
# 62 个监控币种
# =========================================================

SYMBOLS = [
    # Core 45
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

    # Extension 10
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

    # Extension 7
    "MAGMAUSDT",
    "FFUSDT",
    "LISKUSDT",
    "ARKUSDT",
    "EMBERUSDT",
    "PENGUUSDT",
    "JASMYUSDT",
]


# =========================================================
# 全局状态
# =========================================================

last_signal_time = {}

last_direction_refresh = None

direction_cache = {}

stats = {
    "signals": 0,
    "api_errors": 0,
    "scan_count": 0,
}


# =========================================================
# HTTP 健康检查
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

        self.wfile.write(
            b"YeTrading Vegas Bot is running."
        )

    def log_message(self, format, *args):
        return


def run_health_server():
    port = int(os.getenv("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(f"Health server listening on port {port}")

    server.serve_forever()


# =========================================================
# Bybit API
# =========================================================

def get_klines(symbol, interval, limit=800):

    try:

        url = f"{BYBIT_URL}/v5/market/kline"

        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }

        response = requests.get(
            url,
            params=params,
            timeout=15
        )

        if response.status_code != 200:
            stats["api_errors"] += 1
            return []

        data = response.json()

        if data.get("retCode") != 0:
            stats["api_errors"] += 1
            return []

        rows = data.get("result", {}).get("list", [])

        if not rows:
            return []

        candles = []

        for row in rows:

            candles.append({
                "time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
            })

        # Bybit 返回通常是新 -> 旧
        candles.sort(key=lambda x: x["time"])

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

def calculate_ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    ema = sum(values[:period]) / period

    for price in values[period:]:

        ema = (
            price - ema
        ) * multiplier + ema

    return ema


def add_emas(candles):

    closes = [
        candle["close"]
        for candle in candles
    ]

    result = []

    for i in range(len(candles)):

        sub_closes = closes[:i + 1]

        candle = candles[i].copy()

        candle["ema12"] = calculate_ema(
            sub_closes,
            EMA_FAST
        )

        candle["ema144"] = calculate_ema(
            sub_closes,
            EMA_144
        )

        candle["ema169"] = calculate_ema(
            sub_closes,
            EMA_169
        )

        candle["ema576"] = calculate_ema(
            sub_closes,
            EMA_576
        )

        candle["ema676"] = calculate_ema(
            sub_closes,
            EMA_676
        )

        result.append(candle)

    return result


# =========================================================
# 1H Vegas 严格方向
# =========================================================

def get_1h_direction(symbol):

    candles = get_klines(
        symbol,
        "60",
        800
    )

    if len(candles) < 700:

        print(
            f"[1H DATA] {symbol} 数据不足：{len(candles)}"
        )

        return None

    # 排除当前正在形成的 1H K线
    closed = candles[:-1]

    candles_with_ema = add_emas(closed)

    current = candles_with_ema[-1]

    if (
        current["ema12"] is None
        or current["ema144"] is None
        or current["ema169"] is None
        or current["ema576"] is None
        or current["ema676"] is None
    ):
        return None

    close = current["close"]

    # ==========================
    # 严格多头
    # ==========================

    bullish = (
        current["ema12"]
        > current["ema144"]
        > current["ema169"]
        > current["ema576"]
        > current["ema676"]
        and close > current["ema144"]
    )

    # ==========================
    # 严格空头
    # ==========================

    bearish = (
        current["ema12"]
        < current["ema144"]
        < current["ema169"]
        < current["ema576"]
        < current["ema676"]
        and close < current["ema144"]
    )

    if bullish:
        return "LONG"

    if bearish:
        return "SHORT"

    return None


# =========================================================
# 15M EMA
# =========================================================

def prepare_15m(candles):

    return add_emas(candles)


# =========================================================
# A 策略
# =========================================================

def check_strategy_a(candles, direction):

    if len(candles) < 700:
        return None

    data = prepare_15m(candles)

    # 当前正在形成的 K线
    current = data[-1]

    # 最近一根已经收盘 K线
    signal = data[-2]

    if (
        signal["ema12"] is None
        or signal["ema144"] is None
        or signal["ema169"] is None
    ):
        return None

    price = signal["close"]

    ema144 = signal["ema144"]
    ema169 = signal["ema169"]
    ema12 = signal["ema12"]

    # =====================================================
    # LONG
    # =====================================================

    if direction == "LONG":

        # 价格进入 EMA144 ±2%
        zone_low = ema144 * 0.98
        zone_high = ema144 * 1.02

        entered_zone = (
            signal["low"] <= zone_high
            and signal["high"] >= zone_low
        )

        # 不能收盘跌破 EMA169
        structure_ok = (
            signal["close"] >= ema169
        )

        # 价格重新站上 EMA12
        confirmation = (
            signal["close"] > ema12
        )

        if (
            entered_zone
            and structure_ok
            and confirmation
        ):

            return {
                "strategy": "A",
                "side": "LONG",
                "entry": signal["close"],
                "signal_index": len(data) - 2,
            }

    # =====================================================
    # SHORT
    # =====================================================

    if direction == "SHORT":

        zone_low = ema144 * 0.98
        zone_high = ema144 * 1.02

        entered_zone = (
            signal["low"] <= zone_high
            and signal["high"] >= zone_low
        )

        structure_ok = (
            signal["close"] <= ema169
        )

        confirmation = (
            signal["close"] < ema12
        )

        if (
            entered_zone
            and structure_ok
            and confirmation
        ):

            return {
                "strategy": "A",
                "side": "SHORT",
                "entry": signal["close"],
                "signal_index": len(data) - 2,
            }

    return None


# =========================================================
# B2 策略
# =========================================================

def check_strategy_b2(candles, direction):

    if len(candles) < 700:
        return None

    data = prepare_15m(candles)

    signal = data[-2]

    previous = data[-3]

    if (
        signal["ema12"] is None
        or signal["ema144"] is None
        or signal["ema169"] is None
    ):
        return None

    # =====================================================
    # LONG
    # =====================================================

    if direction == "LONG":

        # 前一根出现向下突破
        broke_down = (
            previous["low"] < previous["ema144"]
            and previous["low"] < previous["ema169"]
        )

        # EMA12 没有跟着跌破
        ema12_stays_above = (
            previous["ema12"] >= previous["ema144"]
            and previous["ema12"] >= previous["ema169"]
        )

        # 确认 K 线实体完全站在 EMA144 上方
        candle_above_144 = (
            signal["low"] > signal["ema144"]
        )

        # 收盘站上 EMA169
        close_above_169 = (
            signal["close"] > signal["ema169"]
        )

        if (
            broke_down
            and ema12_stays_above
            and candle_above_144
            and close_above_169
        ):

            return {
                "strategy": "B2",
                "side": "LONG",
                "entry": signal["close"],
                "signal_index": len(data) - 2,
            }

    # =====================================================
    # SHORT
    # =====================================================

    if direction == "SHORT":

        broke_up = (
            previous["high"] > previous["ema144"]
            and previous["high"] > previous["ema169"]
        )

        ema12_stays_below = (
            previous["ema12"] <= previous["ema144"]
            and previous["ema12"] <= previous["ema169"]
        )

        candle_below_144 = (
            signal["high"] < signal["ema144"]
        )

        close_below_169 = (
            signal["close"] < signal["ema169"]
        )

        if (
            broke_up
            and ema12_stays_below
            and candle_below_144
            and close_below_169
        ):

            return {
                "strategy": "B2",
                "side": "SHORT",
                "entry": signal["close"],
                "signal_index": len(data) - 2,
            }

    return None


# =========================================================
# B1 策略
#
# 深破：
# 价格 + EMA12 一起突破
#
# 然后 EMA12 穿越 EMA144
#
# 盘中立即触发
# =========================================================

def check_strategy_b1(candles, direction):

    if len(candles) < 700:
        return None

    data = prepare_15m(candles)

    # 使用最后一根正在形成的 K线
    current = data[-1]

    previous = data[-2]

    if (
        current["ema12"] is None
        or current["ema144"] is None
        or current["ema169"] is None
        or previous["ema12"] is None
        or previous["ema144"] is None
        or previous["ema169"] is None
    ):
        return None

    # =====================================================
    # LONG
    # =====================================================

    if direction == "LONG":

        # 之前 EMA12 已经跌到 144/169 下方
        deep_break = (
            previous["ema12"] < previous["ema144"]
            and previous["ema12"] < previous["ema169"]
            and previous["low"] < previous["ema144"]
            and previous["low"] < previous["ema169"]
        )

        # 当前 EMA12 已经重新站上 EMA144
        crossed = (
            previous["ema12"]
            <= previous["ema144"]
            and current["ema12"]
            > current["ema144"]
        )

        if deep_break and crossed:

            return {
                "strategy": "B1",
                "side": "LONG",
                "entry": current["close"],
                "signal_index": len(data) - 1,
            }

    # =====================================================
    # SHORT
    # =====================================================

    if direction == "SHORT":

        deep_break = (
            previous["ema12"] > previous["ema144"]
            and previous["ema12"] > previous["ema169"]
            and previous["high"] > previous["ema144"]
            and previous["high"] > previous["ema169"]
        )

        crossed = (
            previous["ema12"]
            >= previous["ema144"]
            and current["ema12"]
            < current["ema144"]
        )

        if deep_break and crossed:

            return {
                "strategy": "B1",
                "side": "SHORT",
                "entry": current["close"],
                "signal_index": len(data) - 1,
            }

    return None


# =========================================================
# 止损 / 止盈
# =========================================================

def calculate_sl_tp(candles, signal):

    data = prepare_15m(candles)

    signal_index = signal["signal_index"]

    side = signal["side"]

    entry = signal["entry"]

    # =====================================================
    # 信号 K线之前的 10 根已收盘 K线
    #
    # signal_index 前面 10 根
    # =====================================================

    start = signal_index - 10

    end = signal_index

    if start < 0:
        return None

    previous_10 = data[start:end]

    if len(previous_10) != 10:
        return None

    if side == "LONG":

        lowest_low = min(
            candle["low"]
            for candle in previous_10
        )

        sl = lowest_low * 0.995

        risk = entry - sl

        if risk <= 0:
            return None

        tp = entry + 2 * risk

    else:

        highest_high = max(
            candle["high"]
            for candle in previous_10
        )

        sl = highest_high * 1.005

        risk = sl - entry

        if risk <= 0:
            return None

        tp = entry - 2 * risk

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
    }


# =========================================================
# Telegram
# =========================================================

def send_telegram_message(message):

    if not BOT_TOKEN:
        print("BOT_TOKEN 不存在")
        return False

    try:

        url = (
            f"https://api.telegram.org/"
            f"bot{BOT_TOKEN}/sendMessage"
        )

        payload = {
            "chat_id": CHAT_ID,
            "text": message,
        }

        response = requests.post(
            url,
            json=payload,
            timeout=15
        )

        if response.status_code != 200:

            print(
                f"[Telegram ERROR] "
                f"{response.status_code} "
                f"{response.text}"
            )

            return False

        return True

    except Exception as e:

        print(
            f"[Telegram ERROR] {e}"
        )

        return False


# =========================================================
# 格式化价格
# =========================================================

def format_price(price):

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
# 生成信号
# =========================================================

def process_signal(symbol, signal, candles):

    now = time.time()

    # 4小时冷却
    last_time = last_signal_time.get(symbol, 0)

    if now - last_time < COOLDOWN_SECONDS:

        return

    risk_data = calculate_sl_tp(
        candles,
        signal
    )

    if not risk_data:
        return

    side = signal["side"]

    entry = risk_data["entry"]

    sl = risk_data["sl"]

    tp = risk_data["tp"]

    timestamp = datetime.now().strftime(
        "%Y-%m-%d %H:%M"
    )

    if side == "LONG":

        message = (
            "🟢 多单信号\n\n"
            f"币种：{symbol}\n"
            f"📍 入场价：{format_price(entry)}\n"
            f"🛑 止损：{format_price(sl)}\n"
            f"🎯 止盈：{format_price(tp)}\n\n"
            f"⏱ 信号时间：{timestamp}"
        )

    else:

        message = (
            "🔴 空单信号\n\n"
            f"币种：{symbol}\n"
            f"📍 入场价：{format_price(entry)}\n"
            f"🛑 止损：{format_price(sl)}\n"
            f"🎯 止盈：{format_price(tp)}\n\n"
            f"⏱ 信号时间：{timestamp}"
        )

    success = send_telegram_message(
        message
    )

    if success:

        last_signal_time[symbol] = now

        stats["signals"] += 1

        print(
            f"[SIGNAL] {symbol} "
            f"{side} "
            f"{signal['strategy']} "
            f"entry={entry}"
        )


# =========================================================
# 处理单个币种
# =========================================================

def scan_symbol(symbol):

    try:

        # =================================================
        # 1H 定方向
        # =================================================

        direction = get_1h_direction(
            symbol
        )

        direction_cache[symbol] = direction

        if direction is None:

            return

        # =================================================
        # 获取 15M
        # =================================================

        candles = get_klines(
            symbol,
            "15",
            800
        )

        if len(candles) < 700:
            return

        # =================================================
        # 优先级：
        #
        # B1 > B2 > A
        # =================================================

        signal = check_strategy_b1(
            candles,
            direction
        )

        if signal is None:

            signal = check_strategy_b2(
                candles,
                direction
            )

        if signal is None:

            signal = check_strategy_a(
                candles,
                direction
            )

        if signal is not None:

            process_signal(
                symbol,
                signal,
                candles
            )

    except Exception as e:

        print(
            f"[SCAN ERROR] "
            f"{symbol}: {e}"
        )


# =========================================================
# 交易扫描循环
# =========================================================

async def trading_loop():

    global last_direction_refresh

    print("交易扫描循环启动")

    while True:

        start_time = time.time()

        print(
            "\n"
            "========================================"
        )

        print(
            f"开始扫描 "
            f"{len(SYMBOLS)} 个币种"
        )

        # =================================================
        # requests 是同步的
        # 放到线程中执行，避免阻塞 Telegram
        # =================================================

        tasks = []

        for symbol in SYMBOLS:

            tasks.append(
                asyncio.to_thread(
                    scan_symbol,
                    symbol
                )
            )

        await asyncio.gather(
            *tasks,
            return_exceptions=True
        )

        stats["scan_count"] += 1

        last_direction_refresh = datetime.now()

        long_count = sum(
            1
            for value in direction_cache.values()
            if value == "LONG"
        )

        short_count = sum(
            1
            for value in direction_cache.values()
            if value == "SHORT"
        )

        neutral_count = sum(
            1
            for value in direction_cache.values()
            if value is None
        )

        elapsed = time.time() - start_time

        print(
            f"扫描完成 "
            f"耗时 {elapsed:.1f}s"
        )

        print(
            f"1H 多头：{long_count} "
            f"| 空头：{short_count} "
            f"| 无方向：{neutral_count}"
        )

        print(
            f"累计信号：{stats['signals']} "
            f"| API错误：{stats['api_errors']}"
        )

        print(
            "========================================"
        )

        await asyncio.sleep(
            SCAN_INTERVAL
        )


# =========================================================
# Telegram 命令
# =========================================================

def telegram_api(method, payload=None):

    if not BOT_TOKEN:
        return None

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    try:

        response = requests.post(
            url,
            json=payload or {},
            timeout=30
        )

        if response.status_code != 200:
            return None

        return response.json()

    except Exception:
        return None


# =========================================================
# Telegram Polling
# =========================================================

async def telegram_loop():

    print("Telegram polling 启动")

    offset = 0

    while True:

        try:

            result = await asyncio.to_thread(
                telegram_api,
                "getUpdates",
                {
                    "timeout": 25,
                    "offset": offset,
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

                offset = update["update_id"] + 1

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

                # =========================================
                # /start
                # =========================================

                if text == "/start":

                    reply = (
                        "🤖 YeTrading Vegas Bot\n\n"
                        "机器人已启动。\n"
                        "正在监控 Vegas A / B1 / B2。\n\n"
                        f"监控币种：{len(SYMBOLS)}\n"
                        "交易所：Bybit\n"
                        "方向周期：1H\n"
                        "入场周期：15M\n"
                        "信号冷却：每币种4小时"
                    )

                    await asyncio.to_thread(
                        send_telegram_message_to_chat,
                        chat_id,
                        reply
                    )

                # =========================================
                # /status
                # =========================================

                elif text == "/status":

                    long_count = sum(
                        1
                        for value in direction_cache.values()
                        if value == "LONG"
                    )

                    short_count = sum(
                        1
                        for value in direction_cache.values()
                        if value == "SHORT"
                    )

                    neutral_count = sum(
                        1
                        for value in direction_cache.values()
                        if value is None
                    )

                    if last_direction_refresh:

                        refresh_time = (
                            last_direction_refresh
                            .strftime(
                                "%Y-%m-%d %H:%M"
                            )
                        )

                    else:

                        refresh_time = "尚未刷新"

                    reply = (
                        "🤖 机器人状态：运行中\n\n"
                        f"监控币种：{len(SYMBOLS)}\n"
                        f"1H多头：{long_count}\n"
                        f"1H空头：{short_count}\n"
                        f"1H无方向：{neutral_count}\n"
                        "Bybit：正常\n"
                        f"最后方向刷新：{refresh_time}\n\n"
                        "策略：Vegas A / B1 / B2\n"
                        "信号冷却：每币种4小时"
                    )

                    await asyncio.to_thread(
                        send_telegram_message_to_chat,
                        chat_id,
                        reply
                    )

        except Exception as e:

            print(
                f"[Telegram polling ERROR] {e}"
            )

            await asyncio.sleep(3)


def send_telegram_message_to_chat(
    chat_id,
    message
):

    if not BOT_TOKEN:
        return False

    try:

        url = (
            f"https://api.telegram.org/"
            f"bot{BOT_TOKEN}/sendMessage"
        )

        response = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
            },
            timeout=15
        )

        return response.status_code == 200

    except Exception as e:

        print(
            f"[Telegram SEND ERROR] {e}"
        )

        return False


# =========================================================
# Python 3.14 正确启动方式
# =========================================================

async def async_main():

    await asyncio.gather(
        telegram_loop(),
        trading_loop()
    )


def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "环境变量 BOT_TOKEN 不存在"
        )

    # Render 健康检查服务器
    health_thread = threading.Thread(
        target=run_health_server,
        daemon=True
    )

    health_thread.start()

    print(
        "========================================"
    )

    print(
        "YeTrading Vegas Bot"
    )

    print(
        f"监控币种：{len(SYMBOLS)}"
    )

    print(
        "交易所：Bybit"
   
