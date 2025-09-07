import os, httpx, asyncio, json
from fastapi import FastAPI, Request, HTTPException, Security
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from typing import Dict, Any, AsyncGenerator

# --- Configuration ---
load_dotenv()
app_state = {
    "hku_auth_token": None,
    "admin_api_key": os.getenv("ADMIN_API_KEY", "your-super-secret-key")
}
HKU_API_BASE_URL = "https://api.hku.hk"
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
    version="2.3.0", # Final Corrected Version with robust streaming
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
    defaults = {"max_completion_tokens": 2000, "temperature": 0.7, "top_p": 0.95}
    forward_payload = defaults.copy()
    
    messages = req_payload.get("messages")
    if not messages:
        raise HTTPException(status_code=400, detail="Request must include messages.")
    forward_payload["messages"] = messages
    
    if "max_tokens" in req_payload:
        forward_payload["max_completion_tokens"] = req_payload["max_tokens"]
    for key in ["temperature", "top_p", "stream"]:
        if key in req_payload:
            forward_payload[key] = req_payload[key]
            
    return forward_payload

# --- CORRECTED: Robust async generator for streaming ---
async def stream_generator(response: httpx.Response) -> AsyncGenerator[bytes, None]:
    try:
        async for chunk in response.aiter_bytes():
            yield chunk
    except httpx.ReadError:
        print("Stream ended: Connection closed by the server as expected.")
    finally:
        await response.aclose()


# --- API Endpoints ---
@app.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request):
    if not app_state["hku_auth_token"]:
        raise HTTPException(status_code=401, detail="Auth token not configured.")
    
    original_payload = await request.json()
    is_streaming_request = original_payload.get("stream", False)
    
    forward_payload = build_forward_payload(original_payload)
    
    deployment_id = original_payload.get("model", "gpt-4.1-nano")
    target_url = f"{HKU_API_BASE_URL}/azure-openai-aad-api/stream/chat/completions"
    params = {"deployment-id": deployment_id}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {app_state['hku_auth_token']}",
        "Origin": "https://chatgpt.hku.hk",
        "Referer": "https://chatgpt.hku.hk/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            req = client.build_request("POST", target_url, params=params, json=forward_payload, headers=headers, timeout=300.0)
            
            if is_streaming_request:
                resp = await client.send(req, stream=True)
                resp.raise_for_status()
                # Use the robust stream_generator
                return StreamingResponse(stream_generator(resp), status_code=resp.status_code, media_type=resp.headers.get("content-type"))
            else:
                resp = await client.send(req, stream=True)
                resp.raise_for_status()
                
                full_response_content = ""
                final_choice = {}
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        try:
                            json_str = line[6:]
                            if json_str.strip() and json_str != "[DONE]":
                                data = json.loads(json_str)
                                delta = data.get("choices", [{}])[0].get("delta", {})
                                if "content" in delta:
                                    full_response_content += delta["content"]
                                final_choice = data.get("choices", [{}])[0]
                        except json.JSONDecodeError:
                            continue
                
                final_response_obj = {
                    "id": f"chatcmpl-test-{os.urandom(8).hex()}",
                    "object": "chat.completion",
                    "created": int(__import__('time').time()),
                    "model": deployment_id,
                    "choices": [{
                        "index": 0,
                        "message": { "role": "assistant", "content": full_response_content },
                        "finish_reason": final_choice.get("finish_reason", "stop")
                    }],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                }
                await resp.aclose() # Ensure the stream is closed
                return JSONResponse(content=final_response_obj)

        except httpx.HTTPStatusError as e:
            error_details = await e.response.aread()
            print(f"ERROR from HKU Server: {e.response.status_code} - {error_details.decode()}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Upstream server error: {error_details.decode()}")

@app.post("/update-token")
async def update_token(request: Request, api_key: str = Security(get_api_key)):
    data = await request.json()
    new_token = data.get("token")
    if not new_token:
        raise HTTPException(status_code=400, detail="Payload must contain a 'token' field.")
    app_state["hku_auth_token"] = new_token
    print(f"Successfully updated HKU Auth Token at {__import__('datetime').datetime.now()}.")
    return {"message": "Token updated successfully."}

