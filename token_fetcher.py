import asyncio
import logging
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

async def fetch_hku_token(email, password, headless=True):
    """
    Launches a Playwright browser to log into the HKU service and capture the auth token.
    This version handles the multi-step login flow.
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

                # Step 2: Wait for the Microsoft login pop-up page to appear
                logger.info("Waiting for login pop-up...")
                # --- THIS LINE IS NOW FIXED ---
                popup_page = await context.wait_for_event('page', timeout=15000)
                await popup_page.wait_for_load_state()
                logger.info("Microsoft login pop-up detected.")

                # Step 3: Enter email in the Microsoft login form
                logger.info("Entering email address.")
                email_selector = 'input[type="email"]'
                await popup_page.wait_for_selector(email_selector, timeout=15000)
                await popup_page.fill(email_selector, email)
                await popup_page.click('input[type="submit"]') # Microsoft's "Next" button

                # Step 4: Enter password/PIN on the HKU login page
                logger.info("Entering password (PIN).")
                password_selector = 'input[type="password"]'
                # After submitting email, the pop-up navigates to the password page
                await popup_page.wait_for_selector(password_selector, timeout=15000)
                await popup_page.fill(password_selector, password)
                # The final submit button might be a button or an input
                await popup_page.click('input[type="submit"], button:has-text("Sign in"), button:has-text("登入")')

                # Step 5: Wait for the main page to load the chat interface
                logger.info("Login submitted, waiting for main page to load.")
                await page.wait_for_load_state('networkidle', timeout=45000)
                await asyncio.sleep(4)

                # Step 6: Trigger a request to capture the token
                logger.info("Page loaded, sending a message to capture token.")
                await page.fill('textarea', 'Hello')
                await page.keyboard.press('Enter')
                await asyncio.sleep(4)

            except Exception as e:
                # If anything goes wrong, save a screenshot for debugging
                await page.screenshot(path="debug_screenshot.png")
                logger.error(f"Automated login failed. Screenshot saved to debug_screenshot.png inside the container. Error: {e}", exc_info=True)
                await browser.close()
                return None
        
        try:
            # This part waits for the token to be captured by the interceptor
            await asyncio.wait_for(token_captured.wait(), timeout=180) 
            logger.info("HKU Auth Token captured successfully!")
        except asyncio.TimeoutError:
            logger.error("Timeout: No token was captured. Did you fully log in and send a message?")
        
        await browser.close()
        return token
