#!/bin/bash

# Usage: ./check_env_refs.sh [start_dir] [env_file]
# Example: ./check_env_refs.sh . .env

STARTDIR="${1:-.}"
ENVFILE="${2:-.env}"

# Get all keys in .env (no comments, no blank lines)
env_keys=$(awk -F= '!/^#|^$/ {print $1}' "$ENVFILE" | sort | uniq)
declare -A found_ref

# Recursively scan all .py files under STARTDIR
mapfile -t pyfiles < <(find "$STARTDIR" -type f -name "*.py")

for key in $env_keys; do
    found=0
    for pyfile in "${pyfiles[@]}"; do
        # Match either os.getenv('KEY'), os.getenv("KEY"), or direct raw string KEY
        grep -q "os.getenv(['\"]$key['\"]" "$pyfile" || grep -q "$key" "$pyfile"
        if [ $? -eq 0 ]; then
            found=1
            found_ref[$key]=1
            break
        fi
    done
    if [ $found -eq 0 ]; then
        found_ref[$key]=0
    fi
done

notfound=0
for key in $env_keys; do
    if [ "${found_ref[$key]}" == "0" ]; then
        [ $notfound -eq 0 ] && echo "❌ Unused .env keys (not referenced in any .py file):"
        echo "   $key"
        notfound=1
    fi
done

if [ $notfound -eq 0 ]; then
    echo "✅ All .env keys are referenced in Python code under $STARTDIR."
fi



# Instruction for running this script
# In macOS terminal:

# 1. Make executable:
# chmod +x check_env_refs.sh

# 2. Run from your project root (with default .env and current directory):
# ./check_env_refs.sh
# Or specify .env/start dir:
# ./check_env_refs.sh my/source/dir my/.env



# ./check_env_shell.sh
