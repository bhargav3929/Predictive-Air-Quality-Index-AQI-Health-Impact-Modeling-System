#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -x ./.venv/bin/python ] || python3 -m venv .venv
./.venv/bin/python -m pip install -q -r requirements.txt
echo "AeroPulse running at http://127.0.0.1:8004"
./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8004
