"""One call_llm() for the whole pipeline, backed by a chain of LLM providers.

Providers are tried in LLM_PROVIDERS order (default: gemini only). Gemini goes through its own SDK;
the others (Groq, Mistral, OpenRouter) speak the OpenAI-compatible chat API. A provider is skipped
when it has no API key, cannot take the request's number of images, or is cooling down after a
recent 429. A provider that fails moves the call on to the next one; only when every provider has
failed does the error reach the worker, which then retries the stage with its usual backoff.
"""
import base64
import json
import os
import re

import httpx
from google import genai
from google.genai import errors, types

from common import config
from common.errors import FatalError, RetryableError
from common.jobs import r
from common.limiter import wait_for_llm_slot

PROVIDERS = [p.strip() for p in os.environ.get("LLM_PROVIDERS", "gemini").split(",") if p.strip()]

COMPAT = {
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "key": "GROQ_API_KEY",
        "text_model": os.environ.get("GROQ_TEXT_MODEL", "openai/gpt-oss-120b"),
        "vision_model": os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.8-27b"),
        "max_images": 5,
        "rpm": int(os.environ.get("GROQ_RPM", "30")),
        "format": "json_schema",
    },
    "mistral": {
        "url": "https://api.mistral.ai/v1/chat/completions",
        "key": "MISTRAL_API_KEY",
        "text_model": os.environ.get("MISTRAL_TEXT_MODEL", "mistral-medium-latest"),
        "vision_model": os.environ.get("MISTRAL_VISION_MODEL", "mistral-medium-latest"),
        "max_images": 8,
        "rpm": int(os.environ.get("MISTRAL_RPM", "20")),
        "format": "json_schema",
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key": "OPENROUTER_API_KEY",
        "text_model": os.environ.get("OPENROUTER_TEXT_MODEL", "openrouter/free"),
        "vision_model": os.environ.get("OPENROUTER_VISION_MODEL", "openrouter/free"),
        "max_images": 8,
        "rpm": int(os.environ.get("OPENROUTER_RPM", "10")),
        "format": "json_object",     # the free router picks the model, so only ask for plain JSON
    },
}


def call_llm(contents, system, schema, model=None, rpm=None):
    """Return the parsed JSON answer from the first provider in the chain that succeeds.

    model/rpm select the Gemini model (e.g. the cheaper check model); the other providers use
    their own text or vision model depending on whether the request contains images.
    """
    images = _count_images(contents)
    simulated_down = {p.strip() for p in os.environ.get("FAULT_LLM_DOWN", "").split(",") if p.strip()}
    failures, cooling = [], []

    for name in PROVIDERS:
        spec = COMPAT.get(name)
        if name != "gemini" and spec is None:
            print(f"LLM: unknown provider '{name}' in LLM_PROVIDERS, skipping")
            continue
        if not os.environ.get("GEMINI_API_KEY" if name == "gemini" else spec["key"]):
            continue                                        # not configured
        if spec and images > spec["max_images"]:
            continue                                        # this model cannot take that many images
        chosen = (model or config.LLM_MODEL) if name == "gemini" else \
            spec["vision_model" if images else "text_model"]
        wait = _cooldown_left(name, chosen)
        if wait:
            cooling.append(wait)
            continue

        try:
            if name in simulated_down:                      # testing switch
                raise RetryableError("LLM_RATE_LIMITED", f"Simulated 429 from {name}", retry_after=30)
            if name == "gemini":
                answer = _gemini(contents, system, schema, chosen, rpm or config.LLM_RPM)
            else:
                answer = _compat(name, spec, chosen, contents, system, schema, images)
        except (RetryableError, FatalError) as e:
            if e.code == "LLM_RATE_LIMITED" and len(PROVIDERS) > 1:   # only useful with a fallback
                _cool_down(name, chosen, getattr(e, "retry_after", None) or 60)
            print(f"LLM: {name} ({chosen}) failed with {e.code}: {e}")
            failures.append(e)
            continue
        if failures or cooling:
            print(f"LLM: answered by fallback provider {name} ({chosen})")
        return answer

    if len(failures) == 1 and not cooling:
        raise failures[0]                                   # same error as a single-provider setup
    retryable = [e for e in failures if isinstance(e, RetryableError)]
    if retryable or cooling:
        delays = [e.retry_after for e in retryable if e.retry_after] + cooling
        codes = ", ".join(e.code for e in failures) or "all providers cooling down after 429s"
        code = "LLM_RATE_LIMITED" if cooling or all(e.code == "LLM_RATE_LIMITED" for e in retryable) \
            else "LLM_UNAVAILABLE"
        raise RetryableError(code, f"Every LLM provider failed ({codes})",
                             retry_after=min(delays) if delays else None)
    if failures:
        raise failures[0]
    raise FatalError("LLM_NOT_CONFIGURED",
                     f"No LLM provider in LLM_PROVIDERS can take this request ({images} images)")


# ---------- Gemini (unchanged behaviour) ----------

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(http_options=types.HttpOptions(timeout=120_000))
    return _client

def _retry_delay(e):
    try:
        for d in e.details["error"]["details"]:
            if d.get("@type", "").endswith("RetryInfo"):
                return int(float(d["retryDelay"].rstrip("s"))) + 1
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    return None

def _quota_name(e):
    try:
        for d in e.details["error"]["details"]:
            if d.get("@type", "").endswith("QuotaFailure"):
                return d["violations"][0].get("quotaId", "")
    except (KeyError, TypeError, IndexError, AttributeError):
        pass
    return ""

def _gemini(contents, system, schema, model, rpm):
    wait_for_llm_slot(model, rpm)
    try:
        response = _get_client().models.generate_content(
            model=model, contents=contents,
            config=types.GenerateContentConfig(system_instruction=system,
                                               response_mime_type="application/json",
                                               response_json_schema=schema))
    except errors.ClientError as e:
        if e.code == 429:
            raise RetryableError("LLM_RATE_LIMITED",
                                 f"Gemini rate limit reached on {model} {_quota_name(e)}".strip(),
                                 retry_after=_retry_delay(e))
        if e.code in (401, 403):
            raise FatalError("LLM_AUTH_FAILED", "Gemini rejected the API key")
        if e.code == 404:
            raise FatalError("LLM_MODEL_NOT_FOUND", f"Gemini model {model} does not exist")
        raise RetryableError("LLM_REQUEST_FAILED", f"Gemini error {e.code}: {e.message}")
    except errors.ServerError as e:
        raise RetryableError("LLM_UNAVAILABLE", f"Gemini is unavailable ({e.code})")
    try:
        return json.loads(response.text)
    except (TypeError, ValueError):
        raise RetryableError("LLM_BAD_OUTPUT", "Gemini returned invalid JSON")


# ---------- OpenAI-compatible providers ----------

def _compat(name, spec, model, contents, system, schema, images):
    wait_for_llm_slot(f"{name}:{model}", spec["rpm"])
    instructions = (f"{system}\n\nReply with only a JSON object that matches this JSON Schema, "
                    f"with no other text:\n{json.dumps(schema)}")
    if spec["format"] == "json_schema":
        response_format = {"type": "json_schema",
                           "json_schema": {"name": "result", "schema": schema, "strict": False}}
    else:
        response_format = {"type": "json_object"}
    body = {"model": model, "temperature": 0.2, "response_format": response_format,
            "messages": [{"role": "system", "content": instructions},
                         {"role": "user", "content": _openai_content(contents, images)}]}
    try:
        resp = httpx.post(spec["url"], json=body, timeout=180,
                          headers={"Authorization": f"Bearer {os.environ[spec['key']]}"})
    except httpx.HTTPError as e:
        raise RetryableError("LLM_UNAVAILABLE", f"Could not reach {name}: {e!r}"[:200])

    label = f"{name} {model}"
    if resp.status_code == 429:
        raise RetryableError("LLM_RATE_LIMITED", f"{label} rate limit reached",
                             retry_after=_header_seconds(resp))
    if resp.status_code in (401, 403):
        raise FatalError("LLM_AUTH_FAILED", f"{name} rejected the API key")
    if resp.status_code == 404:
        raise FatalError("LLM_MODEL_NOT_FOUND", f"{name} has no model {model}")
    if resp.status_code == 413:
        raise FatalError("LLM_REQUEST_TOO_LARGE", f"The request is too large for {label}")
    if resp.status_code >= 500:
        raise RetryableError("LLM_UNAVAILABLE", f"{label} is unavailable ({resp.status_code})")
    if resp.status_code >= 400:
        raise FatalError("LLM_REQUEST_FAILED", f"{label} refused the request "
                                               f"({resp.status_code}): {resp.text[:200]}")
    try:
        text = resp.json()["choices"][0]["message"]["content"]
        return _conform(_parse_json(text), schema)
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise RetryableError("LLM_BAD_OUTPUT", f"{label} returned unusable output: {e}"[:200])


def _openai_content(contents, images):
    items = [contents] if isinstance(contents, str) else list(contents)
    if not images:                                          # text-only: one plain string
        return "\n".join(c for c in items if isinstance(c, str))
    parts = []
    for c in items:
        if isinstance(c, str):
            parts.append({"type": "text", "text": c})
        elif getattr(c, "inline_data", None) is not None:
            blob = c.inline_data
            data = base64.b64encode(blob.data).decode()
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{blob.mime_type};base64,{data}"}})
    return parts


def _count_images(contents):
    if isinstance(contents, str):
        return 0
    return sum(1 for c in contents if getattr(c, "inline_data", None) is not None)


def _parse_json(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()   # reasoning models
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in the answer")
        return json.loads(text[start:end + 1])


def _conform(value, schema, path="$"):
    """Check an answer against the (simple) JSON Schemas used here and fix harmless drift,
    such as 12.0 or "12" for an integer. Raises ValueError when the answer cannot be used."""
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} should be an object")
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{path}.{key} is missing")
        return {k: _conform(v, props[k], f"{path}.{k}") for k, v in value.items() if k in props}
    if kind == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} should be a list")
        return [_conform(v, schema.get("items", {}), f"{path}[{i}]") for i, v in enumerate(value)]
    if kind == "integer":
        if isinstance(value, bool):
            raise ValueError(f"{path} should be a whole number")
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value.strip())
        if not isinstance(value, int):
            raise ValueError(f"{path} should be a whole number")
    elif kind == "string":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str):
            raise ValueError(f"{path} should be text")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} should be one of {schema['enum']}")
    return value


def _header_seconds(resp):
    try:
        return int(float(resp.headers["retry-after"])) + 1
    except (KeyError, ValueError):
        return None


# ---------- circuit breaker: skip a provider for a while after it returns 429 ----------

def _cooldown_key(name, model):
    return f"llm:cooldown:{name}:{model}"

def _cool_down(name, model, seconds):
    r.set(_cooldown_key(name, model), 1, ex=max(1, min(int(seconds), 3600)))

def _cooldown_left(name, model):
    ttl = r.ttl(_cooldown_key(name, model))
    return ttl if ttl and ttl > 0 else 0
