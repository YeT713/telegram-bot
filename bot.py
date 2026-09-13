import os
import time
import asyncio
import requests
from datetime import datetime

# =========================================================
# 配置
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID", "8289465171")

BYBIT_URL = "https://api.bybit.com"

SYMBOLS = [
    # Core 45
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "TRXUSDT", "DOGEUSDT", "ADAUSDT", "HYPEUSDT", "ZECUSDT",
    "BCHUSDT", "LINKUSDT", "AVAXUSDT", "XLMUSDT", "SUIUSDT",
    "TONUSDT", "HBARUSDT", "SHIBUSDT", "LTCUSDT", "DOTUSDT",
    "UNIUSDT", "NEARUSDT", "ICPUSDT", "XMRUSDT", "APTUSDT",
    "TAOUSDT", "ATOMUSDT", "ARBUSDT", "CROUSDT", "VETUSDT",
    "FILUSDT", "ALGOUSDT", "RENDERUSDT", "INJUSDT", "OPUSDT",
    "IMXUSDT", "AAVEUSDT", "GRTUSDT", "SEIUSDT", "QNTUSDT",
    "THETAUSDT", "TIAUSDT", "MKRUSDT", "MNTUSDT", "PEPEUSDT",

    # Extension
    "WIFUSDT", "BONKUSDT", "FLOKIUSDT", "JUPUSDT", "ENAUSDT",
    "ONDOUSDT", "RUNEUSDT", "WLDUSDT", "STXUSDT", "CRVUSDT",

    # Extension 2
    "MAGMAUSDT", "FFUSDT", "LISKUSDT", "ARKUSDT",
    "EMBERUSDT", "PENGUUSDT", "JASMYUSDT",
]

COOLDOWN_SECONDS = 4 * 60 * 60

# 每个币最近一次信号时间
last_signal_time = {}

# Telegram update offset
telegram_offset = 0

# =========================================================
# Telegram
# =========================================================

def telegram_url(method):
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"


def send_message(text):
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN 未设置")
        return False

    try:
        r = requests.post(
            telegram_url("sendMessage"),
            json={
                "chat_id": CHAT_ID,
                "text": text
            },
            timeout=20
        )

        if r.status_code != 200:
            print("Telegram发送失败:", r.status_code, r.text)
            return False

        return True

    except Exception as e:
        print("Telegram异常:", e)
        return False


def get_updates():
    global telegram_offset

    try:
        r = requests.get(
            telegram_url("getUpdates"),
            params={
                "offset": telegram_offset,
                "timeout": 10
            },
            timeout=20
        )

        if r.status_code != 200:
            return []

        data = r.json()

        if not data.get("ok"):
            return []

        updates = data.get("result", [])

        if updates:
            telegram_offset = updates[-1]["update_id"] + 1

        return updates

    except Exception as e:
        print("Telegram获取消息异常:", e)
        return []


# =========================================================
# Bybit
# =========================================================

def get_klines(symbol, interval, limit=700):
    """
    Bybit V5 K线
    interval:
    15 = 15分钟
    60 = 1小时
    """

    try:
        r = requests.get(
            f"{BYBIT_URL}/v5/market/kline",
            params={
                "category": "linear",
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            },
            timeout=20
        )

        if r.status_code != 200:
            print(f"{symbol} Bybit HTTP:", r.status_code)
            return []

        data = r.json()

        if data.get("retCode") != 0:
            print(f"{symbol} Bybit错误:", data)
            return []

        rows = data.get("result", {}).get("list", [])

        # Bybit 返回倒序，重新变成正序
        rows = list(reversed(rows))

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
        print(f"{symbol} K线异常:", e)
        return []


# =========================================================
# EMA
# =========================================================

def ema(values, period):
    if len(values) < period:
        return [None] * len(values)

    result = [None] * len(values)

    sma = sum(values[:period]) / period
    result[period - 1] = sma

    multiplier = 2 / (period + 1)

    previous = sma

    for i in range(period, len(values)):
        previous = (
            values[i] - previous
        ) * multiplier + previous

        result[i] = previous

    return result


def add_indicators(candles):
    closes = [x["close"] for x in candles]

    ema12 = ema(closes, 12)
    ema144 = ema(closes, 144)
    ema169 = ema(closes, 169)
    ema576 = ema(closes, 576)
    ema676 = ema(closes, 676)

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


# =========================================================
# 1H方向
# =========================================================

def get_1h_direction(data):
    if len(data) < 700:
        return "NEUTRAL"

    # 使用最后一个已经完成的1H K线
    candle = data[-2]

    e12 = candle["ema12"]
    e144 = candle["ema144"]
    e169 = candle["ema169"]
    e576 = candle["ema576"]
    e676 = candle["ema676"]

    close = candle["close"]

    if None in [e12, e144, e169, e576, e676]:
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


# =========================================================
# Debug
# =========================================================

def format_price(price):
    if price is None:
        return "N/A"

    if price >= 1000:
        return f"{price:.2f}"

    if price >= 1:
        return f"{price:.4f}"

    return f"{price:.8f}"


def debug_symbol(symbol):
    symbol = symbol.upper().strip()

    if not symbol.endswith("USDT"):
        symbol += "USDT"

    if symbol not in SYMBOLS:
        return f"❌ {symbol} 不在当前62个监控币种中"

    data = get_klines(symbol, "60", 700)

    if len(data) < 700:
        return (
            f"❌ {symbol}\n\n"
            f"1H K线数据不足\n"
            f"收到：{len(data)} 根"
        )

    data = add_indicators(data)

    # 最后一根可能是正在形成中的K线
    candle = data[-2]

    e12 = candle["ema12"]
    e144 = candle["ema144"]
    e169 = candle["ema169"]
    e576 = candle["ema576"]
    e676 = candle["ema676"]

    close = candle["close"]

    direction = get_1h_direction(data)

    if (
        e12 is None
        or e144 is None
        or e169 is None
        or e576 is None
        or e676 is None
    ):
        alignment = "❌ EMA数据不足"
    elif e12 > e144 > e169 > e576 > e676:
        alignment = "🟢 多头排列"
    elif e12 < e144 < e169 < e576 < e676:
        alignment = "🔴 空头排列"
    else:
        alignment = "⚪ 无完整排列"

    price_position = "上方" if close > e144 else "下方"

    timestamp = datetime.fromtimestamp(
        candle["time"] / 1000
    ).strftime("%Y-%m-%d %H:%M")

    return (
        f"🔍 {symbol} 1H诊断\n\n"
        f"K线时间：{timestamp}\n"
        f"收盘价：{format_price(close)}\n\n"
        f"EMA12：{format_price(e12)}\n"
        f"EMA144：{format_price(e144)}\n"
        f"EMA169：{format_price(e169)}\n"
        f"EMA576：{format_price(e576)}\n"
        f"EMA676：{format_price(e676)}\n\n"
        f"EMA排列：{alignment}\n"
        f"收盘价在EMA144：{price_position}\n\n"
        f"最终1H方向：{direction}"
    )


# =========================================================
# A策略
# =========================================================

def strategy_a(data15, direction):
    """
    Vegas A
    正常回踩144/169区域
    """

    if direction not in ["LONG", "SHORT"]:
        return None

    if len(data15) < 20:
        return None

    # 使用最后一根已经完成的15M K线
    candle = data15[-2]

    e12 = candle["ema12"]
    e144 = candle["ema144"]
    e169 = candle["ema169"]

    if None in [e12, e144, e169]:
        return None

    close = candle["close"]

    zone_upper = e144 * 1.02
    zone_lower = e144 * 0.98

    # LONG
    if direction == "LONG":

        in_zone = (
            candle["low"] <= zone_upper
            and candle["high"] >= zone_lower
        )

        valid_structure = close >= e169

        confirmation = close > e12

        if in_zone and valid_structure and confirmation:
            return {
                "side": "LONG",
                "index": len(data15) - 2
            }

    # SHORT
    if direction == "SHORT":

        in_zone = (
            candle["low"] <= zone_upper
            and candle["high"] >= zone_lower
        )

        valid_structure = close <= e169

        confirmation = close < e12

        if in_zone and valid_structure and confirmation:
            return {
                "side": "SHORT",
                "index": len(data15) - 2
            }

    return None


# =========================================================
# B2
# =========================================================

def strategy_b2(data15, direction):
    """
    浅破位 + 收复
    """

    if direction not in ["LONG", "SHORT"]:
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

    if None in [p12, p144, p169, c12, c144, c169]:
        return None

    # LONG
    if direction == "LONG":

        previous_break = (
            previous["close"] < p144
            or previous["close"] < p169
        )

        ema12_not_broken = (
            p12 >= p144
            and p12 >= p169
        )

        reclaim = (
            current["low"] > c144
            and current["close"] > c169
        )

        if previous_break and ema12_not_broken and reclaim:
            return {
                "side": "LONG",
                "index": len(data15) - 2
            }

    # SHORT
    if direction == "SHORT":

        previous_break = (
            previous["close"] > p144
            or previous["close"] > p169
        )

        ema12_not_broken = (
            p12 <= p144
            and p12 <= p169
        )

        reclaim = (
            current["high"] < c144
            and current["close"] < c169
        )

        if previous_break and ema12_not_broken and reclaim:
            return {
                "side": "SHORT",
                "index": len(data15) - 2
            }

    return None


# =========================================================
# B1
# =========================================================

def strategy_b1(data15, direction):
    """
    深破位 + EMA12重新穿越EMA144

    注意：
    B1允许当前15M K线还没有收盘。
    """

    if direction not in ["LONG", "SHORT"]:
        return None

    if len(data15) < 20:
        return None

    previous = data15[-2]
    current = data15[-1]

    p12 = previous["ema12"]
    p144 = previous["ema144"]
    p169 = previous["ema169"]

    c12 = current["ema12"]
    c144 = current["ema144"]
    c169 = current["ema169"]

    if None in [p12, p144, p169, c12, c144, c169]:
        return None

    # LONG
    if direction == "LONG":

        deep_break = (
            previous["close"] < p144
            and previous["close"] < p169
            and p12 < p144
            and p12 < p169
        )

        cross_up = (
            p12 <= p144
            and c12 > c144
        )

        if deep_break and cross_up:
            return {
                "side": "LONG",
                "index": len(data15) - 1
            }

    # SHORT
    if direction == "SHORT":

        deep_break = (
            previous["close"] > p144
            and previous["close"] > p169
            and p12 > p144
            and p12 > p169
        )

        cross_down = (
            p12 >= p144
            and c12 < c144
        )

        if deep_break and cross_down:
            return {
                "side": "SHORT",
                "index": len(data15) - 1
            }

    return None


# =========================================================
# 止损止盈
# =========================================================

def calculate_sl_tp(data15, signal_index, side, entry):
    """
    使用信号K线之前的10根已经完成K线。

    B1：
    signal_index = 当前未完成K线
    因此自动排除当前K线。

    A/B2：
    signal_index = 最后一根已完成K线
    同样排除信号K线。
    """

    start = signal_index - 10
    end = signal_index

    if start < 0:
        return None, None

    previous_candles = data15[start:end]

    if len(previous_candles) < 10:
        return None, None

    if side == "LONG":

        lowest = min(
            candle["low"]
            for candle in previous_candles
        )

        sl = lowest * 0.995

        risk = entry - sl

        if risk <= 0:
            return None, None

        tp = entry + 2 * risk

    else:

        highest = max(
            candle["high"]
            for candle in previous_candles
        )

        sl = highest * 1.005

        risk = sl - entry

        if risk <= 0:
            return None, None

        tp = entry - 2 * risk

    return sl, tp


# =========================================================
# 冷却
# =========================================================

def cooldown_ok(symbol):
    now = time.time()

    last = last_signal_time.get(symbol)

    if last is None:
        return True

    return now - last >= COOLDOWN_SECONDS


# =========================================================
# 生成信号
# =========================================================

def make_signal(symbol):
    try:

        # -------------------------
        # 1H
        # -------------------------

        data1h = get_klines(
            symbol,
            "60",
            700
        )

        if len(data1h) < 700:
            return None

        data1h = add_indicators(data1h)

        direction = get_1h_direction(data1h)

        if direction == "NEUTRAL":
            return None

        # -------------------------
        # 15M
        # -------------------------

        data15 = get_klines(
            symbol,
            "15",
            700
        )

        if len(data15) < 700:
            return None

        data15 = add_indicators(data15)

        # -------------------------
        # B1 优先
        # -------------------------

        signal = strategy_b1(
            data15,
            direction
        )

        strategy_name = "B1"

        # -------------------------
        # B2
        # -------------------------

        if signal is None:
            signal = strategy_b2(
                data15,
                direction
            )

            strategy_name = "B2"

        # -------------------------
        # A
        # -------------------------

        if signal is None:
            signal = strategy_a(
                data15,
                direction
            )

            strategy_name = "A"

        if signal is None:
            return None

        side = signal["side"]
        index = signal["index"]

        # B1当前K线是未完成K线
        if index == len(data15) - 1:
            entry = data15[index]["close"]
        else:
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
            "strategy": strategy_name
        }

    except Exception as e:
        print(f"{symbol} 信号计算异常:", e)
        return None


# =========================================================
# 信号格式
# =========================================================

def build_signal_message(signal):

    symbol = signal["symbol"]
    side = signal["side"]

    entry = signal["entry"]
    sl = signal["sl"]
    tp = signal["tp"]

    now = datetime.now().strftime(
        "%Y-%m-%d %H:%M"
    )

    if side == "LONG":

        return (
            "🟢 多单信号\n\n"
            f"币种：{symbol}\n"
            f"📍 入场价：{format_price(entry)}\n"
            f"🛑 止损：{format_price(sl)}\n"
            f"🎯 止盈：{format_price(tp)}\n\n"
            f"⏱ 信号时间：{now}"
        )

    else:

        return (
            "🔴 空单信号\n\n"
            f"币种：{symbol}\n"
            f"📍 入场价：{format_price(entry)}\n"
            f"🛑 止损：{format_price(sl)}\n"
            f"🎯 止盈：{format_price(tp)}\n\n"
            f"⏱ 信号时间：{now}"
        )


# =========================================================
# 状态
# =========================================================

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

            data = add_indicators(data)

            direction = get_1h_direction(data)

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


# =========================================================
# Telegram命令
# =========================================================

def handle_command(text):

    text = text.strip()

    if text.startswith("/start"):

        return (
            "🤖 Vegas交易信号机器人\n\n"
            "机器人已经启动。\n\n"
            "可用命令：\n"
            "/status - 查看机器人状态\n"
            "/debug BTCUSDT - 查看1H EMA诊断"
        )

    if text.startswith("/status"):

        return get_status()

    if text.startswith("/debug"):

        parts = text.split()

        if len(parts) < 2:

            return (
                "用法：\n"
                "/debug BTCUSDT"
            )

        return debug_symbol(parts[1])

    return None


# =========================================================
# Telegram监听
# =========================================================

async def telegram_loop():

    print("Telegram监听启动")

    while True:

        try:

            updates = await asyncio.to_thread(
                get_updates
            )

            for update in updates:

                message = update.get("message")

                if not message:
                    continue

                text = message.get("text", "")

                if not text:
                    continue

                chat_id = str(
                    message.get("chat", {}).get("id", "")
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


# =========================================================
# 交易扫描
# =========================================================

async def trading_loop():

    print(
        f"交易扫描启动，监控 {len(SYMBOLS)} 个币"
    )

    # 每次扫描间隔
    scan_interval = 60

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

                    message = build_signal_message(
                        signal
                    )

                    success = await asyncio.to_thread(
                        send_message,
                        message
                    )

                    if success:

                        last_signal_time[symbol] = time.time()

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

            # 避免一次请求过快
            await asyncio.sleep(0.2)

        print(
            "本轮扫描完成，等待下一轮..."
        )

        await asyncio.sleep(
            scan_interval
        )


# =========================================================
# 主程序
# =========================================================

async def main_async():

    print("=" * 50)
    print("Vegas Telegram Trading Bot")
    print("=" * 50)

    if not BOT_TOKEN:
        print("❌ BOT_TOKEN 未设置")
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
        "策略：Vegas A / B1 / B2"
    )

    print(
        "信号冷却：4小时"
    )

    await asyncio.gather(
        telegram_loop(),
        trading_loop()
    )


def main():

    try:

        asyncio.run(
            main_async()
        )

    except KeyboardInterrupt:

        print(
            "机器人已停止"
        )


if __name__ == "__main__":
    main()
