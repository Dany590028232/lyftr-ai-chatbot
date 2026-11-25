import time
import litellm
from litellm import completion
from app.config import API_KEYS, GEMINI_MODEL
from app.llm_service import generate_response

print("=== Part A: your generate_response, called twice ===")
msgs = [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "What is the capital of Italy?"},
]
for n in (1, 2):
    reply, meta = generate_response(msgs)
    print(n, meta)
    print("   reply:", reply)

print()
print("=== Part B: plain litellm, called twice ===")
raw_msgs = [{"role": "user", "content": "What is the capital of Spain?"}]
for n in (1, 2):
    start = time.time()
    r = completion(
        model=GEMINI_MODEL,
        messages=raw_msgs,
        api_key=API_KEYS[0],
        caching=True,
        max_tokens=1024,
        num_retries=0,
    )
    took = time.time() - start
    print(n, "cache_hit =", r._hidden_params.get("cache_hit"), f"| {took:.1f}s")
