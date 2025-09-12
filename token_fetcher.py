This is fantastic news\! We've successfully navigated the entire complex login flow. The trace shows we have reached the final chat interface.

The script is failing on the very last step due to a simple but important detail: the `id` of the chat box is not `#chat-textarea`.

By inspecting the final page in your `trace.trace` file, I can see that the correct element is a `<textarea>` with a `placeholder` attribute. We just need to update the final locator in the script to match what's actually on the page.

This is the final fix to get the script working perfectly.

-----

### Final `token_fetcher.py` with Corrected Selector

Here is the updated script with the corrected locator for the chat text area.

```python
# token_fetcher.py
import os
import asyncio
import logging
import glob
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

USER_DATA_DIR = "./playwright_user_data"
TRACE_DIR = os.path.abspath("./traces")

def manage_trace_files():
    """Keeps only the 5 most recent trace files and deletes the rest."""
    try:
        if not os.path.exists(TRACE_DIR):
            os.makedirs(TRACE_DIR)

        trace_files = glob.glob(os.path.join(TRACE_DIR, "trace_*.zip"))
        trace_files.sort(key=os.path.getctime, reverse=True)

        if len(trace_files) > 5:
            files_to_delete = trace_files[5:]
            for f in files_to_delete:
                os.remove(f)

    except Exception as e:
        logger.error(f"Error managing trace files: {e}", exc_info=True)

async def fetch_hku_token(email, password, headless=True):
    """
    Launches a Playwright browser for login, handling multiple login steps.
    """
    manage_trace_files()

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=headless,
            slow_mo=50 if headless else None,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )

        await context.tracing.start(screenshots=True, snapshots=True, sources=True)

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
        
        # --- START: FINAL FIX ---
        # Use a more reliable placeholder-based locator for the chat interface elements
        chat_input_locator = page.locator('textarea[placeholder*="Type your query here"]')
        send_button_locator = page.locator('[data-testid="send-button"], button:has-text("Send")')
        # --- END: FINAL FIX ---
        
        try:
            if headless:
                is_logged_in = False
                try:
                    await chat_input_locator.wait_for(state="visible", timeout=30000)
                    is_logged_in = True
                except PlaywrightTimeoutError:
                    is_logged_in = False

                if not is_logged_in:
                    logger.warning("No active session found. Attempting a full login.")
                    async with page.expect_popup() as popup_info:
                        await page.click('button:has-text("Sign In")', timeout=20000)
                    login_page = await popup_info.value
                    await login_page.wait_for_load_state('networkidle', timeout=60000)

                    try:
                        account_picker_locator = login_page.locator(f'div[data-test-id="{email}"]')
                        await account_picker_locator.wait_for(state="visible", timeout=10000)
                        await account_picker_locator.click()
                    except PlaywrightTimeoutError:
                        await login_page.locator('input[type="email"]').fill(email)
                        await login_page.locator('input[type="submit"]').click()

                    await login_page.locator("#passwordInput, input[name='Password']").fill(password)
                    await login_page.locator("#submitButton, input[type='submit']").click()
                    logger.info("Password submitted. Determining next step...")

                    mfa_locator = login_page.locator('text="Approve sign in request"')
                    kmsi_locator = login_page.locator('text="Stay signed in?"')
                    
                    mfa_task = asyncio.create_task(mfa_locator.wait_for(state="visible", timeout=60000))
                    kmsi_task = asyncio.create_task(kmsi_locator.wait_for(state="visible", timeout=60000))
                    success_task = asyncio.create_task(chat_input_locator.wait_for(state="visible", timeout=60000))

                    done, pending = await asyncio.wait([mfa_task, kmsi_task, success_task], return_when=asyncio.FIRST_COMPLETED)
                    
                    for task in pending:
                        task.cancel()

                    if mfa_task in done:
                        logger.error("MFA PROMPT DETECTED. Automated login cannot proceed.")
                        raise Exception("MFA validation is required.")
                    
                    elif kmsi_task in done:
                        logger.info("'Stay signed in?' prompt detected. Clicking Yes.")
                        yes_button_locator = login_page.locator('[data-testid="KmsiYes"], input[type="submit"][value="Yes"]')
                        await yes_button_locator.click()
                        
                        logger.info("Waiting for main page to redirect to the chat interface...")
                        await page.wait_for_url("**/home", timeout=60000)
                        
                        await chat_input_locator.wait_for(state="visible", timeout=10000)
                        logger.info("Login successful after handling 'Stay signed in?' prompt.")

                    elif success_task in done:
                        logger.info("Direct login successful. Chat interface is visible.")
                else:
                    logger.info("✅ Valid session found. Skipping login.")

                await chat_input_locator.fill('Hello')
                await send_button_locator.click()
                logger.info("Sent a message to capture token.")
            else:
                logger.info("Browser is open. Please complete the login process manually.")

            await asyncio.wait_for(token_captured.wait(), timeout=None if not headless else 180)
            logger.info("✅ HKU Auth Token captured successfully!")

        except Exception as e:
            logger.error(f"Token acquisition failed. Check trace file. Error: {e}", exc_info=True)
            if headless:
                await page.screenshot(path="debug_screenshot.png")
            return None
        finally:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            trace_path = os.path.join(TRACE_DIR, f"trace_{timestamp}.zip")
            try:
                await context.tracing.stop(path=trace_path)
                logger.info(f"Debugging trace saved to '{trace_path}'.")
            except Exception as e:
                logger.error(f"Error saving trace file: {e}", exc_info=True)
            await context.close()
            
        return token
```
