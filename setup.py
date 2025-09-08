# setup.py
import subprocess
import sys
import os
import secrets
import getpass
import shutil
import asyncio
from playwright.async_api import async_playwright
import smtplib
from email.mime.text import MIMEText

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

def is_env_file_configured(filepath=".env"):
    """
    Checks if a .env file exists and contains actual user-entered credentials,
    not just the default template placeholders.
    """
    if not os.path.exists(filepath):
        return False
        
    placeholders = {
        "yourhkuid@connect.hku.hk", "your_password",
        "your-own-super-long-and-secret-admin-key", "paste_your_long_bearer_token_here",
        "your_alert_target@example.com", "your_gmail_account@gmail.com",
        "your_16_character_gmail_app_password",
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
        if key in env_vars and env_vars[key] and env_vars[key] not in placeholders:
            return True
            
    return False
    
def send_test_email(to_email, from_email, password, server, port):
    """Attempts to send a test email and returns True on success, False on failure."""
    subject = "[HKU Proxy] Email Alert System Test"
    body = "This is a test message from the HKU ChatGPT Proxy setup script.\n\nIf you received this, your email alert system is configured correctly."
    msg = MIMEText(body)
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
    except Exception as e:
        print(f"❌ Failed to send test email. Error: {e}")
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
    
    print("\nPlease enter your HKU Portal credentials (for auto-token renewal).")
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
    
    print("\nPlease specify the token auto-renewal interval in minutes.")
    refresh_interval = input("Enter the refresh interval in minutes [default: 15]: ")
    if not refresh_interval.isdigit():
        refresh_interval = "15"

    env_content.append("\n# --- Auto-Renewal & Alert Settings ---")
    env_content.append(f"TOKEN_REFRESH_INTERVAL_MINUTES={refresh_interval}")
    env_content.append("EMAIL_ALERT_FAILURES=3")
    env_content.append(f'PROXY_PORT={proxy_port}')
    env_content.append(f'PROXY_HOST="{proxy_host}"')
    
    setup_email = input("\nDo you want to set up email alerts for Multi-Factor Authentication (MFA) notifications? (y/n): ").lower()
    if setup_email == 'y':
        while True:
            print("\nPlease provide your Gmail details for sending alerts.")
            print("NOTE: You must use a 16-character 'App Password' from Google.")
            alert_to = input("Enter the email address where you want to RECEIVE alerts: ")
            alert_from = input("Enter the Gmail account the proxy will use to SEND alerts from: ")
            alert_password = getpass.getpass("Your Gmail App Password for the sending account (will be hidden): ")
            
            # --- MODIFIED SECTION ---
            # Added a confirmation prompt before sending the email.
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
                    print("⚠️ Email alerts will be disabled.")
                    break
    
    env_content.append('\n# --- Initial HKU Auth Token (Optional) ---')
    env_content.append('HKU_AUTH_TOKEN=""')

    try:
        with open(".env", "w") as f:
            f.write("\n".join(env_content))
        print("\n✅ Successfully created .env file.")
        return True
    except IOError as e:
        print(f"❌ Error writing .env file: {e}")
        return False

def install_local_dependencies():
    """Installs local Python and Playwright dependencies, detecting `uv`."""
    print("\n--- Installing Local Dependencies for MFA Script ---")
    installer = "pip"
    if shutil.which("uv"):
        installer = "uv pip"
        print("✅ Detected `uv` package manager.")
    else:
        print("⚠️  NOTE: `uv` was not found. Falling back to standard `pip`.")
    install_cmd = f"{installer} install -r requirements.txt"
    if not run_command(install_cmd, f"Failed to install Python packages using `{installer}`."):
        return False
    print("✅ Python packages installed.")
    if not run_command("playwright install", "Failed to install Playwright browsers."):
        return False
    print("✅ Playwright browsers installed.")
    return True

def start_docker_service():
    """Builds and starts the Docker service, with a retry loop on failure."""
    print("\n--- Building and Starting the Proxy Service ---")
    print("This may take a few minutes...")
    while True:
        if run_command("docker-compose up --build -d", "Failed to build or start the Docker container."):
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
            retry = input("The Docker service failed to start. Would you like to try again? (y/n): ").lower()
            if retry != 'y':
                print("Exiting setup.")
                return False

def main():
    """Main function that orchestrates the entire setup process."""
    print("=====================================================")
    print("  Welcome to the HKU ChatGPT Proxy Setup Script!  ")
    print("=====================================================")
    if not check_prerequisites(): sys.exit(1)
    # The initial login feature has been removed as it was buggy.
    # The service will now fetch the token on its first run automatically.
    if not create_env_file(): sys.exit(1)
    if not install_local_dependencies(): sys.exit(1)
    if not start_docker_service(): sys.exit(1)

if __name__ == "__main__":
    main()
