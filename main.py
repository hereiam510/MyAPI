# main.py
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
import smtplib
from email.mime.text import MIMEText

# Import the shared token fetching function, logger, and custom exceptions
from token_fetcher import fetch_hku_token, MfaTimeoutError, MfaNotificationError
from logger_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

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

def send_mfa_alert(reason="MFA intervention required."):
    if not all([ALERT_EMAIL_TO, ALERT_EMAIL_FROM, ALERT_EMAIL_PASSWORD]):
        logger.warning("Email alerts not configured. A background refresh has failed and is now stopped.")
        return False

    subject = "[HKU ChatGPT Proxy] ACTION REQUIRED: Auto-Refresh Paused"
    body = (
        "Automatic HKU token refresh has failed and is now paused.\\n\\n"
        f"Reason: {reason}\\n\\n"
        "Please run the manual MFA recovery script (`python manual_mfa_refresh.py`) to restore service.\\n"
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

async def refresh_token_background_loop(app_state):
    failure_intervals = [1, 5]
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

                if failure_count < len(failure_intervals):
                    wait_time = failure_intervals[failure_count - 1] * 60
                    logger.info(f"Retrying token refresh in {wait_time / 60} minute(s).")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error("All retry attempts failed. This may be an MFA issue.")
                    send_mfa_alert("All automated refresh attempts failed.")
                    app_state["is_paused"].set()

        except MfaTimeoutError as e:
            logger.error(f"MFA approval timed out: {e}")
            send_mfa_alert(f"MFA approval timed out. The user did not respond in time.")
            app_state["is_paused"].set()

        except MfaNotificationError as e:
            logger.error(f"MFA notification failed: {e}")
            send_mfa_alert(f"Could not send the MFA number alert email after multiple retries. The email system may be misconfigured.")
            app_state["is_paused"].set()

        except Exception as e:
            logger.error(f"An unexpected error occurred during token refresh: {e}", exc_info=True)
            await asyncio.sleep(60)

@asynccontextmanager
async def lifespan(app: FastAPI):
    app_state["background_task"] = asyncio.create_task(refresh_token_background_loop(app_state))
    yield
    app_state["background_task"].cancel()

app = FastAPI(title="HKU ChatGPT Proxy", version="8.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_api_key(api_key: str = Security(API_KEY_HEADER)):
    if api_key != app_state["admin_api_key"]: raise HTTPException(status_code=403, detail="Invalid credentials")
    return api_key

async def stream_generator(response: httpx.Response):
    async for chunk in response.aiter_bytes():
        yield chunk

@app.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request):
    if not app_state["hku_auth_token"]:
        raise HTTPException(status_code=401, detail="No HKU token available.")

    req_payload = await request.json()

    deployment_id = req_payload.get("model", "gpt-4.1-nano")
    forward_payload = {
        "messages": req_payload.get("messages", []),
        "stream": True,  # We always request a stream from the backend for efficiency
        "max_completion_tokens": req_payload.get("max_tokens", 2000),
        "temperature": req_payload.get("temperature", 0.7),
        "top_p": req_payload.get("top_p", 0.95),
    }

    target_url = f"{HKU_API_BASE_URL}/azure-openai-aad-api/stream/chat/completions"
    headers = {
        "Authorization": f"Bearer {app_state['hku_auth_token']}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": request.headers.get("user-agent", "Mozilla/5.0"),
    }

    try:
        client = httpx.AsyncClient(http2=True)
        resp = await client.request(
            "POST", 
            target_url, 
            params={"deployment-id": deployment_id}, 
            json=forward_payload, 
            headers=headers, 
            timeout=600.0
        )

        if resp.status_code != 200:
            error_body = await resp.aread()
            logger.error(f"Upstream error: {resp.status_code} - {error_body.decode()}")
            raise HTTPException(status_code=resp.status_code, detail=error_body.decode())

        client_wants_stream = req_payload.get("stream", False)

        if client_wants_stream:
            # If the client wants a stream, we forward it.
            return StreamingResponse(
                stream_generator(resp),
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type"),
            )
        else:
            # If the client wants a single JSON response, we aggregate the stream.
            content_chunks = []
            async for line in resp.aiter_lines():
                if line.startswith("data:") and "[DONE]" not in line:
                    try:
                        data = json.loads(line[6:])
                        if "choices" in data and data["choices"]:
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                content_chunks.append(delta["content"])
                    except json.JSONDecodeError:
                        continue

            await resp.aclose()
            full_content = "".join(content_chunks)

            return JSONResponse({
                "id": f"chatcmpl-{os.urandom(8).hex()}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": deployment_id,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": full_content,
                    },
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            })

    except httpx.RequestError as e:
        logger.error(f"Error during request to upstream API: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Bad Gateway: {e}")


@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.post("/update-token")
async def update_token(request: Request, api_key: str = Security(get_api_key)):
    data = await request.json()
    new_token = data.get("token")
    if not new_token:
        raise HTTPException(status_code=400, detail="Payload must contain a 'token' field.")
    app_state["hku_auth_token"] = new_token
    if app_state["is_paused"].is_set():
        app_state["is_paused"].clear()
    logger.info("Token was updated manually via the /update-token endpoint.")
    return {"message": "Token updated successfully and auto-refresh has been resumed."}
