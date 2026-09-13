import os
import json
import asyncio
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

BINANCE_KLINE_API = (
    "https://fapi.binance.com/fapi/v1/klines"
)

BINANCE_EXCHANGE_INFO_API = (
    "https://fapi.binance.com/fapi/v1/exchangeInfo"
)

SCAN_INTERVAL = 180

CHAT_FILE = "chat_ids.json"


# =========================================================
# 监控币种
# 共62个
# =========================================================

SYMBOLS = [

    # =====================================================
    # 核心45
    # =====================================================

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

    # =====================================================
    # 第一批扩展10个
    # =====================================================

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

    # =====================================================
    # 第二批强势组7个
    # =====================================================

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
# 全局状态
# =========================================================

CHAT_IDS = set()

LAST_SIGNALS = set()

VALID_SYMBOLS = set()


# =========================================================
# Telegram 群聊ID
# =========================================================

def load_chat_ids():

    global CHAT_IDS

    try:

        if not os.path.exists(CHAT_FILE):
            return

        with open(
            CHAT_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if isinstance(data, list):

            CHAT_IDS = set(data)

    except Exception as error:

        print(
            "读取 chat_ids 失败:",
            error
        )


def save_chat_ids():

    try:

        with open(
            CHAT_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                list(CHAT_IDS),
                file
            )

    except Exception as error:

        print(
            "保存 chat_ids 失败:",
            error
        )


# =========================================================
# /start
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

        "方向：1H EMA12 / 144 / 169 / 576 / 676\n"

        "入场：15M 144/169回踩 + MACD动能\n"

        "辅助：TD9\n\n"

        "🟢 多头：1H大趋势向上\n"

        "🔴 空头：1H大趋势向下\n\n"

        "数据：Binance USDT永续\n"

        "机器人现在开始监控。"

    )


# =========================================================
# /status
# =========================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    valid_count = len(
        [
            symbol
            for symbol in SYMBOLS
            if symbol in VALID_SYMBOLS
        ]
    )

    await update.message.reply_text(

        "🤖 机器人状态\n\n"

        f"监控列表：{len(SYMBOLS)} 个\n"

        f"有效永续：{valid_count} 个\n"

        f"已绑定群聊：{len(CHAT_IDS)} 个\n"

        f"扫描周期：{SCAN_INTERVAL} 秒\n\n"

        "策略：\n"

        "1H EMA12/144/169/576/676\n"

        "15M EMA144/169 + 0.7%回踩\n"

        "MACD动能重新增强\n"

        "15M结构突破\n"

        "TD9辅助"

    )


# =========================================================
# Render 健康检查
# =========================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain"
        )

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

    print(
        f"Health server running on port {PORT}"
    )

    server.serve_forever()


# =========================================================
# Binance K线
# =========================================================

def get_klines(
    symbol,
    interval,
    limit
):

    params = urlencode({

        "symbol": symbol,

        "interval": interval,

        "limit": limit

    })

    url = (
        BINANCE_KLINE_API
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

        if not isinstance(data, list):

            return []

        return data

    except Exception as error:

        print(
            f"{symbol} {interval} K线获取失败:",
            error
        )

        return []


# =========================================================
# 获取Binance当前有效USDT永续
# =========================================================

def load_valid_symbols():

    global VALID_SYMBOLS

    try:

        request = Request(

            BINANCE_EXCHANGE_INFO_API,

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

            if (

                item.get("contractType")
                == "PERPETUAL"

                and

                item.get("status")
                == "TRADING"

                and

                item.get("quoteAsset")
                == "USDT"

            ):

                VALID_SYMBOLS.add(
                    symbol
                )

        print(
            "Binance有效USDT永续:",
            len(VALID_SYMBOLS)
        )

        invalid = [

            symbol
            for symbol in SYMBOLS
            if symbol not in VALID_SYMBOLS

        ]

        if invalid:

            print(
                "当前不存在或不可交易的币:",
                ", ".join(invalid)
            )

    except Exception as error:

        print(
            "获取Binance合约列表失败:",
            error
        )

        # 如果交易所信息接口暂时失败，
        # 使用原始列表继续运行
        VALID_SYMBOLS = set(
            SYMBOLS
        )


# =========================================================
# EMA
# =========================================================

def ema_series(
    values,
    period
):

    if len(values) < period:

        return []

    multiplier = (
        2.0
        /
        (period + 1.0)
    )

    result = [
        None
    ] * (
        period - 1
    )

    current = sum(
        values[:period]
    ) / period

    result.append(
        current
    )

    for price in values[period:]:

        current = (

            (
                price
                - current
            )
            * multiplier

            + current

        )

        result.append(
            current
        )

    return result


def ema_last(
    values,
    period
):

    series = ema_series(
        values,
        period
    )

    if not series:

        return None

    return series[-1]


# =========================================================
# MACD
# 标准 12 / 26 / 9
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

    macd_line = [
        None
    ] * len(closes)

    for index in range(
        len(closes)
    ):

        if (

            ema12[index] is not None

            and

            ema26[index] is not None

        ):

            macd_line[index] = (

                ema12[index]
                -
                ema26[index]

            )

    valid_macd = [

        value
        for value in macd_line
        if value is not None

    ]

    signal_values = ema_series(
        valid_macd,
        9
    )

    signal_line = [
        None
    ] * len(closes)

    valid_index = 0

    for index in range(
        len(closes)
    ):

        if macd_line[index] is None:

            continue

        if valid_index >= len(
            signal_values
        ):

            break

        signal_line[index] = (
            signal_values[valid_index]
        )

        valid_index += 1

    histogram = [
        None
    ] * len(closes)

    for index in range(
        len(closes)
    ):

        if (

            macd_line[index] is not None

            and

            signal_line[index] is not None

        ):

            histogram[index] = (

                macd_line[index]
                -
                signal_line[index]

            )

    return (
        macd_line,
        signal_line,
        histogram
    )


# =========================================================
# MACD 多头动能重新增强
#
# 前两根动能连续减弱
# 最新一根重新增强
# =========================================================

def macd_long_restrength(
    histogram
):

    values = [

        value
        for value in histogram
        if value is not None

    ]

    if len(values) < 4:

        return False

    h4 = values[-4]
    h3 = values[-3]
    h2 = values[-2]
    h1 = values[-1]

    weakening = (

        h3 < h4

        and

        h2 < h3

    )

    strengthening = (
        h1 > h2
    )

    return (
        weakening
        and
        strengthening
    )


# =========================================================
# MACD 空头动能重新增强
# =========================================================

def macd_short_restrength(
    histogram
):

    values = [

        value
        for value in histogram
        if value is not None

    ]

    if len(values) < 4:

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
#
# 这里作为辅助指标，不参与核心入场条件
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

    for index in range(
        len(closes) - 1,
        3,
        -1
    ):

        current = closes[index]

        previous = closes[
            index - 4
        ]

        if current > previous:

            up += 1
            down = 0

        elif current < previous:

            down += 1
            up = 0

        else:

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
# 价格格式
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
# 分析单个币种
# =========================================================

def analyze_symbol(
    symbol
):

    # =====================================================
    # 第一部分：1H大趋势
    # =====================================================

    data_1h = get_klines(
        symbol,
        "1h",
        800
    )

    if len(data_1h) < 700:

        return None

    # 最后一根是未收盘K线
    closed_1h = data_1h[:-1]

    closes_1h = [

        float(candle[4])
        for candle in closed_1h

    ]

    current_1h_price = closes_1h[-1]

    ema12_1h = ema_last(
        closes_1h,
        12
    )

    ema144_1h = ema_last(
        closes_1h,
        144
    )

    ema169_1h = ema_last(
        closes_1h,
        169
    )

    ema576_1h = ema_last(
        closes_1h,
        576
    )

    ema676_1h = ema_last(
        closes_1h,
        676
    )

    if any(
        value is None
        for value in [
            ema12_1h,
            ema144_1h,
            ema169_1h,
            ema576_1h,
            ema676_1h
        ]
    ):

        return None


    # =====================================================
    # 1H 多头趋势
    #
    # EMA12 > 144/169
    # 价格 > 144/169
    # 价格 > 576/676
    # =====================================================

    long_trend = (

        ema12_1h > ema144_1h

        and

        ema12_1h > ema169_1h

        and

        current_1h_price > ema144_1h

        and

        current_1h_price > ema169_1h

        and

        current_1h_price > ema576_1h

        and

        current_1h_price > ema676_1h

    )


    # =====================================================
    # 1H 空头趋势
    # =====================================================

    short_trend = (

        ema12_1h < ema144_1h

        and

        ema12_1h < ema169_1h

        and

        current_1h_price < ema144_1h

        and

        current_1h_price < ema169_1h

        and

        current_1h_price < ema576_1h

        and

        current_1h_price < ema676_1h

    )


    if not long_trend and not short_trend:

        return None


    # =====================================================
    # 第二部分：15M
    # =====================================================

    data_15m = get_klines(
        symbol,
        "15m",
        250
    )

    if len(data_15m) < 180:

        return None

    # 去掉未收盘K线
    closed_15m = data_15m[:-1]

    highs = [

        float(candle[2])
        for candle in closed_15m

    ]

    lows = [

        float(candle[3])
        for candle in closed_15m

    ]

    closes = [

        float(candle[4])
        for candle in closed_15m

    ]

    if len(closes) < 180:

        return None

    current_price = closes[-1]


    # =====================================================
    # 15M EMA144 / EMA169
    # =====================================================

    ema144_15m_series = ema_series(
        closes,
        144
    )

    ema169_15m_series = ema_series(
        closes,
        169
    )

    if not ema144_15m_series:
        return None

    if not ema169_15m_series:
        return None


    ema144_15m = (
        ema144_15m_series[-1]
    )

    ema169_15m = (
        ema169_15m_series[-1]
    )


    upper_now = max(
        ema144_15m,
        ema169_15m
    )

    lower_now = min(
        ema144_15m,
        ema169_15m
    )


    # =====================================================
    # 第三部分：0.7%回踩区域
    #
    # 多头：
    # 上边界 → 上边界 + 0.7%
    #
    # 空头：
    # 下边界 - 0.7% → 下边界
    # =====================================================

    long_zone_low = upper_now

    long_zone_high = (
        upper_now * 1.007
    )

    short_zone_low = (
        lower_now * 0.993
    )

    short_zone_high = lower_now


    # =====================================================
    # 第四部分：
    # 用“当时的EMA144/169”检查过去8根K线
    #
    # 这样不会使用当前EMA去错误判断过去
    # =====================================================

    long_touched = False
    short_touched = False

    lookback_count = 8

    start_index = max(
        0,
        len(closes) - 1 - lookback_count
    )

    end_index = (
        len(closes) - 1
    )

    for index in range(
        start_index,
        end_index
    ):

        ema144_value = (
            ema144_15m_series[index]
        )

        ema169_value = (
            ema169_15m_series[index]
        )

        if (

            ema144_value is None

            or

            ema169_value is None

        ):

            continue


        upper = max(
            ema144_value,
            ema169_value
        )

        lower = min(
            ema144_value,
            ema169_value
        )


        # 当时的多头回踩区
        long_low = upper

        long_high = (
            upper * 1.007
        )


        # 当时的空头回踩区
        short_low = (
            lower * 0.993
        )

        short_high = lower


        candle_low = lows[index]

        candle_high = highs[index]


        # 多头：
        # K线低点进入上边界+0.7%
        if (

            candle_low >= long_low

            and

            candle_low <= long_high

        ):

            long_touched = True


        # 空头：
        # K线高点进入下边界-0.7%
        if (

            candle_high <= short_high

            and

            candle_high >= short_low

        ):

            short_touched = True


    # =====================================================
    # 第五部分：MACD
    # =====================================================

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


    # =====================================================
    # 第六部分：
    # 15M最近小级别结构
    #
    # 最新一根K线必须突破前面6根
    # =====================================================

    if len(closes) < 8:

        return None


    previous_highs = highs[-7:-1]

    previous_lows = lows[-7:-1]


    recent_high = max(
        previous_highs
    )

    recent_low = min(
        previous_lows
    )


    long_breakout = (

        current_price
        > recent_high

    )


    short_breakout = (

        current_price
        < recent_low

    )


    # =====================================================
    # 第七部分：TD9
    # =====================================================

    (
        td_status,
        td_up,
        td_down

    ) = td9_count(
        closes
    )


    # =====================================================
    # 第八部分：最终信号
    # =====================================================

    direction = None


    # 多头
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


    # 空头
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


    # =====================================================
    # 信号K线时间
    # =====================================================

    candle_time = int(
        closed_15m[-1][0]
    )


    return {

        "symbol": symbol,

        "direction": direction,

        "price": current_price,

        "ema12_1h": ema12_1h,

        "ema144_1h": ema144_1h,

        "ema169_1h": ema169_1h,

        "ema576_1h": ema576_1h,

        "ema676_1h": ema676_1h,

        "ema144_15m": ema144_15m,

        "ema169_15m": ema169_15m,

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
# 生成Telegram信号
# =========================================================

def build_signal_message(
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

            "① 1H EMA12/144/169方向向上\n"

            "② 价格高于EMA576/676\n"

            "③ 15M回踩144/169上边界+0.7%\n"

            "④ MACD动能重新增强\n"

            "⑤ 突破15M近期小高点"

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

            "① 1H EMA12/144/169方向向下\n"

            "② 价格低于EMA576/676\n"

            "③ 15M反弹至144/169下边界-0.7%\n"

            "④ MACD动能重新转弱\n"

            "⑤ 跌破15M近期小低点"

        )


    candle_time = datetime.fromtimestamp(

        signal["candle_time"] / 1000

    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


    return (

        f"📊 {symbol} · 15M\n\n"

        f"{title}\n\n"

        f"💰 价格："
        f"{format_price(price)}\n"

        f"📈 1H方向：{trend}\n\n"

        "━━ 1H EMA ━━\n"

        f"EMA12  ："
        f"{format_price(signal['ema12_1h'])}\n"

        f"EMA144 ："
        f"{format_price(signal['ema144_1h'])}\n"

        f"EMA169 ："
        f"{format_price(signal['ema169_1h'])}\n"

        f"EMA576 ："
        f"{format_price(signal['ema576_1h'])}\n"

        f"EMA676 ："
        f"{format_price(signal['ema676_1h'])}\n\n"

        "━━ 15M回踩 ━━\n"

        f"EMA144："
        f"{format_price(signal['ema144_15m'])}\n"

        f"EMA169："
        f"{format_price(signal['ema169_15m'])}\n"

        f"回踩区域：{zone}\n\n"

        "━━ 入场条件 ━━\n"

        f"{reason}\n\n"

        "━━ TD9辅助 ━━\n"

        f"{signal['td_status']}\n"

        f"上涨计数：{signal['td_up']}\n"

        f"下跌计数：{signal['td_down']}\n\n"

        f"⏰ K线：{candle_time}\n\n"

        "⚠️ 仅为交易信号，不自动下单"

    )


# =========================================================
# 发送信号
# =========================================================

async def send_signal(
    bot,
    signal
):

    message = build_signal_message(
        signal
    )

    if not CHAT_IDS:

        print(
            "没有绑定Telegram群聊，"
            "信号不会发送"
        )

        return


    for chat_id in list(
        CHAT_IDS
    ):

        try:

            await bot.send_message(

                chat_id=chat_id,

                text=message

            )

            print(
                "信号已发送:",
                signal["symbol"],
                signal["direction"],
                "→",
                chat_id
            )

        except Exception as error:

            print(
                f"发送到 {chat_id} 失败:",
                error
            )


# =========================================================
# 扫描器
# =========================================================

async def scanner(
    application
):

    print(
        "================================"
    )

    print(
        "扫描器启动"
    )

    print(
        "扫描数量:",
        len(SYMBOLS)
    )

    print(
        "================================"
    )


    while True:

        scan_start = datetime.now()


        try:

            valid_count = len(
                [
                    symbol
                    for symbol in SYMBOLS
                    if symbol in VALID_SYMBOLS
                ]
            )

            print(
                f"[{scan_start:%Y-%m-%d %H:%M:%S}] "
                f"开始扫描 "
                f"{valid_count}/{len(SYMBOLS)} 个币"
            )


            scanned = 0
            signals = 0


            for symbol in SYMBOLS:

                # Binance当前没有这个USDT永续
                if symbol not in VALID_SYMBOLS:

                    continue


                scanned += 1


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


                    # 同一信号不重复发送
                    if key in LAST_SIGNALS:

                        continue


                    LAST_SIGNALS.add(
                        key
                    )


                    # 防止内存无限增长
                    if len(LAST_SIGNALS) > 5000:

                        LAST_SIGNALS.clear()


                    signals += 1


                    print(
                        "🔥 发现信号:",
                        signal["symbol"],
                        signal["direction"]
                    )


                    await send_signal(

                        application.bot,

                        signal

                    )


                except Exception as error:

                    print(
                        f"{symbol} 分析失败:",
                        error
                    )


                # 控制请求速度
                await asyncio.sleep(
                    0.15
                )


            scan_end = datetime.now()

            elapsed = (
                scan_end
                - scan_start
            ).total_seconds()


            print(
                f"[{scan_end:%Y-%m-%d %H:%M:%S}] "
                f"扫描结束 | "
                f"扫描:{scanned} | "
                f"新信号:{signals} | "
                f"耗时:{elapsed:.1f}秒"
            )


        except Exception as error:

            print(
                "扫描器发生错误:",
                error
            )


        print(
            f"等待 {SCAN_INTERVAL} 秒后下一轮..."
        )


        await asyncio.sleep(
            SCAN_INTERVAL
        )


# =========================================================
# Telegram启动后的任务
# =========================================================

async def post_init(
    application
):

    asyncio.create_task(
        scanner(
            application
        )
    )


# =========================================================
# 主程序
# =========================================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "没有找到 BOT_TOKEN"
        )


    # 读取之前绑定的群聊
    load_chat_ids()


    # 获取Binance有效合约
    load_valid_symbols()


    print(
        "========================================"
    )

    print(
        "Vegas + MACD + TD9 Telegram Bot"
    )

    print(
        f"策略监控币种: {len(SYMBOLS)}"
    )

    print(
        "1H: EMA12 / 144 / 169 / 576 / 676"
    )

    print(
        "15M: EMA144 / EMA169 + 0.7%"
    )

    print(
        "MACD: 动能重新增强"
    )

    print(
        "TD9: 辅助"
    )

    print(
        f"扫描周期: {SCAN_INTERVAL}秒"
    )

    print(
        "========================================"
    )


    # Render健康检查
    threading.Thread(

        target=run_web_server,

        daemon=True

    ).start()


    # Telegram
    application = (

        Application
        .builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()

    )


    application.add_handler(

        CommandHandler(
            "start",
            start
        )

    )


    application.add_handler(

        CommandHandler(
            "status",
            status
        )

    )


    print(
        "Telegram Bot 正在启动..."
    )


    application.run_polling()


# =========================================================
# 程序入口
# =========================================================

if __name__ == "__main__":

    main()import os
import json
import asyncio
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

BINANCE_KLINE_API = (
    "https://fapi.binance.com/fapi/v1/klines"
)

BINANCE_EXCHANGE_INFO_API = (
    "https://fapi.binance.com/fapi/v1/exchangeInfo"
)

SCAN_INTERVAL = 180

CHAT_FILE = "chat_ids.json"


# =========================================================
# 监控币种
# 共62个
# =========================================================

SYMBOLS = [

    # =====================================================
    # 核心45
    # =====================================================

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

    # =====================================================
    # 第一批扩展10个
    # =====================================================

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

    # =====================================================
    # 第二批强势组7个
    # =====================================================

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
# 全局状态
# =========================================================

CHAT_IDS = set()

LAST_SIGNALS = set()

VALID_SYMBOLS = set()


# =========================================================
# Telegram 群聊ID
# =========================================================

def load_chat_ids():

    global CHAT_IDS

    try:

        if not os.path.exists(CHAT_FILE):
            return

        with open(
            CHAT_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if isinstance(data, list):

            CHAT_IDS = set(data)

    except Exception as error:

        print(
            "读取 chat_ids 失败:",
            error
        )


def save_chat_ids():

    try:

        with open(
            CHAT_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                list(CHAT_IDS),
                file
            )

    except Exception as error:

        print(
            "保存 chat_ids 失败:",
            error
        )


# =========================================================
# /start
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

        "方向：1H EMA12 / 144 / 169 / 576 / 676\n"

        "入场：15M 144/169回踩 + MACD动能\n"

        "辅助：TD9\n\n"

        "🟢 多头：1H大趋势向上\n"

        "🔴 空头：1H大趋势向下\n\n"

        "数据：Binance USDT永续\n"

        "机器人现在开始监控。"

    )


# =========================================================
# /status
# =========================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    valid_count = len(
        [
            symbol
            for symbol in SYMBOLS
            if symbol in VALID_SYMBOLS
        ]
    )

    await update.message.reply_text(

        "🤖 机器人状态\n\n"

        f"监控列表：{len(SYMBOLS)} 个\n"

        f"有效永续：{valid_count} 个\n"

        f"已绑定群聊：{len(CHAT_IDS)} 个\n"

        f"扫描周期：{SCAN_INTERVAL} 秒\n\n"

        "策略：\n"

        "1H EMA12/144/169/576/676\n"

        "15M EMA144/169 + 0.7%回踩\n"

        "MACD动能重新增强\n"

        "15M结构突破\n"

        "TD9辅助"

    )


# =========================================================
# Render 健康检查
# =========================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain"
        )

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

    print(
        f"Health server running on port {PORT}"
    )

    server.serve_forever()


# =========================================================
# Binance K线
# =========================================================

def get_klines(
    symbol,
    interval,
    limit
):

    params = urlencode({

        "symbol": symbol,

        "interval": interval,

        "limit": limit

    })

    url = (
        BINANCE_KLINE_API
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

        if not isinstance(data, list):

            return []

        return data

    except Exception as error:

        print(
            f"{symbol} {interval} K线获取失败:",
            error
        )

        return []


# =========================================================
# 获取Binance当前有效USDT永续
# =========================================================

def load_valid_symbols():

    global VALID_SYMBOLS

    try:

        request = Request(

            BINANCE_EXCHANGE_INFO_API,

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

            if (

                item.get("contractType")
                == "PERPETUAL"

                and

                item.get("status")
                == "TRADING"

                and

                item.get("quoteAsset")
                == "USDT"

            ):

                VALID_SYMBOLS.add(
                    symbol
                )

        print(
            "Binance有效USDT永续:",
            len(VALID_SYMBOLS)
        )

        invalid = [

            symbol
            for symbol in SYMBOLS
            if symbol not in VALID_SYMBOLS

        ]

        if invalid:

            print(
                "当前不存在或不可交易的币:",
                ", ".join(invalid)
            )

    except Exception as error:

        print(
            "获取Binance合约列表失败:",
            error
        )

        # 如果交易所信息接口暂时失败，
        # 使用原始列表继续运行
        VALID_SYMBOLS = set(
            SYMBOLS
        )


# =========================================================
# EMA
# =========================================================

def ema_series(
    values,
    period
):

    if len(values) < period:

        return []

    multiplier = (
        2.0
        /
        (period + 1.0)
    )

    result = [
        None
    ] * (
        period - 1
    )

    current = sum(
        values[:period]
    ) / period

    result.append(
        current
    )

    for price in values[period:]:

        current = (

            (
                price
                - current
            )
            * multiplier

            + current

        )

        result.append(
            current
        )

    return result


def ema_last(
    values,
    period
):

    series = ema_series(
        values,
        period
    )

    if not series:

        return None

    return series[-1]


# =========================================================
# MACD
# 标准 12 / 26 / 9
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

    macd_line = [
        None
    ] * len(closes)

    for index in range(
        len(closes)
    ):

        if (

            ema12[index] is not None

            and

            ema26[index] is not None

        ):

            macd_line[index] = (

                ema12[index]
                -
                ema26[index]

            )

    valid_macd = [

        value
        for value in macd_line
        if value is not None

    ]

    signal_values = ema_series(
        valid_macd,
        9
    )

    signal_line = [
        None
    ] * len(closes)

    valid_index = 0

    for index in range(
        len(closes)
    ):

        if macd_line[index] is None:

            continue

        if valid_index >= len(
            signal_values
        ):

            break

        signal_line[index] = (
            signal_values[valid_index]
        )

        valid_index += 1

    histogram = [
        None
    ] * len(closes)

    for index in range(
        len(closes)
    ):

        if (

            macd_line[index] is not None

            and

            signal_line[index] is not None

        ):

            histogram[index] = (

                macd_line[index]
                -
                signal_line[index]

            )

    return (
        macd_line,
        signal_line,
        histogram
    )


# =========================================================
# MACD 多头动能重新增强
#
# 前两根动能连续减弱
# 最新一根重新增强
# =========================================================

def macd_long_restrength(
    histogram
):

    values = [

        value
        for value in histogram
        if value is not None

    ]

    if len(values) < 4:

        return False

    h4 = values[-4]
    h3 = values[-3]
    h2 = values[-2]
    h1 = values[-1]

    weakening = (

        h3 < h4

        and

        h2 < h3

    )

    strengthening = (
        h1 > h2
    )

    return (
        weakening
        and
        strengthening
    )


# =========================================================
# MACD 空头动能重新增强
# =========================================================

def macd_short_restrength(
    histogram
):

    values = [

        value
        for value in histogram
        if value is not None

    ]

    if len(values) < 4:

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
#
# 这里作为辅助指标，不参与核心入场条件
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

    for index in range(
        len(closes) - 1,
        3,
        -1
    ):

        current = closes[index]

        previous = closes[
            index - 4
        ]

        if current > previous:

            up += 1
            down = 0

        elif current < previous:

            down += 1
            up = 0

        else:

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
# 价格格式
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
# 分析单个币种
# =========================================================

def analyze_symbol(
    symbol
):

    # =====================================================
    # 第一部分：1H大趋势
    # =====================================================

    data_1h = get_klines(
        symbol,
        "1h",
        800
    )

    if len(data_1h) < 700:

        return None

    # 最后一根是未收盘K线
    closed_1h = data_1h[:-1]

    closes_1h = [

        float(candle[4])
        for candle in closed_1h

    ]

    current_1h_price = closes_1h[-1]

    ema12_1h = ema_last(
        closes_1h,
        12
    )

    ema144_1h = ema_last(
        closes_1h,
        144
    )

    ema169_1h = ema_last(
        closes_1h,
        169
    )

    ema576_1h = ema_last(
        closes_1h,
        576
    )

    ema676_1h = ema_last(
        closes_1h,
        676
    )

    if any(
        value is None
        for value in [
            ema12_1h,
            ema144_1h,
            ema169_1h,
            ema576_1h,
            ema676_1h
        ]
    ):

        return None


    # =====================================================
    # 1H 多头趋势
    #
    # EMA12 > 144/169
    # 价格 > 144/169
    # 价格 > 576/676
    # =====================================================

    long_trend = (

        ema12_1h > ema144_1h

        and

        ema12_1h > ema169_1h

        and

        current_1h_price > ema144_1h

        and

        current_1h_price > ema169_1h

        and

        current_1h_price > ema576_1h

        and

        current_1h_price > ema676_1h

    )


    # =====================================================
    # 1H 空头趋势
    # =====================================================

    short_trend = (

        ema12_1h < ema144_1h

        and

        ema12_1h < ema169_1h

        and

        current_1h_price < ema144_1h

        and

        current_1h_price < ema169_1h

        and

        current_1h_price < ema576_1h

        and

        current_1h_price < ema676_1h

    )


    if not long_trend and not short_trend:

        return None


    # =====================================================
    # 第二部分：15M
    # =====================================================

    data_15m = get_klines(
        symbol,
        "15m",
        250
    )

    if len(data_15m) < 180:

        return None

    # 去掉未收盘K线
    closed_15m = data_15m[:-1]

    highs = [

        float(candle[2])
        for candle in closed_15m

    ]

    lows = [

        float(candle[3])
        for candle in closed_15m

    ]

    closes = [

        float(candle[4])
        for candle in closed_15m

    ]

    if len(closes) < 180:

        return None

    current_price = closes[-1]


    # =====================================================
    # 15M EMA144 / EMA169
    # =====================================================

    ema144_15m_series = ema_series(
        closes,
        144
    )

    ema169_15m_series = ema_series(
        closes,
        169
    )

    if not ema144_15m_series:
        return None

    if not ema169_15m_series:
        return None


    ema144_15m = (
        ema144_15m_series[-1]
    )

    ema169_15m = (
        ema169_15m_series[-1]
    )


    upper_now = max(
        ema144_15m,
        ema169_15m
    )

    lower_now = min(
        ema144_15m,
        ema169_15m
    )


    # =====================================================
    # 第三部分：0.7%回踩区域
    #
    # 多头：
    # 上边界 → 上边界 + 0.7%
    #
    # 空头：
    # 下边界 - 0.7% → 下边界
    # =====================================================

    long_zone_low = upper_now

    long_zone_high = (
        upper_now * 1.007
    )

    short_zone_low = (
        lower_now * 0.993
    )

    short_zone_high = lower_now


    # =====================================================
    # 第四部分：
    # 用“当时的EMA144/169”检查过去8根K线
    #
    # 这样不会使用当前EMA去错误判断过去
    # =====================================================

    long_touched = False
    short_touched = False

    lookback_count = 8

    start_index = max(
        0,
        len(closes) - 1 - lookback_count
    )

    end_index = (
        len(closes) - 1
    )

    for index in range(
        start_index,
        end_index
    ):

        ema144_value = (
            ema144_15m_series[index]
        )

        ema169_value = (
            ema169_15m_series[index]
        )

        if (

            ema144_value is None

            or

            ema169_value is None

        ):

            continue


        upper = max(
            ema144_value,
            ema169_value
        )

        lower = min(
            ema144_value,
            ema169_value
        )


        # 当时的多头回踩区
        long_low = upper

        long_high = (
            upper * 1.007
        )


        # 当时的空头回踩区
        short_low = (
            lower * 0.993
        )

        short_high = lower


        candle_low = lows[index]

        candle_high = highs[index]


        # 多头：
        # K线低点进入上边界+0.7%
        if (

            candle_low >= long_low

            and

            candle_low <= long_high

        ):

            long_touched = True


        # 空头：
        # K线高点进入下边界-0.7%
        if (

            candle_high <= short_high

            and

            candle_high >= short_low

        ):

            short_touched = True


    # =====================================================
    # 第五部分：MACD
    # =====================================================

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


    # =====================================================
    # 第六部分：
    # 15M最近小级别结构
    #
    # 最新一根K线必须突破前面6根
    # =====================================================

    if len(closes) < 8:

        return None


    previous_highs = highs[-7:-1]

    previous_lows = lows[-7:-1]


    recent_high = max(
        previous_highs
    )

    recent_low = min(
        previous_lows
    )


    long_breakout = (

        current_price
        > recent_high

    )


    short_breakout = (

        current_price
        < recent_low

    )


    # =====================================================
    # 第七部分：TD9
    # =====================================================

    (
        td_status,
        td_up,
        td_down

    ) = td9_count(
        closes
    )


    # =====================================================
    # 第八部分：最终信号
    # =====================================================

    direction = None


    # 多头
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


    # 空头
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


    # =====================================================
    # 信号K线时间
    # =====================================================

    candle_time = int(
        closed_15m[-1][0]
    )


    return {

        "symbol": symbol,

        "direction": direction,

        "price": current_price,

        "ema12_1h": ema12_1h,

        "ema144_1h": ema144_1h,

        "ema169_1h": ema169_1h,

        "ema576_1h": ema576_1h,

        "ema676_1h": ema676_1h,

        "ema144_15m": ema144_15m,

        "ema169_15m": ema169_15m,

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
# 生成Telegram信号
# =========================================================

def build_signal_message(
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

            "① 1H EMA12/144/169方向向上\n"

            "② 价格高于EMA576/676\n"

            "③ 15M回踩144/169上边界+0.7%\n"

            "④ MACD动能重新增强\n"

            "⑤ 突破15M近期小高点"

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

            "① 1H EMA12/144/169方向向下\n"

            "② 价格低于EMA576/676\n"

            "③ 15M反弹至144/169下边界-0.7%\n"

            "④ MACD动能重新转弱\n"

            "⑤ 跌破15M近期小低点"

        )


    candle_time = datetime.fromtimestamp(

        signal["candle_time"] / 1000

    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


    return (

        f"📊 {symbol} · 15M\n\n"

        f"{title}\n\n"

        f"💰 价格："
        f"{format_price(price)}\n"

        f"📈 1H方向：{trend}\n\n"

        "━━ 1H EMA ━━\n"

        f"EMA12  ："
        f"{format_price(signal['ema12_1h'])}\n"

        f"EMA144 ："
        f"{format_price(signal['ema144_1h'])}\n"

        f"EMA169 ："
        f"{format_price(signal['ema169_1h'])}\n"

        f"EMA576 ："
        f"{format_price(signal['ema576_1h'])}\n"

        f"EMA676 ："
        f"{format_price(signal['ema676_1h'])}\n\n"

        "━━ 15M回踩 ━━\n"

        f"EMA144："
        f"{format_price(signal['ema144_15m'])}\n"

        f"EMA169："
        f"{format_price(signal['ema169_15m'])}\n"

        f"回踩区域：{zone}\n\n"

        "━━ 入场条件 ━━\n"

        f"{reason}\n\n"

        "━━ TD9辅助 ━━\n"

        f"{signal['td_status']}\n"

        f"上涨计数：{signal['td_up']}\n"

        f"下跌计数：{signal['td_down']}\n\n"

        f"⏰ K线：{candle_time}\n\n"

        "⚠️ 仅为交易信号，不自动下单"

    )


# =========================================================
# 发送信号
# =========================================================

async def send_signal(
    bot,
    signal
):

    message = build_signal_message(
        signal
    )

    if not CHAT_IDS:

        print(
            "没有绑定Telegram群聊，"
            "信号不会发送"
        )

        return


    for chat_id in list(
        CHAT_IDS
    ):

        try:

            await bot.send_message(

                chat_id=chat_id,

                text=message

            )

            print(
                "信号已发送:",
                signal["symbol"],
                signal["direction"],
                "→",
                chat_id
            )

        except Exception as error:

            print(
                f"发送到 {chat_id} 失败:",
                error
            )


# =========================================================
# 扫描器
# =========================================================

async def scanner(
    application
):

    print(
        "================================"
    )

    print(
        "扫描器启动"
    )

    print(
        "扫描数量:",
        len(SYMBOLS)
    )

    print(
        "================================"
    )


    while True:

        scan_start = datetime.now()


        try:

            valid_count = len(
                [
                    symbol
                    for symbol in SYMBOLS
                    if symbol in VALID_SYMBOLS
                ]
            )

            print(
                f"[{scan_start:%Y-%m-%d %H:%M:%S}] "
                f"开始扫描 "
                f"{valid_count}/{len(SYMBOLS)} 个币"
            )


            scanned = 0
            signals = 0


            for symbol in SYMBOLS:

                # Binance当前没有这个USDT永续
                if symbol not in VALID_SYMBOLS:

                    continue


                scanned += 1


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


                    # 同一信号不重复发送
                    if key in LAST_SIGNALS:

                        continue


                    LAST_SIGNALS.add(
                        key
                    )


                    # 防止内存无限增长
                    if len(LAST_SIGNALS) > 5000:

                        LAST_SIGNALS.clear()


                    signals += 1


                    print(
                        "🔥 发现信号:",
                        signal["symbol"],
                        signal["direction"]
                    )


                    await send_signal(

                        application.bot,

                        signal

                    )


                except Exception as error:

                    print(
                        f"{symbol} 分析失败:",
                        error
                    )


                # 控制请求速度
                await asyncio.sleep(
                    0.15
                )


            scan_end = datetime.now()

            elapsed = (
                scan_end
                - scan_start
            ).total_seconds()


            print(
                f"[{scan_end:%Y-%m-%d %H:%M:%S}] "
                f"扫描结束 | "
                f"扫描:{scanned} | "
                f"新信号:{signals} | "
                f"耗时:{elapsed:.1f}秒"
            )


        except Exception as error:

            print(
                "扫描器发生错误:",
                error
            )


        print(
            f"等待 {SCAN_INTERVAL} 秒后下一轮..."
        )


        await asyncio.sleep(
            SCAN_INTERVAL
        )


# =========================================================
# Telegram启动后的任务
# =========================================================

async def post_init(
    application
):

    asyncio.create_task(
        scanner(
            application
        )
    )


# =========================================================
# 主程序
# =========================================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "没有找到 BOT_TOKEN"
        )


    # 读取之前绑定的群聊
    load_chat_ids()


    # 获取Binance有效合约
    load_valid_symbols()


    print(
        "========================================"
    )

    print(
        "Vegas + MACD + TD9 Telegram Bot"
    )

    print(
        f"策略监控币种: {len(SYMBOLS)}"
    )

    print(
        "1H: EMA12 / 144 / 169 / 576 / 676"
    )

    print(
        "15M: EMA144 / EMA169 + 0.7%"
    )

    print(
        "MACD: 动能重新增强"
    )

    print(
        "TD9: 辅助"
    )

    print(
        f"扫描周期: {SCAN_INTERVAL}秒"
    )

    print(
        "========================================"
    )


    # Render健康检查
    threading.Thread(

        target=run_web_server,

        daemon=True

    ).start()


    # Telegram
    application = (

        Application
        .builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()

    )


    application.add_handler(

        CommandHandler(
            "start",
            start
        )

    )


    application.add_handler(

        CommandHandler(
            "status",
            status
        )

    )


    print(
        "Telegram Bot 正在启动..."
    )


    application.run_polling()


# =========================================================
# 程序入口
# =========================================================

if __name__ == "__main__":

    main()
