import os
import time
import asyncio
import threading
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes


# =========================================================
# 基础配置
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = "8289465171"

BYBIT_BASE_URL = "https://api.bybit.com"

SCAN_INTERVAL = 15
ONE_HOUR_REFRESH = 15 * 60
SIGNAL_COOLDOWN = 4 * 60 * 60

EMA_FAST = 12
EMA_144 = 144
EMA_169 = 169
EMA_576 = 576
EMA_676 = 676

# =========================================================
# 62个币
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

session = requests.Session()
session.headers.update({
    "User-Agent": "YeTradingVegasBot/1.0"
})

telegram_bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None

beijing_tz = timezone(timedelta(hours=8))

one_hour_directions = {}
last_direction_refresh = None

direction_errors = {}
last_direction_details = {}

strategy_states = {}

last_signal_time = {}


# =========================================================
# 时间
# =========================================================

def now_beijing():
    return datetime.now(timezone.utc).astimezone(beijing_tz)


def format_time(dt=None):
    if dt is None:
        dt = now_beijing()

    return dt.strftime("%Y-%m-%d %H:%M")


# =========================================================
# EMA
# =========================================================

def calculate_ema(values, period):
    if not values or len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    ema = sum(values[:period]) / period

    for price in values[period:]:
        ema = (price - ema) * multiplier + ema

    return ema


def calculate_ema_series(values, period):
    if len(values) < period:
        return []

    multiplier = 2 / (period + 1)

    first_ema = sum(values[:period]) / period

    result = [None] * (period - 1)
    result.append(first_ema)

    ema = first_ema

    for price in values[period:]:
        ema = (price - ema) * multiplier + ema
        result.append(ema)

    return result


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

    返回：
    [
        {
            "timestamp": ...,
            "open": ...,
            "high": ...,
            "low": ...,
            "close": ...,
            "volume": ...
        }
    ]
    """

    url = f"{BYBIT_BASE_URL}/v5/market/kline"

    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    try:
        response = session.get(
            url,
            params=params,
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        ret_code = data.get("retCode")

        if ret_code != 0:
            raise RuntimeError(
                f"Bybit retCode={ret_code}, "
                f"retMsg={data.get('retMsg')}"
            )

        result = data.get("result", {})
        raw_list = result.get("list", [])

        if not raw_list:
            raise RuntimeError("Bybit返回K线为空")

        candles = []

        for item in raw_list:
            if len(item) < 6:
                continue

            candles.append({
                "timestamp": int(item[0]),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
            })

        # Bybit返回通常是新 -> 旧
        # 统一整理成旧 -> 新
        candles.sort(key=lambda x: x["timestamp"])

        return candles

    except Exception as e:
        raise RuntimeError(
            f"{symbol} {interval}K获取失败: {e}"
        )


# =========================================================
# 1H方向
# =========================================================

def get_1h_direction(symbol):
    """
    严格Vegas 1H方向：

    多头：
    EMA12 > EMA144 > EMA169 > EMA576 > EMA676
    且收盘价 > EMA144

    空头：
    EMA12 < EMA144 < EMA169 < EMA576 < EMA676
    且收盘价 < EMA144

    注意：
    只使用已经收盘的1H K线。
    """

    candles = get_klines(symbol, "60", 800)

    if len(candles) < 700:
        raise RuntimeError(
            f"K线数量不足：{len(candles)}"
        )

    # 最后一根可能是正在形成的1H K线
    closed_candles = candles[:-1]

    closes = [
        candle["close"]
        for candle in closed_candles
    ]

    if len(closes) < EMA_676:
        raise RuntimeError(
            f"收盘K线不足：{len(closes)}"
        )

    ema12 = calculate_ema(closes, EMA_FAST)
    ema144 = calculate_ema(closes, EMA_144)
    ema169 = calculate_ema(closes, EMA_169)
    ema576 = calculate_ema(closes, EMA_576)
    ema676 = calculate_ema(closes, EMA_676)

    close_price = closes[-1]

    if None in (
        ema12,
        ema144,
        ema169,
        ema576,
        ema676
    ):
        raise RuntimeError("EMA计算失败")

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
        direction = "LONG"
        reason = "EMA12>144>169>576>676 且价格在EMA144上方"

    elif bearish:
        direction = "SHORT"
        reason = "EMA12<144<169<576<676 且价格在EMA144下方"

    else:
        direction = None

        # 详细说明为什么没有方向
        if ema12 > ema144:
            side = "EMA12在EMA144上方"
        else:
            side = "EMA12在EMA144下方"

        reason = side

    last_direction_details[symbol] = {
        "candles": len(candles),
        "closed_candles": len(closed_candles),
        "close": close_price,
        "ema12": ema12,
        "ema144": ema144,
        "ema169": ema169,
        "ema576": ema576,
        "ema676": ema676,
        "reason": reason,
    }

    return direction


# =========================================================
# 刷新全部1H方向
# =========================================================

def refresh_all_directions():
    global last_direction_refresh

    long_count = 0
    short_count = 0
    neutral_count = 0
    error_count = 0

    for symbol in SYMBOLS:

        try:
            direction = get_1h_direction(symbol)

            old_direction = one_hour_directions.get(symbol)

            one_hour_directions[symbol] = direction

            direction_errors.pop(symbol, None)

            if direction == "LONG":
                long_count += 1

            elif direction == "SHORT":
                short_count += 1

            else:
                neutral_count += 1

            # 方向发生变化，重置策略状态
            if old_direction != direction:
                reset_strategy_state(symbol)

            details = last_direction_details.get(symbol, {})

            print(
                f"[1H] {symbol} => "
                f"{direction or '无方向'} | "
                f"close={details.get('close')} | "
                f"EMA12={details.get('ema12')} | "
                f"EMA144={details.get('ema144')} | "
                f"EMA169={details.get('ema169')} | "
                f"EMA576={details.get('ema576')} | "
                f"EMA676={details.get('ema676')}"
            )

        except Exception as e:
            error_count += 1

            one_hour_directions[symbol] = None

            direction_errors[symbol] = str(e)

            print(
                f"[1H ERROR] {symbol}: {e}"
            )

    last_direction_refresh = now_beijing()

    print(
        f"[1H REFRESH] "
        f"LONG={long_count} "
        f"SHORT={short_count} "
        f"无方向={neutral_count} "
        f"错误={error_count}"
    )


# =========================================================
# 策略状态
# =========================================================

def get_state(symbol):
    if symbol not in strategy_states:
        strategy_states[symbol] = {
            "a_active": False,

            "b1_active": False,
            "b1_crossed": False,

            "b2_active": False,

            "last_candle_timestamp": None,
        }

    return strategy_states[symbol]


def reset_strategy_state(symbol):
    strategy_states[symbol] = {
        "a_active": False,
        "b1_active": False,
        "b1_crossed": False,
        "b2_active": False,
        "last_candle_timestamp": None,
    }


# =========================================================
# 15M指标
# =========================================================

def calculate_15m_indicators(candles):
    closes = [
        c["close"]
        for c in candles
    ]

    ema12_series = calculate_ema_series(
        closes,
        EMA_FAST
    )

    ema144_series = calculate_ema_series(
        closes,
        EMA_144
    )

    ema169_series = calculate_ema_series(
        closes,
        EMA_169
    )

    result = []

    for i, candle in enumerate(candles):

        ema12 = (
            ema12_series[i]
            if i < len(ema12_series)
            else None
        )

        ema144 = (
            ema144_series[i]
            if i < len(ema144_series)
            else None
        )

        ema169 = (
            ema169_series[i]
            if i < len(ema169_series)
            else None
        )

        result.append({
            **candle,
            "ema12": ema12,
            "ema144": ema144,
            "ema169": ema169,
        })

    return result


def get_15m_data(symbol):
    candles = get_klines(
        symbol,
        "15",
        250
    )

    if len(candles) < 180:
        raise RuntimeError(
            f"15M K线不足：{len(candles)}"
        )

    return calculate_15m_indicators(candles)


# =========================================================
# EMA144 ±2%
# =========================================================

def price_in_ema144_zone(candle):
    ema144 = candle["ema144"]

    if ema144 is None:
        return False

    price = candle["close"]

    lower = ema144 * 0.98
    upper = ema144 * 1.02

    return lower <= price <= upper


# =========================================================
# Strategy A
# =========================================================

def check_strategy_a(symbol, candles, direction):
    """
    A：正常回踩

    多：
    1H多头
    15M进入EMA144 ±2%
    回踩过程中15M收盘不能跌破EMA169
    最后15M收盘重新站上EMA12

    空头反过来。
    """

    if len(candles) < 20:
        return None

    state = get_state(symbol)

    signal_candle = candles[-2]
    previous_candles = candles[:-1]

    if (
        signal_candle["ema12"] is None
        or signal_candle["ema144"] is None
        or signal_candle["ema169"] is None
    ):
        return None

    # -------------------------------------
    # 多头
    # -------------------------------------

    if direction == "LONG":

        # 如果之前已经进入A回踩状态
        if not state["a_active"]:

            # 允许用已经收盘的15M K线进入区域
            if price_in_ema144_zone(signal_candle):

                # 进入时不能已经收盘跌破EMA169
                if signal_candle["close"] >= signal_candle["ema169"]:
                    state["a_active"] = True

            return None

        # A已经激活

        # 结构失效
        if signal_candle["close"] < signal_candle["ema169"]:
            state["a_active"] = False
            return None

        # 15M收盘重新站上EMA12
        if signal_candle["close"] > signal_candle["ema12"]:

            state["a_active"] = False

            return {
                "type": "LONG",
                "strategy": "A",
                "candle": signal_candle,
            }

    # -------------------------------------
    # 空头
    # -------------------------------------

    elif direction == "SHORT":

        if not state["a_active"]:

            if price_in_ema144_zone(signal_candle):

                if signal_candle["close"] <= signal_candle["ema169"]:
                    state["a_active"] = True

            return None

        # 结构失效
        if signal_candle["close"] > signal_candle["ema169"]:
            state["a_active"] = False
            return None

        # 15M收盘跌破EMA12
        if signal_candle["close"] < signal_candle["ema12"]:

            state["a_active"] = False

            return {
                "type": "SHORT",
                "strategy": "A",
                "candle": signal_candle,
            }

    return None


# =========================================================
# Strategy B2
# =========================================================

def check_strategy_b2(symbol, candles, direction):
    """
    B2：

    多头：
    价格跌破144/169
    EMA12没有跌破144/169
    价格重新恢复
    确认K线：
        low > EMA144
        close > EMA169

    空头镜像。
    """

    if len(candles) < 20:
        return None

    state = get_state(symbol)

    # 只用已经收盘K线
    candle = candles[-2]

    if (
        candle["ema12"] is None
        or candle["ema144"] is None
        or candle["ema169"] is None
    ):
        return None

    # -------------------------------------
    # 多头
    # -------------------------------------

    if direction == "LONG":

        price_break = (
            candle["low"] < candle["ema144"]
            or candle["low"] < candle["ema169"]
        )

        ema12_stays_above = (
            candle["ema12"] >= candle["ema144"]
            and candle["ema12"] >= candle["ema169"]
        )

        if price_break and ema12_stays_above:
            state["b2_active"] = True

        if state["b2_active"]:

            confirmed = (
                candle["low"] > candle["ema144"]
                and candle["close"] > candle["ema169"]
            )

            if confirmed:

                state["b2_active"] = False

                return {
                    "type": "LONG",
                    "strategy": "B2",
                    "candle": candle,
                }

            # 如果EMA12也已经跌破结构
            if (
                candle["ema12"] < candle["ema144"]
                and candle["ema12"] < candle["ema169"]
            ):
                state["b2_active"] = False

    # -------------------------------------
    # 空头
    # -------------------------------------

    elif direction == "SHORT":

        price_break = (
            candle["high"] > candle["ema144"]
            or candle["high"] > candle["ema169"]
        )

        ema12_stays_below = (
            candle["ema12"] <= candle["ema144"]
            and candle["ema12"] <= candle["ema169"]
        )

        if price_break and ema12_stays_below:
            state["b2_active"] = True

        if state["b2_active"]:

            confirmed = (
                candle["high"] < candle["ema144"]
                and candle["close"] < candle["ema169"]
            )

            if confirmed:

                state["b2_active"] = False

                return {
                    "type": "SHORT",
                    "strategy": "B2",
                    "candle": candle,
                }

            if (
                candle["ema12"] > candle["ema144"]
                and candle["ema12"] > candle["ema169"]
            ):
                state["b2_active"] = False

    return None


# =========================================================
# Strategy B1
# =========================================================

def check_strategy_b1(symbol, candles, direction):
    """
    B1：

    多：
    价格先跌破144/169
    EMA12也跌破144/169
    然后EMA12上穿EMA144
    立即信号

    空头镜像。

    注意：
    当前代码采用15秒轮询，所以“立即”是指
    下一次扫描发现交叉后立即发送。
    """

    if len(candles) < 20:
        return None

    state = get_state(symbol)

    # 当前最新K线允许是正在形成的15M K线
    current = candles[-1]
    previous = candles[-2]

    if any(
        x is None
        for x in (
            current["ema12"],
            current["ema144"],
            current["ema169"],
            previous["ema12"],
            previous["ema144"],
            previous["ema169"],
        )
    ):
        return None

    # -------------------------------------
    # 多头
    # -------------------------------------

    if direction == "LONG":

        deep_break = (
            current["low"] < current["ema144"]
            and current["low"] < current["ema169"]
            and current["ema12"] < current["ema144"]
            and current["ema12"] < current["ema169"]
        )

        if deep_break:
            state["b1_active"] = True

        if state["b1_active"]:

            crossed_up = (
                previous["ema12"] <= previous["ema144"]
                and current["ema12"] > current["ema144"]
            )

            if crossed_up:

                state["b1_active"] = False

                return {
                    "type": "LONG",
                    "strategy": "B1",
                    "candle": current,
                }

            # 如果重新深度向下，可以继续等待
            # 如果1H方向改变，则外部会reset

    # -------------------------------------
    # 空头
    # -------------------------------------

    elif direction == "SHORT":

        deep_break = (
            current["high"] > current["ema144"]
            and current["high"] > current["ema169"]
            and current["ema12"] > current["ema144"]
            and current["ema12"] > current["ema169"]
        )

        if deep_break:
            state["b1_active"] = True

        if state["b1_active"]:

            crossed_down = (
                previous["ema12"] >= previous["ema144"]
                and current["ema12"] < current["ema144"]
            )

            if crossed_down:

                state["b1_active"] = False

                return {
                    "type": "SHORT",
                    "strategy": "B1",
                    "candle": current,
                }

    return None


# =========================================================
# 止损
# =========================================================

def calculate_stop_loss(candles, signal_type):
    """
    使用信号K线之前的10根已经收盘15M K线。

    多：
    最低价 * 0.995

    空：
    最高价 * 1.005
    """

    if len(candles) < 12:
        return None

    # 最后一根是当前信号K线
    # 取它之前10根
    previous_10 = candles[-11:-1]

    if len(previous_10) != 10:
        return None

    if signal_type == "LONG":

        lowest_low = min(
            candle["low"]
            for candle in previous_10
        )

        return lowest_low * 0.995

    else:

        highest_high = max(
            candle["high"]
            for candle in previous_10
        )

        return highest_high * 1.005


# =========================================================
# 止盈
# =========================================================

def calculate_take_profit(
    entry,
    stop_loss,
    signal_type
):
    if signal_type == "LONG":

        risk = entry - stop_loss

        return entry + risk * 2

    else:

        risk = stop_loss - entry

        return entry - risk * 2


# =========================================================
# 价格格式
# =========================================================

def format_price(price):
    if price is None:
        return "N/A"

    if price >= 1000:
        return f"{price:.2f}"

    if price >= 100:
        return f"{price:.3f}"

    if price >= 1:
        return f"{price:.4f}"

    if price >= 0.01:
        return f"{price:.6f}"

    return f"{price:.8f}"


# =========================================================
# 信号冷却
# =========================================================

def can_send_signal(symbol):
    last_time = last_signal_time.get(symbol)

    if last_time is None:
        return True

    return (
        time.time() - last_time
        >= SIGNAL_COOLDOWN
    )


def mark_signal_sent(symbol):
    last_signal_time[symbol] = time.time()


# =========================================================
# Telegram消息
# =========================================================

def build_signal_message(
    symbol,
    signal_type,
    entry,
    stop_loss,
    take_profit,
    signal_time
):

    if signal_type == "LONG":

        return (
            "🟢 多单信号\n\n"
            f"币种：{symbol}\n"
            f"📍 入场价：{format_price(entry)}\n"
            f"🛑 止损：{format_price(stop_loss)}\n"
            f"🎯 止盈：{format_price(take_profit)}\n\n"
            f"⏱ 信号时间：{signal_time}"
        )

    else:

        return (
            "🔴 空单信号\n\n"
            f"币种：{symbol}\n"
            f"📍 入场价：{format_price(entry)}\n"
            f"🛑 止损：{format_price(stop_loss)}\n"
            f"🎯 止盈：{format_price(take_profit)}\n\n"
            f"⏱ 信号时间：{signal_time}"
        )


async def send_telegram_message(message):
    if telegram_bot is None:
        raise RuntimeError("BOT_TOKEN不存在")

    await telegram_bot.send_message(
        chat_id=CHAT_ID,
        text=message
    )


# =========================================================
# 处理信号
# =========================================================

def process_signal(
    symbol,
    signal,
    all_candles
):
    if signal is None:
        return False

    if not can_send_signal(symbol):
        print(
            f"[COOLDOWN] {symbol} "
            f"4小时冷却中"
        )
        return False

    signal_type = signal["type"]
    signal_candle = signal["candle"]

    entry = signal_candle["close"]

    # B1是当前正在形成的K线
    # A/B2是已经收盘的K线
    #
    # 为了统一计算SL：
    # 都排除信号K线
    stop_loss = calculate_stop_loss(
        all_candles,
        signal_type
    )

    if stop_loss is None:
        return False

    take_profit = calculate_take_profit(
        entry,
        stop_loss,
        signal_type
    )

    if signal_type == "LONG":

        if stop_loss >= entry:
            print(
                f"[INVALID SL] {symbol} "
                f"多单止损>=入场"
            )
            return False

        if take_profit <= entry:
            return False

    else:

        if stop_loss <= entry:
            print(
                f"[INVALID SL] {symbol} "
                f"空单止损<=入场"
            )
            return False

        if take_profit >= entry:
            return False

    message = build_signal_message(
        symbol=symbol,
        signal_type=signal_type,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        signal_time=format_time()
    )

    try:

        # 同步线程中调用Telegram异步函数
        asyncio.run(
            send_telegram_message(message)
        )

        mark_signal_sent(symbol)

        print(
            f"[SIGNAL] {symbol} "
            f"{signal_type} "
            f"strategy={signal['strategy']} "
            f"entry={entry} "
            f"SL={stop_loss} "
            f"TP={take_profit}"
        )

        return True

    except Exception as e:

        print(
            f"[TELEGRAM ERROR] "
            f"{symbol}: {e}"
        )

        return False


# =========================================================
# 扫描单币
# =========================================================

def scan_symbol(symbol):
    direction = one_hour_directions.get(symbol)

    if direction not in ("LONG", "SHORT"):
        return

    try:

        candles = get_15m_data(symbol)

        state = get_state(symbol)

        current_timestamp = candles[-1]["timestamp"]

        # -------------------------------------------------
        # B1
        # -------------------------------------------------

        signal = check_strategy_b1(
            symbol,
            candles,
            direction
        )

        if signal:

            if process_signal(
                symbol,
                signal,
                candles
            ):
                return

        # -------------------------------------------------
        # B2
        # -------------------------------------------------

        signal = check_strategy_b2(
            symbol,
            candles,
            direction
        )

        if signal:

            if process_signal(
                symbol,
                signal,
                candles
            ):
                return

        # -------------------------------------------------
        # A
        # -------------------------------------------------

        signal = check_strategy_a(
            symbol,
            candles,
            direction
        )

        if signal:

            process_signal(
                symbol,
                signal,
                candles
            )

        state["last_candle_timestamp"] = current_timestamp

    except Exception as e:

        print(
            f"[15M ERROR] "
            f"{symbol}: {e}"
        )


# =========================================================
# 扫描全部币
# =========================================================

def scan_all_symbols():

    for symbol in SYMBOLS:

        scan_symbol(symbol)

        time.sleep(0.05)


# =========================================================
# 方向刷新
# =========================================================

def refresh_directions_if_needed():

    global last_direction_refresh

    if last_direction_refresh is None:

        refresh_all_directions()

        return

    elapsed = (
        now_beijing()
        - last_direction_refresh
    ).total_seconds()

    if elapsed >= ONE_HOUR_REFRESH:

        refresh_all_directions()


# =========================================================
# Telegram /start
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🤖 Vegas交易机器人已启动\n\n"
        "策略：Vegas A / B1 / B2\n"
        "数据源：Bybit\n"
        "监控币种：62\n"
        "信号冷却：每币种4小时\n\n"
        "输入 /status 查看机器人状态"
    )


# =========================================================
# Telegram /status
# =========================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    long_count = sum(
        1
        for x in one_hour_directions.values()
        if x == "LONG"
    )

    short_count = sum(
        1
        for x in one_hour_directions.values()
        if x == "SHORT"
    )

    neutral_count = sum(
        1
        for x in one_hour_directions.values()
        if x is None
    )

    error_count = len(direction_errors)

    refresh_time = (
        format_time(last_direction_refresh)
        if last_direction_refresh
        else "尚未刷新"
    )

    text = (
        "🤖 机器人状态：运行中\n\n"
        f"监控币种：{len(SYMBOLS)}\n"
        f"🟢 1H多头：{long_count}\n"
        f"🔴 1H空头：{short_count}\n"
        f"⚪ 无方向：{neutral_count}\n"
        f"⚠️ 数据错误：{error_count}\n\n"
        "数据源：Bybit\n"
        f"最后方向刷新：{refresh_time}\n\n"
        "策略：Vegas A / B1 / B2\n"
        "信号冷却：每币种4小时"
    )

    await update.message.reply_text(text)


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
            b"Vegas Trading Bot is running."
        )

    def log_message(self, format, *args):
        return


def run_health_server():

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
        f"[HEALTH] Server running on port {port}"
    )

    server.serve_forever()


# =========================================================
# Telegram循环
# =========================================================

async def telegram_loop():

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status_command
        )
    )

    print("[TELEGRAM] Bot starting...")

    await application.initialize()

    await application.start()

    await application.updater.start_polling()

    print("[TELEGRAM] Polling started.")

    try:

        while True:
            await asyncio.sleep(3600)

    finally:

        await application.updater.stop()

        await application.stop()

        await application.shutdown()


# =========================================================
# 交易扫描循环
# =========================================================

async def trading_loop():

    print(
        f"[TRADING] "
        f"开始监控 {len(SYMBOLS)} 个币"
    )

    while True:

        try:

            refresh_directions_if_needed()

            scan_all_symbols()

        except Exception as e:

            print(
                f"[TRADING LOOP ERROR] {e}"
            )

        await asyncio.sleep(
            SCAN_INTERVAL
        )


# =========================================================
# 主程序
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "环境变量 BOT_TOKEN 不存在"
        )

    # Render健康检查
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
    )

    print(
        "策略：Vegas A / B1 / B2"
    )

    print(
        "========================================"
    )

    asyncio.run(
        asyncio.gather(
            telegram_loop(),
            trading_loop()
        )
    )


if __name__ == "__main__":
    main()
