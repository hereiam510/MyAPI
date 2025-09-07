import os
import httpx
import asyncio
from fastapi import FastAPI, Request, HTTPException, Security
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from dotenv import load_dotenv
from contextlib import asynccontextmanager

# --- Configuration ---
load_dotenv()

# We will now manage the token in a global state dictionary
app_state = {
    "hku_auth_token": None,
    "admin_api_key": os.getenv("ADMIN_API_KEY", "your-super-secret-key") # Add this to your .env for security!
}

HKU_API_BASE_URL = "https://api.hku.hk/azure-openai-api"
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# --- Lifespan Management ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the initial token at startup
    app_state["hku_auth_token"] = os.getenv("HKU_AUTH_TOKEN")
    if app_state["hku_auth_token"]:
        print("Successfully loaded HKU Auth Token.")
    else:
        print("WARNING: HKU_AUTH_TOKEN not found in .env file.")
    yield
    # Clean up state if needed on shutdown
    print("Shutting down.")

# --- FastAPI Application ---
app = FastAPI(
    title="HKU ChatGPT Proxy",
    description="A proxy for the HKU Azure service with hot-reload for auth tokens.",
    version="1.1.0",
    lifespan=lifespan
)

# --- Security Function ---
def get_api_key(api_key: str = Security(api_key_header)):
    if api_key == app_state["admin_api_key"]:
        return api_key
    else:
        raise HTTPException(
            status_code=403, detail="Could not validate credentials"
        )

# --- API Endpoints ---
@app.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request):
    if not app_state["hku_auth_token"]:
        raise HTTPException(status_code=401, detail="Authentication token is not configured.")

    request_payload = await request.json()
    deployment_id = request_payload.get("model", "gpt-4.1-nano")
    target_url = f"{HKU_API_BASE_URL}/stream/chat/completions?deployment-id={deployment_id}"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {app_state['hku_auth_token']}",
    }

    async with httpx.AsyncClient() as client:
        hku_request = client.build_request(
            method="POST", url=target_url, json=request_payload, headers=headers, timeout=300.0
        )
        hku_response = await client.send(hku_request, stream=True)

    return StreamingResponse(
        hku_response.aiter_bytes(),
        status_code=hku_response.status_code,
        media_type=hku_response.headers.get("content-type"),
    )

@app.post("/update-token")
async def update_token(request: Request, api_key: str = Security(get_api_key)):
    """
    A secure endpoint to update the HKU Bearer token while the service is running.
    """
    data = await request.json()
    new_token = data.get("token")
    if not new_token:
        raise HTTPException(status_code=400, detail="JSON payload must contain a 'token' field.")
    
    app_state["hku_auth_token"] = new_token
    print(f"Successfully updated HKU Auth Token at {asyncio.to_thread(lambda: __import__('datetime').datetime.now())}.")
    return {"message": "Token updated successfully."}
