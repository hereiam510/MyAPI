# token_fetcher.py
import os
import asyncio
import logging
import glob
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

USER_DATA_DIR = "./playwright_user_data"
# --- MODIFIED: Use an absolute path for the trace directory for clarity ---
TRACE_DIR = os.path.abspath("./traces")
# --- END: Modification ---

def manage_trace_files():
    """Keeps only the 5 most recent trace files and deletes the rest."""
    try:
        # --- MODIFIED: More robust directory creation and logging ---
        logger.info(f"Checking for trace directory: {TRACE_DIR}")
        if not os.path.exists(TRACE_DIR):
            logger.warning(f"Trace directory does not exist. Attempting to create it at: {TRACE_DIR}")
            try:
                os.makedirs(TRACE_DIR)
                logger.info("Successfully created trace directory.")
            except OSError as e:
                logger.error(f"FATAL: Failed to create trace directory. Please check permissions. Error: {e}", exc_info=True)
                return # Exit if we can't create the directory
        # --- END: Modification ---
            
        trace_files = glob.glob(os.path.join(TRACE_DIR, "trace_*.zip"))
        trace_files.sort(key=os.path.getctime, reverse=True)
        
        if len(trace_files) > 5:
            files_to_delete = trace_files[5:]
            logger.info(f"Found {len(trace_files)} traces. Deleting {len(files_to_delete)} oldest ones.")
            for f in files_to_delete:
                try:
                    os.remove(f)
                    logger.info(f"Deleted old trace: {f}")
                except OSError as e:
                    logger.error(f"Error deleting old trace file {f}: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"An unexpected error occurred while managing trace files: {e}", exc_info=True)

async def fetch_hku_token(email, password, headless=True):
    """
    Launches a Playwright browser for login and captures a trace for debugging.
    """
    # --- ADDED: Call manage_trace_files early to ensure directory exists before starting ---
    manage_trace_files()
    # --- END: Addition ---

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

        try:
            # ... (the rest of the logic remains the same)
            if headless:
                chat_interface_locator = page.locator("#chat-textarea")
                
                logger.info("Checking for an existing valid session...")
                is_logged_in = False
                try:
                    await chat_interface_locator.wait_for(state="visible", timeout=30000)
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
                    logger.info("Password submitted.")

                    logger.info("Handling 'Stay signed in?' prompt if it appears...")
                    try:
                        stay_signed_in_button = login_page.locator(
                            '[data-testid="KmsiYes"], input[type="submit"][value="Yes"], input[type="submit"][value="是"]'
                        )
                        await stay_signed_in_button.click(timeout=10000)
                        logger.info("Handled 'Stay signed in?' prompt.")
                    except PlaywrightTimeoutError:
                        logger.info("'Stay signed in?' prompt did not appear, continuing.")

                    logger.info("Waiting for the main chat page to finish loading...")
                    await page.wait_for_load_state("networkidle", timeout=90000)
                    
                    await chat_interface_locator.wait_for(state="visible", timeout=10000)
                    logger.info("Chat interface is ready.")
                else:
                    logger.info("✅ Valid session found. Skipping login.")

                chat_input = page.locator("#chat-textarea")
                send_button = page.locator('[data-testid="send-button"]')

                await chat_input.wait_for(state="visible", timeout=10000)
                await chat_input.fill('Hello')
                
                await send_button.wait_for(state="enabled", timeout=10000)
                await send_button.click()
                logger.info("Sent a message to capture token.")
            else:
                logger.info("Browser is open. Please complete the login and MFA process manually.")
                logger.info("The script will wait until a token is captured.")

            await asyncio.wait_for(token_captured.wait(), timeout=None if not headless else 180)
            logger.info("✅ HKU Auth Token captured successfully!")

        except Exception as e:
            logger.error(f"Token acquisition failed. Check the latest trace file for details. Error: {e}", exc_info=True)
            if headless:
                await page.screenshot(path="debug_screenshot.png")
            return None
        finally:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            trace_path = os.path.join(TRACE_DIR, f"trace_{timestamp}.zip")
            
            logger.info(f"Attempting to save debugging trace to: {trace_path}")
            try:
                await context.tracing.stop(path=trace_path)
                logger.info(f"Successfully saved debugging trace.")
                # Verify file exists after saving
                if os.path.exists(trace_path):
                    logger.info(f"VERIFIED: Trace file exists at {trace_path}")
                else:
                    logger.error(f"CRITICAL ERROR: Playwright reported saving trace, but file does not exist at {trace_path}")
            except Exception as e:
                logger.error(f"FATAL: An error occurred while trying to save the trace file: {e}", exc_info=True)
            
            await context.close()
            
        return token
