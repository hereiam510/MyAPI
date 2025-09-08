# token_fetcher.py
import os
import asyncio
import logging
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

USER_DATA_DIR = "./playwright_user_data"

async def fetch_hku_token(email, password, headless=True):
    """
    Launches a Playwright browser for login.
    - In headless mode, it attempts a fully automated login.
    - In non-headless mode (for setup), it opens a responsive browser for manual login.
    """
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=headless,
            slow_mo=50 if headless else None,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        page = context.pages[0] if context.pages else await context.new_page()

        await page.goto("https://chatgpt.hku.hk/", wait_until="networkidle")

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

        try:
            if headless:
                is_logged_in = await page.locator('textarea').is_visible(timeout=10000)
                
                if not is_logged_in:
                    logger.info("Not logged in. Starting the full automated login process.")
                    
                    logger.info("Waiting for login pop-up window...")
                    async with page.expect_popup() as popup_info:
                        await page.click('button:has-text("Sign In")', timeout=20000)
                    
                    login_page = await popup_info.value
                    await login_page.wait_for_load_state('networkidle', timeout=60000)
                    logger.info("Pop-up window detected. Proceeding with Microsoft login.")

                    # Microsoft email page
                    await login_page.locator('input[type="email"]').fill(email)
                    await login_page.locator('input[type="submit"]').click()
                    logger.info("Email submitted. Waiting for HKU password page.")

                    # --- FINAL FIX ---
                    # Use specific selectors for the HKU password page based on screenshots.
                    # This is more robust than general selectors.
                    # Wait for the password input with ID 'passwordInput' and fill it.
                    await login_page.locator("#passwordInput").fill(password)
                    # Click the sign-in button with ID 'submitButton'.
                    await login_page.locator("#submitButton").click()
                    # --- END FINAL FIX ---
                    
                    logger.info("Password submitted. Waiting for login to complete.")
                    await page.wait_for_load_state('networkidle', timeout=90000) # Increased timeout for safety
                    logger.info("Login complete. Main page has loaded.")

                else:
                    logger.info("Session is already active. Skipping login steps.")

                logger.info("Sending a message to capture token.")
                await page.locator('textarea').fill('Hello')
                await page.locator('textarea').press('Enter')

            else:
                logger.info("Browser is open. Please complete the login and MFA process manually.")
                logger.info("The script will wait until a token is captured.")

            await asyncio.wait_for(token_captured.wait(), timeout=None if not headless else 180)
            logger.info("✅ HKU Auth Token captured successfully!")

        except Exception as e:
            if headless:
                await page.screenshot(path="debug_screenshot.png")
            logger.error(f"Token acquisition failed. Error: {e}", exc_info=True)
            return None
        finally:
            await context.close()
            
        return token
