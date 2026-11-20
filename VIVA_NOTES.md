# AeroPulse: viva notes

## One-line answer
AeroPulse turns live air-quality data into a 72-hour forecast and a profile-specific respiratory advisory written by Groq AI.

## Pipeline (5 nodes, streamed as NDJSON)
1. **Geocoder:** city name → latitude/longitude (Open-Meteo geocoding).
2. **Telemetry:** current + hourly US AQI, PM2.5, PM10, NO2, O3 (Open-Meteo air quality, no key).
3. **Analyzer (`analyze()` in `app/pipeline.py`):** 24/48/72 h means and peaks, dominant pollutant (ratio to WHO 2021 guideline), trend, best and worst 3-hour windows. Pure Python, deterministic.
4. **Advisory:** Groq writes JSON (headline, mask protocol, HEPA, exercise, symptoms). A rules-based advisory is the fallback.
5. **Persistence:** forecast saved to SQLite (WAL); a Markdown health brief is built.

## Design decisions to explain
- **Groq only.** One module (`app/llm.py`): 40 s timeout, 3 attempts per model with 1/2/4 s backoff, honours "try again in Xs", falls back to `GROQ_FALLBACK_MODEL`, turns failures into plain messages (`AI_NOT_CONFIGURED`, `AI_KEY_INVALID`, `AI_BUSY`, `AI_FAILED`).
- **Key handling.** A key counts only if it starts with `gsk_` and is not the placeholder. Saved key first, then `.env`. Changes are accepted only from the same machine (no `X-Forwarded-For`). The key is never sent back unmasked.
- **Failed runs are free.** The hourly quota (15 per IP, sliding window in SQLite) is refunded when geocoding, telemetry or the AI fails.
- **Same codebase, two versions.** `DEMO_MODE` switches on signed-ticket checks, a daily cap, per-visitor history and hides the keys screen. The student download keeps it off.
- **Relative URLs.** The frontend calls `api/...` so it works under a sub-path.
- **Windows-safe.** Pure-Python packages only (`fastapi`, `uvicorn`, `httpx`, `python-dotenv`, `python-multipart`) and `python -m uvicorn`, so no blocked compiled DLLs.
- **Samples.** `samples/` holds inputs; `samples/results/` holds saved output of real runs, shown instantly without calling Groq.

## Standard routes
`GET /health`, `GET /api/info`, `GET /api/samples`, `GET /api/samples/{id}`, `GET/POST /api/keys`, `DELETE /api/keys/groq`. Feature routes: `POST /api/forecast` (stream), `GET /api/history`, `GET /api/forecast/{id}`, `GET /api/compare`, `GET /api/quota`. Errors always look like `{"error": "...", "code": "..."}`.

## Likely questions
- *Why is it not a medical device?* It gives general guidance from public data; the brief says so.
- *What if Open-Meteo is down?* The run stops at node 1 or 2 with a clear message and is not counted.
- *Why a rules fallback?* So the app still works with no key or when Groq is overloaded.
- *Why WHO thresholds for the dominant pollutant?* They give one common scale to compare pollutants with different units.
