import os
import time
import asyncio
import threading
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from telegram import Bot


# =========================================================
# 基础配置
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = "8289465171"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN 环境变量不存在")


# =========================================================
# Bybit
# =========================================================

BYBIT_BASE_URL = "https://api.bybit.com"

# 15分钟扫描间隔
SCAN_INTERVAL = 15

# 1H方向重新计算间隔
ONE_HOUR_REFRESH = 15 * 60

# 同一个币种4小时最多一个信号
SIGNAL_COOLDOWN = 4 * 60 * 60


# =========================================================
# Vegas EMA参数
# =========================================================

EMA_FAST = 12
EMA_TUNNEL_1 = 144
EMA_TUNNEL_2 = 169
EMA_LONG_1 = 576
EMA_LONG_2 = 676


# =========================================================
# 监控币种
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
# HTTP Session
# =========================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Vegas-Telegram-Signal-Bot/1.0"
})


# =========================================================
# Telegram Bot
# =========================================================

telegram_bot = Bot(token=BOT_TOKEN)


# =========================================================
# 时区
# =========================================================

BEIJING_TZ = timezone(timedelta(hours=8))


def now_beijing():
    return datetime.now(BEIJING_TZ)


def format_time(dt=None):
    if dt is None:
        dt = now_beijing()

    return dt.strftime("%Y-%m-%d %H:%M")


# =========================================================
# EMA计算
# =========================================================

def calculate_ema(values, period):
    """
    计算EMA
    values: 收盘价列表
    """

    if not values:
        return []

    multiplier = 2 / (period + 1)

    ema = [values[0]]

    for price in values[1:]:
        previous = ema[-1]
        current = (price - previous) * multiplier + previous
        ema.append(current)

    return ema


# =========================================================
# Bybit K线
# =========================================================

def get_klines(symbol, interval, limit=800):
    """
    Bybit V5:
    category=linear
    interval:
        15 = 15分钟
        60 = 1小时

    返回按照时间从旧到新排列的K线。
    """

    url = f"{BYBIT_BASE_URL}/v5/market/kline"

    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": str(interval),
        "limit": limit,
    }

    try:
        response = session.get(
            url,
            params=params,
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        if data.get("retCode") != 0:
            return None

        result = data.get("result", {})
        rows = result.get("list", [])

        if not rows:
            return None

        candles = []

        for row in rows:
            candles.append({
                "timestamp": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            })

        # Bybit通常返回倒序，这里统一成旧 -> 新
        candles.sort(key=lambda x: x["timestamp"])

        return candles

    except Exception as e:
        print(f"[Bybit] {symbol} interval={interval} 获取失败: {e}")
        return None


# =========================================================
# K线数据检查
# =========================================================

def valid_candles(candles, minimum=700):
    if candles is None:
        return False

    if len(candles) < minimum:
        return False

    return True


# =========================================================
# 1H Vegas方向
# =========================================================

def get_1h_direction(symbol):
    """
    严格Vegas方向：

    多头：
    EMA12 > EMA144 > EMA169 > EMA576 > EMA676
    并且1H收盘价 > EMA144

    空头：
    EMA12 < EMA144 < EMA169 < EMA576 < EMA676
    并且1H收盘价 < EMA144

    使用已经完成的1H K线，避免未收盘K线导致方向反复变化。
    """

    candles = get_klines(
        symbol,
        60,
        800
    )

    if not valid_candles(candles, 700):
        return None

    # 最后一根可能是正在形成的1H K线
    closed = candles[:-1]

    if len(closed) < 676:
        return None

    closes = [x["close"] for x in closed]

    ema12 = calculate_ema(closes, EMA_FAST)[-1]
    ema144 = calculate_ema(closes, EMA_TUNNEL_1)[-1]
    ema169 = calculate_ema(closes, EMA_TUNNEL_2)[-1]
    ema576 = calculate_ema(closes, EMA_LONG_1)[-1]
    ema676 = calculate_ema(closes, EMA_LONG_2)[-1]

    close_price = closes[-1]

    bullish = (
        ema12 > ema144
        and ema144 > ema169
        and ema169 > ema576
        and ema576 > ema676
        and close_price > ema144
    )

    bearish = (
        ema12 < ema144
        and ema144 < ema169
        and ema169 < ema576
        and ema576 < ema676
        and close_price < ema144
    )

    if bullish:
        return "LONG"

    if bearish:
        return "SHORT"

    return None


# =========================================================
# 全部1H方向
# =========================================================

def refresh_all_directions():
    directions = {}

    print("========== 开始刷新1H Vegas方向 ==========")

    for symbol in SYMBOLS:
        direction = get_1h_direction(symbol)

        directions[symbol] = direction

        print(
            f"{symbol}: "
            f"{direction if direction else '无方向'}"
        )

        # 稍微降低请求密度
        time.sleep(0.05)

    print("========== 1H方向刷新完成 ==========")

    return directions


# =========================================================
# 信号冷却
# =========================================================

last_signal_time = {}


def can_send_signal(symbol):
    last_time = last_signal_time.get(symbol)

    if last_time is None:
        return True

    elapsed = time.time() - last_time

    return elapsed >= SIGNAL_COOLDOWN


def mark_signal_sent(symbol):
    last_signal_time[symbol] = time.time()


# =========================================================
# 价格格式化
# =========================================================

def format_price(price):
    """
    根据价格大小自动选择显示小数位。
    """

    if price >= 10000:
        return f"{price:.2f}"

    if price >= 1000:
        return f"{price:.3f}"

    if price >= 100:
        return f"{price:.3f}"

    if price >= 1:
        return f"{price:.4f}"

    if price >= 0.01:
        return f"{price:.5f}"

    return f"{price:.8f}"


# =========================================================
# Telegram发送
# =========================================================

async def send_telegram_message(text):
    try:
        await telegram_bot.send_message(
            chat_id=CHAT_ID,
            text=text
        )

        return True

    except Exception as e:
        print(f"[Telegram] 发送失败: {e}")
        return False
        # =========================================================
# 15M策略状态
# =========================================================

strategy_states = {}

# 每个币种保存：
# {
#     "A": ...,
#     "B1": ...,
#     "B2": ...,
#     "direction": ...
# }


def get_state(symbol):
    if symbol not in strategy_states:
        strategy_states[symbol] = {
            "direction": None,

            # A策略
            "a_active": False,

            # B1策略
            "b1_active": False,

            # B2策略
            "b2_active": False,

            # 防止同一根K线重复处理
            "last_candle_timestamp": None,

            # B1触发记录
            "b1_crossed": False,
        }

    return strategy_states[symbol]


# =========================================================
# 重置策略状态
# =========================================================

def reset_strategy_state(symbol, direction):
    state = get_state(symbol)

    state["direction"] = direction

    state["a_active"] = False
    state["b1_active"] = False
    state["b2_active"] = False

    state["b1_crossed"] = False


# =========================================================
# EMA数据
# =========================================================

def calculate_15m_indicators(candles):
    closes = [x["close"] for x in candles]

    ema12 = calculate_ema(closes, 12)
    ema144 = calculate_ema(closes, 144)
    ema169 = calculate_ema(closes, 169)

    for i, candle in enumerate(candles):
        candle["ema12"] = ema12[i]
        candle["ema144"] = ema144[i]
        candle["ema169"] = ema169[i]

    return candles


# =========================================================
# 获取15M数据
# =========================================================

def get_15m_data(symbol):
    candles = get_klines(
        symbol,
        15,
        250
    )

    if candles is None:
        return None

    if len(candles) < 180:
        return None

    return calculate_15m_indicators(candles)


# =========================================================
# 判断价格是否进入EMA144 ±2%区域
# =========================================================

def price_in_ema144_zone(candle):
    ema144 = candle["ema144"]

    upper = ema144 * 1.02
    lower = ema144 * 0.98

    return lower <= candle["close"] <= upper


# =========================================================
# A策略：正常回踩
# =========================================================

def check_strategy_a(symbol, candles, direction):
    """
    A策略：

    LONG：

    1H看多

    15M进入EMA144 ±2%

    回踩过程中：
    15M收盘不能有效跌破EMA169

    等待任意数量15M K线

    当15M收盘重新/继续站上EMA12
    → 多单信号

    SHORT完全镜像。
    """

    if len(candles) < 20:
        return None

    state = get_state(symbol)

    # 使用当前正在形成的K线进行实时监测
    current = candles[-1]

    # -----------------------------------------
    # LONG
    # -----------------------------------------

    if direction == "LONG":

        # 进入EMA144 ±2%
        if price_in_ema144_zone(current):

            # 记录A策略开始
            if not state["a_active"]:
                state["a_active"] = True

        if state["a_active"]:

            # 如果15M收盘跌破EMA169
            # A策略失效
            if current["close"] < current["ema169"]:
                state["a_active"] = False

                return None

            # 收盘重新/继续站上EMA12
            if current["close"] > current["ema12"]:

                state["a_active"] = False

                return {
                    "strategy": "A",
                    "side": "LONG",
                    "entry": current["close"],
                    "candle": current,
                }

    # -----------------------------------------
    # SHORT
    # -----------------------------------------

    if direction == "SHORT":

        if price_in_ema144_zone(current):

            if not state["a_active"]:
                state["a_active"] = True

        if state["a_active"]:

            # 收盘突破EMA169
            # A策略失效
            if current["close"] > current["ema169"]:
                state["a_active"] = False

                return None

            # 收盘跌破EMA12
            if current["close"] < current["ema12"]:

                state["a_active"] = False

                return {
                    "strategy": "A",
                    "side": "SHORT",
                    "entry": current["close"],
                    "candle": current,
                }

    return None


# =========================================================
# B2策略：浅破 + 收回
# =========================================================

def check_strategy_b2(symbol, candles, direction):
    """
    B2：

    LONG：

    价格先跌破144/169

    但是EMA12没有跌破144/169

    随后价格恢复

    确认K线：

        low > EMA144
        close > EMA169

    → 多单

    SHORT完全镜像。
    """

    if len(candles) < 20:
        return None

    state = get_state(symbol)

    current = candles[-1]

    # =====================================================
    # LONG
    # =====================================================

    if direction == "LONG":

        # 价格跌破EMA144 / EMA169
        price_break = (
            current["low"] < current["ema144"]
            or current["low"] < current["ema169"]
        )

        # EMA12仍然没有跌破
        ema12_protected = (
            current["ema12"] >= current["ema144"]
            and current["ema12"] >= current["ema169"]
        )

        if price_break and ema12_protected:

            state["b2_active"] = True

        if state["b2_active"]:

            # 确认K线必须整个在EMA144上方
            entire_above_144 = (
                current["low"] > current["ema144"]
            )

            # 并且收盘站上EMA169
            close_above_169 = (
                current["close"] > current["ema169"]
            )

            if entire_above_144 and close_above_169:

                state["b2_active"] = False

                return {
                    "strategy": "B2",
                    "side": "LONG",
                    "entry": current["close"],
                    "candle": current,
                }

    # =====================================================
    # SHORT
    # =====================================================

    if direction == "SHORT":

        price_break = (
            current["high"] > current["ema144"]
            or current["high"] > current["ema169"]
        )

        ema12_protected = (
            current["ema12"] <= current["ema144"]
            and current["ema12"] <= current["ema169"]
        )

        if price_break and ema12_protected:

            state["b2_active"] = True

        if state["b2_active"]:

            # 确认K线必须整个在EMA144下方
            entire_below_144 = (
                current["high"] < current["ema144"]
            )

            # 收盘跌破EMA169
            close_below_169 = (
                current["close"] < current["ema169"]
            )

            if entire_below_144 and close_below_169:

                state["b2_active"] = False

                return {
                    "strategy": "B2",
                    "side": "SHORT",
                    "entry": current["close"],
                    "candle": current,
                }

    return None


# =========================================================
# B1策略：深破 + EMA12穿越EMA144
# =========================================================

def check_strategy_b1(symbol, candles, direction):
    """
    B1：

    LONG：

    1. 价格跌破144/169
    2. EMA12也跌破144/169
    3. 后面EMA12恢复
    4. EMA12从下向上穿越EMA144
    5. 立即发信号

    注意：

    B1不等待15M K线收盘。

    只要当前实时EMA12从EMA144下方穿越到上方，
    就立即触发。

    SHORT完全镜像。
    """

    if len(candles) < 20:
        return None

    state = get_state(symbol)

    current = candles[-1]

    previous = candles[-2]

    # =====================================================
    # LONG
    # =====================================================

    if direction == "LONG":

        # 第一步：
        # 价格曾经跌破144/169
        price_deep_break = (
            current["low"] < current["ema144"]
            and current["low"] < current["ema169"]
        )

        # 第二步：
        # EMA12也跌破144/169
        ema12_deep_break = (
            current["ema12"] < current["ema144"]
            and current["ema12"] < current["ema169"]
        )

        if price_deep_break and ema12_deep_break:

            state["b1_active"] = True
            state["b1_crossed"] = False

        if state["b1_active"]:

            # EMA12从下方向上穿越EMA144
            crossed_up = (
                previous["ema12"] <= previous["ema144"]
                and current["ema12"] > current["ema144"]
            )

            if crossed_up:

                state["b1_active"] = False
                state["b1_crossed"] = True

                return {
                    "strategy": "B1",
                    "side": "LONG",

                    # B1是即时触发
                    "entry": current["close"],

                    # 当前K线不能参与止损
                    "candle": current,
                }

    # =====================================================
    # SHORT
    # =====================================================

    if direction == "SHORT":

        price_deep_break = (
            current["high"] > current["ema144"]
            and current["high"] > current["ema169"]
        )

        ema12_deep_break = (
            current["ema12"] > current["ema144"]
            and current["ema12"] > current["ema169"]
        )

        if price_deep_break and ema12_deep_break:

            state["b1_active"] = True
            state["b1_crossed"] = False

        if state["b1_active"]:

            # EMA12从上方向下穿越EMA144
            crossed_down = (
                previous["ema12"] >= previous["ema144"]
                and current["ema12"] < current["ema144"]
            )

            if crossed_down:

                state["b1_active"] = False
                state["b1_crossed"] = True

                return {
                    "strategy": "B1",
                    "side": "SHORT",
                    "entry": current["close"],
                    "candle": current,
                }

    return None


# =========================================================
# 计算止损
# =========================================================

def calculate_stop_loss(candles, side):
    """
    非常重要：

    当前信号K线绝对不参与止损计算。

    使用：
    信号发生之前已经完全收盘的10根15M K线。

    LONG：
        10根最低点 × 0.995

    SHORT：
        10根最高点 × 1.005
    """

    if len(candles) < 12:
        return None

    # 最后一根是当前信号K线
    # 所以排除最后一根
    previous_10 = candles[-11:-1]

    if len(previous_10) != 10:
        return None

    if side == "LONG":

        lowest_low = min(
            candle["low"]
            for candle in previous_10
        )

        stop_loss = lowest_low * 0.995

        return stop_loss

    if side == "SHORT":

        highest_high = max(
            candle["high"]
            for candle in previous_10
        )

        stop_loss = highest_high * 1.005

        return stop_loss

    return None


# =========================================================
# 计算止盈
# =========================================================

def calculate_take_profit(entry, stop_loss, side):
    """
    固定1:2盈亏比
    """

    if side == "LONG":

        risk = entry - stop_loss

        if risk <= 0:
            return None

        take_profit = entry + risk * 2

        return take_profit

    if side == "SHORT":

        risk = stop_loss - entry

        if risk <= 0:
            return None

        take_profit = entry - risk * 2

        return take_profit

    return None


# =========================================================
# 构造Telegram信号
# =========================================================

def build_signal_message(
    symbol,
    side,
    entry,
    stop_loss,
    take_profit,
):
    signal_time = format_time()

    if side == "LONG":

        return (
            "🟢 多单信号\n"
            "\n"
            f"币种：{symbol}\n"
            f"📍 入场价：{format_price(entry)}\n"
            f"🛑 止损：{format_price(stop_loss)}\n"
            f"🎯 止盈：{format_price(take_profit)}\n"
            "\n"
            f"⏱ 信号时间：{signal_time}"
        )

    else:

        return (
            "🔴 空单信号\n"
            "\n"
            f"币种：{symbol}\n"
            f"📍 入场价：{format_price(entry)}\n"
            f"🛑 止损：{format_price(stop_loss)}\n"
            f"🎯 止盈：{format_price(take_profit)}\n"
            "\n"
            f"⏱ 信号时间：{signal_time}"
        )


# =========================================================
# 处理最终信号
# =========================================================

async def process_signal(symbol, signal, candles):
    if signal is None:
        return False

    # 4小时冷却
    if not can_send_signal(symbol):
        print(
            f"[冷却中] {symbol} "
            f"暂时不发送新信号"
        )

        return False

    side = signal["side"]

    entry = signal["entry"]

    # 当前信号K线不参与止损
    stop_loss = calculate_stop_loss(
        candles,
        side
    )

    if stop_loss is None:
        return False

    take_profit = calculate_take_profit(
        entry,
        stop_loss,
        side
    )

    if take_profit is None:
        return False

    # 防止出现明显错误的SL
    if side == "LONG" and stop_loss >= entry:
        print(
            f"[错误] {symbol} 多单止损 >= 入场价"
        )
        return False

    if side == "SHORT" and stop_loss <= entry:
        print(
            f"[错误] {symbol} 空单止损 <= 入场价"
        )
        return False

    message = build_signal_message(
        symbol=symbol,
        side=side,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )

    print("\n==============================")
    print(message)
    print("==============================\n")

    success = await send_telegram_message(message)

    if success:
        # 只有Telegram发送成功之后才进入4小时冷却
        mark_signal_sent(symbol)

        print(
            f"[信号已发送] {symbol} {side}"
        )

        return True

    return False
    # =========================================================
# 全局1H方向
# =========================================================

one_hour_directions = {}

last_direction_refresh = 0


# =========================================================
# 刷新1H方向
# =========================================================

def refresh_directions_if_needed():
    global one_hour_directions
    global last_direction_refresh

    now = time.time()

    if (
        not one_hour_directions
        or now - last_direction_refresh >= ONE_HOUR_REFRESH
    ):
        try:
            new_directions = refresh_all_directions()

            one_hour_directions = new_directions

            last_direction_refresh = now

            # 方向变化以后，重置该币种策略状态
            for symbol in SYMBOLS:

                new_direction = new_directions.get(symbol)

                state = get_state(symbol)

                old_direction = state.get("direction")

                if old_direction != new_direction:

                    print(
                        f"[方向变化] {symbol}: "
                        f"{old_direction} -> {new_direction}"
                    )

                    reset_strategy_state(
                        symbol,
                        new_direction
                    )

        except Exception as e:

            print(
                f"[1H方向刷新异常] {e}"
            )


# =========================================================
# 单个币种扫描
# =========================================================

async def scan_symbol(symbol):
    try:

        direction = one_hour_directions.get(symbol)

        # 没有严格Vegas方向
        if direction not in ("LONG", "SHORT"):
            return

        candles = get_15m_data(symbol)

        if candles is None:
            return

        if len(candles) < 180:
            return

        state = get_state(symbol)

        state["direction"] = direction

        # =================================================
        # 先检测B1
        # =================================================

        signal = check_strategy_b1(
            symbol,
            candles,
            direction
        )

        if signal is not None:

            await process_signal(
                symbol,
                signal,
                candles
            )

            return

        # =================================================
        # 再检测B2
        # =================================================

        signal = check_strategy_b2(
            symbol,
            candles,
            direction
        )

        if signal is not None:

            await process_signal(
                symbol,
                signal,
                candles
            )

            return

        # =================================================
        # 最后检测A
        # =================================================

        signal = check_strategy_a(
            symbol,
            candles,
            direction
        )

        if signal is not None:

            await process_signal(
                symbol,
                signal,
                candles
            )

            return

    except Exception as e:

        print(
            f"[扫描异常] {symbol}: {e}"
        )


# =========================================================
# 全部币种扫描
# =========================================================

async def scan_all_symbols():
    """
    扫描全部62个币。

    为了避免Bybit请求过于集中，
    每个币之间稍微留一点间隔。
    """

    print(
        f"\n[{format_time()}] "
        f"开始扫描 {len(SYMBOLS)} 个币种"
    )

    for symbol in SYMBOLS:

        await scan_symbol(symbol)

        await asyncio.sleep(0.05)

    print(
        f"[{format_time()}] "
        f"本轮扫描完成"
    )


# =========================================================
# Telegram /start
# =========================================================

async def handle_start(update, context):

    try:

        await update.message.reply_text(
            "🤖 Vegas交易信号机器人已启动\n\n"
            "交易所：Bybit\n"
            "策略：Vegas EMA 12 / 144 / 169 / 576 / 676\n"
            "周期：1H + 15M\n"
            "监控：62个币种\n\n"
            "机器人只发送交易信号，不执行交易。"
        )

    except Exception as e:

        print(
            f"[/start错误] {e}"
        )


# =========================================================
# Telegram /status
# =========================================================

async def handle_status(update, context):

    try:

        active_count = sum(
            1
            for x in one_hour_directions.values()
            if x in ("LONG", "SHORT")
        )

        await update.message.reply_text(
            "🤖 机器人状态：运行中\n\n"
            f"监控币种：{len(SYMBOLS)}\n"
            f"当前有1H方向：{active_count}\n"
            f"Bybit：正常\n"
            f"最后方向刷新："
            f"{format_time()}\n\n"
            "策略：Vegas A / B1 / B2\n"
            "信号冷却：每币种4小时"
        )

    except Exception as e:

        print(
            f"[/status错误] {e}"
        )


# =========================================================
# Telegram polling
# =========================================================

async def telegram_loop():

    from telegram.ext import (
        Application,
        CommandHandler,
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            handle_start
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            handle_status
        )
    )

    print(
        "========== Telegram机器人启动 =========="
    )

    await application.initialize()

    await application.start()

    await application.updater.start_polling()

    try:

        while True:

            await asyncio.sleep(3600)

    finally:

        await application.updater.stop()

        await application.stop()

        await application.shutdown()


# =========================================================
# Render健康检查
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
            b"Vegas Telegram Bot is running."
        )

    def log_message(self, format, *args):
        return


def start_health_server():

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(
        f"[Render] Health server listening on port {port}"
    )

    server.serve_forever()


# =========================================================
# 主扫描程序
# =========================================================

async def trading_loop():

    print(
        "======================================"
    )

    print(
        "      Vegas Telegram Signal Bot"
    )

    print(
        "======================================"
    )

    print(
        f"Bybit: {BYBIT_BASE_URL}"
    )

    print(
        f"监控币种数量: {len(SYMBOLS)}"
    )

    print(
        "策略: EMA 12 / 144 / 169 / 576 / 676"
    )

    print(
        "周期: 1H + 15M"
    )

    print(
        "信号冷却: 4小时/币种"
    )

    print(
        "======================================"
    )

    # ---------------------------------------------
    # 第一次立即刷新1H方向
    # ---------------------------------------------

    refresh_directions_if_needed()

    # ---------------------------------------------
    # 无限运行
    # ---------------------------------------------

    while True:

        try:

            refresh_directions_if_needed()

            await scan_all_symbols()

        except Exception as e:

            print(
                f"[主循环异常] {e}"
            )

        # 15秒扫描一次
        await asyncio.sleep(
            SCAN_INTERVAL
        )


# =========================================================
# 程序入口
# =========================================================

def main():

    # ---------------------------------------------
    # Render健康检查
    # ---------------------------------------------

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    # ---------------------------------------------
    # 启动交易循环
    # ---------------------------------------------

    async def runner():

        # Telegram和交易扫描同时运行
        await asyncio.gather(
            telegram_loop(),
            trading_loop()
        )

    try:

        asyncio.run(runner())

    except KeyboardInterrupt:

        print(
            "机器人已停止"
        )

    except Exception as e:

        print(
            f"[致命错误] {e}"
        )


# =========================================================
# 启动
# =========================================================

if __name__ == "__main__":
    main()
