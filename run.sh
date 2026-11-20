#!/usr/bin/env sh
# Start the checker (Linux/macOS). Installs the one dependency (aiohttp) the first time.
cd "$(dirname "$0")" || exit 1
python3 -c "import aiohttp" 2>/dev/null || python3 -m pip install --quiet aiohttp
exec python3 checker.py "$@"
