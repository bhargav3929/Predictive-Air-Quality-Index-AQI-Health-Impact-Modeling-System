"""Groq-only LLM client: timeout, retries with backoff, model fallback, plain-English errors."""
import asyncio
import json
import logging
import random
import re
import time

import httpx

from . import config

log = logging.getLogger("aeropulse.llm")
URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_TOKENS = 900
TIMEOUT = 40.0
RETRY_STATUS = {429, 500, 502, 503, 504}

MESSAGES = {
    "AI_NOT_CONFIGURED": "No Groq key is set. Open API Keys and paste a free key from console.groq.com/keys.",
    "AI_KEY_INVALID": "Your Groq key was rejected. Open API Keys and paste a fresh key from console.groq.com/keys.",
    "AI_BUSY": "The AI service is busy right now. Wait a minute and try again.",
    "AI_FAILED": "The AI couldn't process this input.",
}


class LLMError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.message = MESSAGES[code]


def _clean(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text


def _parse_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError("no JSON in model output")
        return json.loads(m.group(0))


def _wait_hint(resp: httpx.Response, attempt: int) -> float:
    m = re.search(r"try again in ([\d.]+)s", resp.text or "")
    if m and float(m.group(1)) <= 20:
        return float(m.group(1))
    return 2 ** attempt + random.random()


async def _call_model(client, key, model, system, prompt):
    """Up to 3 attempts. Returns parsed JSON or raises LLMError."""
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS, "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    if "gpt-oss" in model:
        body["reasoning_effort"] = "low"
    bad_output = False
    for attempt in range(3):
        wait = 2 ** attempt + random.random()
        try:
            r = await client.post(URL, headers={"Authorization": f"Bearer {key}"}, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            log.warning("groq %s attempt %d: %s", model, attempt + 1, type(e).__name__)
            if attempt < 2:
                await asyncio.sleep(wait)
            continue
        if r.status_code == 401:
            raise LLMError("AI_KEY_INVALID", "401")
        if r.status_code in RETRY_STATUS:
            log.warning("groq %s attempt %d: HTTP %d", model, attempt + 1, r.status_code)
            if attempt < 2:
                await asyncio.sleep(_wait_hint(r, attempt) if r.status_code == 429 else wait)
            continue
        if r.status_code >= 400:  # 400 / 413 / other: do not retry
            log.warning("groq %s: HTTP %d %s", model, r.status_code, _clean(r.text[:200], key))
            raise LLMError("AI_FAILED", str(r.status_code))
        try:
            return _parse_json(r.json()["choices"][0]["message"]["content"])
        except Exception:
            if bad_output:
                raise LLMError("AI_FAILED", "invalid output")
            bad_output = True  # retry once on invalid output
    raise LLMError("AI_BUSY", "retries exhausted")


async def generate(system: str, prompt: str, key: str) -> dict:
    """Return {data, provider, model, fallback, latency_ms} or raise LLMError."""
    if not config.key_usable(key):
        raise LLMError("AI_NOT_CONFIGURED")
    t0 = time.perf_counter()
    models = [config.GROQ_MODEL]
    if config.GROQ_FALLBACK_MODEL != config.GROQ_MODEL:
        models.append(config.GROQ_FALLBACK_MODEL)
    last = None
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for idx, model in enumerate(models):
            try:
                data = await _call_model(client, key, model, system, prompt)
                return {"data": data, "provider": "groq", "model": model, "fallback": idx > 0,
                        "latency_ms": int((time.perf_counter() - t0) * 1000)}
            except LLMError as e:
                if e.code == "AI_KEY_INVALID":
                    raise
                last = e
    raise last or LLMError("AI_FAILED")
