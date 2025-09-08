import asyncio
import logging
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

async def fetch_hku_token(email, password, headless=True):
    # ... (function signature remains the same)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        # ... (rest of the logic remains the same)
        
        # Automated login for headless mode
        if headless:
            try:
                # ... (login steps remain the same)
                
            except Exception as e:
                logger.error(f"Automated login failed: {e}", exc_info=True)
                await browser.close()
                return None
        
        # For non-headless mode, wait for user interaction
        try:
            await asyncio.wait_for(token_captured.wait(), timeout=180) 
            logger.info("HKU Auth Token captured successfully!")
        except asyncio.TimeoutError:
            logger.error("Timeout: No token was captured. User may not have completed login.")
        
        await browser.close()
        return token
