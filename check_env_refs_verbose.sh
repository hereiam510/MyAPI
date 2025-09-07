#!/bin/bash

# Usage: ./check_env_refs_verbose.sh [start_dir] [env_file]
# Example: ./check_env_refs_verbose.sh . .env

STARTDIR="${1:-.}"
ENVFILE="${2:-.env}"

# Get all keys in .env (skip comments and empties)
env_keys=$(awk -F= '!/^#|^$/ {print $1}' "$ENVFILE" | sort | uniq)

# Find all py files recursively
mapfile -t pyfiles < <(find "$STARTDIR" -type f -name "*.py")

all_used=1

for key in $env_keys; do
    found_files=()
    for pyfile in "${pyfiles[@]}"; do
        # Match: os.getenv('KEY') or os.getenv("KEY") or direct bare KEY
        if grep -q "os.getenv(['\"]$key['\"]" "$pyfile" || grep -q "$key" "$pyfile"; then
            found_files+=("$pyfile")
        fi
    done

    if [ ${#found_files[@]} -gt 0 ]; then
        echo "✅ $key referenced in:"
        for f in "${found_files[@]}"; do
            echo "    $f"
        done
    else
        echo "❌ $key  -->  NOT REFERENCED IN ANY .py FILE"
        all_used=0
    fi
done

if [ "$all_used" -eq 1 ]; then
    echo ""
    echo "✅ ALL .env keys are referenced in Python code under $STARTDIR."
else
    echo ""
    echo "⚠️  Some .env keys are not referenced in your Python code."
fi

# How to use
# In macOS terminal
# Make it executable:
# chmod +x check_env_refs_verbose.sh
# Run:
# ./check_env_refs_verbose.sh
# or specify subdir and env file:
# ./check_env_refs_verbose.sh src/ custom.env

