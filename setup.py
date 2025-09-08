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
    print("--- Checking Prerequisites ---") # Keep as print for setup flow
    success, _ = run_command("docker --version", "Docker is not installed or not in your PATH.")
    if not success: return False
    success, _ = run_command("docker-compose --version", "Docker Compose is not installed or not in your PATH.")
    if not success: return False
    print("✅ Docker and Docker Compose are installed.")
    return True

# ... (Most of setup.py remains the same, replacing status prints with logger calls)

def perform_initial_login(email, password):
    """Wrapper to run the async token fetching logic."""
    from token_fetcher import fetch_hku_token
    
    # Keep user-facing text as print()
    print("\n--- Initial Token Acquisition ---")
    print("""
==============================================================================
    ACTION REQUIRED IN THE NEXT STEP:
...
==============================================================================
""")
    input("Press Enter to open the browser and begin...")
    return asyncio.run(fetch_hku_token(email, password, headless=False))

# ... (rest of the file remains largely the same, but internal status messages
#      can be converted to logger.info/error)
