#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# Update mode: pull latest from git before installing
if [ "${1:-}" = "--update" ] || [ "${1:-}" = "-u" ]; then
    echo "=== Emberwalk Update ==="
    echo ""
    cd "$SCRIPT_DIR"
    if [ -d ".git" ]; then
        BEFORE=$(git rev-parse HEAD)
        git pull --ff-only
        AFTER=$(git rev-parse HEAD)
        if [ "$BEFORE" = "$AFTER" ]; then
            echo "Already up to date."
            exit 0
        fi
        echo ""
        git log --oneline "$BEFORE".."$AFTER"
        echo ""
    else
        echo "ERROR: not a git repo — clone from GitHub first."
        exit 1
    fi
fi

echo "=== Emberwalk Installer ==="
echo ""

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.10+ first."
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python: $PY_VERSION"

# Create venv
if [ -d "$VENV_DIR" ]; then
    echo "Venv exists at $VENV_DIR, reinstalling dependencies..."
else
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

echo "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"

# Install Playwright Chromium
echo "Installing Playwright Chromium browser..."
"$VENV_DIR/bin/python3" -m playwright install chromium

# Verify
echo ""
echo "Verifying installation..."
"$VENV_DIR/bin/python3" -c "
import ddgs, requests, trafilatura, html2text, bs4, mcp, pypdf
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
print('  All imports OK')
"

echo ""
echo "=== Installation complete ==="
echo ""
echo "Usage:"
echo "  CLI:  $VENV_DIR/bin/python3 $SCRIPT_DIR/emberwalk.py \"your query\" --results 10"
echo "  MCP:  $VENV_DIR/bin/python3 $SCRIPT_DIR/emberwalk.py --serve"
echo ""
echo "To add as Claude Code MCP server, add to ~/.claude/.mcp.json:"
echo ""
echo "  \"emberwalk\": {"
echo "    \"command\": \"$VENV_DIR/bin/python3\","
echo "    \"args\": [\"$SCRIPT_DIR/emberwalk.py\", \"--serve\"]"
echo "  }"
echo ""
