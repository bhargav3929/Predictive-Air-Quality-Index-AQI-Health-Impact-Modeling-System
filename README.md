# AeroPulse — Real-Time Air Quality Forecasting & Respiratory Health Advisory System

Local MVP served at **http://127.0.0.1:8004** (dedicated port — never conflicts with 8001/8002/8003).

## Features
- **5-node streaming pipeline** (NDJSON): Geocoder → live Open-Meteo telemetry → 72-hour trend/peak analyzer → Groq-only AI advisory (`openai/gpt-oss-120b` → `qwen/qwen3.8-27b` → `llama-3.3-70b-versatile` fallback, 120 s circuit breaker, deterministic rules fallback without a key) → SQLite persistence (WAL).
- **3 views:** Live Forecast & Advisory · City vs. City Compare · Clinical Health Brief (copy / download `.md`).
- 4 one-click presets: New Delhi (asthma), Hyderabad (runner), Zurich (alpine baseline), Beijing (elderly/cardiac).
- Server-side SQLite sliding-window rate limit: 15 requests / 3600 s / IP. Forecast history drawer. BYOK API-keys modal (SQLite `settings`, `.env` fallback).
- Live 0.1 s timer and collapsible *Execution & Debug Logs* (per-node ms, API latency, AI provider/model, raw NDJSON).
- Without any AI key, a deterministic rules-based advisory is used so the app still works.
- Pure-Python dependencies only: `fastapi`, `uvicorn`, `httpx`, `python-dotenv`, `python-multipart`.

## Configuration
Copy `.env.example` to `.env` (optional) or paste keys in the **API Keys** modal.

## Launch (2 commands)

**Windows**
```
cd 04-aeropulse-aqi-health
start.bat
```

**macOS / Linux**
```
cd 04-aeropulse-aqi-health
bash start.sh
```

Then open http://127.0.0.1:8004

Run the commands

Windows (PowerShell):
python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8004

macOS / Linux (Terminal):
python3 -m venv .venv && ./.venv/bin/python -m pip install -r requirements.txt && ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8004
