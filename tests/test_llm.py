"""Tests for the retry/fallback policy in app/llm.py.

All provider calls are mocked - no real Groq or Gemini network access.
`app.llm.get_groq_client` is monkeypatched to a fake client that raises or
returns canned responses in sequence, and `app.llm._call_gemini` is
monkeypatched directly for the fallback scenarios, so these tests don't
depend on the real google-genai wire format.
"""

from types import SimpleNamespace

import groq
import httpx
import pytest
from pydantic import BaseModel

from app.llm import (
    LLMError,
    SchemaValidationError,
    _PermanentProviderError,
    _TransientProviderError,
    request_structured_json,
)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class Dummy(BaseModel):
    content: str


def _fake_completion(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeGroqClient:
    """Each item in `effects` is either a raw JSON string or an Exception,
    consumed in order across successive `create()` calls."""

    def __init__(self, effects: list):
        self._effects = list(effects)
        self.call_count = 0
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.call_count += 1
        effect = self._effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return _fake_completion(effect)


def _patch_groq(monkeypatch, effects: list) -> FakeGroqClient:
    client = FakeGroqClient(effects)
    monkeypatch.setattr("app.llm.get_groq_client", lambda: client)
    return client


def _patch_sleep(monkeypatch) -> list:
    sleeps = []
    monkeypatch.setattr("app.llm.time.sleep", lambda s: sleeps.append(s))
    return sleeps


def _forbid_gemini(monkeypatch) -> None:
    def _fail(*args, **kwargs):
        raise AssertionError("Gemini must not be called in this scenario")

    monkeypatch.setattr("app.llm._call_gemini", _fail)


def _rate_limit_error(retry_after: str | None = None) -> groq.RateLimitError:
    request = httpx.Request("POST", GROQ_URL)
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(429, request=request, headers=headers)
    return groq.RateLimitError("rate limited", response=response, body=None)


def _daily_quota_exhausted_error(retry_after: str | None = "5.6") -> groq.RateLimitError:
    """Mirrors Groq's real response when the daily token budget (TPD) is
    exhausted: a short suggested wait that won't actually free up enough
    tokens for a real request."""
    request = httpx.Request("POST", GROQ_URL)
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(429, request=request, headers=headers)
    body = {
        "error": {
            "message": (
                "Rate limit reached for model `openai/gpt-oss-20b` ... on "
                "tokens per day (TPD): Limit 200000, Used 199207, Requested 806. "
                "Please try again in 5.616s."
            ),
            "type": "tokens",
            "code": "rate_limit_exceeded",
        }
    }
    return groq.RateLimitError("rate limited", response=response, body=body)


def _internal_server_error() -> groq.InternalServerError:
    request = httpx.Request("POST", GROQ_URL)
    response = httpx.Response(500, request=request)
    return groq.InternalServerError("internal error", response=response, body=None)


def _connection_error() -> groq.APIConnectionError:
    request = httpx.Request("POST", GROQ_URL)
    return groq.APIConnectionError(request=request)


def _auth_error() -> groq.AuthenticationError:
    request = httpx.Request("POST", GROQ_URL)
    response = httpx.Response(401, request=request)
    return groq.AuthenticationError("invalid api key", response=response, body=None)


def _not_found_error() -> groq.NotFoundError:
    request = httpx.Request("POST", GROQ_URL)
    response = httpx.Response(404, request=request)
    return groq.NotFoundError("model not found", response=response, body=None)


def _bad_request_error() -> groq.BadRequestError:
    request = httpx.Request("POST", GROQ_URL)
    response = httpx.Response(400, request=request)
    return groq.BadRequestError("invalid request parameters", response=response, body=None)


def _json_validate_failed_error() -> groq.BadRequestError:
    """The specific Groq 400 that means the model failed its own JSON-mode
    constraint on this generation - a sampling hiccup, not a bad request."""
    request = httpx.Request("POST", GROQ_URL)
    response = httpx.Response(400, request=request)
    body = {
        "error": {
            "message": "Failed to validate JSON. Please adjust your prompt.",
            "type": "invalid_request_error",
            "code": "json_validate_failed",
            "failed_generation": "",
        }
    }
    return groq.BadRequestError("json validate failed", response=response, body=body)


# --- Success on the first attempt --------------------------------------------


def test_groq_success_without_fallback(monkeypatch):
    client = _patch_groq(monkeypatch, ['{"content": "hello"}'])
    _forbid_gemini(monkeypatch)

    result = request_structured_json("system", "user", Dummy)

    assert result.content == "hello"
    assert client.call_count == 1


# --- Transient failure, successful retry -------------------------------------


@pytest.mark.parametrize(
    "error_factory",
    [_rate_limit_error, _internal_server_error, _connection_error],
)
def test_transient_groq_failure_then_successful_retry(monkeypatch, error_factory):
    client = _patch_groq(monkeypatch, [error_factory(), '{"content": "retried ok"}'])
    sleeps = _patch_sleep(monkeypatch)
    _forbid_gemini(monkeypatch)

    result = request_structured_json("system", "user", Dummy)

    assert result.content == "retried ok"
    assert client.call_count == 2
    assert len(sleeps) == 1  # exactly one backoff, no retry loop


def test_retry_backoff_honors_retry_after_header(monkeypatch):
    _patch_groq(monkeypatch, [_rate_limit_error(retry_after="7"), '{"content": "ok"}'])
    sleeps = _patch_sleep(monkeypatch)
    _forbid_gemini(monkeypatch)

    request_structured_json("system", "user", Dummy)

    assert sleeps == [7.0]


# --- Transient failure twice -> Gemini fallback succeeds ---------------------


def test_groq_failure_then_successful_gemini_fallback(monkeypatch):
    client = _patch_groq(monkeypatch, [_rate_limit_error(), _internal_server_error()])
    _patch_sleep(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    gemini_calls = []

    def fake_call_gemini(system_prompt, user_prompt):
        gemini_calls.append((system_prompt, user_prompt))
        return '{"content": "from gemini"}'

    monkeypatch.setattr("app.llm._call_gemini", fake_call_gemini)

    result = request_structured_json("system", "user", Dummy)

    assert result.content == "from gemini"
    assert client.call_count == 2  # Groq tried exactly twice, not more
    assert len(gemini_calls) == 1  # Gemini tried exactly once


def test_no_fallback_configured_raises_clear_error_after_retry(monkeypatch):
    client = _patch_groq(monkeypatch, [_rate_limit_error(), _internal_server_error()])
    _patch_sleep(monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _forbid_gemini(monkeypatch)

    with pytest.raises(LLMError, match="no Gemini fallback is configured"):
        request_structured_json("system", "user", Dummy)

    assert client.call_count == 2


# --- Permanent failures: no retry, no fallback -------------------------------


@pytest.mark.parametrize(
    "error_factory", [_auth_error, _not_found_error, _bad_request_error]
)
def test_permanent_groq_failure_is_not_retried_or_fallen_back(monkeypatch, error_factory):
    client = _patch_groq(monkeypatch, [error_factory()])
    _patch_sleep(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")  # configured but must be unused
    _forbid_gemini(monkeypatch)

    with pytest.raises(LLMError):
        request_structured_json("system", "user", Dummy)

    assert client.call_count == 1  # no retry


# --- A daily/quota-level rate limit is not worth retrying ---------------------


def test_daily_quota_exhaustion_skips_groq_retry_and_falls_back_to_gemini(monkeypatch):
    client = _patch_groq(monkeypatch, [_daily_quota_exhausted_error()])
    sleeps = _patch_sleep(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setattr("app.llm._call_gemini", lambda s, u: '{"content": "from gemini"}')

    result = request_structured_json("system", "user", Dummy)

    assert result.content == "from gemini"
    assert client.call_count == 1  # no pointless retry against an exhausted daily budget
    assert sleeps == []  # no backoff wasted either


def test_daily_quota_exhaustion_without_gemini_fails_cleanly(monkeypatch):
    client = _patch_groq(monkeypatch, [_daily_quota_exhausted_error()])
    sleeps = _patch_sleep(monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _forbid_gemini(monkeypatch)

    with pytest.raises(LLMError, match="not worth retrying"):
        request_structured_json("system", "user", Dummy)

    assert client.call_count == 1
    assert sleeps == []


def test_ordinary_rate_limit_with_short_retry_after_is_still_retried(monkeypatch):
    # A brief per-minute throttle (no daily-quota wording, short Retry-After)
    # must still get the normal one-retry treatment - this fix should not
    # make every 429 fail fast.
    client = _patch_groq(monkeypatch, [_rate_limit_error(retry_after="2"), '{"content": "ok"}'])
    _patch_sleep(monkeypatch)
    _forbid_gemini(monkeypatch)

    result = request_structured_json("system", "user", Dummy)

    assert result.content == "ok"
    assert client.call_count == 2


def test_long_retry_after_falls_back_to_gemini_without_waiting(monkeypatch):
    # Mirrors the real per-minute (TPM) case: a 19s Retry-After is too long
    # to wait out mid-request, so Groq isn't retried and Gemini takes over.
    client = _patch_groq(monkeypatch, [_rate_limit_error(retry_after="19")])
    sleeps = _patch_sleep(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setattr("app.llm._call_gemini", lambda s, u: '{"content": "from gemini"}')

    result = request_structured_json("system", "user", Dummy)

    assert result.content == "from gemini"
    assert client.call_count == 1
    assert sleeps == []


def test_rate_limit_fallback_output_is_schema_validated(monkeypatch):
    _patch_groq(monkeypatch, [_rate_limit_error(retry_after="120")])
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setattr("app.llm._call_gemini", lambda s, u: '{"wrong_field": "oops"}')

    with pytest.raises(SchemaValidationError):
        request_structured_json("system", "user", Dummy)


def test_rate_limit_then_gemini_failure_raises_controlled_error(monkeypatch):
    client = _patch_groq(monkeypatch, [_rate_limit_error(retry_after="120")])
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    def failing_gemini(system_prompt, user_prompt):
        raise _PermanentProviderError("Gemini request failed (permanent): quota exceeded")

    monkeypatch.setattr("app.llm._call_gemini", failing_gemini)

    with pytest.raises(LLMError, match="Gemini fallback also failed"):
        request_structured_json("system", "user", Dummy)

    assert client.call_count == 1


# --- json_validate_failed is the one 400 worth retrying -----------------------


def test_json_validate_failed_is_retried_like_a_transient_error(monkeypatch):
    client = _patch_groq(monkeypatch, [_json_validate_failed_error(), '{"content": "ok on retry"}'])
    sleeps = _patch_sleep(monkeypatch)
    _forbid_gemini(monkeypatch)

    result = request_structured_json("system", "user", Dummy)

    assert result.content == "ok on retry"
    assert client.call_count == 2
    assert len(sleeps) == 1


def test_json_validate_failed_falls_back_to_gemini_if_retry_also_fails(monkeypatch):
    client = _patch_groq(
        monkeypatch, [_json_validate_failed_error(), _json_validate_failed_error()]
    )
    _patch_sleep(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setattr("app.llm._call_gemini", lambda s, u: '{"content": "from gemini"}')

    result = request_structured_json("system", "user", Dummy)

    assert result.content == "from gemini"
    assert client.call_count == 2


# --- Gemini itself fails ------------------------------------------------------


def test_permanent_gemini_failure_returns_a_clear_error_without_retry(monkeypatch):
    _patch_groq(monkeypatch, [_rate_limit_error(), _internal_server_error()])
    _patch_sleep(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    gemini_calls = []

    def failing_gemini(system_prompt, user_prompt):
        gemini_calls.append(1)
        raise _PermanentProviderError("Gemini request failed (permanent): bad model name")

    monkeypatch.setattr("app.llm._call_gemini", failing_gemini)

    with pytest.raises(LLMError, match="Gemini fallback also failed") as exc_info:
        request_structured_json("system", "user", Dummy)

    # The original Groq failure must still be visible, not swallowed -
    # otherwise a user sees only "Gemini failed" with no idea why Groq did.
    assert "Groq request failed" in str(exc_info.value)
    assert "bad model name" in str(exc_info.value)
    assert len(gemini_calls) == 1  # permanent Gemini errors are not retried


def test_transient_gemini_failure_is_retried_once_then_succeeds(monkeypatch):
    _patch_groq(monkeypatch, [_rate_limit_error(), _internal_server_error()])
    sleeps = _patch_sleep(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    gemini_calls = []

    def flaky_gemini(system_prompt, user_prompt):
        gemini_calls.append(1)
        if len(gemini_calls) == 1:
            raise _TransientProviderError(
                "Gemini request failed (transient): overloaded", RuntimeError("503")
            )
        return '{"content": "from gemini on retry"}'

    monkeypatch.setattr("app.llm._call_gemini", flaky_gemini)

    result = request_structured_json("system", "user", Dummy)

    assert result.content == "from gemini on retry"
    assert len(gemini_calls) == 2  # Gemini itself got one retry
    assert len(sleeps) == 2  # one backoff before the Groq retry, one before the Gemini retry


def test_transient_gemini_failure_still_fails_cleanly_after_its_own_retry(monkeypatch):
    _patch_groq(monkeypatch, [_rate_limit_error(), _internal_server_error()])
    _patch_sleep(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    gemini_calls = []

    def always_overloaded(system_prompt, user_prompt):
        gemini_calls.append(1)
        raise _TransientProviderError(
            "Gemini request failed (transient): overloaded", RuntimeError("503")
        )

    monkeypatch.setattr("app.llm._call_gemini", always_overloaded)

    with pytest.raises(LLMError, match="also failed after its own retry"):
        request_structured_json("system", "user", Dummy)

    assert len(gemini_calls) == 2  # exactly one retry, no loop


def test_call_gemini_classifies_server_error_as_transient(monkeypatch):
    from google.genai import errors

    class FakeModels:
        def generate_content(self, **kwargs):
            raise errors.ServerError(503, {"error": {"message": "overloaded", "status": "UNAVAILABLE"}})

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = FakeModels()

    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setattr("google.genai.Client", FakeClient)

    from app.llm import _call_gemini

    with pytest.raises(_TransientProviderError):
        _call_gemini("system", "user")


def test_call_gemini_classifies_client_error_as_permanent(monkeypatch):
    from google.genai import errors

    class FakeModels:
        def generate_content(self, **kwargs):
            raise errors.ClientError(404, {"error": {"message": "model not found", "status": "NOT_FOUND"}})

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = FakeModels()

    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setattr("google.genai.Client", FakeClient)

    from app.llm import _call_gemini

    with pytest.raises(_PermanentProviderError):
        _call_gemini("system", "user")


# --- Invalid Gemini output is rejected, not silently accepted ---------------


def test_invalid_gemini_json_is_rejected(monkeypatch):
    _patch_groq(monkeypatch, [_rate_limit_error(), _internal_server_error()])
    _patch_sleep(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setattr("app.llm._call_gemini", lambda s, u: "not valid json")

    with pytest.raises(LLMError, match="not valid JSON"):
        request_structured_json("system", "user", Dummy)


def test_gemini_response_failing_schema_is_rejected(monkeypatch):
    _patch_groq(monkeypatch, [_rate_limit_error(), _internal_server_error()])
    _patch_sleep(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    # Valid JSON, but missing the required "content" field for Dummy.
    monkeypatch.setattr("app.llm._call_gemini", lambda s, u: '{"wrong_field": "oops"}')

    with pytest.raises(SchemaValidationError):
        request_structured_json("system", "user", Dummy)
