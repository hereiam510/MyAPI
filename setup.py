# setup.py
import subprocess
import sys
import os
import secrets
import getpass
import shutil
import asyncio
from playwright.async_api import async_playwright

def run_command(command, error_message):
    """Runs a command and exits if it fails, printing the detailed error."""
    try:
        process = subprocess.run(command, check=True, shell=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Error: {error_message}")
        print("\n--- Command Output (Error) ---")
        print(e.stderr)
        print("------------------------------")
        return False
    except FileNotFoundError:
        print(f"❌ Error: {error_message}")
        return False

def check_prerequisites():
    """Checks if Docker and Docker Compose are installed."""
    print("--- Checking Prerequisites ---")
    if not run_command("docker --version", "Docker is not installed or not in your PATH. Please install it to continue."):
        return False
    if not run_command("docker-compose --version", "Docker Compose is not installed or not in your PATH. Please install it to continue."):
        return False
    print("✅ Docker and Docker Compose are installed.")
    return True

def install_local_dependencies():
    """Installs local Python and Playwright dependencies, detecting `uv`."""
    print("\n--- Installing Local Dependencies ---")
    installer = "pip"
    if shutil.which("uv"):
        installer = "uv pip"
        print("✅ Detected `uv` package manager. Using `uv pip` for installation.")
    else:
        print("⚠️  NOTE: `uv` was not found. Falling back to standard `pip`.")
    install_cmd = f"{installer} install -r requirements.txt"
    error_msg = f"Failed to install Python packages using `{installer}`."
    if not run_command(install_cmd, error_msg):
        return False
    print("✅ Python packages installed.")
    if not run_command("playwright install", "Failed to install Playwright browsers."):
        return False
    print("✅ Playwright browsers installed.")
    return True

async def fetch_initial_token(email, password):
    """
    Launches a VISIBLE browser window for the user to perform the initial login and MFA.
    Captures and returns the first auth token.
    """
    print("\n--- Initial Token Acquisition ---")
    # --- MODIFIED SECTION ---
    # Instructions are now shown BEFORE the browser opens.
    print("""
==============================================================================
    ACTION REQUIRED IN THE NEXT STEP:
    
    A browser window will open. Please log in to the HKU service.
    
    1. Complete the login and any Multi-Factor Authentication (MFA) steps.
    2. Once you see the chat interface, send one message (e.g., "hello").
    3. The script will then automatically capture the required token.
==============================================================================
""")
    input("Press Enter to open the browser and begin...")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://chatgpt.hku.hk/")

        token = None
        token_captured = asyncio.Event()

        # --- BUG FIX SECTION ---
        # The handler now correctly uses the 'request' object directly.
        async def intercept_request(request):
            nonlocal token
            if "completions" in request.url:
                auth_header = request.headers.get("authorization")
                if auth_header and auth_header.startswith("Bearer "):
                    token = auth_header.split(" ")[1]
                    token_captured.set()
        
        page.on("request", intercept_request)

        try:
            await asyncio.wait_for(token_captured.wait(), timeout=180) # 3-minute timeout
            print("✅ Initial HKU Auth Token captured successfully!")
        except asyncio.TimeoutError:
            print("❌ Timeout: No token was captured. Did you fully log in and send a message?")
        
        await browser.close()
        return token

def create_env_file():
    """Interactively gathers user input and creates the .env file."""
    print("\n--- Configuring .env File ---")
    
    env_content = []
    
    print("\nPlease enter your HKU Portal credentials.")
    hku_email = input("Enter your HKU email (e.g., yourhkuid@connect.hku.hk): ")
    hku_password = getpass.getpass("Enter your HKU password (will be hidden): ")
    env_content.append(f'HKU_EMAIL="{hku_email}"')
    env_content.append(f'HKU_PASSWORD="{hku_password}"')

    print("\nNext, you will set an Admin API Key.")
    print("This key is required for the manual MFA refresh script, so you MUST save it.")
    admin_key = input("Enter your desired Admin API Key (or leave blank to generate a secure random one): ")
    if not admin_key:
        admin_key = secrets.token_hex(32)
        print("\n==================================================================")
        print("    ✅ Your new, randomly generated Admin API Key is:")
        print(f"    {admin_key}")
        print("\n    ** IMPORTANT: Please copy this key and save it securely. **")
        print("==================================================================")
        input("Press Enter to continue after you have saved the key...")
    env_content.append(f'ADMIN_API_KEY="{admin_key}"')

    print("\nPlease choose a port for the proxy service.")
    proxy_port = input("Enter the port number [default: 8000]: ")
    if not proxy_port.isdigit():
        proxy_port = "8000"
    
    proxy_host = f"http://localhost:{proxy_port}"
    
    initial_token = asyncio.run(fetch_initial_token(hku_email, hku_password))
    if not initial_token:
        print("❌ Could not get initial token. It will be fetched when the service starts.")
    
    env_content.append('\n# --- Initial HKU Auth Token ---')
    env_content.append(f'HKU_AUTH_TOKEN="{initial_token or ""}"')
    
    env_content.append("\n# --- Auto-Renewal & Alert Settings ---")
    env_content.append("TOKEN_REFRESH_INTERVAL_MINUTES=60")
    env_content.append("EMAIL_ALERT_FAILURES=3")
    env_content.append(f'PROXY_PORT={proxy_port}')
    env_content.append(f'PROXY_HOST="{proxy_host}"')
    
    setup_email = input("\nDo you want to set up email alerts for MFA notifications? (y/n): ").lower()
    if setup_email == 'y':
        print("\nPlease provide your Gmail details for sending alerts.")
        alert_to = input("Enter the email address where you want to RECEIVE alerts: ")
        alert_from = input("Enter the Gmail account the proxy will use to SEND alerts from: ")
        alert_password = getpass.getpass("Your Gmail App Password for the sending account (will be hidden): ")
        env_content.append("\n# --- EMAIL ALERT SETTINGS (for Gmail App Password) ---")
        env_content.append(f'ALERT_EMAIL_TO="{alert_to}"')
        env_content.append(f'ALERT_EMAIL_FROM="{alert_from}"')
        env_content.append(f'ALERT_EMAIL_PASSWORD="{alert_password}"')
        env_content.append('SMTP_SERVER="smtp.gmail.com"')
        env_content.append('SMTP_PORT=587')

    try:
        with open(".env", "w") as f:
            f.write("\n".join(env_content))
        print("\n✅ Successfully created .env file.")
        return True
    except IOError as e:
        print(f"❌ Error writing .env file: {e}")
        return False

def start_docker_service():
    """Builds and starts the Docker service."""
    print("\n--- Building and Starting the Proxy Service ---")
    print("This may take a few minutes...")
    if not run_command("docker-compose up --build -d", "Failed to build or start the Docker container."):
        return False
    
    config = {}
    if os.path.exists('.env'):
        with open('.env', 'r') as f:
            for line in f:
                if '=' in line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    config[key.strip()] = value.strip()
    port = config.get("PROXY_PORT", "8000")
    
    print("\n🎉 Success! The HKU ChatGPT Proxy is now running in the background.")
    print(f"Your OpenAI-compatible endpoint is available at: http://localhost:{port}")
    print("You can view logs with the command: docker-compose logs -f")
    return True

def main():
    """Main function that orchestrates the entire setup process."""
    print("=====================================================")
    print("  Welcome to the HKU ChatGPT Proxy Setup Script!  ")
    print("=====================================================")
    
    if not check_prerequisites(): sys.exit(1)
    if not install_local_dependencies(): sys.exit(1)
    if not create_env_file(): sys.exit(1)
    if not start_docker_service(): sys.exit(1)

if __name__ == "__main__":
    main()
