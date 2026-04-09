import os
import asyncio
import anthropic
import base64
import pytz
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
TV_SESSION       = os.getenv("TV_SESSION")

CHART_URLS = {
    "XAUUSD": "https://www.tradingview.com/chart/tSivPh6K/",
    "EURUSD": "https://www.tradingview.com/chart/uVaeDcEL/",
    "GBPUSD": "https://www.tradingview.com/chart/imDxNYnU/",
    "USDJPY": "https://www.tradingview.com/chart/b816agG0/",
}

INSTRUMENTS = {
    "XAUUSD": {"oanda": "XAU_USD", "min": 1,   "max": 10,   "decimals": 2,  "min_stop": 3.0},
    "EURUSD": {"oanda": "EUR_USD", "min": 100,  "max": 5000, "decimals": 5,  "min_stop": 0.0010},
    "GBPUSD": {"oanda": "GBP_USD", "min": 100,  "max": 5000, "decimals": 5,  "min_stop": 0.0010},
    "USDJPY": {"oanda": "USD_JPY", "min": 100,  "max": 5000, "decimals": 3,  "min_stop": 0.100},
}

CONFIDENCE_THRESHOLD = 80
VALID_SETUPS         = ["A+"]

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


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

    if os.path.exists(screenshot_path):
        os.remove(screenshot_path)
        print("Cleared old screenshot")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )

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
            print("No session - attempting login...")
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

        chart_url = CHART_URLS.get(instrument, CHART_URLS["XAUUSD"])
        print("Loading: " + chart_url)

        await page.goto(chart_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)

        try:
            await page.wait_for_selector('.chart-container', timeout=15000)
            print("Chart loaded!")
        except:
            print("Chart timeout - continuing anyway")

        await page.wait_for_timeout(8000)

        try:
            await page.evaluate("""
                var sidebar = document.querySelector('.layout__area--left');
                if (sidebar) sidebar.remove();
                var header = document.querySelector('.header-chart-panel');
                if (header) header.style.display = 'none';
            """)
            await page.wait_for_timeout(1000)
        except:
            pass

        await page.screenshot(path=screenshot_path, full_page=False)
        await browser.close()

    print("Screenshot saved: " + screenshot_path)
    return screenshot_path


def get_current_price(instrument):
    try:
        import oandapyV20.endpoints.pricing as pricing
        oanda  = oandapyV20.API(access_token=OANDA_API_KEY, environment=OANDA_ENV)
        inst   = INSTRUMENTS.get(instrument, {})
        symbol = inst.get("oanda", "XAU_USD")
        r      = pricing.PricingInfo(OANDA_ACCOUNT_ID, params={"instruments": symbol})
        oanda.request(r)
        price  = float(r.response["prices"][0]["closeoutAsk"])
        print("Current " + instrument + " price: " + str(price))
        return price
    except Exception as e:
        print("Price fetch error: " + str(e))
        return None


def analyse_chart(screenshot_path, instrument):
    print("Sending " + instrument + " chart to Claude...")

    with open(screenshot_path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    prompt = (
        "You are the world's best institutional forex and gold trader at a top hedge fund.\n\n"
        "You are looking at a " + instrument + " 1 hour chart with the HF Edge indicator.\n\n"
        "Analyse this chart using the A+ setup framework:\n\n"
        "STEP 1 - TREND BIAS:\n"
        "- Is the overall trend clearly bullish or bearish on this timeframe?\n"
        "- Where is price relative to the EMAs (blue=21, orange=50, red=200)?\n"
        "- Are all 3 EMAs aligned in the same direction?\n\n"
        "STEP 2 - MARKET STRUCTURE:\n"
        "- Is price making clear higher highs and higher lows (bullish)?\n"
        "- Or clear lower highs and lower lows (bearish)?\n"
        "- Is price in consolidation? If so this is NOT tradeable.\n\n"
        "STEP 3 - KEY LEVELS:\n"
        "- Identify nearest major support and resistance levels\n"
        "- Are there equal highs or equal lows acting as liquidity pools?\n"
        "- Has there been a CLEAR liquidity grab (sharp spike through a level then reversal)?\n\n"
        "STEP 4 - A+ SETUP CRITERIA (ALL must be true for A+):\n"
        "1. Clear trend direction on 1H\n"
        "2. All 3 EMAs aligned with trend\n"
        "3. Clear liquidity grab has occurred\n"
        "4. Price has broken structure in new direction after grab\n"
        "5. Price has corrected back after the break\n"
        "6. Clean entry point exists right now\n"
        "7. Risk reward is at least 2.5:1\n\n"
        "If even ONE of these 7 criteria is missing it is NOT an A+ setup.\n\n"
        "STEP 5 - DECISION:\n"
        "Respond with EXACTLY this format and nothing else:\n\n"
        "BIAS: [BULLISH / BEARISH / NEUTRAL]\n"
        "SETUP_QUALITY: [A+ / A / B / C / NO SETUP]\n"
        "TRADE: [YES / NO]\n"
        "DIRECTION: [LONG / SHORT / NONE]\n"
        "INSTRUMENT: [" + instrument + "]\n"
        "STOP_LOSS: [specific price level]\n"
        "TAKE_PROFIT_1: [specific price level]\n"
        "TAKE_PROFIT_2: [specific price level]\n"
        "CONFIDENCE: [0-100]\n"
        "REASON: [two sentences maximum]\n\n"
        "STRICT RULES:\n"
        "- Only recommend TRADE: YES if setup is A+ AND confidence is 80 or above\n"
        "- If ANY of the 7 A+ criteria are missing set SETUP_QUALITY to A or lower\n"
        "- Never trade consolidation or ranging markets\n"
        "- Always provide real price levels based on what you see - never use 0 or N/A\n"
        "- For XAUUSD stop loss must be at least 3 dollars away from current price\n"
        "- For USDJPY stop loss must be at least 0.10 away from current price\n"
        "- For EURUSD and GBPUSD stop loss must be at least 0.0010 away from current price\n"
        "- Risk reward must be minimum 2.5:1 or do not trade\n"
        "- DO NOT include ENTRY in your response - the trade will execute at current market price"
    )

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=600,
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
    print("Analysis:")
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


def validate_stop_distance(instrument, current_price, stop_loss):
    try:
        inst          = INSTRUMENTS.get(instrument, {})
        min_stop      = inst.get("min_stop", 0)
        stop_distance = abs(float(current_price) - float(stop_loss))

        if stop_distance < min_stop:
            print("Stop too close: " + str(round(stop_distance, 5)) + " minimum: " + str(min_stop))
            return False

        print("Stop distance OK: " + str(round(stop_distance, 5)))
        return True

    except Exception as e:
        print("Stop validation error: " + str(e))
        return False


def calculate_units(instrument, current_price, stop_loss, balance):
    try:
        risk_amount   = balance * 0.02
        stop_distance = abs(float(current_price) - float(stop_loss))

        if stop_distance == 0:
            print("Stop distance is zero - skipping")
            return None

        inst = INSTRUMENTS.get(instrument, {})

        if instrument == "XAUUSD":
            units = int(risk_amount / stop_distance)
        elif instrument == "USDJPY":
            units = int((risk_amount / stop_distance) * 100)
        else:
            units = int((risk_amount / stop_distance) * 10000)

        min_u = inst.get("min", 1)
        max_u = inst.get("max", 5000)
        units = max(min_u, min(units, max_u))

        print("Units: " + str(units) + " | Risk: AUD " + str(round(risk_amount, 2)) + " | Stop: " + str(round(stop_distance, 5)))
        return units

    except Exception as e:
        print("Units error: " + str(e))
        return None


def format_price(price, instrument):
    inst     = INSTRUMENTS.get(instrument, {})
    decimals = inst.get("decimals", 5)
    try:
        return str(round(float(price), decimals))
    except:
        return price


def place_trade(analysis):
    try:
        direction  = analysis.get("DIRECTION", "NONE").strip()
        instrument = analysis.get("INSTRUMENT", "XAUUSD").strip()
        stop_loss  = analysis.get("STOP_LOSS", "0").strip().replace(",", "")
        tp1        = analysis.get("TAKE_PROFIT_1", "0").strip().replace(",", "")

        if direction == "NONE":
            print("Direction is NONE - no trade")
            return False

        if stop_loss in ["0", "N/A", ""] or tp1 in ["0", "N/A", ""]:
            print("Invalid price levels - no trade")
            return False

        current_price = get_current_price(instrument)
        if not current_price:
            print("Could not get current price")
            return False

        if not validate_stop_distance(instrument, current_price, stop_loss):
            print("Stop distance too small - no trade")
            return False

        inst_config  = INSTRUMENTS.get(instrument, {})
        oanda_symbol = inst_config.get("oanda", "XAU_USD")

        balance = get_balance()
        if not balance:
            print("Could not get balance")
            return False

        units = calculate_units(instrument, current_price, stop_loss, balance)
        if not units:
            print("Could not calculate units")
            return False

        if direction == "SHORT":
            units = -units

        sl_formatted = format_price(stop_loss, instrument)
        tp_formatted = format_price(tp1, instrument)

        print("Placing market order:")
        print("  Symbol:        " + oanda_symbol)
        print("  Units:         " + str(units))
        print("  Current price: " + str(current_price))
        print("  SL:            " + sl_formatted)
        print("  TP:            " + tp_formatted)

        order_data = {
            "order": {
                "type": "MARKET",
                "instrument": oanda_symbol,
                "units": str(units),
                "timeInForce": "GTC",
                "positionFill": "DEFAULT",
                "stopLossOnFill": {
                    "price": sl_formatted,
                    "timeInForce": "GTC"
                },
                "takeProfitOnFill": {
                    "price": tp_formatted,
                    "timeInForce": "GTC"
                }
            }
        }

        oanda = oandapyV20.API(access_token=OANDA_API_KEY, environment=OANDA_ENV)
        r = orders.OrderCreate(OANDA_ACCOUNT_ID, data=order_data)
        oanda.request(r)
        print("Trade placed successfully at market price!")
        return True

    except Exception as e:
        print("Trade error: " + str(e))
        return False


async def send_report(analysis, trade_placed, screenshot_path, current_price=None):
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
            msg += "Entry: Market price " + (str(current_price) if current_price else "N/A") + "\n"
            msg += "Setup: " + quality + "\n"
            msg += "Confidence: " + confidence + "%\n"
            msg += "Bias: " + bias + "\n"
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
                    caption=instrument + " chart at time of analysis"
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

            trade_placed  = False
            current_price = None

            if trade_ok and quality in VALID_SETUPS and confidence >= CONFIDENCE_THRESHOLD:
                print("A+ setup confirmed - placing trade!")
                current_price = get_current_price(instrument)
                trade_placed  = place_trade(analysis)
            else:
                print("Skipped - requires A+ setup with 80%+ confidence")

            asyncio.run(send_report(analysis, trade_placed, screenshot_path, current_price))

        except Exception as e:
            print("Error on " + instrument + ": " + str(e))
            continue


if __name__ == "__main__":
    print("AI Trading Agent Starting...")
    print("Instruments: XAUUSD EURUSD GBPUSD USDJPY")
    print("Sessions: London + New York (AEST)")
    print("Risk: 2% per trade")
    print("Min confidence: 80%")
    print("Setup required: A+ only")
    print("Execution: Market price (instant fill)")
    print("Scanning every 30 minutes")
    print("="*40)

    run_analysis()

    scheduler = BlockingScheduler()
    scheduler.add_job(run_analysis, "interval", minutes=30)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Agent stopped.")