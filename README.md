# AeroPulse: Real-Time Air Quality Forecasting & Respiratory Health Advisory

AeroPulse takes a city name and a health profile (for example "Asthma / Respiratory"). It pulls live air-quality data, builds a 72-hour outlook, and writes a short health advisory with AI. It is for students, health-conscious people and anyone who wants to know when it is safe to go outside.

## What you need
- **Python 3.10 or newer.** Download: https://www.python.org/downloads/ (on Windows, tick "Add python.exe to PATH").
- **A free Groq key** from https://console.groq.com/keys (about 1 minute, no card).
- An internet connection (live data comes from Open-Meteo, which needs no key).

## Run it (one command)
- **macOS / Linux:** `chmod +x start.sh && ./start.sh`
- **Windows:** double-click `start.bat` (or run it in a terminal).

The first run installs everything and creates a `.env` file for you. Your browser then opens at **http://127.0.0.1:8004**. The next runs start in seconds.

## Add your Groq key
Click **API Keys** in the app and paste your key, or put it in `.env` as `GROQ_API_KEY=gsk_...`. Without a key the app asks you to add one before it runs a live forecast. The saved samples still open without a key.

## How to use it
1. Click one of the **Sample** buttons. A saved forecast appears instantly.
2. Click a city preset (New Delhi, Hyderabad, Zurich, Beijing) to run a live forecast.
3. Or type any city, pick a health profile and press **Run Forecast**.
4. Use the tabs: **Live Forecast & Advisory**, **City vs. City Compare** (needs two forecasts), and **Clinical Health Brief** (copy or download a `.md` report).
5. **Forecast History** lists your earlier forecasts.

## How it works
City name → **geocoder** (Open-Meteo) → **live air-quality data** (US AQI, PM2.5, PM10, NO2, O3) → **72-hour trend and peak-window analysis** → **Groq AI advisory** → **SQLite save and health brief**.
- The AI tries `GROQ_MODEL` first, then `GROQ_FALLBACK_MODEL`. Busy or slow answers are retried automatically.
- If the AI is busy or fails, you still get a simple rules-based advisory with a clear message saying so.

Main files: `app/main.py` (routes), `app/pipeline.py` (the 5 steps), `app/llm.py` (Groq calls), `app/db.py` (SQLite), `app/config.py` (settings).

## Project structure
```
start.sh / start.bat   one-command start
.env.example           settings (copied to .env on first run)
requirements.txt       Python packages (pure Python, no compiler needed)
run.py                 optional launcher: python run.py
app/                   backend (FastAPI)
  main.py  pipeline.py  llm.py  db.py  config.py
static/                frontend (index.html, css/style.css, js/app.js)
samples/               sample inputs; samples/results/ has their saved results
```

## Troubleshooting
- **Port already in use:** another program is using 8004. Close it, or change `PORT` in `.env` and run the start script again.
- **"AI service is busy":** the free Groq service is overloaded. Wait a minute and try again.
- **"Your Groq key was rejected":** open **API Keys** and paste a fresh key from https://console.groq.com/keys.
- **Python not found:** install Python 3.10+ from https://www.python.org/downloads/, then run the start script again.
- **Install failed:** check your internet connection and run the start script again. It resumes where it stopped.

## Run it by hand (optional)
If you prefer typing the commands yourself:

**Windows (PowerShell):**
```
python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8004
```

**macOS / Linux (Terminal):**
```
python3 -m venv .venv && ./.venv/bin/python -m pip install -r requirements.txt && ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8004
```
