import os
import json
import asyncio
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# ============================================================
# 基础设置
# ============================================================

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))

# Binance USDT 永续合约
BINANCE_API = "https://fapi.binance.com/fapi/v1/klines"

# 固定监控币种
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
]

# 记录已经收到 /start 的聊天
CHAT_FILE = "chat_ids.json"

# 防止同一个信号重复发送
last_signals = {}

# 每个币最后一次扫描时间
last_scan_time = {}


# ============================================================
# Render 健康检查网页
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Telegram trading bot is running!")

    def log_message(self, format, *args):
        pass


def run_web_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


threading.Thread(
    target=run_web_server,
    daemon=True
).start()


# ============================================================
# Telegram Chat ID 管理
# ============================================================

def load_chat_ids():
    try:
        with open(CHAT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_chat_ids(chat_ids):
    try:
        with open(CHAT_FILE, "w", encoding="utf-8") as f:
            json.dump(chat_ids, f)
    except Exception:
        pass


CHAT_IDS = load_chat_ids()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat is None:
        return

    chat_id = update.effective_chat.id

    if chat_id not in CHAT_IDS:
        CHAT_IDS.append(chat_id)
        save_chat_ids(CHAT_IDS)

    await update.message.reply_text(
        "机器人启动成功！🎉\n\n"
        "📊 Vegas + MACD + TD9 信号机器人\n"
        "监控币种：45个\n"
        "方向：1H EMA\n"
        "入场：15M MACD\n"
        "辅助：TD9\n\n"
        "现在已经开始监控。"
    )


# ============================================================
# Binance K线
# ============================================================

def get_klines(symbol, interval, limit=700):

    params = urlencode({
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    })

    url = BINANCE_API + "?" + params

    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    try:
        with urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))

        return data

    except Exception as e:
        print(f"{symbol} {interval} K线获取失败:", e)
        return []


# ============================================================
# EMA
# ============================================================

def ema(values, period):

    if len(values) < period:
        return []

    multiplier = 2 / (period + 1)

    result = [sum(values[:period]) / period]

    for price in values[period:]:
        result.append(
            (price - result[-1]) * multiplier + result[-1]
        )

    return result


# ============================================================
# MACD
# 标准参数：12 / 26 / 9
# ============================================================

def calculate_macd(closes):

    if len(closes) < 35:
        return None

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)

    # 对齐
    offset = 26 - 12

    macd_line = []

    for i in range(len(ema26)):
        macd_line.append(
            ema12[i + offset] - ema26[i]
        )

    if len(macd_line) < 10:
        return None

    signal_line = ema(macd_line, 9)

    if not signal_line:
        return None

    signal_offset = 9 - 1

    macd_now = macd_line[-1]
    macd_prev = macd_line[-2]

    signal_now = signal_line[-1]
    signal_prev = signal_line[-2]

    golden_cross = (
        macd_prev <= signal_prev
        and macd_now > signal_now
    )

    death_cross = (
        macd_prev >= signal_prev
        and macd_now < signal_now
    )

    return {
        "macd": macd_now,
        "signal": signal_now,
        "golden_cross": golden_cross,
        "death_cross": death_cross
    }


# ============================================================
# TD9
# 简化版 TD Sequential
#
# 收盘价 > 4根K线前收盘：上涨计数
# 收盘价 < 4根K线前收盘：下跌计数
# ============================================================

def calculate_td9(closes):

    if len(closes) < 10:
        return {
            "direction": "无",
            "count": 0
        }

    up_count = 0
    down_count = 0

    # 从最近一根往前统计
    for i in range(len(closes) - 1, 3, -1):

        current = closes[i]
        four_back = closes[i - 4]

        if current > four_back:

            if down_count > 0:
                break

            up_count += 1

            if up_count >= 9:
                break

        elif current < four_back:

            if up_count > 0:
                break

            down_count += 1

            if down_count >= 9:
                break

        else:
            break

    if up_count > 0:
        return {
            "direction": "上涨",
            "count": up_count
        }

    if down_count > 0:
        return {
            "direction": "下跌",
            "count": down_count
        }

    return {
        "direction": "无",
        "count": 0
    }


# ============================================================
# 1H Vegas 方向
#
# EMA12 > EMA144/169 + 价格在144/169上方 = 多
# EMA12 < EMA144/169 + 价格在144/169下方 = 空
# ============================================================

def calculate_1h_direction(closes):

    if len(closes) < 200:
        return None

    ema12 = ema(closes, 12)
    ema144 = ema(closes, 144)
    ema169 = ema(closes, 169)

    if not ema12 or not ema144 or not ema169:
        return None

    price = closes[-1]

    e12 = ema12[-1]

    # 因为不同周期 EMA 长度不同，需要用最后值判断
    e144 = ema144[-1]
    e169 = ema169[-1]

    if (
        e12 > e144
        and e12 > e169
        and price > e144
        and price > e169
    ):
        return "多"

    if (
        e12 < e144
        and e12 < e169
        and price < e144
        and price < e169
    ):
        return "空"

    return "震荡"


# ============================================================
# 分析一个币
# ============================================================

def analyze_symbol(symbol):

    # 1小时
    klines_1h = get_klines(
        symbol,
        "1h",
        700
    )

    if len(klines_1h) < 200:
        return None

    closes_1h = [
        float(k[4])
        for k in klines_1h
    ]

    direction = calculate_1h_direction(
        closes_1h
    )

    # 15分钟
    klines_15m = get_klines(
        symbol,
        "15m",
        200
    )

    if len(klines_15m) < 50:
        return None

    closes_15m = [
        float(k[4])
        for k in klines_15m
    ]

    macd = calculate_macd(
        closes_15m
    )

    td9 = calculate_td9(
        closes_15m
    )

    if macd is None:
        return None

    price = closes_15m[-1]

    signal = None

    # ========================================================
    # Vegas + MACD
    # ========================================================

    if direction == "多" and macd["golden_cross"]:
        signal = "LONG"

    elif direction == "空" and macd["death_cross"]:
        signal = "SHORT"

    return {
        "symbol": symbol,
        "price": price,
        "direction": direction,
        "macd": macd,
        "td9": td9,
        "signal": signal
    }


# ============================================================
# 格式化信号
# ============================================================

def format_signal(data):

    symbol = data["symbol"]
    coin = symbol.replace("USDT", "")

    price = data["price"]

    direction = data["direction"]

    macd = data["macd"]
    td9 = data["td9"]

    if data["signal"] == "LONG":

        title = "🟢 做多信号"

        reason = (
            "1H Vegas方向：多头\n"
            "15M MACD：金叉"
        )

    else:

        title = "🔴 做空信号"

        reason = (
            "1H Vegas方向：空头\n"
            "15M MACD：死叉"
        )

    if td9["direction"] == "上涨":
        td9_text = f"上涨计数 {td9['count']}"

    elif td9["direction"] == "下跌":
        td9_text = f"下跌计数 {td9['count']}"

    else:
        td9_text = "无明显计数"

    now = datetime.now(
        timezone.utc
    ).astimezone()

    message = (
        f"🚨 <b>{coin}USDT 永续</b>\n\n"
        f"<b>{title}</b>\n\n"
        f"💰 当前价格：<code>{price:g}</code>\n\n"
        f"📊 {reason}\n"
        f"TD9：{td9_text}\n\n"
        f"⏱ 方向周期：1H\n"
        f"🎯 入场周期：15M\n"
        f"📈 策略：Vegas + MACD + TD9\n\n"
        f"时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    return message


# ============================================================
# 扫描所有币
# ============================================================

async def scanner(application):

    print("交易信号扫描器启动")

    while True:

        try:

            print(
                f"\n开始扫描 {len(SYMBOLS)} 个币..."
            )

            for symbol in SYMBOLS:

                try:

                    data = await asyncio.to_thread(
                        analyze_symbol,
                        symbol
                    )

                    if data is None:
                        continue

                    signal = data["signal"]

                    if signal is None:
                        continue

                    # 当前K线时间 + 信号类型
                    klines = await asyncio.to_thread(
                        get_klines,
                        symbol,
                        "15m",
                        2
                    )

                    if len(klines) < 2:
                        continue

                    candle_time = klines[-1][0]

                    signal_key = (
                        symbol,
                        signal,
                        candle_time
                    )

                    # 已经发送过，不重复发送
                    if signal_key in last_signals:
                        continue

                    last_signals[signal_key] = True

                    message = format_signal(
                        data
                    )

                    # 发到所有已经 /start 的聊天
                    for chat_id in CHAT_IDS.copy():

                        try:

                            await application.bot.send_message(
                                chat_id=chat_id,
                                text=message,
                                parse_mode="HTML"
                            )

                        except Exception as e:

                            print(
                                f"发送到 {chat_id} 失败:",
                                e
                            )

                    print(
                        f"发现信号：{symbol} {signal}"
                    )

                except Exception as e:

                    print(
                        f"{symbol} 分析失败:",
                        e
                    )

            print("本轮扫描完成")

        except Exception as e:

            print(
                "扫描器错误:",
                e
            )

        # 每3分钟扫描一次
        await asyncio.sleep(180)


# ============================================================
# Application 启动
# ============================================================

async def post_init(application):

    application.create_task(
        scanner(application)
    )


# ============================================================
# 启动 Telegram
# ============================================================

if not TOKEN:

    raise RuntimeError(
        "没有找到 BOT_TOKEN 环境变量"
    )


app = (
    Application.builder()
    .token(TOKEN)
    .post_init(post_init)
    .build()
)

app.add_handler(
    CommandHandler(
        "start",
        start
    )
)


print("Telegram Trading Bot 启动中...")

app.run_polling()
