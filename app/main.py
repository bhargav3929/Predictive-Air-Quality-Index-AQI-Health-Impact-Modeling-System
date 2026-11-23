"""AeroPulse FastAPI app. Port comes from .env (PORT, default 8004)."""
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, db, llm, pipeline
from .tickets import verify_ticket

BASE = config.BASE
SAMPLES = BASE / "samples"
log = logging.getLogger("aeropulse")

TICKET_STATUS = {"TICKET_REQUIRED": 401, "TICKET_INVALID": 401, "TICKET_EXPIRED": 401,
                 "TICKET_USED_UP": 429, "DAILY_CAP_REACHED": 429}
TICKET_MSG = {
    "TICKET_REQUIRED": "Sign in on lastsem.com to try your own input.",
    "TICKET_INVALID": "This demo ticket isn't valid. Go back to lastsem.com and start again.",
    "TICKET_EXPIRED": "This demo ticket has expired. Go back to lastsem.com and start again.",
    "TICKET_USED_UP": "You've used all live runs on this ticket. Sample forecasts are still available.",
    "DAILY_CAP_REACHED": "The demo has reached its daily limit. Try again tomorrow or use the samples.",
}


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    if config.DEMO_MODE and not config.DEMO_TICKET_SECRET:
        log.warning("DEMO_MODE is on but DEMO_TICKET_SECRET is empty: every ticket is invalid, samples only.")
    yield


app = FastAPI(title="AeroPulse", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


def _err(status: int, code: str, message: str):
    return JSONResponse({"error": message, "code": code}, status_code=status)


@app.exception_handler(AppError)
async def _app_error(_: Request, e: AppError):
    return _err(e.status, e.code, e.message)


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, e: StarletteHTTPException):
    if e.status_code == 404:
        return _err(404, "NOT_FOUND", "That page or record was not found.")
    if e.status_code == 413:
        return _err(413, "TOO_LARGE", "That input is too large.")
    if e.status_code < 500:
        return _err(e.status_code, "BAD_INPUT", "That request isn't valid. Check it and try again.")
    return _err(502, "AI_FAILED", "Something went wrong on the server. Please try again.")


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, e: RequestValidationError):
    return _err(400, "BAD_INPUT", "The request was missing a valid field. Check your input and try again.")


@app.exception_handler(Exception)
async def _unhandled(_: Request, e: Exception):
    log.exception("unhandled error")
    return _err(502, "AI_FAILED", "Something went wrong on the server. Please try again.")


# ---------------------------------------------------------------- helpers
def _ticket(request: Request):
    return verify_ticket(request.headers.get("x-lastsem-ticket"), config.DEMO_TICKET_SECRET, config.PROJECT_SLUG)


def owner_of(request: Request) -> str:
    """Download version: one shared local history (''). Demo: private per ticket id."""
    if not config.DEMO_MODE:
        return ""
    payload, code = _ticket(request)
    return payload["tid"] if payload else "-none-"


def require_local_change(request: Request):
    peer = request.client.host if request.client else ""
    if peer not in ("127.0.0.1", "::1", "localhost") or request.headers.get("x-forwarded-for") \
            or request.headers.get("forwarded"):
        raise AppError(403, "KEYS_LOCAL_ONLY", "API keys can only be changed from the computer running the app.")


def keys_enabled():
    if config.DEMO_MODE:
        raise AppError(404, "NOT_FOUND", "That page or record was not found.")


class ForecastReq(BaseModel):
    city: str
    profile: str = "General Adult"


class KeysReq(BaseModel):
    groq_api_key: str


# ---------------------------------------------------------------- standard routes
@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/info")
async def info(request: Request):
    key, _src = db.resolve_groq_key()
    demo = None
    if config.DEMO_MODE:
        payload, _code = _ticket(request)
        runs_left = max(0, int(payload.get("runs", 0)) - db.ticket_runs(payload["tid"])) if payload else 0
        demo = {"ticket_valid": bool(payload), "runs_left": runs_left,
                "daily_cap_reached": db.daily_runs() >= config.DEMO_DAILY_CAP}
    return {
        "name": config.PROJECT_NAME, "slug": config.PROJECT_SLUG, "number": config.PROJECT_NUMBER,
        "version": config.VERSION, "mode": "demo" if config.DEMO_MODE else "local",
        "ai": {"provider": "groq", "ready": bool(key), "model": config.GROQ_MODEL},
        "limits": {"max_upload_mb": config.DEMO_MAX_UPLOAD_MB if config.DEMO_MODE else config.MAX_UPLOAD_MB},
        "demo": demo,
    }


def _sample_list():
    out = []
    for f in sorted(SAMPLES.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


@app.get("/api/samples")
async def samples():
    return {"samples": [
        {"id": s["id"], "title": s["title"], "input_text": s["input_text"], "profile": s["profile"],
         "has_saved_result": (SAMPLES / "results" / f"{s['id']}.json").is_file()}
        for s in _sample_list()]}


@app.get("/api/samples/{sid}")
async def sample(sid: str):
    s = next((x for x in _sample_list() if x["id"] == sid), None)
    if not s:
        raise AppError(404, "NOT_FOUND", "That sample doesn't exist.")
    rf = SAMPLES / "results" / f"{sid}.json"
    result = json.loads(rf.read_text(encoding="utf-8")) if rf.is_file() else None
    return {**s, "result": result}


@app.get("/api/keys")
async def get_keys():
    keys_enabled()
    key, src = db.resolve_groq_key()
    return {"groq": {"configured": bool(key), "source": src, "masked": config.mask(key) if key else ""}}


@app.post("/api/keys")
async def save_keys(req: KeysReq, request: Request):
    keys_enabled()
    require_local_change(request)
    k = req.groq_api_key.strip()
    if not config.key_usable(k):
        raise AppError(400, "BAD_INPUT", "That doesn't look like a Groq key. It should start with gsk_ and have no spaces.")
    db.set_setting("GROQ_API_KEY", k)
    return {"saved": ["groq"]}


@app.delete("/api/keys/groq")
async def delete_key(request: Request):
    keys_enabled()
    require_local_change(request)
    db.delete_setting("GROQ_API_KEY")
    return {"removed": "groq"}


# ---------------------------------------------------------------- feature routes
@app.post("/api/forecast")
async def forecast(req: ForecastReq, request: Request):
    ticket = None
    if config.DEMO_MODE:
        ticket, code = _ticket(request)
        if not ticket:
            raise AppError(TICKET_STATUS[code], code, TICKET_MSG[code])
        if db.ticket_runs(ticket["tid"]) >= int(ticket.get("runs", 0)):
            raise AppError(429, "TICKET_USED_UP", TICKET_MSG["TICKET_USED_UP"])
        if db.daily_runs() >= config.DEMO_DAILY_CAP:
            raise AppError(429, "DAILY_CAP_REACHED", TICKET_MSG["DAILY_CAP_REACHED"])

    owner = ticket["tid"] if ticket else ""
    key, _src = db.resolve_groq_key()
    if not key:
        msg = ("The demo AI is not available right now. The samples still work." if config.DEMO_MODE
               else llm.MESSAGES["AI_NOT_CONFIGURED"])
        raise AppError(503, "AI_NOT_CONFIGURED", msg)

    async def gen():
        # A run is recorded against a demo ticket only after it succeeds.
        async for ev in pipeline.run(req.city, req.profile, owner, key):
            if ev["type"] == "result" and ticket and not ev["data"]["provider"].get("error_code"):
                db.record_run(ticket["tid"])
            yield json.dumps(ev, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.delete("/api/forecast/{fid}")
async def delete_forecast(fid: int, request: Request):
    db.delete_forecast(fid, owner_of(request))
    return {"deleted": fid}


@app.get("/api/history")
async def history(request: Request, limit: int = 50):
    return db.list_forecasts(max(1, min(limit, 200)), owner_of(request))


@app.get("/api/forecast/{fid}")
async def get_forecast(fid: int, request: Request):
    f = db.get_forecast(fid, owner_of(request))
    if not f:
        raise AppError(404, "NOT_FOUND", "Forecast not found.")
    return f


@app.get("/api/compare")
async def compare(a: int, b: int, request: Request):
    owner = owner_of(request)
    fa, fb = db.get_forecast(a, owner), db.get_forecast(b, owner)
    if not fa or not fb:
        raise AppError(404, "NOT_FOUND", "Forecast not found.")
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT)
