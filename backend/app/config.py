
import os
from dotenv import load_dotenv

load_dotenv()

API_KEYS = [
    key.strip()
    for key in os.getenv("LLM_API_KEYS", "").split(",")
    if key.strip()
]

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini/gemini-3.6-flash"
)

# Gemini "thinking" models spend part of this budget on reasoning tokens,
# so keep it generous to avoid empty replies.
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "2048"))

# Optional extra models, tried with the same key pool only after every key
# has failed on GEMINI_MODEL (e.g. the primary model is overloaded).
FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv("FALLBACK_MODELS", "").split(",")
    if model.strip()
]

# Per-call provider timeout, so one hung key doesn't stall the request.
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "25"))

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./app.db"
)

ENABLE_FAILURE_SIMULATION = (
    os.getenv("ENABLE_FAILURE_SIMULATION", "false").lower() == "true"
)

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173"
    ).split(",")
    if origin.strip()
]

DEFAULT_SYSTEM_PROMPT = """
You are Lyftr AI, a helpful and intelligent AI assistant.
Answer questions clearly, accurately and politely.
If you do not know something, be honest about it.
"""
