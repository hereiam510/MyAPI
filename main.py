import os
import httpx
import asyncio
from fastapi import FastAPI, Request, HTTPException, Security
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware  # <-- IMPORT THIS
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from typing import Dict, Any

# --- Configuration ---
load_dotenv()

app_state = {
    "hku_auth_token": None,
    "admin_api_key": os.getenv("ADMIN_API_KEY", "your-super-secret-key")
}

HKU_API_BASE_URL = "https://api.hku.hk/azure-openai-api"
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# --- Lifespan Management ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    app_state["hku_auth_token"] = os.getenv("HKU_AUTH_TOKEN")
    if app_state["hku_auth_token"]:
        print("Successfully loaded HKU Auth Token.")
    else:
        print("WARNING: HKU_AUTH_TOKEN not found in .env file.")
    yield
    print("Shutting down.")

# --- FastAPI Application ---
app = FastAPI(
    title="HKU ChatGPT Proxy",
    description="A proxy for the HKU Azure service with parameter forwarding and token hot-reload.",
    version="1.3.0", # Version updated
    lifespan=lifespan
)

# --- ADD THIS CORS MIDDLEWARE SECTION ---
# This allows your web-based frontend to connect to the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)
# -----------------------------------------


# --- Security Function ---
def get_api_key(api_key: str = Security(api_key_header)):
    if api_key == app_state["admin_api_key"]:
        return api_key
    else:
        raise HTTPException(
            status_code=403, detail="Could not validate credentials"
        )

# --- Helper Function to Build the Payload ---
def build_forward_payload(request_payload: Dict[str, Any]) -> Dict[str, Any]:
    defaults = {
        "max_completion_tokens": 2000,
        "temperature": 0.7,
        "top_p": 0.95,
        "stream": True
    }
    forward_payload = defaults.copy()
    messages = request_payload.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="Request must include messages.")
    forward_payload["messages"] = messages
    if "max_tokens" in request_payload:
        forward_payload["max_completion_tokens"] = request_payload["max_tokens"]
    if "temperature" in request_payload:
        forward_payload["temperature"] = request_payload["temperature"]
    if "top_p" in request_payload:
        forward_payload["top_p"] = request_payload["top_p"]
    if "stream" in request_payload:
        forward_payload["stream"] = request_payload["stream"]
    return forward_payload

# --- API Endpoints ---
@app.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request):
    if not app_state["hku_auth_token"]:
        raise HTTPException(status_code=401, detail="Authentication token is not configured.")
    original_payload = await request.json()
    forward_payload = build_forward_payload(original_payload)
    deployment_id = original_payload.get("model", "gpt-4.1-nano")
    target_url = f"{HKU_API_BASE_URL}/stream/chat/completions?deployment-id={deployment_id}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {app_state['hku_auth_token']}",
    }
    async with httpx.AsyncClient() as client:
        hku_request = client.build_request(
            method="POST", url=target_url, json=forward_payload, headers=headers, timeout=300.0
        )
        hku_response = await client.send(hku_request, stream=True)
    return StreamingResponse(
        hku_response.aiter_bytes(),
        status_code=hku_response.status_code,
        media_type=hku_response.headers.get("content-type"),
    )

@app.post("/update-token")
async def update_token(request: Request, api_key: str = Security(get_api_key)):
    data = await request.json()
    new_token = data.get("token")
    if not new_token:
        raise HTTPException(status_code=400, detail="JSON payload must contain a 'token' field.")
    app_state["hku_auth_token"] = new_token
    print(f"Successfully updated HKU Auth Token at {asyncio.to_thread(lambda: __import__('datetime').datetime.now())}.")
    return {"message": "Token updated successfully."}

