import asyncio
import logging
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

async def fetch_hku_token(email, password, headless=True):
    """
    Launches a Playwright browser to log into the HKU service and capture the auth token.
    This version handles the multi-step redirect login flow.
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
                # Step 1: Click the initial "Sign In" button on the terms page
                logger.info("Clicking the initial 'Sign In' button.")
                await page.click('button:has-text("Sign In")', timeout=10000)

                # --- MODIFIED SECTION ---
                # We no longer wait for a pop-up. We assume the current page navigates
                # to the Microsoft login page. All actions are now on the 'page' object.
                
                # Step 2: Enter email in the Microsoft login form
                logger.info("Waiting for email page to load and entering email address.")
                email_selector = 'input[type="email"]'
                # Increased timeout to allow for page redirect
                await page.wait_for_selector(email_selector, timeout=30000)
                await page.fill(email_selector, email)
                await page.click('input[type="submit"]')

                # Step 3: Enter password/PIN on the HKU login page
                logger.info("Waiting for password page to load and entering password (PIN).")
                password_selector = 'input[type="password"]'
                await page.wait_for_selector(password_selector, timeout=30000)
                await page.fill(password_selector, password)
                await page.click('input[type="submit"], button:has-text("Sign in"), button:has-text("登入")')

                # Step 4: Wait for the main page to load the chat interface
                logger.info("Login submitted, waiting for main page to load.")
                await page.wait_for_load_state('networkidle', timeout=45000)
                await asyncio.sleep(4)

                # Step 5: Trigger a request to capture the token
                logger.info("Page loaded, sending a message to capture token.")
                # It's possible the chat textarea is inside an iframe after login
                chat_frame = page.frame_locator('iframe').first
                textarea = chat_frame.locator('textarea')
                await textarea.fill('Hello')
                await textarea.press('Enter')
                await asyncio.sleep(4)

            except Exception as e:
                await page.screenshot(path="debug_screenshot.png")
                logger.error(f"Automated login failed. Screenshot saved. Error: {e}", exc_info=True)
                await browser.close()
                return None
        
        try:
            await asyncio.wait_for(token_captured.wait(), timeout=180) 
            logger.info("HKU Auth Token captured successfully!")
        except asyncio.TimeoutError:
            logger.error("Timeout: No token was captured. Did you fully log in and send a message?")
        
        await browser.close()
        return token
