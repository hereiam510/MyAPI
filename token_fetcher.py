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

# Define custom exceptions for specific failure scenarios
class MfaTimeoutError(Exception):
    """Raised when the user does not approve the MFA prompt in time."""
    pass

class MfaNotificationError(Exception):
    """Raised when the MFA alert email fails to send after multiple retries."""
    pass

async def send_mfa_number_alert_with_retries(number: str):
    """
    Attempts to send an email with the MFA number, retrying on failure with longer delays.
    Returns True on success, raises MfaNotificationError on persistent failure.
    """
    load_dotenv()
    to_email = os.getenv("ALERT_EMAIL_TO")
    from_email = os.getenv("ALERT_EMAIL_FROM")
    password = os.getenv("ALERT_EMAIL_PASSWORD")
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    time_zone_str = os.getenv("TIME_ZONE", "Asia/Hong_Kong")

    if not all([to_email, from_email, password]):
        logger.error("Email alert settings are not fully configured in .env file.")
        raise MfaNotificationError("Email alert settings are incomplete.")

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
    
    retry_delays = [90, 105] 
    max_retries = 3

    for attempt in range(max_retries):
        try:
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
            server.login(from_email, password)
            server.sendmail(from_email, to_email, msg.as_string())
            server.quit()
            logger.info(f"MFA number email sent to {to_email} (Attempt #{attempt + 1}). Waiting for approval...")
            return True 
        except Exception as e:
            logger.error(f"Failed to send MFA number email (Attempt #{attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                delay = retry_delays[attempt]
                logger.info(f"Retrying to send email in {delay} seconds...")
                await asyncio.sleep(delay)
            else:
                logger.error("All attempts to send MFA notification email have failed.")
                raise MfaNotificationError("Could not send MFA notification email after multiple retries.")
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
    login_page = None
    context = None
    async with async_playwright() as p:
        try:
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
                    auth_header = request.headers.get("authorization", "")
                    if " " in auth_header:
                        token = auth_header.split(" ")[1]
                        token_captured.set()
            
            page.on("request", intercept_request)
            
            chat_input_locator = page.locator('textarea[placeholder*="Type your query here"]')
            send_button_locator = page.locator('[data-testid="send-button"], button:has-text("Send")')

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
                    
                    account_picker_locator = login_page.locator(f'div[data-test-id="{email}"]')
                    if await account_picker_locator.is_visible(timeout=10000):
                        logger.info("Account picker screen detected. Selecting saved account.")
                        await account_picker_locator.click()

                    ms_email_page_locator = login_page.locator("input[type='email']")
                    if await ms_email_page_locator.is_visible(timeout=5000):
                        await ms_email_page_locator.fill(email)
                        await login_page.locator('input[type="submit"]').click()

                    await login_page.locator("input[name='PIN']").fill(password)
                    await login_page.locator("input[type='submit']").click()
                    logger.info("Password submitted. Determining next step...")

                    mfa_number_locator = login_page.locator("div.displaySign")
                    await mfa_number_locator.wait_for(state="visible", timeout=60000)

                    mfa_max_attempts = 3
                    login_successful = False
                    for attempt in range(mfa_max_attempts):
                        if await mfa_number_locator.is_visible():
                            mfa_number = await mfa_number_locator.inner_text()
                            logger.warning(f"MFA PROMPT (Attempt #{attempt + 1}/{mfa_max_attempts}) DETECTED with number: {mfa_number}.")
                            await send_mfa_number_alert_with_retries(mfa_number)
                            
                            logger.info("Waiting for user to approve MFA (up to 4m 45s)...")
                            
                            # Define locators for all possible outcomes
                            kmsi_locator = login_page.locator('text="Stay signed in?"')
                            denied_locator = login_page.locator('text="Request denied"')
                            error_alert_locator = login_page.locator('div[role="alert"]')

                            # Race the outcomes
                            kmsi_task = asyncio.create_task(kmsi_locator.wait_for(state="visible", timeout=285000))
                            success_task = asyncio.create_task(chat_input_locator.wait_for(state="visible", timeout=285000))
                            denied_task = asyncio.create_task(denied_locator.wait_for(state="visible", timeout=285000))
                            error_task = asyncio.create_task(error_alert_locator.wait_for(state="visible", timeout=285000))

                            done, pending = await asyncio.wait([kmsi_task, success_task, denied_task, error_task], return_when=asyncio.FIRST_COMPLETED)
                            for task in pending: task.cancel()

                            if success_task in done:
                                logger.info("MFA approved. Login successful.")
                                login_successful = True
                                break
                            
                            elif kmsi_task in done:
                                logger.info("MFA approved, now handling 'Stay signed in?' prompt.")
                                await login_page.locator('input[type="submit"][value="Yes"]').click()
                                await chat_input_locator.wait_for(state="visible", timeout=60000)
                                logger.info("MFA flow complete. Login successful.")
                                login_successful = True
                                break

                            elif denied_task in done:
                                logger.error(f"MFA request was denied by the user (Attempt #{attempt + 1}).")
                                if attempt < mfa_max_attempts - 1:
                                    logger.warning("Requesting a new MFA prompt...")
                                    await login_page.locator('a:has-text("Send another request")').click()
                                    await mfa_number_locator.wait_for(state="visible", timeout=30000)
                                    continue
                                else:
                                    raise MfaTimeoutError("MFA request denied multiple times. Aborting.")
                            
                            elif error_task in done:
                                error_text = await error_alert_locator.inner_text()
                                raise MfaTimeoutError(f"MFA failed. The portal displayed an error: '{error_text}'")
                            
                            else:
                                # This case handles the timeout if none of the locators are found
                                raise MfaTimeoutError("User did not approve MFA within the time limit.")
                        else:
                            break 
                    
                    if not login_successful:
                        raise MfaTimeoutError("Could not complete the MFA login flow after all attempts.")
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
            if headless and context:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                trace_path = os.path.join(TRACE_DIR, f"trace_error_{timestamp}.zip")
                try:
                    await context.tracing.stop(path=trace_path)
                    logger.info(f"Debugging trace saved to '{trace_path}'.")
                except Exception as trace_err:
                    logger.error(f"Error saving trace file: {trace_err}", exc_info=True)
            
            if isinstance(e, (MfaTimeoutError, MfaNotificationError)):
                raise e
            return None
        finally:
            if context:
                await context.close()
            
        return token
