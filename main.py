import os
import asyncio
import json
import httpx
import time
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
        print("[TokenRefresh] Email alerts not configured. A background refresh has failed and is now stopped.")
        print("[TokenRefresh] Please run `python manual_mfa_refresh.py` to fix.")
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
        print(f"[ALERT] Email sent to {ALERT_EMAIL_TO}! Auto-refresh is now paused.")
        return True
    except Exception as e:
        print(f"[ALERT] Failed to send alert email: {e}")
        return False

# =================================================
#           PLAYWRIGHT HKU LOGIN/TOKEN FUNCTION
# =================================================
async def fetch_hku_token(email, password):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://chatgpt.hku.hk/")
        try:
            await page.wait_for_selector('input[type="email"],input[name="username"]', timeout=10000)
            await page.fill('input[type="email"],input[name="username"]', email)
            await page.click('button[type="submit"],input[type="submit"]')
        except Exception: pass
        try:
            await page.wait_for_selector('input[type="password"]', timeout=10000)
            await page.fill('input[type="password"]', password)
            await page.click('button[type="submit"],input[type="submit"]')
        except Exception: pass

        await page.wait_for_load_state('networkidle', timeout=30000)
        await asyncio.sleep(4)
        token = None
        
        async def intercept_request(request):
            nonlocal token
            if "completions" in request.url:
                auth = request.headers.get("authorization")
                if auth and auth.startswith("Bearer "):
                    token = auth.split("Bearer ")[1]
        page.on("request", intercept_request)

        try:
            await page.fill('textarea', 'Hello')
            await page.keyboard.press('Enter')
            await asyncio.sleep(4)
        except Exception:
            await asyncio.sleep(8)
        
        await browser.close()
        return token

# =================================================
#           INTELLIGENT TOKEN AUTO-REFRESH LOGIC
# =================================================
async def refresh_token_background_loop(app_state):
    """A more intelligent background task with rapid retries and a pause state."""
    failure_intervals = [1, 5] # Retry after 1 min, then 5 min on failure.
    failure_count = 0
    
    while True:
        if app_state["is_paused"].is_set():
            print("[TokenRefresh] Paused. Waiting for manual token update...")
            await app_state["is_paused"].wait()
            print("[TokenRefresh] Resuming automatic refresh.")
            failure_count = 0

        try:
            print(f"[TokenRefresh] Attempting to auto-refresh HKU token.")
            token = await fetch_hku_token(HKU_EMAIL, HKU_PASSWORD)
            
            if token:
                app_state["hku_auth_token"] = token
                print("[TokenRefresh] Token updated successfully.")
                failure_count = 0
                await asyncio.sleep(TOKEN_REFRESH_INTERVAL_MINUTES * 60)
            else:
                print(f"[TokenRefresh] Failed to get new token (Attempt #{failure_count + 1}).")
                failure_count += 1
                
                if failure_count <= len(failure_intervals):
                    wait_time = failure_intervals[failure_count - 1] * 60
                    print(f"[TokenRefresh] Retrying in {wait_time / 60} minute(s).")
                    await asyncio.sleep(wait_time)
                else:
                    print("[TokenRefresh] All retry attempts failed. This may be an MFA issue.")
                    send_mfa_alert("All automated refresh attempts failed.")
                    app_state["is_paused"].set()

        except Exception as e:
            print(f"[TokenRefresh] An unexpected error occurred: {e}")
            await asyncio.sleep(60)

# =================================================
#           FASTAPI LIFESPAN & APP DEFINITION
# =================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Starting up...")
    app_state["background_task"] = asyncio.create_task(refresh_token_background_loop(app_state))
    print("HKU token refresh background task started.")
    yield
    print("Shutting down...")
    app_state["background_task"].cancel()

app = FastAPI(title="HKU ChatGPT Proxy", version="8.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_api_key(api_key: str = Security(API_KEY_HEADER)):
    if api_key != app_state["admin_api_key"]: raise HTTPException(status_code=403, detail="Invalid credentials")
    return api_key

async def stream_generator(response: httpx.Response) -> AsyncGenerator[bytes, None]:
    try:
        async for chunk in response.aiter_bytes(): yield chunk
    except httpx.ReadError: print("Stream ended.")
    finally: await response.aclose()

# =================================================
#           MAIN PROXY ENDPOINT
# =================================================
@app.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request):
    if not app_state["hku_auth_token"]:
        raise HTTPException(status_code=401, detail="No HKU token available. The service may be waiting for initial refresh.")

    req_payload = await request.json()
    messages = req_payload.get("messages", [])
    if not messages: raise HTTPException(status_code=400, detail="Request must include messages.")
    if not any(msg.get("role") == "system" for msg in messages):
        messages.insert(0, {"role": "system", "content": "You are an AI assistant that helps people find information."})
    
    deployment_id = req_payload.get("model", "gpt-4.1-nano")
    forward_payload = {
        "messages": messages, "stream": True, "max_completion_tokens": req_payload.get("max_tokens", 2000)
    }
    if deployment_id in REASONING_MODELS:
        forward_payload["temperature"] = req_payload.get("temperature", 1.0)
        forward_payload["reasoning_effort"] = req_payload.get("reasoning_effort", "medium")
    else:
        forward_payload["temperature"] = req_payload.get("temperature", 0.7)
        forward_payload["top_p"] = req_payload.get("top_p", 0.95)
    
    target_url = f"{HKU_API_BASE_URL}/azure-openai-aad-api/stream/chat/completions"
    headers = {
        "accept": "text/event-stream", "authorization": f"Bearer {app_state['hku_auth_token']}",
        "content-type": "application/json", "origin": "https://chatgpt.hku.hk", "referer": "https://chatgpt.hku.hk/",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    }
    
    async with httpx.AsyncClient() as client:
        try:
            req = client.build_request("POST", target_url, params={"deployment-id": deployment_id}, json=forward_payload, headers=headers, timeout=300.0)
            resp = await client.send(req, stream=True)
            if resp.status_code == 401:
                print("[Proxy] Received 401 Unauthorized. Manual refresh may be needed.")
                raise HTTPException(status_code=401, detail="The HKU Auth Token is invalid or expired. Please use the manual refresh script if the problem persists.")
            
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
                                if "content" in delta: content_chunks.append(delta["content"])
                        except json.JSONDecodeError: continue
                full_content = "".join(content_chunks)
                await resp.aclose()
                return JSONResponse({
                    "id": f"chatcmpl-test-{os.urandom(8).hex()}", "object": "chat.completion",
                    "created": int(time.time()), "model": deployment_id,
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
    return {"status": "ok"}

@app.post("/update-token")
async def update_token(request: Request, api_key: str = Security(get_api_key)):
    data = await request.json()
    new_token = data.get("token")
    if not new_token: raise HTTPException(status_code=400, detail="Payload must contain a 'token' field.")
    
    app_state["hku_auth_token"] = new_token
    if app_state["is_paused"].is_set():
        app_state["is_paused"].clear()
    
    return {"message": "Token updated successfully and auto-refresh has been resumed."}
