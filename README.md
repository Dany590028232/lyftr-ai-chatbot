# Lyftr AI Chatbot: Key Failover and Response Caching

A full-stack chatbot. The React frontend talks to a FastAPI backend, which calls Gemini through **LiteLLM**. The backend rotates through a pool of API keys when a key fails, serves repeated prompts from **LiteLLM's built-in cache**, and stores every session's history in **SQLite**.

```
React (Vite, nginx)  ──HTTP/JSON──▶  FastAPI  ──▶  LiteLLM (cache + completion)  ──▶  Gemini
                                        │
                                        └──▶ SQLite (/data/app.db, Docker volume)
```

## 1. Live URLs

| Tier     | URL |
|----------|-----|
| Frontend | `https://<your-frontend>.vercel.app` |
| Backend  | `https://<your-backend>.onrender.com` (try `/health`, `/docs`) |

## 2. Run locally

### With Docker

```bash
cp .env.example .env        # then put two real Gemini keys in LLM_API_KEYS
make up                     # builds and starts both containers
# Frontend → http://localhost:3000
# Backend  → http://localhost:8000  (Swagger UI at /docs)
make down                   # stop (chat history stays in the `chat-data` volume)
```

### Without Docker

```bash
# Backend (Python 3.12)
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
cp ../.env.example .env     # add keys; set DATABASE_URL=sqlite:///./app.db
uvicorn app.main:app --reload --port 8000
python -m pytest -q         # run the test suite (no network or keys needed)

# Frontend (Node 20+), in a second terminal
cd frontend
npm install
VITE_API_URL=http://localhost:8000 npm run dev   # http://localhost:3000
```

### Environment variables

| Variable | Meaning |
|---|---|
| `LLM_API_KEYS` | Comma-separated key pool, at least 2. Ids are `key_1`, `key_2`, … in this order |
| `GEMINI_MODEL` | Primary LiteLLM model name, e.g. `gemini/gemini-3.6-flash` |
| `FALLBACK_MODELS` | Optional, comma-separated. Tried with every key after the primary model fails on all keys |
| `MAX_OUTPUT_TOKENS` | Completion token limit (default 2048) |
| `LLM_TIMEOUT_SECONDS` | Per-call provider timeout (default 25) |
| `DATABASE_URL` | SQLite location, e.g. `sqlite:////data/app.db` |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins |
| `ENABLE_FAILURE_SIMULATION` | `true` enables the `X-Simulate-Failure` header |
| `VITE_API_URL` | Frontend only: backend base URL, baked in at build time |

## 3. Design decisions

### Key fallback

- The keys are tried in order (`key_1`, `key_2`, …) through `litellm.completion(..., api_key=..., num_retries=0)`. Any provider exception moves on to the next key. That covers 429, quota errors, 5xx, timeouts, and empty replies. Each call has a timeout, so a hung key cannot stall the request.
- `num_retries=0` is intentional. Retrying the same rate-limited key only adds latency, so the next key is the retry.
- I wrote an explicit loop rather than LiteLLM's `Router`. The loop gives an exact `key_id` and `fallback_attempts` for every response, and it lets `X-Simulate-Failure` fail chosen keys deterministically. Simulated keys are skipped before any network call, as if their provider had returned that status.
- **Model fallback (optional):** if every key fails on `GEMINI_MODEL`, the same key pool is retried on each model in `FALLBACK_MODELS`. `metadata.model` always reports the model that actually answered.
- When every attempt fails, the response is `503 {"detail": "all providers unavailable"}`, and nothing is written to history.
- Simulated requests bypass the cache in both directions (no read, no write). A failover demo therefore always shows a real live answer from the next key.
- Provider error messages are never logged or returned, because Gemini request URLs carry the key. Logs contain only the key id, the error type, and the status code.

### Caching

- `litellm.cache = Cache(type="local")` is LiteLLM's built-in in-memory cache. Its cache key is the **model plus the full message list** (system prompt, history, and the new message) plus generation params. The API key is not part of it. So the same first message in a new session is a hit, and any different message or history is a miss.
- The response's `_hidden_params["cache_hit"]` sets `resolution_source` to `LLM_CACHE` or `LIVE_INFERENCE`. On a hit, the reply text and token usage are the ones stored with the cached response, `key_id` is `null`, and `fallback_attempts` is `0`.
- Before calling anything, the backend probes the cache for each model in the pool. That way a response cached under a fallback model is returned immediately (around 40 ms), instead of first waiting on a failing primary model.
- Trade-off: the in-memory cache is per process and is lost on restart. That's fine for one instance. With several replicas you would switch to LiteLLM's disk or Redis cache.

### SQLite session storage

- There are two tables. `sessions` holds `session_id`, the session's `system_prompt`, and `created_at`. `messages` uses the required schema, plus an index on `(session_id, id)`. The schema is created on startup.
- The client sends only the new message. The server rebuilds the prompt as system prompt + stored history + new message.
- The user and assistant messages are written in **one transaction, only after a successful reply**. So a 422 or 503 never leaves a half-saved turn. Each request opens its own connection and always closes it.
- In Docker the database lives at `/data/app.db` on the named volume `chat-data`, so it survives container restarts.

## 4. Verification

Set `B=http://localhost:8000` (or the live backend URL).

**Cache hit**: send the same first message in two different sessions:

```bash
curl -s $B/chat -H 'Content-Type: application/json' -d '{"session_id":"s-1","message":"What is the capital of France?"}'
curl -s $B/chat -H 'Content-Type: application/json' -d '{"session_id":"s-2","message":"What is the capital of France?"}'
# 1st → "resolution_source":"LIVE_INFERENCE", "key_id":"key_1", latency ~1-3 s
# 2nd → "resolution_source":"LLM_CACHE", identical reply, "key_id":null, latency ~tens of ms
```

**Failover**:

```bash
curl -s $B/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"s-3","message":"hi"}'          # 200, key_id = key_2, fallback_attempts = 1
curl -s $B/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: key_1=500' \
  -d '{"session_id":"s-3","message":"hi again"}'    # 200, key_id = key_2
curl -s -i $B/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: *=429' \
  -d '{"session_id":"s-4","message":"hi"}'          # 503 {"detail":"all providers unavailable"}
curl -s $B/sessions/s-4/messages                    # 404: nothing was stored
```

In the UI, open **Settings → Simulate key failure** to send the same header from the browser.

**Multi-turn and history**:

```bash
curl -s $B/chat -H 'Content-Type: application/json' -d '{"session_id":"s-5","message":"My name is Ada"}'
curl -s $B/chat -H 'Content-Type: application/json' -d '{"session_id":"s-5","message":"What is my name?"}'
curl -s $B/sessions/s-5/messages    # oldest first, no system prompt, metadata on assistant messages
```

**Per-message token usage**: every assistant bubble in the UI shows the source, model, latency, and the input and output tokens for *that request only*. Two equal-length first messages in new sessions report about the same `input_tokens`.

**Validation**: an empty or whitespace-only `message`, a message over 4000 characters, a missing `session_id`, or malformed JSON returns `422`.

**Automated tests**: `cd backend && python -m pytest -q` runs 19 tests. They use LiteLLM's real cache with `mock_response`, so they need no network or keys. They cover the contract, cache hit and miss, failover on simulated and real LiteLLM errors, model fallback, 503 with no history write, 422 validation, and session isolation.

## 5. Setup used

- Editor: VS Code
- AI tools: Claude Code (Claude Opus 5.5)

## 6. Bonus features

- **Failure simulator in the UI**: a dropdown that sets `X-Simulate-Failure`, so failover can be shown without curl.
- **Model fallback (`FALLBACK_MODELS`)**: keeps the app answering when the primary model is overloaded or out of quota on every key.
- **Cache-first probe across models**: cache hits stay fast even when the primary model is down.
- **Extra metadata in the UI**: `key_id` and `fallback_attempts` appear next to each reply, so failover is visible in the chat.
- **Optional system prompt** (`system-prompt-input`): it is persisted per session.
- **New chat button, Enter to send, dark mode.**
- **Hardening**: non-root containers, Docker healthcheck, a JSON-only error handler, and no API keys in logs.
