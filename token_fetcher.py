import asyncio
import logging
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

async def fetch_hku_token(email, password, headless=True):
    """
    Launches a Playwright browser to log into the HKU service and capture the auth token.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://chatgpt.hku.hk/")

        token = None
        token_captured = asyncio.Event()

        async def intercept_request(request):
            nonlocal token
            if "completions" in request.url:
                auth_header = request.headers.get("authorization")
                if auth_header and auth_header.startswith("Bearer "):
                    token = auth_header.split(" ")[1]
                    token_captured.set()
        
        page.on("request", intercept_request)

        if headless:
            try:
                await page.wait_for_selector('input[type="email"],input[name="username"]', timeout=10000)
                await page.fill('input[type="email"],input[name="username"]', email)
                await page.click('button[type="submit"],input[type="submit"]')
                await page.wait_for_selector('input[type="password"]', timeout=10000)
                await page.fill('input[type="password"]', password)
                await page.click('button[type="submit"],input[type="submit"]')
                
                await page.wait_for_load_state('networkidle', timeout=30000)
                await asyncio.sleep(4)

                await page.fill('textarea', 'Hello')
                await page.keyboard.press('Enter')
                await asyncio.sleep(4)

            except Exception as e:
                await page.screenshot(path="debug_screenshot.png")
                logger.error(f"Automated login failed. Screenshot saved to debug_screenshot.png inside the container. Error: {e}", exc_info=True)
                await browser.close()
                return None
        
        try:
            await asyncio.wait_for(token_captured.wait(), timeout=180) 
            logger.info("HKU Auth Token captured successfully!")
        except asyncio.TimeoutError:
            logger.error("Timeout: No token was captured. Did you fully log in and send a message?")
        
        await browser.close()
        return token
