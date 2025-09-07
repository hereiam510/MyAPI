HKU ChatGPT OpenAI-Compatible Proxy
This project provides a simple, self-hosted proxy that exposes an OpenAI-compatible API for the HKU Azure-hosted ChatGPT service. This allows you to use standard OpenAI client libraries and tools to interact with the university's AI service.

Features
✅ OpenAI API Compatibility: Use any tool or library that works with the OpenAI API.

🚀 Simple Setup: Get up and running with a single command using Docker Compose.

🔒 Secure Credential Management: Keeps your authentication token secure using an environment file.

💨 Streaming Support: Fully supports streaming responses for a real-time chat experience.

Prerequisites
Docker and Docker Compose must be installed on your machine.

An active account for the HKU ChatGPT Service.

🚀 Quick Start Guide
Step 1: Get Your Authentication Token
You need to get a temporary Bearer token from your browser to allow the proxy to make requests on your behalf.

Log in to the Service: In your browser, navigate to https://chatgpt.hku.hk/ and log in.

Open Developer Tools: Press F12 (or Ctrl+Shift+I / Cmd+Option+I) to open the Developer Tools, and click on the Network tab.

Send a Message: Type a message in the chat and press Enter.

Find the API Request: In the Network tab, look for a request named completions?deployment-id=gpt-4.1-nano. Click on it.

Copy the Bearer Token:

In the details pane, go to the Headers tab.

Scroll down to the Request Headers section.

Find the Authorization header.

Copy the entire long string that comes after Bearer . It will start with ey....

!(https://www.google.com/search?q=https://i.imgur.com/fK5nC2j.png) <!-- You can replace this with your own screenshot -->

Step 2: Configure the Project
Clone or download this project to your local machine.

Create an Environment File: In the root of the project folder, create a file named .env.

Add Your Token: Open the .env file and add the token you copied in the previous step, like this:

# .env file
HKU_AUTH_TOKEN="paste_your_long_token_string_here"

Step 3: Run the Service with Docker
With your .env file in place, open a terminal in the project's root directory and run the following command:

docker-compose up --build -d

--build: This builds the Docker image the first time you run it.

-d: This runs the service in the background (detached mode).

Your OpenAI-compatible API is now running and accessible at http://localhost:8000.

⚙️ How to Use the API
You can now point any OpenAI-compatible client to your local proxy.

Configuration for Clients:
API Base URL / Endpoint: http://localhost:8000/v1

API Key: You can enter any value (e.g., hk_token). The proxy doesn't validate it, as it uses your Bearer token for authentication.

Model: gpt-4.1-nano (or any other model/deployment ID available through the service).

Example curl Request
You can test if the service is working from your terminal with this command:

curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer any_key" \
  -d '{
    "model": "gpt-4.1-nano",
    "messages": [
      {
        "role": "user",
        "content": "Hello! Can you tell me a fun fact about Hong Kong?"
      }
    ],
    "stream": true
  }'

⚠️ Important Notes
Token Expiration: The Authorization token you obtain from your browser is temporary and will expire after a certain period (e.g., a few hours or a day). When it expires, you will get authentication errors. You will need to repeat Step 1 to get a new token and update your .env file, then restart the Docker container (docker-compose down && docker-compose up -d).

Fair Use Policy: This service allows for easier integration, but you must still adhere to the university's fair usage policy. Do not use this proxy to send an abusive number of requests. The IT services can and will see the traffic associated with your account.

Disclaimer: This is an unofficial tool and is not affiliated with or endorsed by HKU. It is intended for personal and educational use. Use it at your own risk.
