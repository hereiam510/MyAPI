# manual_mfa_refresh.py
import os
import asyncio
import httpx
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# --- CONFIGURATION SECTION ---
# Load all necessary variables from the .env file.
load_dotenv()
HKU_EMAIL = os.getenv("HKU_EMAIL")
HKU_PASSWORD = os.getenv("HKU_PASSWORD")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
PROXY_HOST = os.getenv("PROXY_HOST", "http://localhost:8000")

# --- Playwright Token Fetcher ---
async def fetch_hku_token_manual(email, password):
    """
    Launches a VISIBLE browser window for you to manually complete the login and MFA process.
    It then listens for the API call triggered by sending a message and captures the token.
    """
    async with async_playwright() as p:
        print("Opening Chromium browser in VISIBLE mode (not headless).")
        print(">> Please use the browser window to login and complete any MFA (2FA) requirements.")
        # Launch a non-headless browser so the user can see and interact with it.
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://chatgpt.hku.hk/")

        # The script attempts to fill in email and password, but the main goal is to get the user
        # to the point where they can solve the MFA challenge themselves.
        try:
            await page.wait_for_selector('input[type="email"],input[name="username"]', timeout=15000)
            await page.fill('input[type="email"],input[name="username"]', email)
            await page.click('button[type="submit"],input[type="submit"]')
        except Exception:
            print("No email field on this step (SSO redirect may be in place). Continuing.")

        try:
            await page.wait_for_selector('input[type="password"]', timeout=15000)
            await page.fill('input[type="password"]', password)
            await page.click('button[type="submit"],input[type="submit"]')
        except Exception:
            print("No password step or already authenticated.")

        print("""
==============================================================================
!! If you see a Multi-Factor Authentication (MFA) step, complete it.
-> After login, send a message in the chat (e.g. "ping") to trigger the API.
-> This script will wait (up to 3 minutes) for you to finish.
==============================================================================
""")
        token = None
        token_captured = asyncio.Event() # An event to signal when the token is found.

        # This function will run for every network request the browser makes.
        async def intercept_request(route):
            nonlocal token
            request = route.request
            # We look for the specific API call that contains the auth token.
            if "completions" in request.url:
                auth = request.headers.get("authorization")
                if auth and auth.startswith("Bearer "):
                    token = auth.split("Bearer ")[1]
                    token_captured.set() # Signal that we are done.
            await route.continue_() # Let the request proceed normally.
        
        # Register the interceptor.
        page.on("request", lambda req: asyncio.create_task(intercept_request(req)))

        # Wait for the user to finish. If the token_captured event isn't set
        # within 3 minutes, it will time out.
        try:
            await asyncio.wait_for(token_captured.wait(), timeout=180)
        except asyncio.TimeoutError:
            print("❌ Timeout: No token was captured. Did you fully log in and send a message?")
        
        await browser.close()
        return token

# --- Main logic ---
async def main():
    """
    Orchestrates the manual refresh process:
    1. Validates required settings are present.
    2. Calls the browser function to get a new token.
    3. Sends the new token to the running proxy service.
    """
    print("=== Manual MFA Token Recovery Utility ===\n")

    # Pre-flight checks to ensure the user has configured their .env file.
    if not all([HKU_EMAIL, HKU_PASSWORD, ADMIN_API_KEY]):
        print("❌ Error: HKU_EMAIL, HKU_PASSWORD, or ADMIN_API_KEY is missing from your .env file.")
        return
    if ADMIN_API_KEY == "your-own-super-long-and-secret-admin-key":
        print("❌ Error: Please set a unique ADMIN_API_KEY in your .env file first.")
        return

    print(f"HKU Email: {HKU_EMAIL}")
    print(f"Using API backend: {PROXY_HOST}\n")
    print("-- A Chromium browser window will open shortly. --\n")

    token = await fetch_hku_token_manual(HKU_EMAIL, HKU_PASSWORD)
    
    if token:
        print("\n--- New HKU Auth Token Captured ---")
        print("Updating the running proxy backend...")
        try:
            # Use the captured token to call the /update-token endpoint on the proxy.
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f'{PROXY_HOST}/update-token',
                    headers={'X-API-Key': ADMIN_API_KEY},
                    json={'token': token},
                    timeout=30.0
                )
                if resp.status_code == 200:
                    print("✔️  Token updated successfully. You can close this script now.")
                else:
                    print(f"❌ Failed to update token! Server responded: {resp.status_code}, {resp.text}")
        except Exception as e:
            print(f"❌ An error occurred while contacting the proxy: {e}")
    else:
        print("\n❌ Failed to grab a new token.")

if __name__ == "__main__":
    asyncio.run(main())
