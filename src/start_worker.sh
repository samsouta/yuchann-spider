#!/bin/bash
# start_worker.sh - read paths from .env

set -e

# Load .env file (default to .env in current folder)
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
else
    echo ".env file not found!"
    exit 1
fi

echo "Starting worker at $WORKER_PATH using venv $VENV_PATH"

# Activate virtual environment
source "$VENV_PATH/bin/activate"

# Go to project directory
cd "$WORKER_PATH"

# Run worker
python main.py