import os
import asyncio
import json
import httpx
import time
import logging
from fastapi import FastAPI, Request, HTTPException, Security
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from typing import AsyncGenerator
import smtplib
from email.mime.text import MIMEText

from token_fetcher import fetch_hku_token
from logger_config import setup_logging

# --- Setup Logging ---
setup_logging()
logger = logging.getLogger(__name__)

# =================================================
#           CONFIGURATION SECTION
# =================================================
load_dotenv()

TOKEN_REFRESH_INTERVAL_MINUTES = int(os.getenv("TOKEN_REFRESH_INTERVAL_MINUTES", 15))

app_state = {
    "hku_auth_token": os.getenv("HKU_AUTH_TOKEN"),
    "admin_api_key": os.getenv("ADMIN_API_KEY"),
    "background_task": None,
    "is_paused": asyncio.Event(),
}

HKU_API_BASE_URL = "https://api.hku.hk"
HKU_EMAIL = os.getenv("HKU_EMAIL")
HKU_PASSWORD = os.getenv("HKU_PASSWORD")

ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO")
ALERT_EMAIL_FROM = os.getenv("ALERT_EMAIL_FROM")
ALERT_EMAIL_PASSWORD = os.getenv("ALERT_EMAIL_PASSWORD")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

REASONING_MODELS = {"gpt-5", "gpt-5-mini", "gpt-5-nano", "o4-mini"}
STANDARD_MODELS = {"gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "gpt-5-chat", "DeepSeek-V3", "DeepSeek-R1"}

# =================================================
#           EMAIL ALERTING FUNCTION
# =================================================
def send_mfa_alert(reason="MFA intervention required."):
    if not (ALERT_EMAIL_TO and ALERT_EMAIL_FROM and ALERT_EMAIL_PASSWORD):
        logger.warning("Email alerts not configured. A background refresh has failed and is now stopped.")
        logger.warning("Please run `python manual_mfa_refresh.py` to fix.")
        return False
    
    subject = "[HKU ChatGPT Proxy] ACTION REQUIRED: MFA Token Refresh"
    body = (
        "Automatic HKU token refresh has failed repeatedly and is now paused.\n\n"
        f"Reason: {reason}\n\n"
        "Please run the manual MFA recovery script (`python manual_mfa_refresh.py`) to restore service.\n"
        "The automatic refresh will resume after a successful manual update.\n"
        "--\nHKU Proxy System\n"
    )
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = ALERT_EMAIL_FROM
    msg['To'] = ALERT_EMAIL_TO

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(ALERT_EMAIL_FROM, ALERT_EMAIL_PASSWORD)
        server.sendmail(ALERT_EMAIL_FROM, ALERT_EMAIL_TO, msg.as_string())
        server.quit()
        logger.info(f"Email alert sent to {ALERT_EMAIL_TO}! Auto-refresh is now paused.")
        return True
    except Exception as e:
        logger.error(f"Failed to send alert email: {e}", exc_info=True)
        return False

# =================================================
#           INTELLIGENT TOKEN AUTO-REFRESH LOGIC
# =================================================
async def refresh_token_background_loop(app_state):
    """A more intelligent background task with rapid retries and a pause state."""
    failure_intervals = [1, 5] # Retry after 1 min, then 5 min on failure.
    failure_count = 0
    
    while True:
        if app_state["is_paused"].is_set():
            logger.warning("TokenRefresh is paused. Waiting for manual token update...")
            await app_state["is_paused"].wait()
            logger.info("TokenRefresh is resuming automatic refresh.")
            failure_count = 0

        try:
            logger.info("Attempting to auto-refresh HKU token.")
            token = await fetch_hku_token(HKU_EMAIL, HKU_PASSWORD, headless=True)
            
            if token:
                app_state["hku_auth_token"] = token
                logger.info("Token updated successfully via background refresh.")
                failure_count = 0
                await asyncio.sleep(TOKEN_REFRESH_INTERVAL_MINUTES * 60)
            else:
                logger.warning(f"Failed to get new token (Attempt #{failure_count + 1}).")
                failure_count += 1
                
                if failure_count <= len(failure_intervals):
                    wait_time = failure_intervals[failure_count - 1] * 60
                    logger.info(f"Retrying token refresh in {wait_time / 60} minute(s).")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error("All retry attempts failed. This may be an MFA issue.")
                    send_mfa_alert("All automated refresh attempts failed.")
                    app_state["is_paused"].set()

        except Exception as e:
            logger.error(f"An unexpected error occurred during token refresh: {e}", exc_info=True)
            await asyncio.sleep(60)

# =================================================
#           FASTAPI LIFESPAN & APP DEFINITION
# =================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application starting up...")
    app_state["background_task"] = asyncio.create_task(refresh_token_background_loop(app_state))
    logger.info("HKU token refresh background task started.")
    yield
    logger.info("Application shutting down...")
    app_state["background_task"].cancel()

app = FastAPI(title="HKU ChatGPT Proxy", version="8.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_api_key(api_key: str = Security(API_KEY_HEADER)):
    if api_key != app_state["admin_api_key"]: raise HTTPException(status_code=403, detail="Invalid credentials")
    return api_key

async def stream_generator(response: httpx.Response) -> AsyncGenerator[bytes, None]:
    try:
        async for chunk in response.aiter_bytes(): yield chunk
    except httpx.ReadError: 
        logger.warning("Stream ended unexpectedly by client.")
    finally: await response.aclose()

# =================================================
#           MAIN PROXY ENDPOINT
# =================================================
@app.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request):
    if not app_state["hku_auth_token"]:
        raise HTTPException(status_code=401, detail="No HKU token available. Service may be initializing.")

    req_payload = await request.json()
    # ... (rest of the function remains the same)
    
    async with httpx.AsyncClient() as client:
        try:
            req = client.build_request("POST", target_url, params={"deployment-id": deployment_id}, json=forward_payload, headers=headers, timeout=300.0)
            resp = await client.send(req, stream=True)
            if resp.status_code == 401:
                logger.error("Received 401 Unauthorized from upstream. Token is likely expired.")
                raise HTTPException(status_code=401, detail="The HKU Auth Token is invalid or expired.")
            
            resp.raise_for_status()
            client_wants_stream = req_payload.get("stream", False)
            if client_wants_stream:
                return StreamingResponse(stream_generator(resp), media_type=resp.headers.get("content-type"))
            else:
                # ... (non-streaming logic remains the same)
                return JSONResponse(...)
        except httpx.HTTPStatusError as e:
            error_body = await e.response.aread()
            logger.error(f"Upstream API error: {e.response.status_code} - {error_body.decode()}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Upstream error: {error_body.decode()}")

# ... (rest of the file remains the same)
