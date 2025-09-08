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
# ... (rest of the configuration remains the same)

# --- Main logic ---
async def main():
    print("=== Manual MFA Token Recovery Utility ===\n") # Keep as print for CLI utility feel

    if not all([HKU_EMAIL, HKU_PASSWORD, ADMIN_API_KEY]):
        logger.error("HKU_EMAIL, HKU_PASSWORD, or ADMIN_API_KEY is missing from .env file.")
        return
    # ... (rest of the checks remain the same)

    logger.info(f"HKU Email: {HKU_EMAIL}")
    logger.info(f"Using API backend: {PROXY_HOST}\n")
    
    # Keep user instructions as print statements
    print("""
==============================================================================
    ACTION REQUIRED: Please log in to the HKU service in the browser window.
...
==============================================================================
""")
    input("Press Enter to open the browser and begin...")

    token = await fetch_hku_token(HKU_EMAIL, HKU_PASSWORD, headless=False)
    
    if token:
        logger.info("New HKU Auth Token Captured. Updating the running proxy backend...")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f'{PROXY_HOST}/update-token',
                    headers={'X-API-Key': ADMIN_API_KEY},
                    json={'token': token},
                    timeout=30.0
                )
                if resp.status_code == 200:
                    logger.info("✔️ Token updated successfully. You can close this script now.")
                else:
                    logger.error(f"Failed to update token! Server responded: {resp.status_code}, {resp.text}")
        except Exception as e:
            logger.error(f"An error occurred while contacting the proxy: {e}", exc_info=True)
    else:
        logger.error("Failed to grab a new token.")

if __name__ == "__main__":
    asyncio.run(main())
