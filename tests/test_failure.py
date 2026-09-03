from pllla_bridge.failure import (
    build_failure,
    classify_failure_kind,
    failure_chat_text,
    failure_from_gateway_reply,
)


def test_classifies_the_errors_runtimes_actually_produce():
    assert classify_failure_kind("Failed to authenticate: OAuth session expired and could not be refreshed") == "authentication"
    # Hermes' own wording when no provider is configured (measured 2026-09-03).
    assert classify_failure_kind("No inference provider configured. Run 'hermes model' …") == "authentication"
    assert classify_failure_kind("429 Too Many Requests") == "rate_limited"
    assert classify_failure_kind("request timed out") == "timeout"
    assert classify_failure_kind("prompt is too long") == "context_exhausted"
    assert classify_failure_kind("something odd") == "unknown"


def test_build_failure_matches_the_server_shape():
    assert build_failure("OAuth session expired") == {
        "kind": "authentication",
        "engineType": "hermes",
        "model": "hermes",
    }
    assert build_failure("x", model="m" * 300)["model"] == "m" * 256
    assert build_failure("anything", kind="timeout")["kind"] == "timeout"


def test_gateway_error_replies_carry_a_failure_but_normal_replies_do_not():
    assert failure_from_gateway_reply("⚠️ Provider authentication failed. Check the configured credentials.")["kind"] == "authentication"
    assert failure_from_gateway_reply("안녕하세요! 저는 Hermes 예요.") is None


def test_chat_text_names_the_runtime_and_keeps_the_cause():
    text = failure_chat_text("authentication", "OAuth session expired")
    assert "Hermes" in text and "다시 로그인" in text and "OAuth session expired" in text
    assert "boom" in failure_chat_text("unknown", "boom")
