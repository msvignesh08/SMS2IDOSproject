#!/usr/bin/env bash
# SMS-2 IDOS — one-time server preparation (Ubuntu/Debian intranet server)
set -e
cd "$(dirname "$0")/.."
python3 -m pip install -r backend/requirements.txt
echo
echo "Installed. Start the system with:  ./deploy/run.sh"
echo "Then open  http://<server-ip>:8020  from any plant workstation browser."
