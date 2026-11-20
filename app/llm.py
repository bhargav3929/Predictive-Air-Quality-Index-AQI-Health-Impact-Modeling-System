"""Groq-only LLM client with automatic model fallback and a circuit breaker."""
import asyncio
import json
import re
import time

import httpx

GROQ_MODELS = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "llama-3.3-70b-versatile"]
BREAKER_SECONDS = 120
MAX_TOKENS = 900

_breaker: dict[str, float] = {}


class LLMUnavailable(Exception):
    pass


def _open(provider: str) -> bool:
    return _breaker.get(provider, 0) > time.time()


def _trip(provider: str):
    _breaker[provider] = time.time() + BREAKER_SECONDS


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError("no JSON in model output")
        return json.loads(m.group(0))


async def _groq(client: httpx.AsyncClient, key: str, system: str, prompt: str):
    last = None
    for idx, model in enumerate(GROQ_MODELS):
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "max_tokens": MAX_TOKENS,
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }
        if "gpt-oss" in model:
            body["reasoning_effort"] = "low"
        try:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=body,
            )
            r.raise_for_status()
            data = _parse_json(r.json()["choices"][0]["message"]["content"])
            return {"data": data, "provider": "groq", "model": model, "fallback": idx > 0}
        except Exception as e:  # try next model
            last = e
    raise last or RuntimeError("groq failed")


async def generate(system: str, prompt: str, keys: dict, timeout: float = 18.0) -> dict:
    """Return {data, provider, model, fallback, latency_ms, attempts[]} or raise LLMUnavailable."""
    t0 = time.perf_counter()
    groq_key = keys.get("GROQ_API_KEY")
    if not groq_key or _open("groq"):
        raise LLMUnavailable("no AI provider available (missing Groq key or circuit open)")
    attempts = ["groq started +0ms"]
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await asyncio.wait_for(_groq(client, groq_key, system, prompt), timeout)
    except Exception as e:
        _trip("groq")
        attempts.append(f"groq failed: {type(e).__name__}")
        raise LLMUnavailable("; ".join(attempts))
    res["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    res["attempts"] = attempts
    return res
