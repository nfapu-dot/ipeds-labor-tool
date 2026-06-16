#!/usr/bin/env bash
# Launches the v2 IPEDS Completions + Labor Market web app.
#
# Double-click this file in Finder to start the v2 tool.
# A Terminal window will open, the app will start, and your browser
# will open to http://localhost:8502 (different port than v1).
#
# v1 lives at `Launch IPEDS Tool.command` and remains unaffected — you
# can run both apps side-by-side.
#
# To stop the app: close this Terminal window, or press Ctrl+C in it.

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

cat <<'BANNER'
────────────────────────────────────────────────────────────
  APU · IPEDS Completions + Labor (v2)
────────────────────────────────────────────────────────────
  Starting the v2 web app on port 8502…
  Browser will open to http://localhost:8502

  v1 app is unaffected — launch it separately via
  "Launch IPEDS Tool.command" if you want both running.

  To stop: close this window or press Ctrl+C
────────────────────────────────────────────────────────────
BANNER

# Port 8502 leaves v1's default port 8501 free for the v1 app.
exec python3 -m streamlit run src/app_v2.py --server.port 8502
