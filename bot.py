import os
import json
import time
import threading
from datetime import datetime

from urllib.parse import urlencode
from urllib.request import Request, urlopen
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# =========================================================
# 基础配置
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")

PORT = int(os.getenv("PORT", "10000"))

BINANCE_API = "https://fapi.binance.com/fapi/v1/klines"

BINANCE_EXCHANGE_INFO = (
    "https://fapi.binance.com/fapi/v1/exchangeInfo"
)

SCAN_INTERVAL = 180

CHAT_FILE = "chat_ids.json"


# =========================================================
# 监控币种
# =========================================================

SYMBOLS = [

    # =========================
    # 核心45
    # =========================

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


    # =========================
    # 第一批扩展强势组
    # =========================

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


    # =========================
    # 第二批近期强势组
    # =========================

    "MAGMAUSDT",
    "FFUSDT",
    "LISKUSDT",
    "ARKUSDT",
    "EMBERUSDT",
    "PENGUUSDT",
    "JASMYUSDT",

]


# 自动去重
SYMBOLS = list(dict.fromkeys(SYMBOLS))


# =========================================================
# 全局变量
# =========================================================

CHAT_IDS = set()

LAST_SIGNALS = {}

VALID_SYMBOLS = set()


# =========================================================
# 读取 / 保存群聊ID
# =========================================================

def load_chat_ids():

    global CHAT_IDS

    try:

        if os.path.exists(CHAT_FILE):

            with open(
                CHAT_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                data = json.load(f)

            CHAT_IDS = set(data)

    except Exception as e:

        print("读取chat_ids失败:", e)


def save_chat_ids():

    try:

        with open(
            CHAT_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                list(CHAT_IDS),
                f
            )

    except Exception as e:

        print("保存chat_ids失败:", e)


# =========================================================
# Telegram /start
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_chat:

        chat_id = update.effective_chat.id

        CHAT_IDS.add(chat_id)

        save_chat_ids()

    await update.message.reply_text(

        "机器人启动成功！🎉\n\n"

        "📊 Vegas + MACD + TD9 信号机器人\n\n"

        f"监控币种：{len(SYMBOLS)}个\n"

        "方向：1H EMA12/144/169/576/676\n"

        "入场：15M 144/169回踩 + MACD动能\n"

        "辅助：TD9\n\n"

        "🟢 多头：1H趋势向上\n"

        "🔴 空头：1H趋势向下\n\n"

        "机器人现在开始监控。"

    )


# =========================================================
# HTTP健康检查
# =========================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(200)

        self.end_headers()

        self.wfile.write(
            b"Telegram Vegas trading bot is running!"
        )

    def log_message(
        self,
        format,
        *args
    ):

        pass


def run_web_server():

    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    server.serve_forever()


# =========================================================
# Binance K线
# =========================================================

def get_klines(
    symbol,
    interval,
    limit=800
):

    params = urlencode({

        "symbol": symbol,

        "interval": interval,

        "limit": limit

    })

    url = (
        BINANCE_API
        + "?"
        + params
    )

    request = Request(

        url,

        headers={
            "User-Agent": "Mozilla/5.0"
        }

    )

    try:

        with urlopen(
            request,
            timeout=15
        ) as response:

            data = json.loads(
                response
                .read()
                .decode("utf-8")
            )

        return data

    except Exception as e:

        print(
            f"{symbol} {interval} K线获取失败:",
            e
        )

        return []


# =========================================================
# 获取Binance有效永续合约
# =========================================================

def load_valid_symbols():

    global VALID_SYMBOLS

    try:

        request = Request(

            BINANCE_EXCHANGE_INFO,

            headers={
                "User-Agent": "Mozilla/5.0"
            }

        )

        with urlopen(
            request,
            timeout=20
        ) as response:

            data = json.loads(
                response
                .read()
                .decode("utf-8")
            )

        for item in data.get(
            "symbols",
            []
        ):

            symbol = item.get(
                "symbol"
            )

            contract_type = item.get(
                "contractType"
            )

            status = item.get(
                "status"
            )

            quote_asset = item.get(
                "quoteAsset"
            )

            if (

                contract_type
                == "PERPETUAL"

                and status
                == "TRADING"

                and quote_asset
                == "USDT"

            ):

                VALID_SYMBOLS.add(
                    symbol
                )

        print(
            "Binance有效USDT永续数量:",
            len(VALID_SYMBOLS)
        )

    except Exception as e:

        print(
            "获取Binance合约列表失败:",
            e
        )

        # 如果获取失败，
        # 暂时使用原始列表
        VALID_SYMBOLS = set(
            SYMBOLS
        )


# =========================================================
# EMA
# =========================================================

def ema(
    values,
    period
):

    if len(values) < period:

        return []

    multiplier = (
        2
        / (period + 1)
    )

    result = []

    value = sum(
        values[:period]
    ) / period

    result.append(value)

    for price in values[period:]:

        value = (
            (price - value)
            * multiplier
            + value
        )

        result.append(value)

    return result


def ema_series(
    values,
    period
):

    if len(values) < period:

        return []

    multiplier = (
        2
        / (period + 1)
    )

    result = [
        None
    ] * (
        period - 1
    )

    value = sum(
        values[:period]
    ) / period

    result.append(value)

    for price in values[period:]:

        value = (
            (price - value)
            * multiplier
            + value
        )

        result.append(value)

    return result


# =========================================================
# MACD
# =========================================================

def calculate_macd(
    closes
):

    ema12 = ema_series(
        closes,
        12
    )

    ema26 = ema_series(
        closes,
        26
    )

    macd_line = []

    for i in range(
        len(closes)
    ):

        if (
            ema12[i] is None
            or ema26[i] is None
        ):

            macd_line.append(None)

        else:

            macd_line.append(
                ema12[i]
                - ema26[i]
            )

    valid_macd = [

        x
        for x in macd_line
        if x is not None

    ]

    signal_values = ema_series(
        valid_macd,
        9
    )

    signal_line = [
        None
    ] * (
        len(closes)
        - len(signal_values)
    )

    signal_line += signal_values

    histogram = []

    for i in range(
        len(closes)
    ):

        if (
            macd_line[i] is None
            or signal_line[i] is None
        ):

            histogram.append(None)

        else:

            histogram.append(
                macd_line[i]
                - signal_line[i]
            )

    return (
        macd_line,
        signal_line,
        histogram
    )


# =========================================================
# MACD多头动能重新增强
# =========================================================

def macd_long_restrength(
    histogram
):

    values = [

        x
        for x in histogram
        if x is not None

    ]

    if len(values) < 5:

        return False

    h4 = values[-4]
    h3 = values[-3]
    h2 = values[-2]
    h1 = values[-1]

    # 前面至少两根连续减弱
    weakening = (
        h3 < h4
        and
        h2 < h3
    )

    # 最新一根重新增强
    strengthening = (
        h1 > h2
    )

    return (
        weakening
        and
        strengthening
    )


# =========================================================
# MACD空头动能重新增强
# =========================================================

def macd_short_restrength(
    histogram
):

    values = [

        x
        for x in histogram
        if x is not None

    ]

    if len(values) < 5:

        return False

    h4 = values[-4]
    h3 = values[-3]
    h2 = values[-2]
    h1 = values[-1]

    weakening = (
        h3 > h4
        and
        h2 > h3
    )

    strengthening = (
        h1 < h2
    )

    return (
        weakening
        and
        strengthening
    )


# =========================================================
# TD9
# =========================================================

def td9_count(
    closes
):

    if len(closes) < 5:

        return (
            "未走完",
            0,
            0
        )

    up = 0
    down = 0

    for i in range(
        len(closes) - 1,
        3,
        -1
    ):

        current = closes[i]

        previous = closes[i - 4]

        if current > previous:

            up += 1

            down = 0

        elif current < previous:

            down += 1

            up = 0

        else:

            break

        if (
            up >= 9
            or down >= 9
        ):

            break

    if up >= 9:

        return (
            "上涨9",
            up,
            down
        )

    if down >= 9:

        return (
            "下跌9",
            up,
            down
        )

    return (
        "未走完",
        up,
        down
    )


# =========================================================
# 获取最近小高点
# =========================================================

def recent_swing_high(
    highs
):

    if len(highs) < 7:

        return None

    return max(
        highs[-7:-1]
    )


# =========================================================
# 获取最近小低点
# =========================================================

def recent_swing_low(
    lows
):

    if len(lows) < 7:

        return None

    return min(
        lows[-7:-1]
    )


# =========================================================
# 1H趋势
# =========================================================

def get_1h_trend():

    data = get_klines(
        "",
        ""
    )

    return None


# =========================================================
# 分析单个币种
# =========================================================

def analyze_symbol(
    symbol
):

    # ---------------------------------
    # 1H
    # ---------------------------------

    data_1h = get_klines(
        symbol,
        "1h",
        800
    )

    if len(data_1h) < 700:

        return None

    # 去掉当前未收盘K线
    closed_1h = data_1h[:-1]

    closes_1h = [

        float(x[4])
        for x in closed_1h

    ]

    price_1h = closes_1h[-1]

    ema12_1h = ema(
        closes_1h,
        12
    )[-1]

    ema144_1h = ema(
        closes_1h,
        144
    )[-1]

    ema169_1h = ema(
        closes_1h,
        169
    )[-1]

    ema576_1h = ema(
        closes_1h,
        576
    )[-1]

    ema676_1h = ema(
        closes_1h,
        676
    )[-1]


    # ---------------------------------
    # 1H 多头
    # ---------------------------------

    long_trend = (

        ema12_1h
        > ema144_1h

        and

        ema12_1h
        > ema169_1h

        and

        price_1h
        > ema144_1h

        and

        price_1h
        > ema169_1h

        and

        price_1h
        > ema576_1h

        and

        price_1h
        > ema676_1h

    )


    # ---------------------------------
    # 1H 空头
    # ---------------------------------

    short_trend = (

        ema12_1h
        < ema144_1h

        and

        ema12_1h
        < ema169_1h

        and

        price_1h
        < ema144_1h

        and

        price_1h
        < ema169_1h

        and

        price_1h
        < ema576_1h

        and

        price_1h
        < ema676_1h

    )


    if not long_trend and not short_trend:

        return None


    # ---------------------------------
    # 15M
    # ---------------------------------

    data_15m = get_klines(
        symbol,
        "15m",
        250
    )

    if len(data_15m) < 180:

        return None

    closed_15m = data_15m[:-1]


    opens = [
        float(x[1])
        for x in closed_15m
    ]

    highs = [
        float(x[2])
        for x in closed_15m
    ]

    lows = [
        float(x[3])
        for x in closed_15m
    ]

    closes = [
        float(x[4])
        for x in closed_15m
    ]


    price = closes[-1]


    # ---------------------------------
    # 15M EMA144 / EMA169
    # ---------------------------------

    ema144_series = ema_series(
        closes,
        144
    )

    ema169_series = ema_series(
        closes,
        169
    )

    ema144 = ema144_series[-1]

    ema169 = ema169_series[-1]


    upper = max(
        ema144,
        ema169
    )

    lower = min(
        ema144,
        ema169
    )


    # ---------------------------------
    # 用户确定的0.7%回踩区域
    # ---------------------------------

    long_zone_low = upper

    long_zone_high = (
        upper * 1.007
    )

    short_zone_low = (
        lower * 0.993
    )

    short_zone_high = lower


    # ---------------------------------
    # 最近8根K线是否进入回踩区
    # ---------------------------------

    long_touched = False

    short_touched = False

    lookback_start = max(
        0,
        len(closes) - 8
    )

    for i in range(
        lookback_start,
        len(closes)
    ):

        candle_low = lows[i]

        candle_high = highs[i]

        # 多头：
        # K线低点进入
        # EMA144/169上边界
        # 到上方0.7%的区域
        if (

            candle_low
            >= long_zone_low

            and

            candle_low
            <= long_zone_high

        ):

            long_touched = True


        # 空头：
        # K线高点进入
        # 下边界向下0.7%的区域
        if (

            candle_high
            <= short_zone_high

            and

            candle_high
            >= short_zone_low

        ):

            short_touched = True


    # ---------------------------------
    # 避免明显穿透隧道
    # ---------------------------------

    latest_close = closes[-1]

    if latest_close < lower:

        long_touched = False

    if latest_close > upper:

        short_touched = False


    # ---------------------------------
    # MACD
    # ---------------------------------

    (
        macd_line,
        signal_line,
        histogram
    ) = calculate_macd(
        closes
    )


    long_macd = (
        macd_long_restrength(
            histogram
        )
    )

    short_macd = (
        macd_short_restrength(
            histogram
        )


    )


    # ---------------------------------
    # 小级别突破
    # ---------------------------------

    swing_high = recent_swing_high(
        highs
    )

    swing_low = recent_swing_low(
        lows
    )


    long_breakout = (

        swing_high is not None

        and

        latest_close
        > swing_high

    )


    short_breakout = (

        swing_low is not None

        and

        latest_close
        < swing_low

    )


    # ---------------------------------
    # TD9
    # ---------------------------------

    (
        td_status,
        td_up,
        td_down

    ) = td9_count(
        closes
    )


    # ---------------------------------
    # 最终信号
    # ---------------------------------

    direction = None


    if (

        long_trend

        and

        long_touched

        and

        long_macd

        and

        long_breakout

    ):

        direction = "LONG"


    elif (

        short_trend

        and

        short_touched

        and

        short_macd

        and

        short_breakout

    ):

        direction = "SHORT"


    if direction is None:

        return None


    # ---------------------------------
    # 信号时间
    # ---------------------------------

    candle_time = int(
        closed_15m[-1][0]
    )


    return {

        "symbol": symbol,

        "direction": direction,

        "price": price,

        "ema12_1h": ema12_1h,

        "ema144_1h": ema144_1h,

        "ema169_1h": ema169_1h,

        "ema576_1h": ema576_1h,

        "ema676_1h": ema676_1h,

        "ema144_15m": ema144,

        "ema169_15m": ema169,

        "long_zone_low": long_zone_low,

        "long_zone_high": long_zone_high,

        "short_zone_low": short_zone_low,

        "short_zone_high": short_zone_high,

        "td_status": td_status,

        "td_up": td_up,

        "td_down": td_down,

        "candle_time": candle_time

    }


# =========================================================
# 格式化价格
# =========================================================

def format_price(
    price
):

    if price >= 1000:

        return f"{price:,.2f}"

    if price >= 1:

        return f"{price:.4f}"

    if price >= 0.01:

        return f"{price:.6f}"

    return f"{price:.8f}"


# =========================================================
# 生成信号消息
# =========================================================

def build_message(
    signal
):

    symbol = signal["symbol"]

    direction = signal["direction"]

    price = signal["price"]


    if direction == "LONG":

        title = "🟢 做多信号"

        trend = "1H 多头趋势"

        zone = (

            f"{format_price(signal['long_zone_low'])}"

            " → "

            f"{format_price(signal['long_zone_high'])}"

        )

        reason = (

            "价格回踩144/169隧道上边界"

            " + 0.7%区域\n"

            "MACD动能重新增强\n"

            "突破15M近期小高点"

        )

    else:

        title = "🔴 做空信号"

        trend = "1H 空头趋势"

        zone = (

            f"{format_price(signal['short_zone_low'])}"

            " → "

            f"{format_price(signal['short_zone_high'])}"

        )

        reason = (

            "价格反弹至144/169隧道下边界"

            " - 0.7%区域\n"

            "MACD动能重新转弱\n"

            "跌破15M近期小低点"

        )


    candle_time = datetime.fromtimestamp(

        signal["candle_time"] / 1000

    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


    return (

        f"📊 {symbol} 15M\n\n"

        f"{title}\n\n"

        f"💰 当前价格："
        f"{format_price(price)}\n"

        f"📈 1H方向：{trend}\n\n"

        "EMA趋势过滤：\n"

        f"EMA12："
        f"{format_price(signal['ema12_1h'])}\n"

        f"EMA144："
        f"{format_price(signal['ema144_1h'])}\n"

        f"EMA169："
        f"{format_price(signal['ema169_1h'])}\n"

        f"EMA576："
        f"{format_price(signal['ema576_1h'])}\n"

        f"EMA676："
        f"{format_price(signal['ema676_1h'])}\n\n"

        "🎯 15M回踩区域：\n"

        f"{zone}\n\n"

        "📌 入场确认：\n"

        f"{reason}\n\n"

        f"TD9：{signal['td_status']} "
        f"(上涨{signal['td_up']} / "
        f"下跌{signal['td_down']})\n\n"

        f"⏰ K线时间：{candle_time}\n\n"

        "⚠️ 仅作为交易信号参考，"
        "不是自动下单。"

    )


# =========================================================
# 发送消息
# =========================================================

async def send_signal(
    bot,
    signal
):

    message = build_message(
        signal
    )

    for chat_id in list(
        CHAT_IDS
    ):

        try:

            await bot.send_message(

                chat_id=chat_id,

                text=message

            )

        except Exception as e:

            print(
                f"发送到 {chat_id} 失败:",
                e
            )


# =========================================================
# 扫描器
# =========================================================

async def scanner(
    application
):

    print(
        "扫描器启动"
    )

    while True:

        try:

            if not VALID_SYMBOLS:

                await asyncio_sleep(
                    5
                )

                continue


            print(
                "开始扫描",
                len(SYMBOLS),
                "个币..."
            )


            for symbol in SYMBOLS:

                # Binance没有这个永续
                if symbol not in VALID_SYMBOLS:

                    print(
                        symbol,
                        "不是当前有效USDT永续，跳过"
                    )

                    continue


                try:

                    signal = analyze_symbol(
                        symbol
                    )


                    if signal is None:

                        continue


                    key = (

                        signal["symbol"],

                        signal["direction"],

                        signal["candle_time"]

                    )


                    # 防重复
                    if key in LAST_SIGNALS:

                        continue


                    LAST_SIGNALS[key] = True


                    print(
                        "发现信号:",
                        signal["symbol"],
                        signal["direction"]
                    )


                    await send_signal(
                        application.bot,
                        signal
                    )


                except Exception as e:

                    print(
                        symbol,
                        "分析失败:",
                        e
                    )


                # 稍微停一下
                await asyncio_sleep(
                    0.15
                )


        except Exception as e:

            print(
                "扫描器错误:",
                e
            )


        print(
            f"{SCAN_INTERVAL}秒后重新扫描..."
        )


        await asyncio_sleep(
            SCAN_INTERVAL
        )


# =========================================================
# 异步sleep
# =========================================================

async def asyncio_sleep(
    seconds
):

    import asyncio

    await asyncio.sleep(
        seconds
    )


# =========================================================
# 主程序
# =========================================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "没有找到 BOT_TOKEN"
        )


    load_chat_ids()


    load_valid_symbols()


    print(
        "================================"
    )

    print(
        "Vegas + MACD + TD9"
    )

    print(
        "Telegram Trading Signal Bot"
    )

    print(
        "监控币种:",
        len(SYMBOLS)
    )

    print(
        "有效币种:",
        len(
            [
                x
                for x in SYMBOLS
                if x in VALID_SYMBOLS
            ]
        )
    )

    print(
        "================================"
    )


    # Render健康检查
    threading.Thread(

        target=run_web_server,

        daemon=True

    ).start()


    application = (

        Application
        .builder()
        .token(TOKEN)
        .build()

    )


    application.add_handler(

        CommandHandler(
            "start",
            start
        )

    )


    async def post_init(
        app
    ):

        import asyncio

        asyncio.create_task(

            scanner(
                app
            )

        )


    application.post_init = post_init


    application.run_polling()


# =========================================================
# 启动
# =========================================================

if __name__ == "__main__":

    main()
