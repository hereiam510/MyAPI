# setup.py
import subprocess
import sys
import os
import secrets
import getpass

def run_command(command, error_message):
    """Runs a command and exits if it fails."""
    try:
        subprocess.run(command, check=True, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
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

def create_env_file():
    """Interactively gathers user input and creates the .env file."""
    print("\n--- Configuring .env File ---")
    
    if os.path.exists(".env"):
        overwrite = input("⚠️ A .env file already exists. Do you want to overwrite it? (y/n): ").lower()
        if overwrite != 'y':
            print("Exiting setup without changes.")
            return False

    env_content = []
    
    # HKU Credentials
    print("\nPlease enter your HKU Portal credentials (for auto-token renewal).")
    hku_email = input("Enter your HKU email (e.g., yourhkuid@connect.hku.hk): ")
    hku_password = getpass.getpass("Enter your HKU password (will be hidden): ")
    env_content.append(f'HKU_EMAIL="{hku_email}"')
    env_content.append(f'HKU_PASSWORD="{hku_password}"')

    # Admin API Key
    print("\nPlease set an Admin API Key to secure the proxy's admin endpoints.")
    admin_key = input("Enter your desired Admin API Key (leave blank to generate a random one): ")
    if not admin_key:
        admin_key = secrets.token_hex(32)
        print(f"🔑 Generated secure Admin API Key: {admin_key}")
    env_content.append(f'ADMIN_API_KEY="{admin_key}"')

    # Default settings
    env_content.append("\n# --- Auto-Renewal & Alert Settings ---")
    env_content.append("TOKEN_REFRESH_INTERVAL_MINUTES=60")
    env_content.append("EMAIL_ALERT_FAILURES=3")
    env_content.append('PROXY_HOST="http://localhost:8000"')

    # Email Alerts
    setup_email = input("\nDo you want to set up email alerts for MFA notifications? (y/n): ").lower()
    if setup_email == 'y':
        print("\nPlease provide your Gmail details for sending alerts.")
        print("NOTE: You must use a 16-character 'App Password' from Google, not your regular password.")
        print("See: https://support.google.com/accounts/answer/185833")
        alert_to = input("Email address to send alerts TO: ")
        alert_from = input("Your Gmail address to send alerts FROM: ")
        alert_password = getpass.getpass("Your Gmail App Password (will be hidden): ")
        
        env_content.append("\n# --- EMAIL ALERT SETTINGS (for Gmail App Password) ---")
        env_content.append(f'ALERT_EMAIL_TO="{alert_to}"')
        env_content.append(f'ALERT_EMAIL_FROM="{alert_from}"')
        env_content.append(f'ALERT_EMAIL_PASSWORD="{alert_password}"')
        env_content.append('SMTP_SERVER="smtp.gmail.com"')
        env_content.append('SMTP_PORT=587')
    
    # Initial Token (Optional)
    env_content.append('\n# --- Initial HKU Auth Token (Optional) ---')
    env_content.append('# This will be fetched automatically on the first run.')
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
    """Installs local Python and Playwright dependencies."""
    print("\n--- Installing Local Dependencies for MFA Script ---")
    if not run_command(f"{sys.executable} -m pip install -r requirements.txt", "Failed to install Python packages from requirements.txt."):
        return False
    print("✅ Python packages installed.")
    
    if not run_command("playwright install", "Failed to install Playwright browsers."):
        return False
    print("✅ Playwright browsers installed.")
    return True

def start_docker_service():
    """Builds and starts the Docker service."""
    print("\n--- Building and Starting the Proxy Service ---")
    print("This may take a few minutes on the first run...")
    if not run_command("docker-compose up --build -d", "Failed to build or start the Docker container."):
        return False
    print("\n🎉 Success! The HKU ChatGPT Proxy is now running in the background.")
    print("Your OpenAI-compatible endpoint is available at: http://localhost:8000")
    print("You can view logs with the command: docker-compose logs -f")
    return True

def main():
    """Main function to run all setup steps."""
    print("=====================================================")
    print("  Welcome to the HKU ChatGPT Proxy Setup Script!  ")
    print("=====================================================")
    
    if not check_prerequisites():
        sys.exit(1)
        
    if not create_env_file():
        sys.exit(1)

    if not install_local_dependencies():
        sys.exit(1)
        
    if not start_docker_service():
        sys.exit(1)

if __name__ == "__main__":
    main()
