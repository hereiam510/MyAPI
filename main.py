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
#           (edit .env or these variables)
# =================================================
load_dotenv()

# How many consecutive failures before email alert?
EMAIL_ALERT_FAILURES = int(os.getenv('EMAIL_ALERT_FAILURES', 3))
# This is the number you'll want to change to 5 if needed

# How often to refresh the HKU token (in minutes)?
TOKEN_REFRESH_INTERVAL_MINUTES = int(os.getenv("TOKEN_REFRESH_INTERVAL_MINUTES", 60))

# App and credentials pulled from env for safety
app_state = {
    "hku_auth_token": os.getenv("HKU_AUTH_TOKEN"),
    "admin_api_key": os.getenv("ADMIN_API_KEY"),
}
HKU_API_BASE_URL = "https://api.hku.hk"
HKU_EMAIL = os.getenv("HKU_EMAIL")
HKU_PASSWORD = os.getenv("HKU_PASSWORD")

# Email alert config (edit .env for sensitive info)
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
    """
    Send an email to ALERT_EMAIL_TO if auto-refresh fails.
    Edit email template here as needed.
    """
    if not (ALERT_EMAIL_TO and ALERT_EMAIL_FROM and ALERT_EMAIL_PASSWORD):
        print("[ALERT] Missing email address or password in .env -- cannot send alert!")
        return
    subject = "[HKU ChatGPT Proxy] MFA Required"
    body = (
        "Automatic HKU token refresh has failed repeatedly.\n\n"
        f"Reason: {reason}\n\n"
        "Please run the manual MFA recovery script to restore service.\n"
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
    Automated browser login to HKU ChatGPT, returns token if successful.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://chatgpt.hku.hk/")
        try:
            await page.wait_for_selector('input[type="email"],input[name="username"]', timeout=10000)
            await page.fill('input[type="email"],input[name="username"]', email)
            await page.click('button[type="submit"],input[type="submit"]')
        except Exception:
            pass  # Could be instant SSO, no email step

        try:
            await page.wait_for_selector('input[type="password"]', timeout=10000)
            await page.fill('input[type="password"]', password)
            await page.click('button[type="submit"],input[type="submit"]')
        except Exception:
            pass  # Already authenticated

        await page.wait_for_load_state('networkidle')
        await asyncio.sleep(4)  # Wait for SPA to load

        token = None

        # Intercept outgoing token
        async def intercept_request(request):
            nonlocal token
            if "completions" in request.url:
                auth = request.headers.get("authorization")
                if auth and auth.startswith("Bearer "):
                    token = auth.split("Bearer ")[1]
        page.on("request", intercept_request)

        # Try to trigger token issue
        try:
            await page.fill('textarea', 'Hello')
            await page.keyboard.press('Enter')
            await asyncio.sleep(4)
        except Exception:
            await asyncio.sleep(8)
        await browser.close()
        return token

# =================================================
#           TOKEN AUTO-REFRESH - MAIN BG LOGIC
# =================================================
async def refresh_token_background_loop(app_state):
    """
    Background task to refresh the HKU token every TOKEN_REFRESH_INTERVAL_MINUTES minutes.
    After EMAIL_ALERT_FAILURES failures, sends an email alert and resets counter.
    """
    failure_count = 0
    while True:
        try:
            print(f"[TokenRefresh] Auto-refreshing HKU token (every {TOKEN_REFRESH_INTERVAL_MINUTES} min).")
            token = await fetch_hku_token(HKU_EMAIL, HKU_PASSWORD)
            if token:
                app_state["hku_auth_token"] = token
                print("[TokenRefresh] Token updated successfully.")
                failure_count = 0
            else:
                print("[TokenRefresh] Failed to update token!")
                failure_count += 1
                if failure_count >= EMAIL_ALERT_FAILURES:
                    send_mfa_alert("Token refresh failed due to possible MFA requirement.")
                    failure_count = 0  # Avoid spamming
        except Exception as e:
            print(f"[TokenRefresh] ERROR: {e}")
            failure_count += 1
            if failure_count >= EMAIL_ALERT_FAILURES:
                send_mfa_alert(f"Exception while refreshing token: {e}")
                failure_count = 0
        await asyncio.sleep(TOKEN_REFRESH_INTERVAL_MINUTES * 60)

# =================================================
#           FASTAPI LIFESPAN
# =================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup: begin background token refresher.
    """
    asyncio.create_task(refresh_token_background_loop(app_state))
    print("HKU token refresh background task started.")
    yield
    print("App shutdown.")

# =================================================
#           FASTAPI APP DEFINITION
# =================================================
app = FastAPI(title="HKU ChatGPT Proxy", version="6.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# === SECURITY UTILITY ===
def get_api_key(api_key: str = Security(API_KEY_HEADER)):
    if api_key != app_state["admin_api_key"]:
        raise HTTPException(status_code=403, detail="Invalid credentials")
    return api_key

# === EVENT STREAMER ===
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
    """
    Proxy endpoint: for OpenAI-compatible clients.
    If the token is invalid, will attempt to auto-refresh.
    """
    if not app_state["hku_auth_token"]:
        token = await fetch_hku_token(HKU_EMAIL, HKU_PASSWORD)
        if not token:
            raise HTTPException(status_code=401, detail="No HKU token; login failed.")
        app_state["hku_auth_token"] = token

    req_payload = await request.json()
    messages = req_payload.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="Request must include messages.")
    if not any(msg.get("role") == "system" for msg in messages):
        messages.insert(0, {"role": "system", "content": "You are an AI assistant that helps people find information."})
    deployment_id = req_payload.get("model", "gpt-4.1-nano")
    forward_payload = {
        "messages": messages,
        "stream": True,
        "max_completion_tokens": req_payload.get("max_tokens", 2000)
    }
    if deployment_id in REASONING_MODELS:
        forward_payload["temperature"] = req_payload.get("temperature", 1.0)
        forward_payload["reasoning_effort"] = req_payload.get("reasoning_effort", "medium")
    elif deployment_id in STANDARD_MODELS:
        forward_payload["temperature"] = req_payload.get("temperature", 0.7)
        forward_payload["top_p"] = req_payload.get("top_p", 0.95)
    else:
        forward_payload["temperature"] = req_payload.get("temperature", 0.7)
        forward_payload["top_p"] = req_payload.get("top_p", 0.95)
    target_url = f"{HKU_API_BASE_URL}/azure-openai-aad-api/stream/chat/completions"
    headers = {
        "accept": "text/event-stream",
        "authorization": f"Bearer {app_state['hku_auth_token']}",
        "content-type": "application/json",
        "origin": "https://chatgpt.hku.hk",
        "referer": "https://chatgpt.hku.hk/",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
    }
    async with httpx.AsyncClient() as client:
        try:
            req = client.build_request("POST", target_url, params={"deployment-id": deployment_id}, json=forward_payload, headers=headers, timeout=300.0)
            resp = await client.send(req, stream=True)
            # Retry once on 401 error (token expired)
            if resp.status_code == 401:
                print("[Proxy] HKU token expired—fetching new one and retrying.")
                new_token = await fetch_hku_token(HKU_EMAIL, HKU_PASSWORD)
                if not new_token:
                    raise HTTPException(status_code=401, detail="Auto token refresh failed.")
                app_state["hku_auth_token"] = new_token
                headers["authorization"] = f"Bearer {new_token}"
                req = client.build_request("POST", target_url, params={"deployment-id": deployment_id}, json=forward_payload, headers=headers, timeout=300.0)
                resp = await client.send(req, stream=True)
            resp.raise_for_status()
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
                                if content:
                                    content_chunks.append(content)
                        except json.JSONDecodeError:
                            continue
                full_content = "".join(content_chunks)
                await resp.aclose()
                return JSONResponse({
                    "id": f"chatcmpl-test-{os.urandom(8).hex()}",
                    "object": "chat.completion",
                    "created": int(__import__('time').time()),
                    "model": deployment_id,
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": full_content}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                })
        except httpx.HTTPStatusError as e:
            error_body = await e.response.aread()
            raise HTTPException(status_code=e.response.status_code, detail=f"Upstream error: {error_body.decode()}")

# =================================================
#           ADMIN ROUTES
# =================================================
@app.post("/update-token")
async def update_token(request: Request, api_key: str = Security(get_api_key)):
    """
    Update token manually via admin key.
    """
    data = await request.json()
    new_token = data.get("token")
    if not new_token:
        raise HTTPException(status_code=400, detail="Payload must contain a 'token' field.")
    app_state["hku_auth_token"] = new_token
    return {"message": "Token updated successfully."}

@app.post("/trigger-refresh")
async def trigger_refresh(api_key: str = Security(get_api_key)):
    """
    Manually trigger a token refresh. Requires admin key.
    """
    token = await fetch_hku_token(HKU_EMAIL, HKU_PASSWORD)
    if token:
        app_state["hku_auth_token"] = token
        return {"status": "ok", "new_token_set": True}
    else:
        return {"status": "fail"}

# =================================================
#            END OF FILE
# =================================================
