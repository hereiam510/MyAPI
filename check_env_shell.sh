#!/bin/bash

ENVFILE=".env"
PYFILE="main.py"

missing=""
for key in $(awk -F= '!/^#|^$/ {print $1}' $ENVFILE); do
    # Search for os.getenv or raw usage
    grep -q "os.getenv('$key'" $PYFILE || grep -q "os.getenv(\"$key\"" $PYFILE || grep -q "$key" $PYFILE
    if [ $? -ne 0 ]; then
        missing="$missing\n$key"
    fi
done

if [ -z "$missing" ]; then
    echo "✅ All .env keys are referenced in $PYFILE."
else
    echo "The following .env keys exist but are not referenced in $PYFILE:"
    echo -e "$missing"
fi


# Instruction for running this script
# In macOS terminal:
# chmod +x check_env_shell.sh
# ./check_env_shell.sh
