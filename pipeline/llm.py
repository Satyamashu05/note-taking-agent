import json

from google import genai
from google.genai import errors, types

from common import config
from common.errors import FatalError, RetryableError
from common.limiter import wait_for_llm_slot

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

def call_llm(contents, system, schema):
    wait_for_llm_slot()
    try:
        response = _get_client().models.generate_content(
            model=config.LLM_MODEL, contents=contents,
            config=types.GenerateContentConfig(system_instruction=system,
                                               response_mime_type="application/json",
                                               response_json_schema=schema))
    except errors.ClientError as e:
        if e.code == 429:
            raise RetryableError("LLM_RATE_LIMITED", "Gemini rate limit reached",
                                 retry_after=_retry_delay(e))
        if e.code in (401, 403):
            raise FatalError("LLM_AUTH_FAILED", "Gemini rejected the API key")
        if e.code == 404:
            raise FatalError("LLM_MODEL_NOT_FOUND", f"Gemini model {config.LLM_MODEL} does not exist")
        raise RetryableError("LLM_REQUEST_FAILED", f"Gemini error {e.code}: {e.message}")
    except errors.ServerError as e:
        raise RetryableError("LLM_UNAVAILABLE", f"Gemini is unavailable ({e.code})")
    try:
        return json.loads(response.text)
    except (TypeError, ValueError):
        raise RetryableError("LLM_BAD_OUTPUT", "Gemini returned invalid JSON")
