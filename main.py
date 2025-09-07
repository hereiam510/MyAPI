import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv

# --- Configuration ---
# Load the environment variables from the .env file
load_dotenv()

# Get the API endpoint and your auth token from environment variables
HKU_API_BASE_URL = "https://api.hku.hk/azure-openai-api"
HKU_AUTH_TOKEN = os.getenv("HKU_AUTH_TOKEN")

# --- FastAPI Application ---
app = FastAPI(
    title="HKU ChatGPT Proxy",
    description="A simple proxy to provide an OpenAI-compatible API for the HKU Azure service.",
    version="1.0.0",
)

# --- The Proxy Endpoint ---
@app.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request):
    """
    Accepts an OpenAI-compatible request and forwards it to the HKU Azure endpoint.
    """
    if not HKU_AUTH_TOKEN:
        return {"error": "Authentication token is not configured."}

    # Extract the JSON payload from the incoming request
    request_payload = await request.json()
    
    # Get the deployment ID from the request payload (e.g., the 'model' field)
    # The image shows deployment-id in the URL, but it's more flexible to pass it in the body.
    # Let's assume the client will send 'model' as 'gpt-4.1-nano'
    deployment_id = request_payload.get("model", "gpt-4.1-nano")
    
    # Construct the full target URL
    target_url = f"{HKU_API_BASE_URL}/stream/chat/completions?deployment-id={deployment_id}"

    # Prepare the headers for the outgoing request
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {HKU_AUTH_TOKEN}",
    }

    # Use httpx.AsyncClient to handle the request
    async with httpx.AsyncClient() as client:
        # Stream the request to the target URL
        hku_request = client.build_request(
            method="POST",
            url=target_url,
            json=request_payload,
            headers=headers,
            timeout=300.0,
        )
        
        # Get the streaming response from the HKU server
        hku_response = await client.send(hku_request, stream=True)

    # Return a StreamingResponse that passes the content directly to the client
    return StreamingResponse(
        hku_response.aiter_bytes(),
        status_code=hku_response.status_code,
        media_type=hku_response.headers.get("content-type"),
    )
