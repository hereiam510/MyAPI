import os
import asyncio
import logging
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

# Define the path for the persistent browser session
USER_DATA_DIR = "./playwright_user_data"

async def fetch_hku_token(email, password, headless=True):
    """
    Launches a Playwright browser with a persistent context to log in and capture the auth token.
    This approach mimics a real user, saving cookies and session data to avoid detection.
    """
    async with async_playwright() as p:
        # Launch a persistent browser context
        context = await p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=headless,
            slow_mo=50, # Adds a small delay to mimic human interaction
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
            # Check if we are already logged in by looking for the textarea
            is_logged_in = await page.locator('textarea').is_visible()
            if not is_logged_in:
                logger.info("Not logged in. Starting the full login process.")
                
                # Step 1: Click the initial "Sign In" button
                await page.click('button:has-text("Sign In")', timeout=20000)
                await page.wait_for_load_state('networkidle', timeout=30000)
                
                # Step 2: Handle the iframe login
                login_frame = page.frame_locator('iframe').first
                
                # Fill email
                await login_frame.locator('input[type="email"]').fill(email)
                await login_frame.locator('input[type="submit"]').click()
                
                # Fill password
                await login_frame.locator('input[type="password"]').wait_for(timeout=30000)
                await login_frame.locator('input[type="password"]').fill(password)
                await login_frame.locator('input[type="submit"], button:has-text("Sign in")').click()
                
                logger.info("Login submitted. Waiting for final redirect.")
                await page.wait_for_load_state('networkidle', timeout=60000)
            else:
                logger.info("Session is already active. Skipping login steps.")

            # Step 3: Trigger a request to capture the token
            logger.info("Sending a message to capture token.")
            await page.locator('textarea').fill('Hello')
            await page.locator('textarea').press('Enter')
            
            await asyncio.wait_for(token_captured.wait(), timeout=30)
            logger.info("✅ HKU Auth Token captured successfully!")

        except Exception as e:
            await page.screenshot(path="debug_screenshot.png")
            logger.error(f"Automated login failed. Screenshot saved. Error: {e}", exc_info=True)
            return None
        finally:
            await context.close()
            
        return token
