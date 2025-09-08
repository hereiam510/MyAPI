import os
import asyncio
import httpx
import logging
from dotenv import load_dotenv
from token_fetcher import fetch_hku_token
from logger_config import setup_logging

# --- Setup Logging ---
setup_logging()
logger = logging.getLogger(__name__)

# --- CONFIGURATION SECTION ---
load_dotenv()
HKU_EMAIL = os.getenv("HKU_EMAIL")
HKU_PASSWORD = os.getenv("HKU_PASSWORD")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
PROXY_HOST = os.getenv("PROXY_HOST", "http://localhost:8000")

# --- Main logic ---
async def main():
    # Keep print for CLI utility feel
    print("=== Manual MFA Token Recovery Utility ===\n")

    if not all([HKU_EMAIL, HKU_PASSWORD, ADMIN_API_KEY]):
        logger.error("❌ Error: HKU_EMAIL, HKU_PASSWORD, or ADMIN_API_KEY is missing from your .env file.")
        return
    if ADMIN_API_KEY == "your-own-super-long-and-secret-admin-key":
        logger.error("❌ Error: Please set a unique ADMIN_API_KEY in your .env file first.")
        return

    logger.info(f"HKU Email: {HKU_EMAIL}")
    logger.info(f"Using API backend: {PROXY_HOST}\n")

    # Keep user instructions as print statements for interactivity
    print("""
==============================================================================
    ACTION REQUIRED: Please log in to the HKU service in the browser window.
    
    1. Complete the login and any Multi-Factor Authentication (MFA) steps.
    2. Once you see the chat interface, send one message (e.g., "hello").
    3. The script will then automatically capture the required token.
==============================================================================
""")
    input("Press Enter to open the browser and begin...")

    # Use the shared function with the browser visible
    token = await fetch_hku_token(HKU_EMAIL, HKU_PASSWORD, headless=False)
    
    if token:
        logger.info("\n--- New HKU Auth Token Captured ---")
        logger.info("Updating the running proxy backend...")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f'{PROXY_HOST}/update-token',
                    headers={'X-API-Key': ADMIN_API_KEY},
                    json={'token': token},
                    timeout=30.0
                )
                if resp.status_code == 200:
                    logger.info("✔️  Token updated successfully. You can close this script now.")
                else:
                    logger.error(f"❌ Failed to update token! Server responded: {resp.status_code}, {resp.text}")
        except Exception as e:
            logger.error(f"❌ An error occurred while contacting the proxy: {e}", exc_info=True)
    else:
        logger.error("\n❌ Failed to grab a new token.")

if __name__ == "__main__":
    asyncio.run(main())
