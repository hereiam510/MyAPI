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
    Launches a Playwright browser for login with robust, hybrid MFA detection.
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

        try:
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

                    # --- START: New Hybrid MFA Detection ---
                    mfa_detected_by_network = asyncio.Event()

                    async def intercept_mfa_poll(request):
                        # This is the reliable, language-independent check from your trace file
                        if "/SAS/EndAuth" in request.url and "authMethodId" in request.url:
                            logger.info(f"MFA network poll detected: {request.url}")
                            mfa_detected_by_network.set()

                    login_page.on("request", intercept_mfa_poll)
                    # --- END: New Hybrid MFA Detection ---

                    await login_page.wait_for_load_state('networkidle', timeout=60000)
                    logger.info("Pop-up window detected. Proceeding with Microsoft login.")

                    await login_page.locator('input[type="email"]').fill(email)
                    await login_page.locator('input[type="submit"]').click()
                    logger.info("Email submitted. Waiting for HKU password page.")

                    await login_page.locator("#passwordInput").fill(password)
                    await login_page.locator("#submitButton").click()
                    logger.info("Password submitted. Checking for MFA or successful login.")
                    
                    # Define locators for both possible UI outcomes
                    # This locator is more generic and looks for the displayed number code.
                    mfa_ui_locator = login_page.locator("div[role='heading'][aria-level='1']")
                    login_success_locator = page.locator("#chat-textarea")
                    
                    # Race the network detection against the two UI outcomes
                    finished_network_mfa_check = asyncio.create_task(mfa_detected_by_network.wait())
                    finished_ui_mfa_check = asyncio.create_task(mfa_ui_locator.wait_for(state="visible", timeout=60000))
                    finished_login_check = asyncio.create_task(login_success_locator.wait_for(state="visible", timeout=60000))

                    done, pending = await asyncio.wait(
                        [finished_network_mfa_check, finished_ui_mfa_check, finished_login_check], 
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    
                    for task in pending:
                        task.cancel()

                    if finished_network_mfa_check in done or finished_ui_mfa_check in done:
                        logger.error("="*70)
                        logger.error("MFA PROMPT DETECTED. Automated login cannot proceed.")
                        logger.error("Please run `python manual_mfa_refresh.py` to log in manually.")
                        logger.error("="*70)
                        raise Exception("MFA validation is required, aborting auto-refresh.")
                    
                    elif finished_login_check in done:
                         logger.info("No MFA prompt detected. Login appears successful.")
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
                logger.info("Browser is open. Please complete the login process manually.")
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
            
            try:
                await context.tracing.stop(path=trace_path)
                logger.info(f"Debugging trace saved to '{trace_path}'.")
            except Exception as e:
                logger.error(f"An error occurred while trying to save the trace file: {e}", exc_info=True)
            
            await context.close()
            
        return token
