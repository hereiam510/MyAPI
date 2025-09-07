import re

# --- CONFIGURE THESE ---
env_path = ".env"
code_path = "main.py"

# Read keys from .env
with open(env_path, "r") as f:
    env_content = f.readlines()
env_keys = set()
for line in env_content:
    line = line.strip()
    if "=" in line and not line.startswith("#") and line:
        key = line.split("=")[0].strip()
        env_keys.add(key)

# Scan code for os.getenv + direct env usage
with open(code_path, "r") as f:
    code = f.read()

# Find all os.getenv("KEY") or os.getenv('KEY')
matches = set(re.findall(r"os\.getenv\(['\"]([A-Za-z0-9_\-]+)['\"]", code))

# Find all plain usage (advanced, optional)
for key in env_keys:
    if key in code:
        matches.add(key)

# Report missing keys
missing = env_keys - matches

if missing:
    print("The following .env keys exist but are not referenced in {}:".format(code_path))
    for k in missing:
        print("  {}".format(k))
else:
    print("✅ All .env keys are used in your code (via os.getenv or direct string match).")
