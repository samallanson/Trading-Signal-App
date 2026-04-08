import asyncio
from playwright.async_api import async_playwright

async def manual_login():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        print("Opening TradingView...")
        await page.goto("https://www.tradingview.com/")
        
        print("="*50)
        print("Please log in manually in the browser window")
        print("Once logged in and your chart is open")
        print("Come back here and press Enter")
        print("="*50)
        input("Press Enter once you are logged in...")

        await context.storage_state(path="tv_session.json")
        print("Session saved successfully!")
        await browser.close()

asyncio.run(manual_login())