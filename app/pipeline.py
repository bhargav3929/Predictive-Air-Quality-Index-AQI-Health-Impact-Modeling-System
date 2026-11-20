"""5-node AeroPulse pipeline streaming NDJSON-ready event dicts."""
import asyncio
import json
import time
from datetime import datetime, timezone

import httpx

from . import db, llm

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
AQ_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
PROFILES = ["General Adult", "Asthma / Respiratory", "Elderly / Cardiac", "Children / Outdoor Athletics"]

NODES = {
    1: "Location Gatekeeper & Geocoder",
    2: "Live Multi-Pollutant Telemetry",
    3: "72-Hour Trend & Peak Window Analyzer",
    4: "AI Respiratory & Activity Advisory Synthesizer",
    5: "Clinical Brief & SQLite Persistence",
}

# WHO 2021 24h guideline values (µg/m³); O3 = peak-season 8h
WHO = {"pm2_5": 15, "pm10": 45, "no2": 25, "o3": 100}
DRIVERS = {
    "pm2_5": "PM2.5 Fine Particulates",
    "pm10": "PM10 Coarse Dust",
    "no2": "NO2 Traffic Emissions",
    "o3": "O3 Ground-level Ozone",
}


def aqi_category(v):
    if v is None:
        return {"label": "Unknown", "color": "#A8A29E"}
    if v <= 50:
        return {"label": "Good", "color": "#10B981"}
    if v <= 100:
        return {"label": "Moderate", "color": "#F59E0B"}
    if v <= 150:
        return {"label": "Unhealthy for Sensitive Groups", "color": "#F97316"}
    if v <= 200:
        return {"label": "Unhealthy", "color": "#EF4444"}
    return {"label": "Very Unhealthy", "color": "#7E22CE"}


def _clean(vals):
    return [v for v in vals if v is not None]


def _mean(vals):
    c = _clean(vals)
    return round(sum(c) / len(c), 1) if c else None


def _peak(vals):
    c = _clean(vals)
    return round(max(c), 1) if c else None


def _fmt_time(t):
    try:
        return datetime.fromisoformat(t).strftime("%a %H:%M")
    except Exception:
        return t


def analyze(h: dict, cur: dict):
    """Node 3: deterministic analytics over the hourly series."""
    times = h["time"]
    now_hour = (cur.get("time") or "")[:13]
    now = next((i for i, t in enumerate(times) if t[:13] == now_hour), 0)
    ser = {k: h.get(k, []) for k in ("us_aqi", "pm2_5", "pm10", "nitrogen_dioxide")}
    ser["no2"] = ser.pop("nitrogen_dioxide")

    windows = {}
    for n in (24, 48, 72):
        windows[f"{n}h"] = {
            k: {"mean": _mean(v[now:now + n]), "peak": _peak(v[now:now + n])} for k, v in ser.items()
        }

    # dominant driver by ratio to WHO guideline
    ratios = {}
    for k in ("pm2_5", "pm10", "no2", "o3"):
        v = cur.get(k)
        if v is not None:
            ratios[k] = v / WHO[k]
    dom = max(ratios, key=ratios.get) if ratios else "pm2_5"
    dominant = {"key": dom, "label": DRIVERS[dom], "ratio": round(ratios.get(dom, 0), 2)}

    # trend: next 24h mean vs 24-48h mean
    aqi = ser["us_aqi"]
    a = _mean(aqi[now:now + 24])
    b = _mean(aqi[now + 24:now + 48])
    delta = None
    trend = "Stable"
    if a is not None and b is not None:
        delta = round(b - a, 1)
        if delta >= max(8, a * 0.1):
            trend = "Deteriorating"
        elif delta <= -max(8, a * 0.1):
            trend = "Improving"
    elif a is not None and len(_clean(aqi[now:])) >= 6:
        seq = _clean(aqi[now:])
        delta = round(seq[-1] - seq[0], 1)
        trend = "Deteriorating" if delta > 10 else "Improving" if delta < -10 else "Stable"

    def best_window(lo, hi, lowest=True):
        best = None
        for i in range(lo, max(lo, hi - 2)):
            seg = aqi[i:i + 3]
            if len(seg) < 3 or any(x is None for x in seg):
                continue
            m = sum(seg) / 3
            if best is None or (m < best[0] if lowest else m > best[0]):
                best = (m, i)
        if best is None:
            return None
        m, i = best
        return {
            "start_idx": i, "end_idx": i + 2, "start": times[i], "end": times[min(i + 3, len(times) - 1)],
            "label": f"{_fmt_time(times[i])} – {_fmt_time(times[min(i + 3, len(times) - 1)])}",
            "mean_aqi": round(m, 1), "category": aqi_category(m),
        }

    return {
        "now_index": now,
        "windows": windows,
        "dominant": dominant,
        "trend": {"label": trend, "delta": delta, "next24_mean": a, "following24_mean": b},
        "best_window": best_window(now, now + 24, True),
        "peak_window": best_window(now, now + 72, False),
        "who": WHO,
    }


# ---------------- advisory -------------------------------------------------
_MASK_AT = {"General Adult": 150, "Asthma / Respiratory": 100, "Elderly / Cardiac": 100, "Children / Outdoor Athletics": 100}


def fallback_advisory(profile, cur, an):
    aqi = cur.get("us_aqi") or 0
    thr = _MASK_AT.get(profile, 150)
    cat = aqi_category(aqi)["label"]
    sensitive = profile != "General Adult"
    if aqi <= 50:
        act, ex = "Air quality is good — normal outdoor activity is fine.", "Unrestricted outdoor exercise."
    elif aqi <= 100:
        act = "Acceptable air; unusually sensitive people should reduce prolonged heavy exertion." if sensitive else "Acceptable air; normal activity."
        ex = "Moderate exercise outdoors is fine; shorten intense sessions if symptoms appear." if sensitive else "Normal exercise outdoors."
    elif aqi <= 150:
        act = "Limit prolonged outdoor exertion; take more breaks." if sensitive else "Reduce prolonged heavy exertion if you notice symptoms."
        ex = "Move workouts indoors or to the best outdoor window."
    else:
        act = "Avoid prolonged outdoor activity; stay indoors with filtered air."
        ex = "Exercise indoors only until AQI falls below 100."
    bw = an.get("best_window")
    return {
        "headline": f"{cat} air for {profile} — dominated by {an['dominant']['label']}",
        "risk_level": cat,
        "outdoor_activity": act + (f" Best window: {bw['label']}." if bw else ""),
        "mask_protocol": f"Wear a fitted N95/FFP2 respirator outdoors when US AQI exceeds {thr}." + (" Mask recommended now." if aqi > thr else " Not required right now."),
        "mask_threshold": f"N95/FFP2 at US AQI > {thr}",
        "indoor_hepa": "Run a HEPA purifier sized to the room; keep windows closed during peak hours." if aqi > 50 else "Ventilate normally; HEPA optional.",
        "exercise_guidance": ex,
        "symptom_watch": ["Coughing or wheeze", "Chest tightness or shortness of breath", "Eye/throat irritation", "Unusual fatigue or palpitations"],
    }


ADVISORY_KEYS = ["headline", "risk_level", "outdoor_activity", "mask_protocol", "mask_threshold", "indoor_hepa", "exercise_guidance", "symptom_watch"]

SYSTEM = (
    "You are a respiratory public-health advisor. Use only the provided air-quality data. "
    "Respond with ONE JSON object and nothing else, with keys: headline (string), risk_level (string), "
    "outdoor_activity (string), mask_protocol (string), mask_threshold (string, N95/FFP2 AQI threshold), "
    "indoor_hepa (string), exercise_guidance (string), symptom_watch (array of 3-4 short strings). "
    "Be concise (each string under 45 words), tailored to the user's vulnerability profile, not a medical diagnosis."
)


def _num(v, d=1):
    return "n/a" if v is None else f"{v:.{d}f}"


def build_brief(r: dict) -> str:
    loc, cur, an, adv = r["location"], r["current"], r["analysis"], r["advisory"]
    w = an["windows"]
    lines = [
        f"# Public Health & Respiratory Exposure Brief — {loc['name']}, {loc['country']}",
        "",
        f"*Generated {r['generated_at']} · Profile: **{r['profile']}** · Source: Open-Meteo Air Quality*",
        "",
        f"**Location:** {loc['lat']:.3f}, {loc['lon']:.3f} · elevation {_num(loc.get('elevation'), 0)} m · {loc.get('timezone')}",
        "",
        "## Current Conditions",
        f"- **US AQI:** {_num(cur.get('us_aqi'), 0)} ({r['category']['label']}) · European AQI {_num(cur.get('european_aqi'), 0)}",
        f"- **PM2.5:** {_num(cur.get('pm2_5'))} µg/m³ · **PM10:** {_num(cur.get('pm10'))} µg/m³ · **NO2:** {_num(cur.get('no2'))} µg/m³ · **O3:** {_num(cur.get('o3'))} µg/m³ · **CO:** {_num(cur.get('co'), 0)} µg/m³",
        f"- **Dominant driver:** {an['dominant']['label']} ({an['dominant']['ratio']}× WHO guideline)",
        "",
        "## 72-Hour Outlook",
        "| Window | Mean AQI | Peak AQI | Mean PM2.5 | Mean PM10 | Mean NO2 |",
        "|---|---|---|---|---|---|",
    ]
    for k in ("24h", "48h", "72h"):
        x = w[k]
        lines.append(f"| Next {k} | {_num(x['us_aqi']['mean'])} | {_num(x['us_aqi']['peak'], 0)} | {_num(x['pm2_5']['mean'])} | {_num(x['pm10']['mean'])} | {_num(x['no2']['mean'])} |")
    bw, pw = an.get("best_window"), an.get("peak_window")
    lines += [
        "",
        f"- **Trend:** {an['trend']['label']}",
        "- **Best outdoor window (next 24h):** " + (bw["label"] + " (mean AQI " + str(bw["mean_aqi"]) + ")" if bw else "n/a"),
        "- **Peak exposure hazard window:** " + (pw["label"] + " (mean AQI " + str(pw["mean_aqi"]) + ")" if pw else "n/a"),
        "",
        f"## Advisory — {r['profile']}",
        f"**{adv['headline']}**",
        "",
        f"- **Outdoor activity:** {adv['outdoor_activity']}",
        f"- **Mask protocol:** {adv['mask_protocol']} ({adv['mask_threshold']})",
        f"- **Indoor HEPA filtration:** {adv['indoor_hepa']}",
        f"- **Exercise guidance:** {adv['exercise_guidance']}",
        "",
        "**Symptom watch:**",
    ]
    lines += [f"- {s}" for s in adv["symptom_watch"]]
    lines += ["", "---", f"*Advisory source: {r['provider']['label']}. General information only — not medical advice.*"]
    return "\n".join(lines)


_SENS = {"General Adult": 1.0, "Asthma / Respiratory": 1.4, "Elderly / Cardiac": 1.3, "Children / Outdoor Athletics": 1.25}


def risk_index(profile, cur, an):
    """Personal Exposure Index 0-10: profile-weighted blend of current AQI and 24h peak."""
    aqi = cur.get("us_aqi") or 0
    peak = an["windows"]["24h"]["us_aqi"]["peak"] or aqi
    score = round(min(10.0, (0.6 * aqi + 0.4 * peak) * _SENS.get(profile, 1.0) / 40), 1)
    label = "Low" if score < 2.5 else "Elevated" if score < 5 else "High" if score < 7.5 else "Severe"
    return {"score": score, "label": label, "weight": _SENS.get(profile, 1.0)}


# ---------------- pipeline -------------------------------------------------
def _ev(type_, **kw):
    return {"type": type_, "t": round(time.time(), 3), **kw}


async def run(city: str, profile: str):
    T0 = time.perf_counter()
    timings: dict = {}
    profile = profile if profile in PROFILES else PROFILES[0]
    yield _ev("start", city=city, profile=profile, nodes=NODES)

    def done(n, t, **extra):
        ms = int((time.perf_counter() - t) * 1000)
        timings[f"node{n}"] = ms
        return _ev("node", node=n, name=NODES[n], status="done", ms=ms, **extra)

    # ---- Node 1
    city = (city or "").strip()
    yield _ev("node", node=1, name=NODES[1], status="running")
    t = time.perf_counter()
    if len(city) < 2 or len(city) > 80:
        yield _ev("error", node=1, code="invalid_city", message="Enter a city name (2–80 characters).")
        return
    name_q = city.split(",")[0].strip()
    hint = city.split(",", 1)[1].strip().lower() if "," in city else ""
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            g = await asyncio.wait_for(c.get(GEO_URL, params={"name": name_q, "count": 10 if hint else 1, "format": "json"}), 4.0)
            g.raise_for_status()
            res = (g.json().get("results") or [])
    except Exception as e:
        yield _ev("error", node=1, code="geocode_failed", message=f"Geocoding service unavailable ({type(e).__name__}).")
        return
    if not res:
        yield _ev("error", node=1, code="city_not_found", message=f"Could not geocode “{city}”. Check the spelling or try a larger nearby city.")
        return
    g0 = next((x for x in res if hint and hint in (x.get("country", "") + "|" + x.get("country_code", "") + "|" + x.get("admin1", "")).lower()), res[0])
    loc = {
        "name": g0.get("name"), "country": g0.get("country", ""), "admin1": g0.get("admin1", ""),
        "lat": g0["latitude"], "lon": g0["longitude"], "elevation": g0.get("elevation"),
        "timezone": g0.get("timezone", "auto"),
    }
    timings["geocode_api_ms"] = int((time.perf_counter() - t) * 1000)
    yield done(1, t, detail=f"{loc['name']}, {loc['country']} ({loc['lat']:.2f}, {loc['lon']:.2f}, {loc['elevation']} m)", api_ms=timings["geocode_api_ms"])

    # ---- Node 2 (+ Node 3 immediately on hourly series)
    yield _ev("node", node=2, name=NODES[2], status="running")
    t = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await asyncio.wait_for(
                c.get(AQ_URL, params={
                    "latitude": loc["lat"], "longitude": loc["lon"],
                    "current": "us_aqi,european_aqi,pm2_5,pm10,nitrogen_dioxide,ozone,carbon_monoxide",
                    "hourly": "us_aqi,pm2_5,pm10,nitrogen_dioxide",
                    "forecast_days": 3, "timezone": "auto",
                }), 5.0)
            r.raise_for_status()
            aq = r.json()
    except Exception as e:
        yield _ev("error", node=2, code="telemetry_failed", message=f"Open-Meteo air-quality telemetry failed ({type(e).__name__}).")
        return
    c_ = aq.get("current") or {}
    cur = {
        "time": c_.get("time"), "us_aqi": c_.get("us_aqi"), "european_aqi": c_.get("european_aqi"),
        "pm2_5": c_.get("pm2_5"), "pm10": c_.get("pm10"), "no2": c_.get("nitrogen_dioxide"),
        "o3": c_.get("ozone"), "co": c_.get("carbon_monoxide"),
    }
    hourly = aq.get("hourly") or {}
    if not hourly.get("time") or cur["us_aqi"] is None:
        yield _ev("error", node=2, code="no_data", message="No air-quality coverage returned for this location.")
        return
    timings["telemetry_api_ms"] = int((time.perf_counter() - t) * 1000)
    yield done(2, t, detail=f"US AQI {cur['us_aqi']} · PM2.5 {cur['pm2_5']} µg/m³ · {len(hourly['time'])} hourly points", api_ms=timings["telemetry_api_ms"])

    yield _ev("node", node=3, name=NODES[3], status="running")
    t = time.perf_counter()
    an = analyze(hourly, cur)
    yield done(3, t, detail=f"Trend {an['trend']['label']} · driver {an['dominant']['label']}")

    # ---- Node 4
    yield _ev("node", node=4, name=NODES[4], status="running")
    t = time.perf_counter()
    fb = fallback_advisory(profile, cur, an)
    provider = {"label": "Rules-based engine (no AI key configured)", "provider": "rules", "model": "deterministic", "fallback": False}
    adv = fb
    summary = {
        "location": f"{loc['name']}, {loc['country']}", "profile": profile, "current": cur,
        "dominant_driver": an["dominant"]["label"], "trend": an["trend"]["label"],
        "best_window": an["best_window"] and an["best_window"]["label"],
        "peak_window": an["peak_window"] and an["peak_window"]["label"],
        "windows_24h": an["windows"]["24h"], "windows_72h": an["windows"]["72h"],
    }
    attempts = []
    try:
        out = await llm.generate(SYSTEM, "Air quality data:\n" + json.dumps(summary), db.get_keys(), timeout=18.0)
        d = out["data"]
        if isinstance(d, dict) and d.get("outdoor_activity"):
            adv = {k: d.get(k) or fb[k] for k in ADVISORY_KEYS}
            if not isinstance(adv["symptom_watch"], list):
                adv["symptom_watch"] = fb["symptom_watch"]
            provider = {"label": f"{out['provider'].title()} · {out['model']}" + (" (fallback)" if out["fallback"] else ""),
                        "provider": out["provider"], "model": out["model"], "fallback": out["fallback"], "latency_ms": out["latency_ms"]}
        attempts = out.get("attempts", [])
    except llm.LLMUnavailable as e:
        attempts = [str(e)]
        provider["label"] = "Rules-based engine (AI unavailable: " + str(e)[:80] + ")"
    except Exception as e:
        attempts = [f"error {type(e).__name__}"]
        provider["label"] = "Rules-based engine (AI error)"
    provider["attempts"] = attempts
    yield done(4, t, detail=provider["label"], provider=provider)

    # ---- Node 5
    yield _ev("node", node=5, name=NODES[5], status="running")
    t = time.perf_counter()
    result = {
        "location": loc, "profile": profile, "current": cur, "category": aqi_category(cur["us_aqi"]),
        "series": {
            "time": hourly["time"], "us_aqi": hourly.get("us_aqi"), "pm2_5": hourly.get("pm2_5"),
            "pm10": hourly.get("pm10"), "no2": hourly.get("nitrogen_dioxide"), "now_index": an["now_index"],
        },
        "analysis": an, "advisory": adv, "provider": provider,
        "risk_index": risk_index(profile, cur, an),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    result["brief_md"] = build_brief(result)
    result["id"] = db.save_forecast(result)
    timings["total_ms"] = int((time.perf_counter() - T0) * 1000)
    result["timings"] = timings
    yield done(5, t, detail=f"Saved forecast #{result['id']} to aeropulse.db (WAL)")
    timings["total_ms"] = int((time.perf_counter() - T0) * 1000)
    yield _ev("result", data=result)
