import os
import requests
import anthropic
import asyncio
import feedparser
from datetime import datetime
from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler
import telegram
import yfinance as yf
import oandapyV20
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.accounts as accounts
from oandapyV20 import API as OandaAPI

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
OANDA_ENVIRONMENT = os.getenv("OANDA_ENVIRONMENT", "practice")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

seen_articles = set()
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.forexlive.com/feed/news",
    "https://www.fxstreet.com/rss/news",
    "https://www.investing.com/rss/news_25.rss",
]


def fetch_alpaca_news():
    try:
        url = "https://data.alpaca.markets/v1beta1/news?symbols=GLD,USO,UUP&limit=20"
        headers = {
            "APCA-API-KEY-ID": ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY
        }
        response = requests.get(url, headers=headers)
        articles = []
        for item in response.json().get("news", []):
            if item.get("url") not in seen_articles:
                seen_articles.add(item.get("url"))
                articles.append({
                    "title": item.get("headline", ""),
                    "description": item.get("summary", ""),
                    "url": item.get("url", ""),
                    "source": "Alpaca"
                })
        print("Alpaca: found " + str(len(articles)) + " new articles")
        return articles
    except Exception as e:
        print("Alpaca error: " + str(e))
        return []


def fetch_rss_news():
    articles = []
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                url = entry.get("link", "")
                if url not in seen_articles:
                    seen_articles.add(url)
                    articles.append({
                        "title": entry.get("title", ""),
                        "description": entry.get("summary", ""),
                        "url": url,
                        "source": feed.feed.get("title", "RSS")
                    })
            print("RSS " + feed_url.split("/")[2] + ": found " + str(len(feed.entries[:5])) + " articles")
        except Exception as e:
            print("RSS error " + feed_url + ": " + str(e))
    return articles


def fetch_all_news():
    all_articles = []
    all_articles.extend(fetch_alpaca_news())
    all_articles.extend(fetch_rss_news())
    print("Total new articles: " + str(len(all_articles)))
    return all_articles


def get_live_prices():
    try:
        tickers = yf.download(
            ["GC=F", "CL=F", "EURUSD=X", "GBPUSD=X", "USDJPY=X"],
            period="5d",
            interval="1h",
            progress=False,
            threads=False
        )
        prices = {
            "Gold (XAUUSD)": round(tickers["Close"]["GC=F"].dropna().iloc[-1], 2),
            "Oil (WTI)": round(tickers["Close"]["CL=F"].dropna().iloc[-1], 2),
            "EUR/USD": round(tickers["Close"]["EURUSD=X"].dropna().iloc[-1], 5),
            "GBP/USD": round(tickers["Close"]["GBPUSD=X"].dropna().iloc[-1], 5),
            "USD/JPY": round(tickers["Close"]["USDJPY=X"].dropna().iloc[-1], 3),
        }
        return prices
    except Exception as e:
        print("Error fetching live prices: " + str(e))
        return {}


def get_account_balance():
    try:
        oanda = OandaAPI(access_token=OANDA_API_KEY, environment=OANDA_ENVIRONMENT)
        r = accounts.AccountSummary(OANDA_ACCOUNT_ID)
        oanda.request(r)
        balance = float(r.response["account"]["balance"])
        print("  Account balance: AUD " + str(balance))
        return balance
    except Exception as e:
        print("  Balance error: " + str(e))
        return None


def calculate_position_size(balance, stop_loss_str, entry_str, instrument):
    try:
        risk_amount = balance * 0.02
        entry = float(entry_str.split("-")[0].strip().replace("$", "").replace(",", ""))
        stop = float(stop_loss_str.strip().replace("$", "").replace(",", ""))
        stop_distance = abs(entry - stop)
        if stop_distance == 0:
            print("  Stop distance is zero, using default size")
            return "1"
        if instrument in ["XAU_USD"]:
            pip_value = 1.0
            units = round((risk_amount / stop_distance) * pip_value, 0)
            units = max(1, min(int(units), 10))
        elif instrument in ["BCO_USD"]:
            pip_value = 1.0
            units = round((risk_amount / stop_distance) * pip_value, 0)
            units = max(1, min(int(units), 50))
        else:
            pip_value = 0.0001
            units = round(risk_amount / (stop_distance / pip_value) * 10000, 0)
            units = max(100, min(int(units), 100000))
        print("  Risk amount: AUD " + str(round(risk_amount, 2)))
        print("  Stop distance: " + str(round(stop_distance, 5)))
        print("  Position size: " + str(units) + " units")
        return str(units)
    except Exception as e:
        print("  Position size error: " + str(e))
        return "1"


def place_oanda_trade(signal):
    try:
        oanda = OandaAPI(access_token=OANDA_API_KEY, environment=OANDA_ENVIRONMENT)
        direction = signal.get("DIRECTION", "NONE").strip()
        assets = signal.get("ASSETS", "").strip()
        entry = signal.get("ENTRY_ZONE", "0").strip()
        stop = signal.get("STOP", "0").strip()
        if "XAU" in assets or "gold" in assets.lower():
            instrument = "XAU_USD"
        elif "WTI" in assets or "oil" in assets.lower():
            instrument = "BCO_USD"
        elif "EUR" in assets:
            instrument = "EUR_USD"
        elif "GBP" in assets:
            instrument = "GBP_USD"
        elif "JPY" in assets:
            instrument = "USD_JPY"
        else:
            print("  No matching instrument for: " + assets)
            return False
        balance = get_account_balance()
        if balance is None:
            units = "1"
        else:
            units = calculate_position_size(balance, stop, entry, instrument)
        if direction == "SHORT":
            units = "-" + units
        order_data = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": units,
                "timeInForce": "FOK",
                "positionFill": "DEFAULT"
            }
        }
        r = orders.OrderCreate(OANDA_ACCOUNT_ID, data=order_data)
        oanda.request(r)
        print("  Trade placed: " + direction + " " + units + " units of " + instrument)
        return True
    except Exception as e:
        print("  Oanda error: " + str(e))
        return False


def classify_article(article):
    title = article.get("title", "")
    content = article.get("description", "")
    source = article.get("source", "")
    prices = get_live_prices()
    price_text = "\n".join(["- " + k + ": " + str(v) for k, v in prices.items()])
    prompt = (
        "You are a professional forex and commodities trading analyst.\n\n"
        "Analyse this news article and respond ONLY in this exact format:\n\n"
        "SENTIMENT: [BULLISH / BEARISH / NEUTRAL]\n"
        "ASSETS: [affected assets e.g. XAUUSD, EURUSD, WTI Crude, USD]\n"
        "URGENCY: [HIGH / MEDIUM / LOW]\n"
        "DIRECTION: [LONG / SHORT / NONE]\n"
        "CONFIDENCE: [0-100]\n"
        "REASON: [one sentence explaining the trade thesis]\n"
        "ENTRY_ZONE: [price range based on live prices below]\n"
        "TARGET: [estimated target or N/A]\n"
        "STOP: [suggested stop area or N/A]\n\n"
        "Current Live Market Prices:\n" + price_text + "\n\n"
        "Source: " + source + "\n"
        "News Title: " + title + "\n"
        "News Content: " + content + "\n\n"
        "Rules:\n"
        "- Only flag HIGH urgency if this news could move markets more than 0.5 percent within 4 hours\n"
        "- If URGENCY is LOW or SENTIMENT is NEUTRAL set DIRECTION to NONE\n"
        "- Use the live prices above for entry target and stop levels\n"
        "- Focus only on Forex and Commodities such as Gold Oil and USD pairs\n"
        "- IGNORE any articles about stocks crypto politics religion sports or entertainment\n"
        "- If the article is not directly about Forex or Commodities set URGENCY to LOW\n"
        "- Always provide a specific numeric STOP price based on current live prices\n"
        "- Always provide a specific numeric ENTRY_ZONE price based on current live prices\n"
    )
    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


def parse_signal(ai_response):
    lines = ai_response.strip().split("\n")
    signal = {}
    for line in lines:
        if ":" in line:
            key, _, value = line.partition(":")
            signal[key.strip()] = value.strip()
    return signal


async def send_telegram(message):
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text=message)


def format_signal(signal, article):
    msg = "🚨 TRADING SIGNAL FIRED\n\n"
    msg += "📰 News: " + article.get("title", "") + "\n"
    msg += "📡 Source: " + article.get("source", "N/A") + "\n\n"
    msg += "🏷️ Assets: " + signal.get("ASSETS", "N/A") + "\n"
    msg += "📊 Sentiment: " + signal.get("SENTIMENT", "N/A") + "\n"
    msg += "📈 Direction: " + signal.get("DIRECTION", "N/A") + "\n"
    msg += "💡 Reason: " + signal.get("REASON", "N/A") + "\n\n"
    msg += "🎯 Entry Zone: " + signal.get("ENTRY_ZONE", "N/A") + "\n"
    msg += "✅ Target: " + signal.get("TARGET", "N/A") + "\n"
    msg += "🛑 Stop: " + signal.get("STOP", "N/A") + "\n"
    msg += "💰 Confidence: " + signal.get("CONFIDENCE", "N/A") + "%\n\n"
    msg += "🕐 Time: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return msg


def run_scanner():
    print("[" + datetime.now().strftime("%H:%M:%S") + "] Scanning news...")
    try:
        articles = fetch_all_news()
    except Exception as e:
        print("News error: " + str(e))
        return
    if not articles:
        print("No new articles.")
        return
    for article in articles:
        try:
            print("Processing: " + article.get("title", "")[:60])
            ai_response = classify_article(article)
            signal = parse_signal(ai_response)
            urgency = signal.get("URGENCY", "").strip()
            direction = signal.get("DIRECTION", "").strip()
            try:
                confidence = int(signal.get("CONFIDENCE", "0").strip())
            except ValueError:
                confidence = 0
            print("  -> Urgency: " + urgency + " | Direction: " + direction + " | Confidence: " + str(confidence) + "%")
            if urgency == "HIGH" and direction != "NONE" and confidence >= 60:
                message = format_signal(signal, article)
                print("  Sending to Telegram...")
                asyncio.run(send_telegram(message))
                print("  Signal sent!")
                print("  Placing trade on Oanda...")
                trade_placed = place_oanda_trade(signal)
                if trade_placed:
                    asyncio.run(send_telegram("✅ Trade automatically placed on Oanda with 2% risk management!"))
            else:
                print("  Skipped.")
        except Exception as e:
            print("  Error: " + str(e))
            continue


if __name__ == "__main__":
    print("Trading Signal App Starting...")
    print("Monitoring: Gold, Oil, Forex")
    print("Sources: Alpaca + Reuters + FXStreet + Forexlive + Investing.com")
    print("Auto Trading: Oanda (" + OANDA_ENVIRONMENT + ")")
    print("Risk Management: 2% per trade")
    print("Scanning every 2 minutes")
    print("-" * 40)
    run_scanner()
    scheduler = BlockingScheduler()
    scheduler.add_job(run_scanner, "interval", minutes=2)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Stopped.")