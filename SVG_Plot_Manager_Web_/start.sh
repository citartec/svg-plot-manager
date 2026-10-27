#!/usr/bin/env bash
# Launch a LOCAL browser interface for SVG Plot Manager.
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
if ! python3 -c 'import lxml' 2>/dev/null; then
    echo 'Missing Python lxml. Install it on Ubuntu with:'
    echo '  sudo apt install python3-lxml'
    exit 1
fi
exec python3 web_app.py "$@"
