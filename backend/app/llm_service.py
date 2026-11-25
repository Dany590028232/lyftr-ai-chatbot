import logging
import time

import litellm
from litellm import Cache, completion

from app.config import (
    API_KEYS,
    FALLBACK_MODELS,
    GEMINI_MODEL,
    LLM_TIMEOUT_SECONDS,
    MAX_OUTPUT_TOKENS,
)

logger = logging.getLogger("lyftr.llm")

# LiteLLM's built-in in-memory cache. The cache key is derived from the
# model + the full message list (+ generation params), never the API key,
# so a cached answer is shared across keys and sessions.
litellm.cache = Cache(type="local")

ALL_PROVIDERS_UNAVAILABLE = "all providers unavailable"


class AllProvidersUnavailable(RuntimeError):
    def __init__(self):
        super().__init__(ALL_PROVIDERS_UNAVAILABLE)


def cache_enabled():
    return litellm.cache is not None


def is_cached(model, messages):
    """Probe LiteLLM's cache with the same params completion() uses."""
    if litellm.cache is None:
        return False
    try:
        return litellm.cache.get_cache(
            model=model, messages=messages, max_tokens=MAX_OUTPUT_TOKENS
        ) is not None
    except Exception:
        return False


def parse_failure_header(header):
    """Parse 'key_1=429,key_2=500' / '*=429' into {key_id: status}."""
    failures = {}

    if not header:
        return failures

    for item in header.split(","):
        item = item.strip()

        if "=" not in item:
            continue

        key_id, status = item.split("=", 1)

        try:
            failures[key_id.strip()] = int(status.strip())
        except ValueError:
            continue

    return failures


def generate_response(messages, failure_header=None):
    """
    Try each key in order (key_1, key_2, ...). Any provider error
    (429, quota, 5xx, timeout, empty reply) moves on to the next key.
    Returns (reply, metadata) or raises AllProvidersUnavailable.
    """
    if len(API_KEYS) < 2:
        raise AllProvidersUnavailable()

    simulated_failures = parse_failure_header(failure_header)

    # A simulated-failure request must prove failover, so it must not be
    # short-circuited by a cache hit (and must not write to the cache).
    use_cache = not simulated_failures

    start_time = time.perf_counter()
    fallback_attempts = 0

    models = [GEMINI_MODEL, *FALLBACK_MODELS]

    # If a fallback model already has this exact conversation cached, try it
    # first so a cache hit never waits on a slow/failing primary model.
    if use_cache:
        models.sort(key=lambda m: not is_cached(m, messages))

    # Deployment order: every key on the first model, then every key on the
    # next model, and so on.
    deployments = [
        (model, index, api_key)
        for model in models
        for index, api_key in enumerate(API_KEYS)
    ]

    for model, index, api_key in deployments:
        key_id = f"key_{index + 1}"

        simulated_status = simulated_failures.get(
            key_id, simulated_failures.get("*")
        )
        if simulated_status is not None:
            logger.warning("%s simulated failure: HTTP %s", key_id, simulated_status)
            fallback_attempts += 1
            continue

        try:
            response = completion(
                model=model,
                messages=messages,
                api_key=api_key,
                caching=use_cache,
                max_tokens=MAX_OUTPUT_TOKENS,
                num_retries=0,
                timeout=LLM_TIMEOUT_SECONDS,
            )

            reply = response.choices[0].message.content
            if not reply or not reply.strip():
                raise ValueError("empty response from provider")

        except Exception as error:
            fallback_attempts += 1
            # Never log the raw message: provider URLs can contain the key.
            logger.warning(
                "%s on %s failed: %s (status=%s)",
                key_id,
                model,
                type(error).__name__,
                getattr(error, "status_code", None),
            )
            continue

        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        usage = getattr(response, "usage", None)
        hidden_params = getattr(response, "_hidden_params", None) or {}
        cache_hit = hidden_params.get("cache_hit") is True

        metadata = {
            "resolution_source": "LLM_CACHE" if cache_hit else "LIVE_INFERENCE",
            "model": model,
            "latency_ms": elapsed_ms,
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "key_id": None if cache_hit else key_id,
            "fallback_attempts": 0 if cache_hit else fallback_attempts,
        }

        return reply, metadata

    raise AllProvidersUnavailable()
