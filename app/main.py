"""AeroPulse FastAPI app — http://127.0.0.1:8004"""
import json
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")

from . import db, pipeline  # noqa: E402

PORT = 8004


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    yield


app = FastAPI(title="AeroPulse", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


class ForecastReq(BaseModel):
    city: str
    profile: str = "General Adult"


class KeysReq(BaseModel):
    GROQ_API_KEY: str | None = None


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/quota")
async def quota(request: Request):
    return db.rate_status(_ip(request))


@app.post("/api/forecast")
async def forecast(req: ForecastReq, request: Request):
    allowed, remaining, reset = db.rate_hit(_ip(request))

    async def gen():
        refunded = False
        rem = remaining
        if not allowed:
            yield json.dumps({"type": "error", "code": "rate_limited", "remaining": 0,
                              "message": f"Free demo limit reached (15/hour). Try again in {reset // 60 + 1} min."}) + "\n"
            return
        async for ev in pipeline.run(req.city, req.profile):
            if ev["type"] == "error" and ev.get("node") in (1, 2) and not refunded:
                db.rate_refund(_ip(request))  # failed lookups don't consume quota
                refunded = True
                rem = min(db.RATE_LIMIT, rem + 1)
            ev["quota_remaining"] = rem
            yield json.dumps(ev, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.get("/api/health")
async def health():
    return {"status": "ok", "port": PORT}


@app.delete("/api/forecast/{fid}")
async def delete_forecast(fid: int):
    db.delete_forecast(fid)
    return {"deleted": fid}


@app.get("/api/history")
async def history(limit: int = 50):
    return db.list_forecasts(max(1, min(limit, 200)))


@app.get("/api/forecast/{fid}")
async def get_forecast(fid: int):
    f = db.get_forecast(fid)
    if not f:
        raise HTTPException(404, "Forecast not found")
    return f


@app.get("/api/compare")
async def compare(a: int, b: int):
    fa, fb = db.get_forecast(a), db.get_forecast(b)
    if not fa or not fb:
        raise HTTPException(404, "Forecast not found")
    keys = ["us_aqi", "pm2_5", "pm10", "no2", "o3"]
    delta = {k: (None if fa["current"].get(k) is None or fb["current"].get(k) is None
                 else round(fb["current"][k] - fa["current"][k], 1)) for k in keys}
    na, nb = f"{fa['location']['name']}, {fa['location']['country']}", f"{fb['location']['name']}, {fb['location']['country']}"
    aa, ab = fa["current"].get("us_aqi") or 0, fb["current"].get("us_aqi") or 0
    if aa == ab:
        rec = f"{na} and {nb} have identical US AQI right now."
    else:
        safer, worse, lo, hi = (na, nb, aa, ab) if aa < ab else (nb, na, ab, aa)
        rec = (f"{safer} has safer air right now (US AQI {lo:.0f} vs {hi:.0f}, "
               f"{(hi - lo):.0f} points lower than {worse}).")
    summ = lambda f: {k: f[k] for k in ("id", "location", "profile", "current", "category", "analysis", "generated_at")}  # noqa: E731
    return {"a": summ(fa), "b": summ(fb), "delta_b_minus_a": delta, "recommendation": rec}


@app.get("/api/settings")
async def get_settings():
    k = db.get_keys()
    return {n: {"set": bool(v), "hint": ("…" + v[-4:]) if v else ""} for n, v in k.items()}


@app.post("/api/settings")
async def save_settings(req: KeysReq):
    for name in ("GROQ_API_KEY",):
        v = getattr(req, name)
        if v is not None:
            db.set_setting(name, v.strip())
    return await get_settings()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=PORT)
