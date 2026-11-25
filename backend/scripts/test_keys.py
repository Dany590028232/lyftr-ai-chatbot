import time
import litellm
from app.config import API_KEYS

MODELS = [
    "gemini/gemini-3.8-flash",
    "gemini/gemini-3.7-flash",
    "gemini/gemini-3.6-flash",
    "gemini/gemini-3.5-flash-lite",
]

print(f"keys loaded: {len(API_KEYS)}")

for i, key in enumerate(API_KEYS, start=1):
    for model in MODELS:
        start = time.time()
        try:
            litellm.completion(
                model=model,
                messages=[{"role": "user", "content": "Say hi"}],
                api_key=key,
                max_tokens=200,
                num_retries=0,
                timeout=30,
            )
            status = "OK"
        except Exception as e:
            code = getattr(e, "status_code", None)
            status = f"FAIL {type(e).__name__} status={code}"
        print(f"key_{i} | {model} | {status} | {time.time() - start:.1f}s")
