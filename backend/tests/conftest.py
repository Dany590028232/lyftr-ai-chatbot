import os
import tempfile
import uuid

# Configure the app *before* it is imported (config is read at import time).
_tmp_dir = tempfile.mkdtemp()
os.environ["LLM_API_KEYS"] = "test-key-1,test-key-2"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir}/test.db"
os.environ["ENABLE_FAILURE_SIMULATION"] = "true"
os.environ["GEMINI_MODEL"] = "gemini/test-model"

import litellm  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import llm_service  # noqa: E402
from app.main import app  # noqa: E402

_real_completion = llm_service.completion


class FakeProvider:
    """
    Wraps the real litellm.completion with `mock_response`, so LiteLLM's
    real cache is exercised without any network call. Keys listed in
    `failing_keys` raise a real LiteLLM error instead.
    """

    def __init__(self):
        self.failing_keys = {}
        self.failing_models = {}
        self.live_calls = []
        self.attempts = []

    def __call__(self, **kwargs):
        api_key = kwargs["api_key"]
        self.attempts.append((kwargs["model"], api_key))
        if api_key in self.failing_keys:
            kwargs["mock_response"] = self.failing_keys[api_key]
        elif kwargs["model"] in self.failing_models:
            kwargs["mock_response"] = self.failing_models[kwargs["model"]]
        else:
            last_user = kwargs["messages"][-1]["content"]
            kwargs["mock_response"] = f"echo: {last_user}"
        response = _real_completion(**kwargs)
        if not (response._hidden_params or {}).get("cache_hit"):
            self.live_calls.append(api_key)
        return response


@pytest.fixture
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(llm_service, "completion", fake)
    litellm.cache = litellm.Cache(type="local")  # fresh cache per test
    return fake


@pytest.fixture
def client(provider):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session_id():
    return f"s-{uuid.uuid4().hex[:8]}"
