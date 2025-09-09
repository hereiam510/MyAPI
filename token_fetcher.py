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
                logger.info("Checking for an existing valid session...")
                chat_interface_locator = page.locator('div:has-text("Start chatting")')
                is_logged_in = False
                try:
                    await chat_interface_locator.wait_for(timeout=30000)
                    is_logged_in = True
                except PlaywrightTimeoutError:
                    is_logged_in = False

                if not is_logged_in:
                    logger.warning("No active session found. Attempting a full login.")
                    
                    logger.info("Waiting for login pop-up window...")
                    async with page.expect_popup() as popup_info:
                        await page.click('button:has-text("Sign In")', timeout=20000)
                    
                    login_page = await popup_info.value
                    await login_page.wait_for_load_state('networkidle', timeout=60000)
                    logger.info("Pop-up window detected. Proceeding with Microsoft login.")

                    await login_page.locator('input[type="email"]').fill(email)
                    await login_page.locator('input[type="submit"]').click()
                    logger.info("Email submitted. Waiting for HKU password page.")

                    await login_page.locator("#passwordInput").fill(password)
                    await login_page.locator("#submitButton").click()
                    logger.info("Password submitted. Checking for MFA prompt...")

                    # --- FINAL FIX: Language-Independent MFA Detection ---
                    # Look for a hidden input field that only exists on the MFA page.
                    mfa_prompt_locator = login_page.locator('input[name="authMethodId"]')
                    try:
                        await mfa_prompt_locator.wait_for(state="attached", timeout=10000)
                        logger.error("="*70)
                        logger.error("MFA PROMPT DETECTED. Automated login cannot proceed.")
                        logger.error("Please run `python manual_mfa_refresh.py` to log in manually.")
                        logger.error("="*70)
                        raise Exception("MFA validation is required, aborting auto-refresh.")
                    except PlaywrightTimeoutError:
                        logger.info("No MFA prompt detected. Waiting for chat interface to load...")
                        await chat_interface_locator.wait_for(state="visible", timeout=90000)
                        logger.info("Login complete and chat interface is ready.")
                    # --- END FINAL FIX ---

                else:
                    logger.info("✅ Valid session found. Skipping login.")

                chat_input = page.locator('textarea[placeholder^="Join your query"]')
                send_button = page.locator('button:has-text("Send a message")')

                logger.info("Sending a message to capture token.")
                await chat_input.fill('Hello')
                await send_button.click()

            else:
                logger.info("Browser is open. Please complete the login and MFA process manually.")
                logger.info("The script will wait until a token is captured.")

            await asyncio.wait_for(token_captured.wait(), timeout=None if not headless else 180)
            logger.info("✅ HKU Auth Token captured successfully!")

        except Exception as e:
            if headless:
                await page.screenshot(path="debug_screenshot.png")
            if "MFA validation is required" in str(e):
                 logger.error(f"Token acquisition failed: {e}")
            else:
                 logger.error(f"Token acquisition failed. Error: {e}", exc_info=True)
            return None
        finally:
            await context.close()
            
        return token
