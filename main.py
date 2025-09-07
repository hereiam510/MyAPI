import os, httpx, asyncio, json
from fastapi import FastAPI, Request, HTTPException, Security
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from typing import Dict, Any

# --- Configuration ---
load_dotenv()
app_state = {
    "hku_auth_token": None,
    "admin_api_key": os.getenv("ADMIN_API_KEY", "your-super-secret-key")
}
HKU_API_BASE_URL = "https://api.hku.hk"
# AZURE_API_VERSION is no longer needed
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# --- Lifespan & App Setup ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    app_state["hku_auth_token"] = os.getenv("HKU_AUTH_TOKEN")
    print("Successfully loaded HKU Auth Token." if app_state["hku_auth_token"] else "WARNING: HKU_AUTH_TOKEN not found.")
    yield
    print("Shutting down.")

app = FastAPI(
    title="HKU ChatGPT Proxy",
    description="A proxy for the HKU Azure service.",
    version="2.0.0", # Final Corrected Version
    lifespan=lifespan
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Security & Helpers ---
def get_api_key(api_key: str = Security(api_key_header)):
    if api_key != app_state["admin_api_key"]:
        raise HTTPException(status_code=403, detail="Invalid credentials")
    return api_key

def build_forward_payload(req_payload: Dict[str, Any]) -> Dict[str, Any]:
    defaults = {"max_completion_tokens": 2000, "temperature": 0.7, "top_p": 0.95, "stream": True}
    forward_payload = defaults.copy()
    
    messages = req_payload.get("messages")
    if not messages:
        raise HTTPException(status_code=400, detail="Request must include messages.")
    forward_payload["messages"] = messages
    
    # Map OpenAI params to Azure params
    if "max_tokens" in req_payload:
        forward_payload["max_completion_tokens"] = req_payload["max_tokens"]
    for key in ["temperature", "top_p", "stream"]:
        if key in req_payload:
            forward_payload[key] = req_payload[key]
            
    return forward_payload

# --- API Endpoints ---
@app.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request):
    if not app_state["hku_auth_token"]:
        raise HTTPException(status_code=401, detail="Auth token not configured.")
    
    original_payload = await request.json()
    forward_payload = build_forward_payload(original_payload)
    
    # CORRECTED: The path now matches the cURL command exactly.
    target_url = f"{HKU_API_BASE_URL}/azure-openai-aad-api/stream/chat/completions"
    
    # CORRECTED: The params now only contain deployment-id, no api-version.
    params = {
        "deployment-id": original_payload.get("model", "gpt-4.1-nano")
    }

    # CORRECTED: Added Origin, Referer, and User-Agent headers to mimic the browser.
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {app_state['hku_auth_token']}",
        "Origin": "https://chatgpt.hku.hk",
        "Referer": "https://chatgpt.hku.hk/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
    }
    
    print(f"\n--- Forwarding Request to HKU ---\nURL: {target_url}\nParams: {params}\nPayload: {json.dumps(forward_payload, indent=2)}\n---------------------------------\n")
    
    async with httpx.AsyncClient() as client:
        try:
            req = client.build_request("POST", target_url, params=params, json=forward_payload, headers=headers, timeout=300.0)
            resp = await client.send(req, stream=True)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            error_details = await e.response.aread()
            print(f"ERROR from HKU Server: {e.response.status_code} - {error_details.decode()}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Upstream server error: {error_details.decode()}")

    return StreamingResponse(
        resp.aiter_bytes(),
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type"),
    )

@app.post("/update-token")
async def update_token(request: Request, api_key: str = Security(get_api_key)):
    data = await request.json()
    new_token = data.get("token")
    if not new_token:
        raise HTTPException(status_code=400, detail="Payload must contain a 'token' field.")
    app_state["hku_auth_token"] = new_token
    print(f"Successfully updated HKU Auth Token at {__import__('datetime').datetime.now()}.")
    return {"message": "Token updated successfully."}

