import os
import asyncio
import httpx
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# --- CONFIGURATION SECTION ---
load_dotenv()
HKU_EMAIL = os.getenv("HKU_EMAIL")
HKU_PASSWORD = os.getenv("HKU_PASSWORD")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
PROXY_HOST = os.getenv("PROXY_HOST", "http://localhost:8000")

# --- Playwright Token Fetcher ---
async def fetch_hku_token_manual(email, password):
    async with async_playwright() as p:
        print("Opening Chromium browser in VISIBLE mode (not headless).")
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://chatgpt.hku.hk/")

        print("""
==============================================================================
    ACTION REQUIRED: Please log in to the HKU service in the browser window.
    
    1. Complete the login and any Multi-Factor Authentication (MFA) steps.
    2. Once you see the chat interface, send one message (e.g., "hello").
    3. The script will then automatically capture the required token.
==============================================================================
""")
        input("Press Enter to open the browser and begin...")
        
        token = None
        token_captured = asyncio.Event()

        # --- BUG FIX SECTION ---
        # The handler now correctly uses the 'request' object directly.
        async def intercept_request(request):
            nonlocal token
            if "completions" in request.url:
                auth_header = request.headers.get("authorization")
                if auth_header and auth_header.startswith("Bearer "):
                    token = auth_header.split(" ")[1]
                    token_captured.set()
        
        page.on("request", intercept_request)

        try:
            await asyncio.wait_for(token_captured.wait(), timeout=180) # 3-minute timeout
            print("✅ New HKU Auth Token captured successfully!")
        except asyncio.TimeoutError:
            print("❌ Timeout: No token was captured. Did you fully log in and send a message?")
        
        await browser.close()
        return token

# --- Main logic ---
async def main():
    print("=== Manual MFA Token Recovery Utility ===\n")

    if not all([HKU_EMAIL, HKU_PASSWORD, ADMIN_API_KEY]):
        print("❌ Error: HKU_EMAIL, HKU_PASSWORD, or ADMIN_API_KEY is missing from your .env file.")
        return
    if ADMIN_API_KEY == "your-own-super-long-and-secret-admin-key":
        print("❌ Error: Please set a unique ADMIN_API_KEY in your .env file first.")
        return

    print(f"HKU Email: {HKU_EMAIL}")
    print(f"Using API backend: {PROXY_HOST}\n")

    token = await fetch_hku_token_manual(HKU_EMAIL, HKU_PASSWORD)
    if token:
        print("\n--- New HKU Auth Token Captured ---")
        print("Updating the running proxy backend...")
        try:
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
