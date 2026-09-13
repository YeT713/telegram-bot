import os
import time
import asyncio
import threading
import requests

from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer


# ============================================================
# 基础配置
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID", "8289465171")

BYBIT_URL = "https://api.bybit.com"

PORT = int(os.getenv("PORT", "10000"))

COOLDOWN_SECONDS = 4 * 60 * 60


# ============================================================
# 监控币种：62个
# ============================================================

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


# ============================================================
# 全局状态
# ============================================================

last_signal_time = {}

telegram_offset = 0


# ============================================================
# Render Web Service 健康端口
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            b"Vegas Trading Bot is running."
        )

    def log_message(self, format, *args):
        return


def start_web_server():

    print(
        f"HTTP health server starting on port {PORT}"
    )

    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    print(
        f"HTTP health server started on port {PORT}"
    )

    server.serve_forever()


# ============================================================
# Telegram
# ============================================================

def telegram_url(method):

    return (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )


def send_message(text):

    if not BOT_TOKEN:

        print("❌ BOT_TOKEN 未设置")

        return False

    try:

        response = requests.post(
            telegram_url("sendMessage"),
            json={
                "chat_id": CHAT_ID,
                "text": text
            },
            timeout=20
        )

        if response.status_code != 200:

            print(
                "Telegram发送失败:",
                response.status_code,
                response.text
            )

            return False

        return True

    except Exception as e:

        print(
            "Telegram发送异常:",
            e
        )

        return False


def get_updates():

    global telegram_offset

    if not BOT_TOKEN:
        return []

    try:

        response = requests.get(
            telegram_url("getUpdates"),
            params={
                "offset": telegram_offset,
                "timeout": 10
            },
            timeout=20
        )

        if response.status_code != 200:

            print(
                "Telegram getUpdates失败:",
                response.status_code
            )

            return []

        data = response.json()

        if not data.get("ok"):

            return []

        updates = data.get(
            "result",
            []
        )

        if updates:

            telegram_offset = (
                updates[-1]["update_id"] + 1
            )

        return updates

    except Exception as e:

        print(
            "Telegram获取消息异常:",
            e
        )

        return []


# ============================================================
# Bybit K线
# ============================================================

def get_klines(
    symbol,
    interval,
    limit=700
):

    try:

        response = requests.get(
            f"{BYBIT_URL}/v5/market/kline",
            params={
                "category": "linear",
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            },
            timeout=20
        )

        if response.status_code != 200:

            print(
                f"{symbol} Bybit HTTP错误:",
                response.status_code
            )

            return []

        data = response.json()

        if data.get("retCode") != 0:

            print(
                f"{symbol} Bybit API错误:",
                data
            )

            return []

        rows = (
            data
            .get("result", {})
            .get("list", [])
        )

        # Bybit返回最新K线在前
        rows = list(reversed(rows))

        candles = []

        for row in rows:

            candles.append({

                "time": int(row[0]),

                "open": float(row[1]),

                "high": float(row[2]),

                "low": float(row[3]),

                "close": float(row[4]),

                "volume": float(row[5])

            })

        return candles

    except Exception as e:

        print(
            f"{symbol} Bybit请求异常:",
            e
        )

        return []


# ============================================================
# EMA
# ============================================================

def calculate_ema(values, period):

    if len(values) < period:

        return [None] * len(values)

    result = [None] * len(values)

    initial = (
        sum(values[:period])
        / period
    )

    result[period - 1] = initial

    multiplier = 2 / (period + 1)

    previous = initial

    for i in range(
        period,
        len(values)
    ):

        current = (
            (values[i] - previous)
            * multiplier
            + previous
        )

        result[i] = current

        previous = current

    return result


def add_indicators(candles):

    closes = [
        candle["close"]
        for candle in candles
    ]

    ema12 = calculate_ema(
        closes,
        12
    )

    ema144 = calculate_ema(
        closes,
        144
    )

    ema169 = calculate_ema(
        closes,
        169
    )

    ema576 = calculate_ema(
        closes,
        576
    )

    ema676 = calculate_ema(
        closes,
        676
    )

    result = []

    for i, candle in enumerate(candles):

        item = candle.copy()

        item["ema12"] = ema12[i]
        item["ema144"] = ema144[i]
        item["ema169"] = ema169[i]
        item["ema576"] = ema576[i]
        item["ema676"] = ema676[i]

        result.append(item)

    return result


# ============================================================
# 1H Vegas严格方向
# ============================================================

def get_1h_direction(data):

    if len(data) < 700:

        return "NEUTRAL"

    # 最后一根通常是正在形成中的K线
    # 使用最后一根已经完成的K线
    candle = data[-2]

    e12 = candle["ema12"]
    e144 = candle["ema144"]
    e169 = candle["ema169"]
    e576 = candle["ema576"]
    e676 = candle["ema676"]

    close = candle["close"]

    if None in [
        e12,
        e144,
        e169,
        e576,
        e676
    ]:

        return "NEUTRAL"

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
# 价格格式
# ============================================================

def format_price(price):

    if price is None:

        return "N/A"

    if price >= 1000:

        return f"{price:.2f}"

    if price >= 1:

        return f"{price:.4f}"

    if price >= 0.01:

        return f"{price:.6f}"

    return f"{price:.8f}"


# ============================================================
# Vegas A
# ============================================================

def strategy_a(
    data15,
    direction
):

    if direction not in [
        "LONG",
        "SHORT"
    ]:

        return None

    if len(data15) < 20:

        return None

    # 最后一根已经完成的15M K线
    candle = data15[-2]

    e12 = candle["ema12"]
    e144 = candle["ema144"]
    e169 = candle["ema169"]

    if None in [
        e12,
        e144,
        e169
    ]:

        return None

    close = candle["close"]

    zone_upper = e144 * 1.02
    zone_lower = e144 * 0.98

    entered_zone = (
        candle["low"] <= zone_upper
        and candle["high"] >= zone_lower
    )

    if not entered_zone:

        return None

    # LONG
    if direction == "LONG":

        # 不允许有效跌破169
        if close < e169:

            return None

        # 收盘重新站上12
        if close > e12:

            return {
                "side": "LONG",
                "index": len(data15) - 2,
                "strategy": "A"
            }

    # SHORT
    if direction == "SHORT":

        # 不允许有效突破169
        if close > e169:

            return None

        # 收盘重新跌破12
        if close < e12:

            return {
                "side": "SHORT",
                "index": len(data15) - 2,
                "strategy": "A"
            }

    return None


# ============================================================
# Vegas B2
# ============================================================

def strategy_b2(
    data15,
    direction
):

    if direction not in [
        "LONG",
        "SHORT"
    ]:

        return None

    if len(data15) < 20:

        return None

    previous = data15[-3]

    current = data15[-2]

    p12 = previous["ema12"]
    p144 = previous["ema144"]
    p169 = previous["ema169"]

    c12 = current["ema12"]
    c144 = current["ema144"]
    c169 = current["ema169"]

    if None in [
        p12,
        p144,
        p169,
        c12,
        c144,
        c169
    ]:

        return None

    # ========================================================
    # LONG B2
    # ========================================================

    if direction == "LONG":

        price_break = (
            previous["close"] < p144
            or previous["close"] < p169
        )

        ema12_holds = (
            p12 >= p144
            and p12 >= p169
        )

        reclaim = (
            current["low"] > c144
            and current["close"] > c169
        )

        if (
            price_break
            and ema12_holds
            and reclaim
        ):

            return {
                "side": "LONG",
                "index": len(data15) - 2,
                "strategy": "B2"
            }

    # ========================================================
    # SHORT B2
    # ========================================================

    if direction == "SHORT":

        price_break = (
            previous["close"] > p144
            or previous["close"] > p169
        )

        ema12_holds = (
            p12 <= p144
            and p12 <= p169
        )

        reclaim = (
            current["high"] < c144
            and current["close"] < c169
        )

        if (
            price_break
            and ema12_holds
            and reclaim
        ):

            return {
                "side": "SHORT",
                "index": len(data15) - 2,
                "strategy": "B2"
            }

    return None


# ============================================================
# Vegas B1
# ============================================================

def strategy_b1(
    data15,
    direction
):

    if direction not in [
        "LONG",
        "SHORT"
    ]:

        return None

    if len(data15) < 20:

        return None

    # 已完成K线
    previous = data15[-2]

    # 当前正在形成中的15M K线
    current = data15[-1]

    p12 = previous["ema12"]
    p144 = previous["ema144"]
    p169 = previous["ema169"]

    c12 = current["ema12"]
    c144 = current["ema144"]
    c169 = current["ema169"]

    if None in [
        p12,
        p144,
        p169,
        c12,
        c144,
        c169
    ]:

        return None

    # ========================================================
    # LONG B1
    # ========================================================

    if direction == "LONG":

        deep_break = (
            previous["close"] < p144
            and previous["close"] < p169
            and p12 < p144
            and p12 < p169
        )

        ema12_cross_up = (
            p12 <= p144
            and c12 > c144
        )

        if (
            deep_break
            and ema12_cross_up
        ):

            return {
                "side": "LONG",
                "index": len(data15) - 1,
                "strategy": "B1"
            }

    # ========================================================
    # SHORT B1
    # ========================================================

    if direction == "SHORT":

        deep_break = (
            previous["close"] > p144
            and previous["close"] > p169
            and p12 > p144
            and p12 > p169
        )

        ema12_cross_down = (
            p12 >= p144
            and c12 < c144
        )

        if (
            deep_break
            and ema12_cross_down
        ):

            return {
                "side": "SHORT",
                "index": len(data15) - 1,
                "strategy": "B1"
            }

    return None


# ============================================================
# 止损 / 止盈
# ============================================================

def calculate_sl_tp(
    data15,
    signal_index,
    side,
    entry
):

    start = signal_index - 10

    end = signal_index

    if start < 0:

        return None, None

    previous_candles = data15[
        start:end
    ]

    if len(previous_candles) < 10:

        return None, None

    # ========================================================
    # LONG
    # ========================================================

    if side == "LONG":

        lowest = min(
            candle["low"]
            for candle in previous_candles
        )

        sl = lowest * 0.995

        risk = entry - sl

        if risk <= 0:

            return None, None

        tp = entry + (
            risk * 2
        )

    # ========================================================
    # SHORT
    # ========================================================

    else:

        highest = max(
            candle["high"]
            for candle in previous_candles
        )

        sl = highest * 1.005

        risk = sl - entry

        if risk <= 0:

            return None, None

        tp = entry - (
            risk * 2
        )

    return sl, tp


# ============================================================
# 信号冷却
# ============================================================

def cooldown_ok(symbol):

    last_time = last_signal_time.get(
        symbol
    )

    if last_time is None:

        return True

    return (
        time.time() - last_time
        >= COOLDOWN_SECONDS
    )


# ============================================================
# 生成交易信号
# ============================================================

def make_signal(symbol):

    try:

        # ====================================================
        # 1H
        # ====================================================

        data1h = get_klines(
            symbol,
            "60",
            700
        )

        if len(data1h) < 700:

            return None

        data1h = add_indicators(
            data1h
        )

        direction = get_1h_direction(
            data1h
        )

        if direction == "NEUTRAL":

            return None

        # ====================================================
        # 15M
        # ====================================================

        data15 = get_klines(
            symbol,
            "15",
            700
        )

        if len(data15) < 700:

            return None

        data15 = add_indicators(
            data15
        )

        # ====================================================
        # B1优先
        # ====================================================

        signal = strategy_b1(
            data15,
            direction
        )

        # ====================================================
        # B2
        # ====================================================

        if signal is None:

            signal = strategy_b2(
                data15,
                direction
            )

        # ====================================================
        # A
        # ====================================================

        if signal is None:

            signal = strategy_a(
                data15,
                direction
            )

        if signal is None:

            return None

        side = signal["side"]

        index = signal["index"]

        # 当前K线价格
        entry = data15[index]["close"]

        sl, tp = calculate_sl_tp(
            data15,
            index,
            side,
            entry
        )

        if sl is None or tp is None:

            return None

        return {

            "symbol": symbol,

            "side": side,

            "entry": entry,

            "sl": sl,

            "tp": tp,

            "strategy": signal["strategy"]

        }

    except Exception as e:

        print(
            f"{symbol}信号计算异常:",
            e
        )

        return None


# ============================================================
# Telegram信号格式
# ============================================================

def build_signal_message(
    signal
):

    symbol = signal["symbol"]

    side = signal["side"]

    entry = signal["entry"]

    sl = signal["sl"]

    tp = signal["tp"]

    signal_time = (
        datetime.now()
        .strftime("%Y-%m-%d %H:%M")
    )

    if side == "LONG":

        return (
            "🟢 多单信号\n\n"
            f"币种：{symbol}\n"
            f"📍 入场价：{format_price(entry)}\n"
            f"🛑 止损：{format_price(sl)}\n"
            f"🎯 止盈：{format_price(tp)}\n\n"
            f"⏱ 信号时间：{signal_time}"
        )

    return (
        "🔴 空单信号\n\n"
        f"币种：{symbol}\n"
        f"📍 入场价：{format_price(entry)}\n"
        f"🛑 止损：{format_price(sl)}\n"
        f"🎯 止盈：{format_price(tp)}\n\n"
        f"⏱ 信号时间：{signal_time}"
    )


# ============================================================
# /debug
# ============================================================

def debug_symbol(symbol):

    symbol = symbol.upper().strip()

    if not symbol.endswith("USDT"):

        symbol += "USDT"

    if symbol not in SYMBOLS:

        return (
            f"❌ {symbol} 不在监控列表中"
        )

    data = get_klines(
        symbol,
        "60",
        700
    )

    if len(data) < 700:

        return (
            f"❌ {symbol}\n\n"
            "1H K线数据不足\n"
            f"收到：{len(data)}根"
        )

    data = add_indicators(
        data
    )

    candle = data[-2]

    close = candle["close"]

    e12 = candle["ema12"]

    e144 = candle["ema144"]

    e169 = candle["ema169"]

    e576 = candle["ema576"]

    e676 = candle["ema676"]

    direction = get_1h_direction(
        data
    )

    # ========================================================
    # EMA排列
    # ========================================================

    if None in [
        e12,
        e144,
        e169,
        e576,
        e676
    ]:

        alignment = "❌ EMA数据不足"

    elif (
        e12
        > e144
        > e169
        > e576
        > e676
    ):

        alignment = "🟢 完整多头排列"

    elif (
        e12
        < e144
        < e169
        < e576
        < e676
    ):

        alignment = "🔴 完整空头排列"

    else:

        alignment = "⚪ 非完整排列"

    if close > e144:

        price_position = (
            "🟢 收盘价在EMA144上方"
        )

    else:

        price_position = (
            "🔴 收盘价在EMA144下方"
        )

    candle_time = datetime.fromtimestamp(
        candle["time"] / 1000
    ).strftime(
        "%Y-%m-%d %H:%M"
    )

    return (
        f"🔍 {symbol} 1H诊断\n\n"

        f"K线时间：{candle_time}\n"
        f"收盘价：{format_price(close)}\n\n"

        f"EMA12：{format_price(e12)}\n"
        f"EMA144：{format_price(e144)}\n"
        f"EMA169：{format_price(e169)}\n"
        f"EMA576：{format_price(e576)}\n"
        f"EMA676：{format_price(e676)}\n\n"

        f"EMA排列：{alignment}\n"
        f"{price_position}\n\n"

        f"最终1H方向：{direction}"
    )


# ============================================================
# /status
# ============================================================

def get_status():

    long_count = 0

    short_count = 0

    neutral_count = 0

    for symbol in SYMBOLS:

        try:

            data = get_klines(
                symbol,
                "60",
                700
            )

            if len(data) < 700:

                neutral_count += 1

                continue

            data = add_indicators(
                data
            )

            direction = get_1h_direction(
                data
            )

            if direction == "LONG":

                long_count += 1

            elif direction == "SHORT":

                short_count += 1

            else:

                neutral_count += 1

        except Exception:

            neutral_count += 1

    return (
        "🤖 机器人状态：运行中\n\n"

        f"监控币种：{len(SYMBOLS)}\n"

        f"1H多头：{long_count}\n"

        f"1H空头：{short_count}\n"

        f"1H无方向：{neutral_count}\n"

        "Bybit：正常\n\n"

        "策略：Vegas A / B1 / B2\n"

        "信号冷却：每币种4小时"
    )


# ============================================================
# Telegram命令
# ============================================================

def handle_command(text):

    text = text.strip()

    # --------------------------------------------------------
    # /start
    # --------------------------------------------------------

    if text.startswith("/start"):

        return (
            "🤖 Vegas交易信号机器人\n\n"

            "机器人已经启动。\n\n"

            "可用命令：\n"

            "/status - 查看机器人状态\n"

            "/debug BTCUSDT - 查看1H EMA诊断"
        )

    # --------------------------------------------------------
    # /status
    # --------------------------------------------------------

    if text.startswith("/status"):

        return get_status()

    # --------------------------------------------------------
    # /debug
    # --------------------------------------------------------

    if text.startswith("/debug"):

        parts = text.split()

        if len(parts) < 2:

            return (
                "用法：\n"
                "/debug BTCUSDT"
            )

        return debug_symbol(
            parts[1]
        )

    return None


# ============================================================
# Telegram监听
# ============================================================

async def telegram_loop():

    print(
        "Telegram监听启动"
    )

    while True:

        try:

            updates = await asyncio.to_thread(
                get_updates
            )

            for update in updates:

                message = update.get(
                    "message"
                )

                if not message:

                    continue

                text = message.get(
                    "text",
                    ""
                )

                if not text:

                    continue

                chat_id = str(
                    message
                    .get("chat", {})
                    .get("id", "")
                )

                # 只响应指定群
                if chat_id != str(CHAT_ID):

                    continue

                print(
                    "收到Telegram消息:",
                    text
                )

                response = await asyncio.to_thread(
                    handle_command,
                    text
                )

                if response:

                    await asyncio.to_thread(
                        send_message,
                        response
                    )

        except Exception as e:

            print(
                "Telegram监听异常:",
                e
            )

        await asyncio.sleep(1)


# ============================================================
# 交易扫描
# ============================================================

async def trading_loop():

    print(
        f"交易扫描启动，"
        f"监控 {len(SYMBOLS)} 个币"
    )

    while True:

        for symbol in SYMBOLS:

            try:

                # 4小时冷却
                if not cooldown_ok(symbol):

                    continue

                signal = await asyncio.to_thread(
                    make_signal,
                    symbol
                )

                if signal:

                    message = (
                        build_signal_message(
                            signal
                        )
                    )

                    success = (
                        await asyncio.to_thread(
                            send_message,
                            message
                        )
                    )

                    if success:

                        last_signal_time[
                            symbol
                        ] = time.time()

                        print(
                            "发送信号:",
                            symbol,
                            signal["side"],
                            signal["strategy"]
                        )

            except Exception as e:

                print(
                    f"{symbol}扫描异常:",
                    e
                )

            # 防止请求过快
            await asyncio.sleep(
                0.2
            )

        print(
            "本轮扫描完成，"
            "等待下一轮..."
        )

        await asyncio.sleep(
            60
        )


# ============================================================
# 主程序
# ============================================================

async def main_async():

    print("=" * 60)

    print(
        "Vegas Telegram Trading Bot"
    )

    print("=" * 60)

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN 未设置"
        )

        return

    print(
        f"监控币种数量：{len(SYMBOLS)}"
    )

    print(
        f"Telegram Chat ID：{CHAT_ID}"
    )

    print(
        "Bybit API：正常模式"
    )

    print(
        "Vegas EMA：12 / 144 / 169 / 576 / 676"
    )

    print(
        "策略：A / B1 / B2"
    )

    print(
        "信号冷却：4小时"
    )

    await asyncio.gather(
        telegram_loop(),
        trading_loop()
    )


def main():

    # ========================================================
    # Render Free Web Service需要监听PORT
    # ========================================================

    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )

    web_thread.start()

    # ========================================================
    # 启动Telegram + 交易扫描
    # ========================================================

    try:

        asyncio.run(
            main_async()
        )

    except KeyboardInterrupt:

        print(
            "机器人已停止"
        )


# ============================================================
# 程序入口
# ============================================================

if __name__ == "__main__":

    main()
