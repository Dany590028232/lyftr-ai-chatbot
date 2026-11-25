import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.config import (
    ALLOWED_ORIGINS,
    API_KEYS,
    DEFAULT_SYSTEM_PROMPT,
    ENABLE_FAILURE_SIMULATION,
)
from app.database import get_messages, get_session, init_db, save_conversation
from app.llm_service import (
    ALL_PROVIDERS_UNAVAILABLE,
    AllProvidersUnavailable,
    cache_enabled,
    generate_response,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("lyftr.api")

SIMULATABLE_STATUSES = {"429", "500", "502", "503"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Lyftr AI Chatbot API",
    description="LLM Chatbot with Key Failover and Response Caching",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Last line of defence: never leak a stack trace or an HTML error page.
    logger.error("Unhandled error on %s: %s", request.url.path, type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    system_prompt: str | None = None

    # mode="before" so length limits are checked on the *trimmed* value.
    @field_validator("session_id", "message", mode="before")
    @classmethod
    def strip_whitespace(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("system_prompt", mode="before")
    @classmethod
    def blank_prompt_is_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value


def validate_failure_header(header: str):
    valid_ids = {f"key_{i + 1}" for i in range(len(API_KEYS))} | {"*"}

    for item in header.split(","):
        item = item.strip()
        if not item:
            continue

        if "=" not in item:
            raise HTTPException(422, "Invalid X-Simulate-Failure header")

        key_id, status = (part.strip() for part in item.split("=", 1))

        if key_id not in valid_ids:
            raise HTTPException(422, f"Unknown key id: {key_id}")

        if status not in SIMULATABLE_STATUSES:
            raise HTTPException(422, f"Unsupported simulated status: {status}")


@app.get("/health")
def health():
    if len(API_KEYS) < 2:
        raise HTTPException(503, "At least two API keys are required")

    try:
        init_db()
    except Exception:
        raise HTTPException(503, "Database unavailable")

    return {
        "status": "ok",
        "keys_configured": len(API_KEYS),
        "cache_enabled": cache_enabled(),
    }


@app.post("/chat")
def chat(
    request: ChatRequest,
    x_simulate_failure: str | None = Header(default=None, alias="X-Simulate-Failure"),
):
    # The test hook is only honoured when explicitly switched on.
    failure_header = x_simulate_failure if ENABLE_FAILURE_SIMULATION else None
    if failure_header:
        validate_failure_header(failure_header)

    previous_session = get_session(request.session_id)
    previous_messages = get_messages(request.session_id) if previous_session else []

    if request.system_prompt is not None:
        system_prompt = request.system_prompt
    elif previous_session and previous_session["system_prompt"]:
        system_prompt = previous_session["system_prompt"]
    else:
        system_prompt = DEFAULT_SYSTEM_PROMPT

    messages = [{"role": "system", "content": system_prompt}]
    messages += [
        {"role": m["role"], "content": m["content"]} for m in previous_messages
    ]
    messages.append({"role": "user", "content": request.message})

    try:
        reply, metadata = generate_response(messages, failure_header=failure_header)
    except AllProvidersUnavailable:
        # Nothing is persisted: a failed request never enters history.
        raise HTTPException(503, ALL_PROVIDERS_UNAVAILABLE)

    try:
        save_conversation(
            session_id=request.session_id,
            system_prompt=system_prompt,
            user_message=request.message,
            assistant_reply=reply,
            metadata=metadata,
        )
    except Exception:
        logger.exception("Failed to persist conversation")
        raise HTTPException(503, "Unable to save conversation")

    return {
        "session_id": request.session_id,
        "reply": reply,
        "metadata": metadata,
    }


@app.get("/sessions/{session_id}/messages")
def session_history(session_id: str):
    if not get_session(session_id):
        raise HTTPException(404, "session not found")

    return {"session_id": session_id, "messages": get_messages(session_id)}
