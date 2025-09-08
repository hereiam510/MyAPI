import asyncio
import logging
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

async def fetch_hku_token(email, password, headless=True):
    """
    Launches a Playwright browser to log into the HKU service and capture the auth token.
    This version handles the multi-step redirect login flow and iframes.
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
                # Step 1: Click the initial "Sign In" button and wait for navigation
                logger.info("Clicking the initial 'Sign In' button and waiting for redirect.")
                await page.click('button:has-text("Sign In")', timeout=10000)
                await page.wait_for_load_state('networkidle', timeout=30000)
                
                # --- MODIFIED SECTION ---
                # The login form is inside an iframe, so we target it directly.
                logger.info("Locating login iframe.")
                login_frame = page.frame_locator('iframe').first

                # Step 2: Enter email in the Microsoft login form (inside the iframe)
                logger.info("Entering email address inside iframe.")
                email_selector = 'input[type="email"]'
                await login_frame.locator(email_selector).wait_for(timeout=30000)
                await login_frame.locator(email_selector).fill(email)
                await login_frame.locator('input[type="submit"]').click()

                # Step 3: Enter password/PIN on the HKU login page (inside the iframe)
                logger.info("Waiting for password page and entering password (PIN).")
                password_selector = 'input[type="password"]'
                await login_frame.locator(password_selector).wait_for(timeout=30000)
                await login_frame.locator(password_selector).fill(password)
                await login_frame.locator('input[type="submit"], button:has-text("Sign in"), button:has-text("登入")').click()

                # Step 4: Wait for the final redirect back to the chat interface
                logger.info("Login submitted, waiting for main chat page to load.")
                await page.wait_for_load_state('networkidle', timeout=45000)
                await asyncio.sleep(4)

                # Step 5: Trigger a request to capture the token
                logger.info("Page loaded, sending a message to capture token.")
                chat_frame = page.frame_locator('iframe').first
                textarea = chat_frame.locator('textarea')
                await textarea.wait_for(timeout=15000)
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
