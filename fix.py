code = '''import os
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
    query = "gold OR oil OR forex OR interest rate OR OPEC OR central bank"
    url = (
        "https://newsapi.org/v2/everything"
        "?q=" + query +
        "&language=en"
        "&sortBy=publishedAt"
        "&pageSize=10"
        "&apiKey=" + NEWS_API_KEY
    )
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
        "Analyse this news article and respond ONLY in this exact format with no extra text:\n\n"
        "SENTIMENT: [BULLISH / BEARISH / NEUTRAL]\n"
        "ASSETS: [affected assets e.g. XAUUSD, EURUSD, WTI Crude, USD]\n"
        "URGENCY: [HIGH / MEDIUM / LOW]\n"
        "DIRECTION: [LONG / SHORT / NONE]\n"
        "CONFIDENCE: [0-100]\n"
        "REASON: [one sentence explaining the trade thesis]\n"
        "ENTRY_ZONE: [price range based on live prices below]\n"
        "TARGET: [estimated target or N/A]\n"
        "STOP: [suggested stop area or N/A]\n\n"
        "Current Live Market Prices:\n"
        + price_text + "\n\n"
        "News Title: " + title + "\n"
        "News Content: " + content + "\n\n"
        "Rules:\n"
        "- Only flag HIGH urgency if this news could move markets more than 0.5% within 4 hours\n"
        "- If URGENCY is LOW or SENTIMENT is NEUTRAL set DIRECTION to NONE\n"
        "- Use the live prices above for entry target and stop levels\n"
        "- Focus only on Forex and Commodities such as Gold Oil and USD pairs\n"
        "- IGNORE any articles about stocks crypto politics religion sports or entertainment\n"
        "- If the article is not directly about Forex or Commodities set URGENCY to LOW"
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
    await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="Markdown")


def format_signal(signal, article):
    direction = signal.get("DIRECTION", "N/A")
    sentiment = signal.get("SENTIMENT", "N/A")
    assets = signal.get("ASSETS", "N/A")
    reason = signal.get("REASON", "N/A")
    entry = signal.get("ENTRY_ZONE", "N/A")
    target = signal.get("TARGET", "N/A")
    stop = signal.get("STOP", "N/A")
    confidence = signal.get("CONFIDENCE", "N/A")
    title = article.get("title", "")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    msg = "TRADING SIGNAL FIRED\n\n"
    msg += "News: " + title + "\n\n"
    msg += "Assets: " + assets + "\n"
    msg += "Sentiment: " + sentiment + "\n"
    msg += "Direction: " + direction + "\n"
    msg += "Reason: " + reason + "\n\n"
    msg += "Entry Zone: " + entry + "\n"
    msg += "Target: " + target + "\n"
    msg += "Stop: " + stop + "\n"
    msg += "Confidence: " + confidence + "%\n\n"
    msg += "Time: " + now
    return msg


def run_scanner():
    print("[" + datetime.now().strftime("%H:%M:%S") + "] Scanning news...")

    try:
        articles = fetch_news()
    except Exception as e:
        print("Error fetching news: " + str(e))
        return

    if not articles:
        print("No new articles found.")
        return

    print("Found " + str(len(articles)) + " new articles to process...")

    for article in articles:
        try:
            title = article.get("title", "")[:60]
            print("Processing: " + title + "...")
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
                print("  Sending signal to Telegram...")
                asyncio.run(send_telegram(message))
                print("  Signal sent!")
            else:
                print("  Skipped - does not meet signal threshold")

        except Exception as e:
            print("  Error processing article: " + str(e))
            continue


if __name__ == "__main__":
    print("Trading Signal App Starting...")
    print("Monitoring: Gold, Oil, Forex")
    print("Alerts via: Telegram")
    print("Scanning every 5 minutes")
    print("-" * 40)

    run_scanner()

    scheduler = BlockingScheduler()
    scheduler.add_job(run_scanner, "interval", minutes=5)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Signal app stopped.")
'''

with open("main.py", "w", encoding="utf-8") as f:
    f.write(code)

print("main.py written successfully!")