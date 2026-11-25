import litellm


def chat(client, session_id, message, headers=None, **extra):
    return client.post(
        "/chat",
        json={"session_id": session_id, "message": message, **extra},
        headers=headers or {},
    )


# ---------- health ----------

def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


# ---------- chat contract ----------

def test_chat_live_inference_contract(client, session_id):
    response = chat(client, session_id, "What is the capital of France?")
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["reply"]
    meta = body["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["model"] == "gemini/test-model"
    assert meta["key_id"] == "key_1"
    assert meta["fallback_attempts"] == 0
    for field in ("latency_ms", "input_tokens", "output_tokens"):
        assert isinstance(meta[field], int)
    assert meta["input_tokens"] > 0


def test_same_first_message_in_new_session_is_cache_hit(client, provider, session_id):
    first = chat(client, session_id, "Hello there").json()
    second = chat(client, session_id + "-b", "Hello there").json()

    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second["metadata"]["resolution_source"] == "LLM_CACHE"
    assert second["reply"] == first["reply"]
    assert second["metadata"]["key_id"] is None
    assert second["metadata"]["fallback_attempts"] == 0
    assert second["metadata"]["input_tokens"] == first["metadata"]["input_tokens"]
    assert second["metadata"]["output_tokens"] == first["metadata"]["output_tokens"]
    assert len(provider.live_calls) == 1


def test_different_message_is_never_cached(client, session_id):
    chat(client, session_id, "Message A")
    other = chat(client, session_id + "-b", "Message B").json()
    assert other["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_tokens_are_per_request_not_running_total(client, session_id):
    a = chat(client, session_id + "-a", "abcd efgh").json()["metadata"]
    b = chat(client, session_id + "-b", "ijkl mnop").json()["metadata"]
    assert abs(a["input_tokens"] - b["input_tokens"]) <= 2


# ---------- multi-turn + history ----------

def test_multi_turn_history_is_sent_and_stored(client, provider, session_id, monkeypatch):
    sent = []
    original = provider.__call__

    def spy(**kwargs):
        sent.append(kwargs["messages"])
        return original(**kwargs)

    from app import llm_service
    monkeypatch.setattr(llm_service, "completion", spy)

    chat(client, session_id, "My name is Ada")
    chat(client, session_id, "What is my name?")

    second_call = sent[-1]
    assert [m["role"] for m in second_call] == ["system", "user", "assistant", "user"]
    assert second_call[1]["content"] == "My name is Ada"

    history = client.get(f"/sessions/{session_id}/messages").json()
    assert history["session_id"] == session_id
    roles = [m["role"] for m in history["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert "metadata" not in history["messages"][0]
    assert history["messages"][1]["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert all(m["role"] != "system" for m in history["messages"])


def test_sessions_are_isolated(client, session_id):
    chat(client, session_id + "-a", "Only in A")
    history_b = client.get(f"/sessions/{session_id}-b/messages")
    assert history_b.status_code == 404


def test_unknown_session_history_is_404(client):
    response = client.get("/sessions/does-not-exist/messages")
    assert response.status_code == 404
    assert response.json() == {"detail": "session not found"}


def test_custom_system_prompt_is_used(client, provider, session_id, monkeypatch):
    sent = []
    original = provider.__call__
    from app import llm_service
    monkeypatch.setattr(
        llm_service, "completion", lambda **kw: (sent.append(kw["messages"]), original(**kw))[1]
    )
    chat(client, session_id, "hi", system_prompt="You are a pirate.")
    chat(client, session_id, "again")  # prompt persists for the session
    assert sent[0][0] == {"role": "system", "content": "You are a pirate."}
    assert sent[1][0] == {"role": "system", "content": "You are a pirate."}


# ---------- validation (422) ----------

def test_validation_errors(client, session_id):
    assert chat(client, session_id, "").status_code == 422
    assert chat(client, session_id, "    ").status_code == 422
    assert chat(client, "", "hello").status_code == 422
    assert chat(client, "x" * 129, "hello").status_code == 422
    assert chat(client, session_id, "x" * 4001).status_code == 422
    assert client.post("/chat", json={"session_id": session_id}).status_code == 422
    bad_json = client.post(
        "/chat", content="{not json", headers={"Content-Type": "application/json"}
    )
    assert bad_json.status_code == 422
    assert "detail" in bad_json.json()
    # Nothing was stored for any of the failures.
    assert client.get(f"/sessions/{session_id}/messages").status_code == 404


def test_length_is_checked_after_trimming(client, session_id):
    response = chat(client, session_id, "  " + "x" * 4000 + "  ")
    assert response.status_code == 200


# ---------- failover ----------

def test_simulated_429_on_key_1_fails_over_to_key_2(client, session_id):
    response = chat(client, session_id, "hi", headers={"X-Simulate-Failure": "key_1=429"})
    assert response.status_code == 200
    meta = response.json()["metadata"]
    assert meta["key_id"] == "key_2"
    assert meta["fallback_attempts"] == 1
    assert meta["resolution_source"] == "LIVE_INFERENCE"


def test_simulated_500_on_key_1_fails_over_to_key_2(client, session_id):
    response = chat(client, session_id, "hi", headers={"X-Simulate-Failure": "key_1=500"})
    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_2"


def test_simulation_bypasses_cache(client, session_id):
    chat(client, session_id + "-a", "cached?")
    response = chat(
        client, session_id + "-b", "cached?", headers={"X-Simulate-Failure": "key_1=503"}
    )
    assert response.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert response.json()["metadata"]["key_id"] == "key_2"


def test_all_keys_failing_returns_503_and_stores_nothing(client, session_id):
    for header in ("*=429", "key_1=429,key_2=500"):
        response = chat(client, session_id, "hi", headers={"X-Simulate-Failure": header})
        assert response.status_code == 503
        assert response.json() == {"detail": "all providers unavailable"}
    assert client.get(f"/sessions/{session_id}/messages").status_code == 404


def test_real_provider_errors_trigger_failover(client, provider, session_id):
    provider.failing_keys["test-key-1"] = litellm.RateLimitError(
        "quota", llm_provider="gemini", model="test"
    )
    response = chat(client, session_id, "real failure")
    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_2"

    provider.failing_keys["test-key-2"] = litellm.InternalServerError(
        "boom", llm_provider="gemini", model="test"
    )
    response = chat(client, session_id, "both down")
    assert response.status_code == 503
    assert response.json() == {"detail": "all providers unavailable"}


def test_invalid_simulation_header_is_rejected(client, session_id):
    for header in ("garbage", "key_9=429", "key_1=418"):
        response = chat(client, session_id, "hi", headers={"X-Simulate-Failure": header})
        assert response.status_code == 422


def test_simulation_ignored_when_disabled(client, session_id, monkeypatch):
    from app import main
    monkeypatch.setattr(main, "ENABLE_FAILURE_SIMULATION", False)
    response = chat(client, session_id, "hi", headers={"X-Simulate-Failure": "*=429"})
    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_1"


# ---------- fallback model ----------

def test_fallback_model_used_when_primary_fails_on_all_keys(client, provider, session_id, monkeypatch):
    from app import llm_service
    monkeypatch.setattr(llm_service, "FALLBACK_MODELS", ["gemini/backup-model"])
    provider.failing_models["gemini/test-model"] = litellm.ServiceUnavailableError(
        "overloaded", llm_provider="gemini", model="test"
    )
    first = chat(client, session_id, "fallback please")
    assert first.status_code == 200
    meta = first.json()["metadata"]
    assert meta["model"] == "gemini/backup-model"
    assert meta["key_id"] == "key_1"
    assert meta["fallback_attempts"] == 2

    # The answer is cached under the fallback model; a repeat must hit the
    # cache immediately without retrying the failing primary model.
    attempts_before = len(provider.attempts)
    second = chat(client, session_id + "-b", "fallback please")
    meta = second.json()["metadata"]
    assert meta["resolution_source"] == "LLM_CACHE"
    assert meta["model"] == "gemini/backup-model"
    assert second.json()["reply"] == first.json()["reply"]
    assert provider.attempts[attempts_before:] == [("gemini/backup-model", "test-key-1")]
