# token_fetcher.py
import os
import asyncio
import logging
import glob
import smtplib
import pytz
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

USER_DATA_DIR = "./playwright_user_data"
TRACE_DIR = os.path.abspath("./traces")

# Define a custom exception for MFA timeouts
class MfaTimeoutError(Exception):
    pass

def send_mfa_number_alert(number: str):
    """Sends an email with the MFA number and deadlines to the user."""
    load_dotenv()
    to_email = os.getenv("ALERT_EMAIL_TO")
    from_email = os.getenv("ALERT_EMAIL_FROM")
    password = os.getenv("ALERT_EMAIL_PASSWORD")
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    time_zone_str = os.getenv("TIME_ZONE", "Asia/Hong_Kong")

    if not all([to_email, from_email, password]):
        logger.warning("Email alert settings not fully configured. Cannot send MFA number.")
        return False
    
    try:
        tz = pytz.timezone(time_zone_str)
        trigger_time = datetime.now(tz)
    except pytz.UnknownTimeZoneError:
        logger.error(f"Invalid TIME_ZONE '{time_zone_str}' in .env file. Falling back to UTC.")
        trigger_time = datetime.utcnow()

    deadline = trigger_time + timedelta(seconds=285)
    trigger_time_str = trigger_time.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    deadline_str = deadline.strftime("%Y-%m-%d %I:%M:%S %p %Z")

    subject = f"[HKU ChatGPT Proxy] ACTION REQUIRED: Enter MFA Code {number}"
    body = (
        f"The automated login requires your approval.\n\n"
        f"Please open your Outlook/Authenticator app and enter this number:\n\n"
        f"==================\n"
        f"         {number}\n"
        f"==================\n\n"
        f"This prompt was triggered at: {trigger_time_str}\n"
        f"Please enter the code before: {deadline_str}\n\n"
        "The script will wait for up to 4 minutes and 45 seconds for you to complete this step.\n"
    )
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = to_email

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(from_email, password)
        server.sendmail(from_email, to_email, msg.as_string())
        server.quit()
        logger.info(f"MFA number email sent to {to_email}. Waiting for approval...")
        return True
    except Exception as e:
        logger.error(f"Failed to send MFA number email: {e}", exc_info=True)
        return False

def manage_trace_files():
    try:
        if not os.path.exists(TRACE_DIR):
            os.makedirs(TRACE_DIR)
        trace_files = glob.glob(os.path.join(TRACE_DIR, "trace_*.zip"))
        trace_files.sort(key=os.path.getctime, reverse=True)
        if len(trace_files) > 5:
            for f in trace_files[5:]:
                os.remove(f)
    except Exception as e:
        logger.error(f"Error managing trace files: {e}", exc_info=True)

async def fetch_hku_token(email, password, headless=True):
    manage_trace_files()
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR, headless=headless, slow_mo=50 if headless else None
        )
        await context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://chatgpt.hku.hk/", wait_until="networkidle")

        token = None
        token_captured = asyncio.Event()

        async def intercept_request(request):
            nonlocal token
            if "completions" in request.url and "authorization" in request.headers:
                token = request.headers["authorization"].split(" ")[1]
                token_captured.set()
        
        page.on("request", intercept_request)
        
        chat_input_locator = page.locator('textarea[placeholder*="Type your query here"]')
        send_button_locator = page.locator('[data-testid="send-button"], button:has-text("Send")')

        try:
            if headless:
                is_logged_in = False
                try:
                    await chat_input_locator.wait_for(state="visible", timeout=15000)
                    is_logged_in = True
                except PlaywrightTimeoutError:
                    is_logged_in = False

                if not is_logged_in:
                    logger.warning("No active session found. Attempting a full login.")
                    async with page.expect_popup() as popup_info:
                        await page.click('button:has-text("Sign In")', timeout=20000)
                    login_page = await popup_info.value
                    await login_page.wait_for_load_state('networkidle', timeout=60000)

                    hku_pin_page_locator = login_page.locator("input[name='PIN']")
                    ms_email_page_locator = login_page.locator("input[type='email']")
                    
                    try:
                        hku_task = asyncio.create_task(hku_pin_page_locator.wait_for(state="visible", timeout=15000))
                        ms_task = asyncio.create_task(ms_email_page_locator.wait_for(state="visible", timeout=15000))
                        
                        done, pending = await asyncio.wait([hku_task, ms_task], return_when=asyncio.FIRST_COMPLETED)
                        for task in pending: task.cancel()

                        if hku_task in done:
                            await login_page.locator("input[type='email']").fill(email)
                        elif ms_task in done:
                            await ms_email_page_locator.fill(email)
                            await login_page.locator('input[type="submit"]').click()
                    except PlaywrightTimeoutError:
                        logger.error("Could not find the email/username input field on the login page.")
                        raise

                    await login_page.locator("#passwordInput, input[name='PIN']").fill(password)
                    await login_page.locator("#submitButton, input[type='submit']").click()
                    logger.info("Password submitted. Determining next step...")

                    # --- MODIFIED LOGIC: Handle multiple possible MFA screens ---
                    # Define locators for all possible outcomes after password submission
                    mfa_selection_locator = login_page.locator('div.tile[data-value="CompanionAppsNotification"]')
                    mfa_number_locator = login_page.locator("div.displaySign")
                    kmsi_locator = login_page.locator('text="Stay signed in?"')
                    
                    # Race all possible outcomes to see which screen appears first
                    mfa_selection_task = asyncio.create_task(mfa_selection_locator.wait_for(state="visible", timeout=60000))
                    mfa_number_task = asyncio.create_task(mfa_number_locator.wait_for(state="visible", timeout=60000))
                    kmsi_task = asyncio.create_task(kmsi_locator.wait_for(state="visible", timeout=60000))
                    success_task = asyncio.create_task(chat_input_locator.wait_for(state="visible", timeout=60000))

                    done, pending = await asyncio.wait(
                        [mfa_selection_task, mfa_number_task, kmsi_task, success_task],
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending: task.cancel()

                    # -- STATE 1: MFA method selection screen appears --
                    if mfa_selection_task in done:
                        logger.info("MFA method selection screen detected. Automatically choosing 'Approve a request...'.")
                        await mfa_selection_locator.click()
                        # After clicking, we now expect the number matching screen to appear.
                        logger.info("Waiting for the number matching screen...")
                        await mfa_number_locator.wait_for(state="visible", timeout=60000)
                        # The logic will now naturally fall through to the next block.

                    # -- STATE 2: Number matching screen appears (either directly or after selection) --
                    if mfa_number_locator.is_visible():
                        mfa_number = await mfa_number_locator.inner_text()
                        logger.warning(f"MFA PROMPT (Number Match) DETECTED with number: {mfa_number}.")
                        if not send_mfa_number_alert(mfa_number):
                            raise Exception("MFA number prompt detected, but email alert failed.")
                        
                        try:
                            logger.info("Waiting for user to approve MFA (up to 4m 45s)...")
                            # After user approves, wait for either KMSI or final success
                            post_mfa_kmsi_task = asyncio.create_task(kmsi_locator.wait_for(state="visible", timeout=285000))
                            post_mfa_success_task = asyncio.create_task(chat_input_locator.wait_for(state="visible", timeout=285000))
                            
                            done_after_mfa, pending_after_mfa = await asyncio.wait([post_mfa_kmsi_task, post_mfa_success_task], return_when=asyncio.FIRST_COMPLETED)
                            for task in pending_after_mfa: task.cancel()

                            if post_mfa_kmsi_task in done_after_mfa:
                                logger.info("MFA approved, now handling 'Stay signed in?' prompt.")
                                await login_page.locator("#KmsiCheckboxField").check(timeout=5000)
                                await login_page.locator('[data-testid="KmsiYes"], input[type="submit"][value="Yes"]').click()
                            
                            await chat_input_locator.wait_for(state="visible", timeout=60000)
                            logger.info("MFA flow complete. Login successful.")

                        except PlaywrightTimeoutError:
                            raise MfaTimeoutError("User did not approve MFA within the time limit.")

                    # -- STATE 3: "Stay Signed In?" screen appears directly --
                    elif kmsi_task in done:
                        logger.info("'Stay signed in?' prompt detected. Checking box and clicking Yes.")
                        await login_page.locator("#KmsiCheckboxField").check(timeout=5000)
                        await login_page.locator('[data-testid="KmsiYes"], input[type="submit"][value="Yes"]').click()
                        await chat_input_locator.wait_for(state="visible", timeout=60000)
                        logger.info("Login successful after handling prompt.")

                    # -- STATE 4: Login succeeds directly --
                    elif success_task in done:
                        logger.info("Direct login successful.")
                    
                    else:
                        raise Exception("Login flow stalled after password submission. No known next step detected.")
                else:
                    logger.info("✅ Valid session found. Skipping login.")

                await chat_input_locator.fill('Hello')
                await send_button_locator.click()
                logger.info("Sent a message to capture token.")
            else:
                logger.info("Browser is open for manual login.")

            await asyncio.wait_for(token_captured.wait(), timeout=None if not headless else 180)
            logger.info("✅ HKU Auth Token captured successfully!")

        except Exception as e:
            logger.error(f"Token acquisition failed. Error: {e}", exc_info=True)
            if headless: await page.screenshot(path="debug_screenshot.png")
            if isinstance(e, MfaTimeoutError):
                raise e
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
