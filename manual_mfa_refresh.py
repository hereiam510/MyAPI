import os
import asyncio
import httpx
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
HKU_EMAIL = os.getenv("HKU_EMAIL")
HKU_PASSWORD = os.getenv("HKU_PASSWORD")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "0510")
PROXY_HOST = os.getenv("PROXY_HOST", "http://localhost:8000")

async def fetch_hku_token_manual(email, password):
    async with async_playwright() as p:
        print("Opening Chromium browser in VISIBLE mode (not headless).")
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://chatgpt.hku.hk/")
        try:
            await page.wait_for_selector('input[type="email"],input[name="username"]', timeout=15000)
            await page.fill('input[type="email"],input[name="username"]', email)
            await page.click('button[type="submit"],input[type="submit"]')
        except Exception:
            print("No email step - continuing.")

        try:
            await page.wait_for_selector('input[type="password"]', timeout=15000)
            await page.fill('input[type="password"]', password)
            await page.click('button[type="submit"],input[type="submit"]')
        except Exception:
            print("No password step or already authenticated.")

        print("** If you see MFA prompt, complete it in the browser window! **")
        print("--> Wait until the ChatGPT front-end fully loads.")
        print("--> Once you see the chat, SEND a chat message (e.g. 'ping') to trigger the API /completions request.")
        print("--> The script will try to auto-capture the token from the browser.")

        token = None

        async def intercept_request(request):
            nonlocal token
            if "completions" in request.url:
                auth = request.headers.get("authorization")
                if auth and auth.startswith("Bearer "):
                    token = auth.split("Bearer ")[1]

        page.on("request", intercept_request)

        # Wait up to 3 minutes for user to finish all steps and send a message
        for i in range(36):
            if token:
                break
            await asyncio.sleep(5)
            print(f"...[{i*5}s] Waiting for token capture...")

        await browser.close()
        return token

async def main():
    token = await fetch_hku_token_manual(HKU_EMAIL, HKU_PASSWORD)
    if token:
        print("\n--- HKU New Auth Token ---")
        print(token)
        print("\nUpdating the running proxy backend via /update-token ...")
        resp = httpx.post(
            f'{PROXY_HOST}/update-token',
            headers={'X-API-Key': ADMIN_API_KEY},
            json={'token': token}
        )
        if resp.status_code == 200:
            print("✔️  Token updated successfully.")
        else:
            print(f"❌ Failed to update token! {resp.status_code}, {resp.text}")
    else:
        print("❌ Failed to grab a token. Did you fully complete login and send a chat message in browser?")

if __name__ == "__main__":
    asyncio.run(main())
