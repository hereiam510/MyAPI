"""
manual_mfa_refresh.py

This script is for manual HKU ChatGPT token recovery when automation fails due to MFA (multi-factor auth).
It runs a visible browser, lets you complete the login and MFA challenge,  
intercepts the HKU token, and updates your running API proxy automatically.

Typical usage:
  1. You get an email alert from your main proxy backend saying MFA intervention is required.
  2. You run this script (`python manual_mfa_refresh.py`) **on your laptop/desktop**.
  3. Complete login and MFA in the visible Chromium window.
  4. Once you see the ChatGPT chat UI, send a quick message ("ping" or similar).
  5. The script captures the new token and automatically updates your backend.
  6. You’re done: your proxy resumes normal operation, hands-free until MFA is required again.
"""

# --- Imports ---
import os
import asyncio
import httpx
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# --- CONFIGURATION SECTION ---
# * All credentials and server info are loaded from your .env file.
# * Your .env must contain:
#   - HKU_EMAIL
#   - HKU_PASSWORD
#   - ADMIN_API_KEY (for proxy update security)
#   - PROXY_HOST (default: http://localhost:8000)
load_dotenv()
HKU_EMAIL = os.getenv("HKU_EMAIL")
HKU_PASSWORD = os.getenv("HKU_PASSWORD")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "0510")
# Change this if your backend proxy runs elsewhere (eg: in Docker on another machine/port)
PROXY_HOST = os.getenv("PROXY_HOST", "http://localhost:8000")

# --- Playwright Token Fetcher (manual/mfa-friendly) ---
async def fetch_hku_token_manual(email, password):
    """
    Automates the login process for HKU ChatGPT, but uses a *visible browser* (NOT headless!).
    This lets you complete any MFA challenge (app, SMS, email, etc).
    Once login is successful, you MUST send a chat message to trigger API call and token capture!
    The intercepted token is returned.
    """
    async with async_playwright() as p:
        print("Opening Chromium browser in VISIBLE mode (not headless).")
        print(">> Please use the browser window to login and complete any MFA (2FA) requirements.")
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://chatgpt.hku.hk/")

        # Stage 1: Fill username/email if present
        try:
            await page.wait_for_selector('input[type="email"],input[name="username"]', timeout=15000)
            await page.fill('input[type="email"],input[name="username"]', email)
            await page.click('button[type="submit"],input[type="submit"]')
        except Exception:
            print("No email field on this step (SSO redirect may be in place). Continuing.")

        # Stage 2: Fill password if prompted
        try:
            await page.wait_for_selector('input[type="password"]', timeout=15000)
            await page.fill('input[type="password"]', password)
            await page.click('button[type="submit"],input[type="submit"]')
        except Exception:
            print("No password step or already authenticated.")

        print("""
==============================================================================
!! If you see a Multi-Factor Authentication (MFA) step (code, push, etc),
!! complete it in the browser.
------------------------------------------------------------------------------
-> After full login, wait until the ChatGPT chat interface is loaded.
-> IMPORTANT: Send a message in the chat (e.g. "ping") to trigger an API call.
-> This will allow the script to capture the required HKU token!
------------------------------------------------------------------------------
!! This script will wait (up to 3 minutes) for you to finish.
==============================================================================
""")

        token = None

        # Inner function to intercept outgoing requests and extract the token
        async def intercept_request(request):
            nonlocal token
            if "completions" in request.url:
                auth = request.headers.get("authorization")
                if auth and auth.startswith("Bearer "):
                    token = auth.split("Bearer ")[1]

        page.on("request", intercept_request)

        # Wait up to 3 minutes for user to finish login, MFA, and send message.
        for i in range(36):
            if token:
                break
            await asyncio.sleep(5)
            print(f"...[{i*5}s] Waiting for token capture. (Finish login/MFA and send a chat message if not already!)")

        await browser.close()
        return token

# --- Main logic: call fetcher, then update backend ---
async def main():
    print("=== Manual MFA Token Recovery Utility ===\n")
    print(f"HKU Email: {HKU_EMAIL}")
    print(f"Using API backend: {PROXY_HOST}")
    print("\n-- A Chromium browser window will open shortly. --\n")

    token = await fetch_hku_token_manual(HKU_EMAIL, HKU_PASSWORD)
    if token:
        print("\n--- HKU New Auth Token ---")
        print(token)
        print("\nUpdating the running proxy backend via /update-token ...")
        try:
            resp = httpx.post(
                f'{PROXY_HOST}/update-token',
                headers={'X-API-Key': ADMIN_API_KEY},
                json={'token': token}
            )
            if resp.status_code == 200:
                print("✔️  Token updated successfully. You may now close this script & browser.")
            else:
                print(f"❌ Failed to update token! {resp.status_code}, {resp.text}")
        except Exception as e:
            print(f"❌ Error during token update POST: {e}")
    else:
        print("❌ Failed to grab a token. Did you fully complete login, MFA, and send a chat message in browser?")

if __name__ == "__main__":
    asyncio.run(main())


