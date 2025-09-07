import os, httpx, asyncio, json
from fastapi import FastAPI, Request, HTTPException, Security
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from typing import Dict, Any, AsyncGenerator

# --- Configuration & App Setup ---
load_dotenv()
app_state = {
    "hku_auth_token": os.getenv("HKU_AUTH_TOKEN"),
    "admin_api_key": os.getenv("ADMIN_API_KEY")
}
HKU_API_BASE_URL = "https://api.hku.hk"
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Successfully loaded HKU Auth Token." if app_state["hku_auth_token"] else "WARNING: HKU_AUTH_TOKEN not found.")
    yield
    print("Shutting down.")

app = FastAPI(title="HKU ChatGPT Proxy", version="4.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# --- Helpers ---
def get_api_key(api_key: str = Security(API_KEY_HEADER)):
    if api_key != app_state["admin_api_key"]:
        raise HTTPException(status_code=403, detail="Invalid credentials")
    return api_key

async def stream_generator(response: httpx.Response) -> AsyncGenerator[bytes, None]:
    try:
        async for chunk in response.aiter_bytes():
            yield chunk
    except httpx.ReadError:
        print("Stream ended.")
    finally:
        await response.aclose()

# --- API Endpoints ---
@app.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request):
    if not app_state["hku_auth_token"]:
        raise HTTPException(status_code=401, detail="Auth token not configured.")

    req_payload = await request.json()
    
    messages = req_payload.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="Request must include messages.")
    if not any(msg.get("role") == "system" for msg in messages):
        messages.insert(0, {"role": "system", "content": "You are an AI assistant that helps people find information."})
    
    forward_payload = {
        "messages": messages,
        "max_completion_tokens": req_payload.get("max_tokens", 2000),
        "temperature": req_payload.get("temperature", 0.7),
        "top_p": req_payload.get("top_p", 0.95),
        "stream": True,
    }

    deployment_id = req_payload.get("model", "gpt-4.1-nano")
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
            resp.raise_for_status()

            client_wants_stream = req_payload.get("stream", False)

            if client_wants_stream:
                return StreamingResponse(stream_generator(resp), media_type=resp.headers.get("content-type"))
            else:
                # --- BUG FIX ---
                # This logic is now more robust. It safely checks if 'choices' has content
                # before trying to access it, preventing the IndexError crash.
                content_chunks = []
                async for line in resp.aiter_lines():
                    if line.startswith("data:") and "[DONE]" not in line:
                        try:
                            data = json.loads(line[6:])
                            # Safely access the content
                            if "choices" in data and data["choices"]:
                                delta = data["choices"][0].get("delta", {})
                                content = delta.get("content")
                                if content:
                                    content_chunks.append(content)
                        except json.JSONDecodeError:
                            print(f"Warning: Could not decode JSON from line: {line}")
                            continue
                
                full_content = "".join(content_chunks)
                await resp.aclose()

                return JSONResponse({
                    "id": f"chatcmpl-test-{os.urandom(8).hex()}", "object": "chat.completion", "created": int(__import__('time').time()), "model": deployment_id,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": full_content}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                })
        except httpx.HTTPStatusError as e:
            error_body = await e.response.aread()
            raise HTTPException(status_code=e.response.status_code, detail=f"Upstream error: {error_body.decode()}")

@app.post("/update-token")
async def update_token(request: Request, api_key: str = Security(get_api_key)):
    data = await request.json()
    new_token = data.get("token")
    if not new_token:
        raise HTTPException(status_code=400, detail="Payload must contain a 'token' field.")
    app_state["hku_auth_token"] = new_token
    return {"message": "Token updated successfully."}
