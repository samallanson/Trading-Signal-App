import os
import asyncio
import anthropic
import base64
import pytz
import json
from datetime import datetime
from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler
from playwright.async_api import async_playwright
import oandapyV20
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.accounts as accounts
import telegram

load_dotenv()

ANTHROPIC_KEY    = os.getenv("ANTHROPIC_API_KEY")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID          = os.getenv("TELEGRAM_CHAT_ID")
OANDA_API_KEY    = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
OANDA_ENV        = os.getenv("OANDA_ENVIRONMENT", "practice")
TV_USERNAME      = os.getenv("TV_USERNAME")
TV_PASSWORD      = os.getenv("TV_PASSWORD")
TV_CHART_URL     = os.getenv("TV_CHART_URL")
TV_SESSION       = os.getenv("TV_SESSION")

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

INSTRUMENTS = {
    "XAUUSD": {"oanda": "XAU_USD", "min": 1,   "max": 10},
    "EURUSD": {"oanda": "EUR_USD", "min": 100,  "max": 100000},
    "GBPUSD": {"oanda": "GBP_USD", "min": 100,  "max": 100000},
    "USDJPY": {"oanda": "USD_JPY", "min": 100,  "max": 100000},
}


def save_session_from_env():
    if TV_SESSION and not os.path.exists("tv_session.json"):
        try:
            with open("tv_session.json", "w") as f:
                f.write(TV_SESSION)
            print("Session loaded from environment!")
        except Exception as e:
            print("Session load error: " + str(e))


def is_trading_session():
    aest = pytz.timezone("Australia/Sydney")
    now  = datetime.now(aest)
    hour = now.hour
    london  = 15 <= hour < 24
    newyork = 0  <= hour < 2
    if london or newyork:
        print("Active session: " + ("London" if london else "New York"))
        return True
    print("Outside trading session - skipping")
    return False


async def take_screenshot(instrument="XAUUSD"):
    print("Taking screenshot of " + instrument + "...")
    screenshot_path = "chart_" + instrument + ".png"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        if os.path.exists("tv_session.json"):
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                storage_state="tv_session.json"
            )
        else:
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080}
            )

        page = await context.new_page()

        if not os.path.exists("tv_session.json"):
            print("No session found - attempting login...")
            try:
                await page.goto("https://www.tradingview.com/", wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)

                sign_in = page.locator('button:has-text("Sign in")')
                if await sign_in.count() > 0:
                    await sign_in.first.click()
                    await page.wait_for_timeout(3000)

                email_btn = page.locator('span:has-text("Email")')
                if await email_btn.count() > 0:
                    await email_btn.first.click()
                    await page.wait_for_timeout(2000)

                await page.wait_for_selector('input[name="username"]', timeout=15000)
                await page.fill('input[name="username"]', TV_USERNAME)
                await page.fill('input[name="password"]', TV_PASSWORD)
                await page.wait_for_timeout(1000)

                submit = page.locator('button[type="submit"]')
                await submit.first.click()
                await page.wait_for_timeout(8000)

                await context.storage_state(path="tv_session.json")
                print("Login successful!")

            except Exception as e:
                print("Login error: " + str(e))
                await browser.close()
                return None

        print("Loading chart...")
        await page.goto(TV_CHART_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(10000)

        try:
            await page.evaluate("""
                var sidebar = document.querySelector('.layout__area--left');
                if (sidebar) sidebar.remove();
                var toolbar = document.querySelector('.top-toolbar');
                if (toolbar) toolbar.style.display = 'none';
            """)
            await page.wait_for_timeout(2000)
        except:
            pass

        await page.screenshot(path=screenshot_path, full_page=False)
        await browser.close()

    print("Screenshot saved!")
    return screenshot_path


def analyse_chart(screenshot_path, instrument):
    print("Sending chart to Claude for analysis...")

    with open(screenshot_path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    prompt = (
        "You are the world's best institutional forex and gold trader at a top hedge fund.\n\n"
        "You are looking at a " + instrument + " 1 hour chart.\n\n"
        "Analyse this chart using this A+ setup framework:\n\n"
        "STEP 1 - TREND BIAS:\n"
        "- Is the overall trend bullish or bearish?\n"
        "- Where is price relative to the EMAs?\n\n"
        "STEP 2 - MARKET STRUCTURE:\n"
        "- What is the current 1H structure?\n"
        "- Is price trending or consolidating?\n\n"
        "STEP 3 - KEY LEVELS:\n"
        "- Identify nearest major support and resistance\n"
        "- Has there been a liquidity grab?\n"
        "- Has price broken structure in a new direction?\n\n"
        "STEP 4 - SETUP QUALITY:\n"
        "- Is there an A+ setup?\n"
        "- Liquidity grab + reversal + break of structure + correction\n"
        "- Is there a clear entry after a correction?\n\n"
        "STEP 5 - DECISION:\n"
        "Respond with EXACTLY this format and nothing else:\n\n"
        "BIAS: [BULLISH / BEARISH / NEUTRAL]\n"
        "SETUP_QUALITY: [A+ / A / B / C / NO SETUP]\n"
        "TRADE: [YES / NO]\n"
        "DIRECTION: [LONG / SHORT / NONE]\n"
        "INSTRUMENT: [" + instrument + "]\n"
        "ENTRY: [price level]\n"
        "STOP_LOSS: [price level]\n"
        "TAKE_PROFIT_1: [price level]\n"
        "TAKE_PROFIT_2: [price level]\n"
        "CONFIDENCE: [0-100]\n"
        "REASON: [two sentences maximum]\n\n"
        "Only recommend YES if:\n"
        "- Setup is A+ or A\n"
        "- Confidence above 70\n"
        "- Risk reward minimum 2:1\n"
        "- Clear directional bias\n"
        "- Liquidity grab has occurred"
    )

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=500,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ],
            }
        ],
    )

    response = message.content[0].text
    print("Analysis received:")
    print(response)
    return response


def parse_analysis(response):
    lines = response.strip().split("\n")
    result = {}
    for line in lines:
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def get_balance():
    try:
        oanda = oandapyV20.API(access_token=OANDA_API_KEY, environment=OANDA_ENV)
        r = accounts.AccountSummary(OANDA_ACCOUNT_ID)
        oanda.request(r)
        return float(r.response["account"]["balance"])
    except Exception as e:
        print("Balance error: " + str(e))
        return None


def calculate_units(instrument, entry, stop_loss, balance):
    try:
        risk_amount   = balance * 0.02
        stop_distance = abs(float(entry) - float(stop_loss))

        if stop_distance == 0:
            return None

        inst  = INSTRUMENTS.get(instrument, {})

        if instrument == "XAUUSD":
            units = int(risk_amount / stop_distance)
        else:
            units = int((risk_amount / stop_distance) * 10000)

        min_u = inst.get("min", 1)
        max_u = inst.get("max", 10)
        units = max(min_u, min(units, max_u))

        print("Units: " + str(units) + " | Risk: AUD " + str(round(risk_amount, 2)))
        return units

    except Exception as e:
        print("Units error: " + str(e))
        return None


def place_trade(analysis):
    try:
        direction  = analysis.get("DIRECTION", "NONE").strip()
        instrument = analysis.get("INSTRUMENT", "XAUUSD").strip()
        entry      = analysis.get("ENTRY", "0").strip().replace(",", "")
        stop_loss  = analysis.get("STOP_LOSS", "0").strip().replace(",", "")
        tp1        = analysis.get("TAKE_PROFIT_1", "0").strip().replace(",", "")

        if direction == "NONE":
            return False

        inst_config  = INSTRUMENTS.get(instrument, {})
        oanda_symbol = inst_config.get("oanda", "XAU_USD")

        balance = get_balance()
        if not balance:
            return False

        units = calculate_units(instrument, entry, stop_loss, balance)
        if not units:
            return False

        if direction == "SHORT":
            units = -units

        order_data = {
            "order": {
                "type": "MARKET",
                "instrument": oanda_symbol,
                "units": str(units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
                "stopLossOnFill": {
                    "price": str(round(float(stop_loss), 5))
                },
                "takeProfitOnFill": {
                    "price": str(round(float(tp1), 5))
                }
            }
        }

        oanda = oandapyV20.API(access_token=OANDA_API_KEY, environment=OANDA_ENV)
        r = orders.OrderCreate(OANDA_ACCOUNT_ID, data=order_data)
        oanda.request(r)
        print("Trade placed: " + direction + " " + str(units) + " " + oanda_symbol)
        return True

    except Exception as e:
        print("Trade error: " + str(e))
        return False


async def send_report(analysis, trade_placed, screenshot_path):
    try:
        bot = telegram.Bot(token=TELEGRAM_TOKEN)

        direction  = analysis.get("DIRECTION", "NONE")
        quality    = analysis.get("SETUP_QUALITY", "N/A")
        confidence = analysis.get("CONFIDENCE", "0")
        reason     = analysis.get("REASON", "N/A")
        bias       = analysis.get("BIAS", "N/A")
        instrument = analysis.get("INSTRUMENT", "N/A")

        if trade_placed:
            msg  = "TRADE PLACED\n\n"
            msg += "Instrument: " + instrument + "\n"
            msg += "Direction: " + direction + "\n"
            msg += "Setup: " + quality + "\n"
            msg += "Confidence: " + confidence + "%\n"
            msg += "Bias: " + bias + "\n"
            msg += "Entry: " + analysis.get("ENTRY", "N/A") + "\n"
            msg += "Stop Loss: " + analysis.get("STOP_LOSS", "N/A") + "\n"
            msg += "TP1: " + analysis.get("TAKE_PROFIT_1", "N/A") + "\n"
            msg += "TP2: " + analysis.get("TAKE_PROFIT_2", "N/A") + "\n\n"
            msg += "Reason: " + reason + "\n\n"
            msg += "Risk: 2% of account"
        else:
            msg  = "CHART ANALYSED - NO TRADE\n\n"
            msg += "Instrument: " + instrument + "\n"
            msg += "Bias: " + bias + "\n"
            msg += "Setup: " + quality + "\n"
            msg += "Confidence: " + confidence + "%\n\n"
            msg += "Reason: " + reason

        msg += "\nTime: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        await bot.send_message(chat_id=CHAT_ID, text=msg)

        if screenshot_path and os.path.exists(screenshot_path):
            with open(screenshot_path, "rb") as photo:
                await bot.send_photo(
                    chat_id=CHAT_ID,
                    photo=photo,
                    caption="Chart at time of analysis"
                )

        print("Telegram report sent!")

    except Exception as e:
        print("Telegram error: " + str(e))


def run_analysis():
    print("\n" + "="*40)
    print("Run: " + datetime.now().strftime("%H:%M:%S"))
    print("="*40)

    save_session_from_env()

    if not is_trading_session():
        return

    for instrument in ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]:
        try:
            print("\nAnalysing " + instrument + "...")

            screenshot_path = asyncio.run(take_screenshot(instrument))

            if not screenshot_path:
                print("Screenshot failed for " + instrument)
                continue

            response = analyse_chart(screenshot_path, instrument)
            analysis = parse_analysis(response)

            quality  = analysis.get("SETUP_QUALITY", "C")
            trade_ok = analysis.get("TRADE", "NO").strip() == "YES"

            try:
                confidence = int(analysis.get("CONFIDENCE", "0").replace("%", "").strip())
            except:
                confidence = 0

            print("Setup: " + quality + " | Confidence: " + str(confidence) + "% | Trade: " + str(trade_ok))

            trade_placed = False
            if trade_ok and quality in ["A+", "A"] and confidence >= 70:
                print("High quality setup - placing trade!")
                trade_placed = place_trade(analysis)

            asyncio.run(send_report(analysis, trade_placed, screenshot_path))

        except Exception as e:
            print("Error on " + instrument + ": " + str(e))
            continue


if __name__ == "__main__":
    print("AI Trading Agent Starting...")
    print("Instruments: XAUUSD EURUSD GBPUSD USDJPY")
    print("Sessions: London + New York (AEST)")
    print("Risk: 2% per trade")
    print("="*40)

    run_analysis()

    scheduler = BlockingScheduler()
    scheduler.add_job(run_analysis, "interval", minutes=30)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Agent stopped.")