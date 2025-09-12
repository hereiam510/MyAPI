# setup.py
import subprocess
import sys
import os
import secrets
import getpass
import shutil
import asyncio
import time
import smtplib
import logging
from email.mime.text import MIMEText
from logger_config import setup_logging

# --- Setup Logging ---
setup_logging()
logger = logging.getLogger(__name__)

# The "from token_fetcher..." import is now inside perform_initial_login

def run_command(command, error_message, capture_stdout=False):
    """Runs a command and handles success/failure, logging detailed errors."""
    try:
        process = subprocess.run(command, check=True, shell=True, capture_output=True, text=True)
        return True, process.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"Error: {error_message}")
        logger.error(f"Command Output (stderr):\n{e.stderr}")
        return False, e.stderr
    except FileNotFoundError:
        logger.error(f"Error: {error_message}. Command not found.")
        return False, ""

def check_prerequisites():
    """Checks if Docker and Docker Compose are installed."""
    print("--- Checking Prerequisites ---")
    success, _ = run_command("docker --version", "Docker is not installed or not in your PATH.")
    if not success: return False
    success, _ = run_command("docker-compose --version", "Docker Compose is not installed or not in your PATH.")
    if not success: return False
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
    
    install_cmd = f"{sys.executable} -m {installer.split()[0]} install -r requirements.txt"
    if 'uv' in installer:
        install_cmd = "uv pip install -r requirements.txt"

    error_msg = f"Failed to install Python packages using `{installer}`."
    success, _ = run_command(install_cmd, error_msg)
    if not success: return False
    print("✅ Python packages installed.")
    
    success, _ = run_command("playwright install", "Failed to install Playwright browsers.")
    if not success: return False
    print("✅ Playwright browsers installed.")
    return True

def setup_shell_alias():
    """Asks the user if they want to create a convenient shell alias for viewing traces."""
    print("\n--- Optional: Create Shell Alias ---")
    
    # Define the alias command
    alias_name = "pwt"
    alias_command = f"alias {alias_name}='playwright show-trace $(ls -1t traces/*.zip | head -n 1)'"
    
    # Explain what it does
    print(f"This script can add a shell alias '{alias_name}' to your profile.")
    print("It allows you to view the most recent Playwright trace by simply typing 'pwt'.")
    
    create_alias = input("Do you want to add this alias to your shell configuration? (y/n): ").lower()
    if create_alias != 'y':
        print("Skipping alias creation.")
        return

    # Detect shell and config file
    shell = os.environ.get("SHELL", "")
    config_file = None
    if "bash" in shell:
        config_file = os.path.expanduser("~/.bashrc")
    elif "zsh" in shell:
        config_file = os.path.expanduser("~/.zshrc")
    else:
        print(f"⚠️ Could not determine your shell or it's not supported ({shell}). Please add the following alias manually:")
        print(f"   {alias_command}")
        return

    # Check if the alias already exists
    try:
        if os.path.exists(config_file):
            with open(config_file, "r") as f:
                if alias_name in f.read():
                    print(f"✅ The '{alias_name}' alias already seems to exist in {config_file}. No changes made.")
                    return
    except IOError as e:
        logger.error(f"Error reading shell config file: {e}", exc_info=True)
        print(f"❌ Could not read {config_file}. Please add the alias manually.")
        return

    # Add the alias to the file
    try:
        with open(config_file, "a") as f:
            f.write("\n# Alias for HKU ChatGPT Proxy project to view latest trace\n")
            f.write(f"{alias_command}\n")
        print(f"\n✅ Alias '{alias_name}' has been added to {config_file}.")
        print(f"Please run `source {config_file}` or restart your terminal to use it.")
    except IOError as e:
        logger.error(f"Error writing to shell config file: {e}", exc_info=True)
        print(f"❌ Could not write to {config_file}. Please add the alias manually.")

def perform_initial_login(email, password):
    """Wrapper to run the async token fetching logic."""
    from token_fetcher import fetch_hku_token

    print("\n--- Initial Token Acquisition ---")
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
    return asyncio.run(fetch_hku_token(email, password, headless=False))

def send_test_email(to_email, from_email, password, server, port):
    """Attempts to send a test email and returns True on success, False on failure."""
    subject = "[HKU Proxy] Email Alert System Test"
    body = "This is a test message from the HKU ChatGPT Proxy setup script.\n\nIf you received this, your email alert system is configured correctly."
    
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = to_email

    try:
        smtp_server = smtplib.SMTP(server, port)
        smtp_server.starttls()
        smtp_server.login(from_email, password)
        smtp_server.sendmail(from_email, to_email, msg.as_string())
        smtp_server.quit()
        print("✅ Test email sent successfully!")
        return True
    except smtplib.SMTPAuthenticationError:
        print("❌ Failed to send test email. Error: Authentication failed.")
        print("   Please double-check your 'From' email and your 16-character App Password.")
        return False
    except Exception as e:
        logger.error(f"Failed to send test email. An unexpected error occurred: {e}", exc_info=True)
        print(f"❌ Failed to send test email. An unexpected error occurred: {e}")
        return False

def is_env_file_configured(filepath=".env"):
    """
    Checks if a .env file exists and contains actual user-entered credentials,
    not just the default template placeholders.
    """
    if not os.path.exists(filepath):
        return False
        
    placeholders = {
        "yourhkuid@connect.hku.hk",
        "your_hku_portal_password",
        "your-own-super-long-and-secret-admin-key",
        "your_alert_target@example.com",
        "your_gmail_account@gmail.com",
        "your_16_character_gmail_app_password",
        "",
    }
    
    keys_to_check = [
        "HKU_EMAIL", "HKU_PASSWORD", "ADMIN_API_KEY", 
        "ALERT_EMAIL_TO", "ALERT_EMAIL_FROM", "ALERT_EMAIL_PASSWORD"
    ]
    env_vars = {}
    try:
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'): continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip().strip('"').strip("'")
    except IOError:
        return False

    for key in keys_to_check:
        if key in env_vars and env_vars[key] not in placeholders:
            return True
            
    return False

def create_env_file():
    """Interactively gathers user input and creates the .env file."""
    print("\n--- Configuring .env File ---")
    
    if is_env_file_configured(".env"):
        overwrite = input("⚠️ A configured .env file already exists. Do you want to overwrite it with new settings? (y/n): ").lower()
        if overwrite != 'y':
            print("Skipping .env configuration.")
            return True

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

    initial_token = perform_initial_login(hku_email, hku_password)
    if not initial_token:
        logger.warning("Could not get initial token. The service will try to fetch it on its first run.")

    env_content.append('\n# --- Initial HKU Auth Token ---')
    env_content.append(f'HKU_AUTH_TOKEN="{initial_token or ""}"')
    
    print("\nPlease choose a port for the proxy service.")
    proxy_port = input("Enter the port number [default: 8000]: ")
    if not proxy_port.isdigit():
        proxy_port = "8000"
    
    proxy_host = f"http://localhost:{proxy_port}"

    print("\nPlease specify the token auto-renewal interval in minutes.")
    refresh_interval = input("Enter the refresh interval in minutes [default: 15]: ")
    if not refresh_interval.isdigit():
        refresh_interval = "15"
    
    env_content.append("\n# --- Auto-Renewal & Alert Settings ---")
    env_content.append(f"TOKEN_REFRESH_INTERVAL_MINUTES={refresh_interval}")
    env_content.append("EMAIL_ALERT_FAILURES=3")
    env_content.append(f'PROXY_PORT={proxy_port}')
    env_content.append(f'PROXY_HOST="{proxy_host}"')
    
    setup_email = input("\nDo you want to set up email alerts for MFA notifications? (y/n): ").lower()
    if setup_email == 'y':
        while True:
            print("\nPlease provide your Gmail details for sending alerts.")
            print("NOTE: You must use a 16-character 'App Password' from Google.")
            print("See: https://support.google.com/accounts/answer/185833")
            alert_to = input("Enter the email address where you want to RECEIVE alerts: ").strip().replace('\xa0', '')
            alert_from = input("Enter the Gmail account the proxy will use to SEND alerts from: ").strip().replace('\xa0', '')
            alert_password = getpass.getpass("Your Gmail App Password for the sending account (will be hidden): ").strip().replace('\xa0', '')
            
            print(f"\nAbout to send a test email to {alert_to}...")
            input("Press Enter to continue.")
            if send_test_email(alert_to, alert_from, alert_password, "smtp.gmail.com", 587):
                env_content.append("\n# --- EMAIL ALERT SETTINGS (for Gmail App Password) ---")
                env_content.append(f'ALERT_EMAIL_TO="{alert_to}"')
                env_content.append(f'ALERT_EMAIL_FROM="{alert_from}"')
                env_content.append(f'ALERT_EMAIL_PASSWORD="{alert_password}"')
                env_content.append('SMTP_SERVER="smtp.gmail.com"')
                env_content.append('SMTP_PORT=587')
                break
            else:
                retry = input("Would you like to re-enter your email settings? (y/n): ").lower()
                if retry != 'y':
                    logger.warning("Email alerts will be disabled.")
                    print("⚠️ Email alerts will be disabled.")
                    break

    try:
        with open(".env", "w") as f:
            f.write("\n".join(env_content))
        print("\n✅ Successfully created .env file.")
        return True
    except IOError as e:
        logger.error(f"Error writing .env file: {e}", exc_info=True)
        return False

def start_docker_service():
    """Builds and starts the Docker service, then verifies it is running."""
    print("\n--- Building and Starting the Proxy Service ---")
    print("This may take a few minutes...")
    while True:
        success, _ = run_command("docker-compose up --build -d", "Failed to build or start the Docker container.")
        
        if success:
            logger.info("Service starting, waiting 5 seconds to verify status...")
            time.sleep(5)
            
            verify_success, stdout = run_command(
                'docker ps --filter "name=hku_proxy_service" --filter "status=running"',
                "Failed to check service status."
            )
            
            if verify_success and "hku_proxy_service" in stdout:
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
            else:
                print("❌ Error: The container started but appears to have stopped unexpectedly.")
                print("   Please check the logs for more details using: docker-compose logs")

        retry = input("The Docker service failed to start or stay running. Would you like to try again? (y/n): ").lower()
        if retry != 'y':
            print("Exiting setup.")
            return False

def main():
    """Main function that orchestrates the entire setup process."""
    print("=====================================================")
    print("  Welcome to the HKU ChatGPT Proxy Setup Script!  ")
    print("=====================================================")
    
    if not check_prerequisites(): sys.exit(1)
    if not install_local_dependencies(): sys.exit(1)
    if not create_env_file(): sys.exit(1)
    
    # Run the service start first
    if not start_docker_service(): sys.exit(1)
    
    # Offer the optional convenience alias after the main setup is successful.
    setup_shell_alias()

if __name__ == "__main__":
    main()
