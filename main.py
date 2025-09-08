# main.py
import os
import asyncio
import json
import httpx
from fastapi import FastAPI, Request, HTTPException, Security
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from typing import Dict, Any, AsyncGenerator
from playwright.async_api import async_playwright
import smtplib
from email.mime.text import MIMEText

# =================================================
#           CONFIGURATION SECTION
# =================================================
# Load all variables from the .env file.
load_dotenv()

# How many consecutive renewal failures before sending an email alert.
EMAIL_ALERT_FAILURES = int(os.getenv('EMAIL_ALERT_FAILURES', 3))
# How often (in minutes) the background task should try to refresh the token.
TOKEN_REFRESH_INTERVAL_MINUTES = int(os.getenv("TOKEN_REFRESH_INTERVAL_MINUTES", 60))

# A dictionary to hold the application's state, including the current auth token.
app_state = {
    "hku_auth_token": os.getenv("HKU_AUTH_TOKEN"),
    "admin_api_key": os.getenv("ADMIN_API_KEY"),
}

# Credentials and settings for the auto-renewal process.
HKU_API_BASE_URL = "https://api.hku.hk"
HKU_EMAIL = os.getenv("HKU_EMAIL")
HKU_PASSWORD = os.getenv("HKU_PASSWORD")

# Credentials and settings for sending email alerts via SMTP.
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO")
ALERT_EMAIL_FROM = os.getenv("ALERT_EMAIL_FROM")
ALERT_EMAIL_PASSWORD = os.getenv("ALERT_EMAIL_PASSWORD")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))

# Defines the security header for admin endpoints.
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# Sets of model names, categorized by the type of parameters they accept.
REASONING_MODELS = {"gpt-5", "gpt-5-mini", "gpt-5-nano", "o4-mini"}
STANDARD_MODELS = {"gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "gpt-5-chat", "DeepSeek-V3", "DeepSeek-R1"}

# =================================================
#           EMAIL ALERTING FUNCTION
# =================================================
def send_mfa_alert(reason="MFA intervention required."):
    """Constructs and sends an email alert when the auto-renewal process fails."""
    if not (ALERT_EMAIL_TO and ALERT_EMAIL_FROM and ALERT_EMAIL_PASSWORD):
        print("[ALERT] Missing email settings in .env -- cannot send alert!")
        return
    
    subject = "[HKU ChatGPT Proxy] MFA Required"
    body = (
        "Automatic HKU token refresh has failed repeatedly.\n\n"
        f"Reason: {reason}\n\n"
        "Please run the manual MFA recovery script (`python manual_mfa_refresh.py`) to restore service.\n"
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
        print(f"[ALERT] Email sent to {ALERT_EMAIL_TO}!")
    except Exception as e:
        print(f"[ALERT] Failed to send alert email: {e}")

# =================================================
#           PLAYWRIGHT HKU LOGIN/TOKEN FUNCTION
# =================================================
async def fetch_hku_token(email, password):
    """
    Uses a headless (invisible) browser to log in to HKU and capture a new auth token.
    This works as long as no MFA challenge is presented.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://chatgpt.hku.hk/")
        
        # Attempt to fill login form. These steps might be skipped if a session is already active.
        try:
            await page.wait_for_selector('input[type="email"],input[name="username"]', timeout=10000)
            await page.fill('input[type="email"],input[name="username"]', email)
            await page.click('button[type="submit"],input[type="submit"]')
        except Exception:
            pass # No email field, might be an SSO redirect.

        try:
            await page.wait_for_selector('input[type="password"]', timeout=10000)
            await page.fill('input[type="password"]', password)
            await page.click('button[type="submit"],input[type="submit"]')
        except Exception:
            pass # No password field.

        await page.wait_for_load_state('networkidle')
        await asyncio.sleep(4)

        token = None

        # Intercept network requests to find the one containing the auth token.
        async def intercept_request(request):
            nonlocal token
            if "completions" in request.url:
                auth = request.headers.get("authorization")
                if auth and auth.startswith("Bearer "):
                    token = auth.split("Bearer ")[1]
        page.on("request", intercept_request)

        # Trigger an API call by simulating sending a message.
        try:
            await page.fill('textarea', 'Hello')
            await page.keyboard.press('Enter')
            await asyncio.sleep(4)
        except Exception:
            # If the above fails, just wait and hope a token was captured on page load.
            await asyncio.sleep(8)
        
        await browser.close()
        return token

# =================================================
#           TOKEN AUTO-REFRESH BACKGROUND TASK
# =================================================
async def refresh_token_background_loop(app_state):
    """A background task that runs forever, refreshing the token periodically."""
    failure_count = 0
    while True:
        try:
            print(f"[TokenRefresh] Auto-refreshing HKU token (every {TOKEN_REFRESH_INTERVAL_MINUTES} min).")
            token = await fetch_hku_token(HKU_EMAIL, HKU_PASSWORD)
            if token:
                app_state["hku_auth_token"] = token
                print("[TokenRefresh] Token updated successfully.")
                failure_count = 0 # Reset failure count on success.
            else:
                print("[TokenRefresh] Failed to get new token (possible MFA or login issue).")
                failure_count += 1
                if failure_count >= EMAIL_ALERT_FAILURES:
                    send_mfa_alert("Token refresh failed due to possible MFA requirement.")
                    failure_count = 0 # Reset to avoid spamming alerts.
        except Exception as e:
            print(f"[TokenRefresh] An exception occurred: {e}")
            failure_count += 1
            if failure_count >= EMAIL_ALERT_FAILURES:
                send_mfa_alert(f"Exception while refreshing token: {e}")
                failure_count = 0
        
        # Wait for the configured interval before trying again.
        await asyncio.sleep(TOKEN_REFRESH_INTERVAL_MINUTES * 60)

# =================================================
#           FASTAPI LIFESPAN & APP DEFINITION
# =================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles startup and shutdown events for the FastAPI app.
    We use it here to start our background token refresh task.
    """
    print("Starting up...")
    asyncio.create_task(refresh_token_background_loop(app_state))
    print("HKU token refresh background task started.")
    yield
    print("Shutting down.")

app = FastAPI(title="HKU ChatGPT Proxy", version="7.0.0", lifespan=lifespan)
# Add CORS middleware to allow requests from any origin (e.g., web clients).
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Helper function to protect admin endpoints with the ADMIN_API_KEY.
def get_api_key(api_key: str = Security(API_KEY_HEADER)):
    if api_key != app_state["admin_api_key"]:
        raise HTTPException(status_code=403, detail="Invalid credentials")
    return api_key

# Helper function to stream the response back to the client.
async def stream_generator(response: httpx.Response) -> AsyncGenerator[bytes, None]:
    try:
        async for chunk in response.aiter_bytes():
            yield chunk
    except httpx.ReadError:
        print("Stream ended.")
    finally:
        await response.aclose()

# =================================================
#           MAIN PROXY ENDPOINT
# =================================================
@app.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request):
    """The main endpoint that mimics the OpenAI API."""
    # If the token is missing on startup, try to fetch it once.
    if not app_state["hku_auth_token"]:
        token = await fetch_hku_token(HKU_EMAIL, HKU_PASSWORD)
        if not token:
            raise HTTPException(status_code=401, detail="No HKU token available; initial login failed.")
        app_state["hku_auth_token"] = token

    req_payload = await request.json()
    messages = req_payload.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="Request must include messages.")
    if not any(msg.get("role") == "system" for msg in messages):
        messages.insert(0, {"role": "system", "content": "You are an AI assistant that helps people find information."})
    
    deployment_id = req_payload.get("model", "gpt-4.1-nano")
    
    # Dynamically build the payload based on the model's requirements.
    forward_payload = {
        "messages": messages, "stream": True, "max_completion_tokens": req_payload.get("max_tokens", 2000)
    }
    if deployment_id in REASONING_MODELS:
        forward_payload["temperature"] = req_payload.get("temperature", 1.0)
        forward_payload["reasoning_effort"] = req_payload.get("reasoning_effort", "medium")
    elif deployment_id in STANDARD_MODELS:
        forward_payload["temperature"] = req_payload.get("temperature", 0.7)
        forward_payload["top_p"] = req_payload.get("top_p", 0.95)
    else: # Fallback for unknown models
        forward_payload["temperature"] = req_payload.get("temperature", 0.7)
        forward_payload["top_p"] = req_payload.get("top_p", 0.95)
    
    target_url = f"{HKU_API_BASE_URL}/azure-openai-aad-api/stream/chat/completions"
    headers = {
        "accept": "text/event-stream", "authorization": f"Bearer {app_state['hku_auth_token']}",
        "content-type": "application/json", "origin": "https://chatgpt.hku.hk", "referer": "https://chatgpt.hku.hk/",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
        "sec-fetch-mode": "cors", "sec-fetch-site": "same-site",
    }
    
    async with httpx.AsyncClient() as client:
        try:
            req = client.build_request("POST", target_url, params={"deployment-id": deployment_id}, json=forward_payload, headers=headers, timeout=300.0)
            resp = await client.send(req, stream=True)
            
            # If the token is expired (401), try to refresh it once and retry the request.
            if resp.status_code == 401:
                print("[Proxy] HKU token likely expired—attempting on-demand refresh and retry.")
                new_token = await fetch_hku_token(HKU_EMAIL, HKU_PASSWORD)
                if not new_token: raise HTTPException(status_code=401, detail="On-demand token refresh failed.")
                app_state["hku_auth_token"] = new_token
                headers["authorization"] = f"Bearer {new_token}"
                req = client.build_request("POST", target_url, params={"deployment-id": deployment_id}, json=forward_payload, headers=headers, timeout=300.0)
                resp = await client.send(req, stream=True)
            
            resp.raise_for_status() # Raise an exception for any other bad status codes (4xx, 5xx).
            
            # Decide whether to stream or aggregate the response based on the client's request.
            client_wants_stream = req_payload.get("stream", False)
            if client_wants_stream:
                return StreamingResponse(stream_generator(resp), media_type=resp.headers.get("content-type"))
            else:
                content_chunks = []
                async for line in resp.aiter_lines():
                    if line.startswith("data:") and "[DONE]" not in line:
                        try:
                            data = json.loads(line[6:])
                            if "choices" in data and data["choices"]:
                                delta = data["choices"][0].get("delta", {})
                                content = delta.get("content")
                                if content: content_chunks.append(content)
                        except json.JSONDecodeError: continue
                
                full_content = "".join(content_chunks)
                await resp.aclose()
                return JSONResponse({
                    "id": f"chatcmpl-test-{os.urandom(8).hex()}", "object": "chat.completion",
                    "created": int(__import__('time').time()), "model": deployment_id,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": full_content}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                })
        except httpx.HTTPStatusError as e:
            error_body = await e.response.aread()
            raise HTTPException(status_code=e.response.status_code, detail=f"Upstream error: {error_body.decode()}")

# =================================================
#           ADMIN & HEALTH ROUTES
# =================================================
@app.get("/health")
async def health_check():
    """A simple endpoint to check if the service is running."""
    return {"status": "ok"}

@app.post("/update-token")
async def update_token(request: Request, api_key: str = Security(get_api_key)):
    """Allows manual token updates, secured by the Admin API Key."""
    data = await request.json()
    new_token = data.get("token")
    if not new_token:
        raise HTTPException(status_code=400, detail="Payload must contain a 'token' field.")
    app_state["hku_auth_token"] = new_token
    return {"message": "Token updated successfully."}

@app.post("/trigger-refresh")
async def trigger_refresh(api_key: str = Security(get_api_key)):
    """Manually triggers the automated token refresh process."""
    token = await fetch_hku_token(HKU_EMAIL, HKU_PASSWORD)
    if token:
        app_state["hku_auth_token"] = token
        return {"status": "ok", "new_token_set": True}
    return {"status": "fail"}
