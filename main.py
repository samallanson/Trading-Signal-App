import os
import requests
import anthropic
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler
import telegram
import yfinance as yf

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")

seen_articles = set()
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


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


def fetch_news():
    url = "https://newsapi.org/v2/everything?q=gold+OR+oil+OR+forex+OR+OPEC+OR+central+bank&language=en&sortBy=publishedAt&pageSize=10&apiKey=" + NEWS_API_KEY
    response = requests.get(url)
    articles = response.json().get("articles", [])
    new_articles = []
    for a in articles:
        if a["url"] not in seen_articles:
            seen_articles.add(a["url"])
            new_articles.append(a)
    return new_articles


def classify_article(article):
    title = article.get("title", "")
    content = article.get("description", "")
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
        "News Title: " + title + "\n"
        "News Content: " + content + "\n\n"
        "Rules:\n"
        "- Only flag HIGH urgency if this news could move markets more than 0.5 percent within 4 hours\n"
        "- If URGENCY is LOW or SENTIMENT is NEUTRAL set DIRECTION to NONE\n"
        "- Use the live prices above for entry target and stop levels\n"
        "- Focus only on Forex and Commodities such as Gold Oil and USD pairs\n"
        "- IGNORE any articles about stocks crypto politics religion sports or entertainment\n"
        "- If the article is not directly about Forex or Commodities set URGENCY to LOW\n"
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
    msg += "📰 News: " + article.get("title", "") + "\n\n"
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
        articles = fetch_news()
    except Exception as e:
        print("News error: " + str(e))
        return
    if not articles:
        print("No new articles.")
        return
    print("Found " + str(len(articles)) + " articles...")
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
                print("  Sent!")
            else:
                print("  Skipped.")
        except Exception as e:
            print("  Error: " + str(e))
            continue


if __name__ == "__main__":
    print("Trading Signal App Starting...")
    print("Monitoring: Gold, Oil, Forex")
    print("Scanning every 5 minutes")
    print("-" * 40)
    run_scanner()
    scheduler = BlockingScheduler()
    scheduler.add_job(run_scanner, "interval", minutes=5)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Stopped.")