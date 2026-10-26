"""Groq (primary) and Gemini (optional fallback) clients, behind a single
reusable structured-JSON request helper with bounded retry and fallback.

Retry/fallback policy (see `request_structured_json`):
  1. Call Groq once.
  2. A transient Groq error (connection/timeout, rate limit, 5xx) is
     retried exactly once, after a short backoff that honors a provider
     Retry-After header when one is given. A rate limit specifically is
     only retried against Groq if that Retry-After is short (a brief
     per-minute throttle); a long one (a daily/quota-level limit, or a
     per-minute limit that won't clear for a while) is not worth waiting
     out mid-request, so it skips the Groq retry and goes straight to the
     Gemini fallback (step 3). Gemini's own 429s are 4xx and already
     permanent.
  3. If that retry also fails (or was skipped as above), fall back to
     Gemini - but only if GEMINI_API_KEY is configured. A transient Gemini
     error (5xx / server overload, connection/timeout) gets the same
     one-retry treatment as Groq; a permanent one (auth, bad model, bad
     request) fails immediately.
  4. A permanent Groq error (auth, invalid model, malformed request, ...)
     is never retried and never falls back - it fails immediately, since
     retrying or switching providers would just mask a config problem.
     The one exception is a 400 with code "json_validate_failed": that
     means the model itself failed to produce valid JSON for Groq's own
     response_format constraint, which is a generation hiccup rather than
     a malformed request, so it is treated as transient (step 2 above).
  5. Both providers' output is parsed and validated against the same
     Pydantic model. Malformed JSON or a schema mismatch is not "fixed"
     here - that is the planner's job (its existing single
     schema-correction attempt, unchanged by any of this).
"""

import json
import logging
import os
import time
from typing import TypeVar

from groq import (
    APIConnectionError as GroqAPIConnectionError,
    BadRequestError as GroqBadRequestError,
    Groq,
    GroqError,
    InternalServerError as GroqInternalServerError,
    RateLimitError as GroqRateLimitError,
)
from pydantic import BaseModel, ValidationError

DEFAULT_MODEL = "llama-3.3-70b-versatile"
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
RETRY_BACKOFF_SECONDS = 1.0
# Neither SDK bounds requests tightly by default (Groq's default read timeout
# is 60s; google-genai has NO timeout at all unless one is set, so a stalled
# request hangs forever). A fixed, fairly generous ceiling means a stuck
# provider fails fast enough for our retry/fallback to actually run, instead
# of leaving the whole request looking frozen.
PROVIDER_TIMEOUT_SECONDS = 25
# A rate-limit Retry-After longer than this means a daily/quota-level limit,
# not a brief per-minute throttle - waiting it out mid-request would block
# the user for a long, almost certainly futile retry, so that's treated as
# unworthwhile and failed fast instead.
MAX_WORTHWHILE_RETRY_SECONDS = 10.0

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)

# APITimeoutError is a subclass of APIConnectionError, so catching the
# latter covers both. These are the only errors worth retrying/falling
# back on; every other GroqError (auth, not found, ...) is treated as
# permanent. BadRequestError is handled separately below: most 400s mean
# our request was malformed (permanent), but a json_validate_failed 400
# means Groq's own JSON-mode constraint failed on this particular
# generation - a sampling hiccup, not a bad request - so that one specific
# code is treated as transient instead.
TRANSIENT_GROQ_ERRORS = (GroqAPIConnectionError, GroqRateLimitError, GroqInternalServerError)
RETRIABLE_BAD_REQUEST_CODES = {"json_validate_failed"}


def _is_retriable_bad_request(exc: GroqBadRequestError) -> bool:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        code = body.get("error", {}).get("code")
        return code in RETRIABLE_BAD_REQUEST_CODES
    return False


DAILY_QUOTA_MARKERS = ("per day", "tpd", "rpd")


def _is_daily_quota_exhausted(exc: GroqRateLimitError) -> bool:
    """Groq's daily token/request quota errors say so explicitly in the
    message (e.g. "...on tokens per day (TPD): Limit 200000, Used 199207").
    A near-exhausted daily budget won't meaningfully refill in a few
    seconds for a request needing thousands of tokens, even if the
    message's own suggested wait is short - so this is checked regardless
    of Retry-After."""
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return False
    message = str(body.get("error", {}).get("message", "")).lower()
    return any(marker in message for marker in DAILY_QUOTA_MARKERS)


def _is_worth_retrying(exc: Exception) -> bool:
    """False when the failure clearly isn't something a short wait fixes:
    an explicit daily/quota-level limit, or a Retry-After far longer than a
    brief per-minute throttle. With neither signal, default to retrying
    (unchanged prior behavior)."""
    if isinstance(exc, GroqRateLimitError) and _is_daily_quota_exhausted(exc):
        return False
    response = getattr(exc, "response", None)
    retry_after = response.headers.get("retry-after") if response is not None else None
    if retry_after is None:
        return True
    try:
        return float(retry_after) <= MAX_WORTHWHILE_RETRY_SECONDS
    except ValueError:
        return True


class LLMError(Exception):
    """Raised when no provider could produce a usable response."""


class SchemaValidationError(LLMError):
    """Raised when a provider's JSON reply doesn't match the expected schema.

    Carries the raw parsed JSON and the validation error so a caller (the
    planner) can retry with a prompt that corrects the specific problem.
    """

    def __init__(self, message: str, raw_response: dict):
        super().__init__(message)
        self.raw_response = raw_response


class _TransientProviderError(Exception):
    """Internal: a provider call failed in a way worth retrying/falling back on.

    `retry_same_provider=False` marks a failure (a rate limit that won't
    clear soon) where waiting and re-asking the same provider is pointless,
    so the caller should move straight to the fallback provider."""

    def __init__(self, message: str, original: Exception, retry_same_provider: bool = True):
        super().__init__(message)
        self.original = original
        self.retry_same_provider = retry_same_provider


class _PermanentProviderError(Exception):
    """Internal: a Groq call failed in a way that must not be retried."""


def get_groq_api_key() -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return api_key


def get_model_name() -> str:
    return os.getenv("GROQ_MODEL", DEFAULT_MODEL)


def get_groq_client() -> Groq:
    # max_retries=0: we own the single retry decision at the app level
    # instead of stacking our retry on top of the SDK's own automatic one.
    return Groq(api_key=get_groq_api_key(), max_retries=0, timeout=PROVIDER_TIMEOUT_SECONDS)


def get_gemini_api_key() -> str | None:
    """Fallback is optional: returns None (never raises) if unconfigured."""
    return os.getenv("GEMINI_API_KEY") or None


def get_gemini_model_name() -> str:
    return os.getenv("GEMINI_FALLBACK_MODEL", DEFAULT_GEMINI_MODEL)


def _retry_delay_seconds(exc: Exception) -> float:
    """Honor a provider-supplied Retry-After header when the error has one."""
    response = getattr(exc, "response", None)
    retry_after = response.headers.get("retry-after") if response is not None else None
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return RETRY_BACKOFF_SECONDS


def _call_groq(system_prompt: str, user_prompt: str, max_tokens: int | None = None) -> str:
    """Call Groq once; return the raw response text.

    Raises _TransientProviderError / _PermanentProviderError so the caller
    can decide whether to retry or fail fast - these never escape this
    module.
    """
    client = get_groq_client()
    limit = {"max_completion_tokens": max_tokens} if max_tokens else {}
    model = get_model_name()
    if "gpt-oss" in model:
        # Reasoning models spend max_completion_tokens on hidden thinking
        # before the JSON; "low" keeps that from eating the whole cap.
        limit["reasoning_effort"] = "low"
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            **limit,
        )
    except GroqRateLimitError as exc:
        if _is_worth_retrying(exc):
            raise _TransientProviderError(f"Groq request failed (transient): {exc}", exc) from exc
        # Not a permanent config problem: a different provider can still
        # serve this request, so hand off to the fallback without a pointless
        # wait on Groq.
        raise _TransientProviderError(
            f"Groq request failed (rate/quota limit, not worth retrying now): {exc}",
            exc,
            retry_same_provider=False,
        ) from exc
    except TRANSIENT_GROQ_ERRORS as exc:
        raise _TransientProviderError(f"Groq request failed (transient): {exc}", exc) from exc
    except GroqBadRequestError as exc:
        if _is_retriable_bad_request(exc):
            raise _TransientProviderError(
                f"Groq request failed (model could not produce valid JSON): {exc}", exc
            ) from exc
        raise _PermanentProviderError(f"Groq request failed (permanent): {exc}") from exc
    except GroqError as exc:
        raise _PermanentProviderError(f"Groq request failed (permanent): {exc}") from exc

    usage = getattr(completion, "usage", None)
    if usage is not None:
        logger.info(
            "LLM provider=groq prompt_tokens=%s completion_tokens=%s",
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None),
        )
    choice = completion.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        # Hit the output cap (reasoning models also spend it on thinking), so
        # the JSON is cut off. Re-asking Groq with the same cap would just
        # truncate again; hand off to the fallback instead.
        raise _TransientProviderError(
            "Groq response was truncated at the output-token limit",
            RuntimeError("truncated"),
            retry_same_provider=False,
        )
    return choice.message.content


def _call_gemini(system_prompt: str, user_prompt: str, max_tokens: int | None = None) -> str:
    """Call Gemini once; return the raw response text.

    Raises _TransientProviderError / _PermanentProviderError, mirroring
    _call_groq, so a transient Gemini failure (overload, connection/timeout)
    gets the same one-retry treatment Groq gets.
    """
    from google import genai
    from google.genai import errors, types

    client = genai.Client(
        api_key=get_gemini_api_key(),
        http_options=types.HttpOptions(timeout=PROVIDER_TIMEOUT_SECONDS * 1000),  # ms
    )
    try:
        response = client.models.generate_content(
            model=get_gemini_model_name(),
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=max_tokens or None,
            ),
        )
    except errors.ServerError as exc:
        raise _TransientProviderError(f"Gemini request failed (transient): {exc}", exc) from exc
    except errors.ClientError as exc:
        raise _PermanentProviderError(f"Gemini request failed (permanent): {exc}") from exc
    except Exception as exc:
        # Network-level failures (timeout, connection reset, ...) aren't
        # APIError subclasses - treat them as transient too, same as Groq's
        # APIConnectionError/APITimeoutError.
        raise _TransientProviderError(f"Gemini request failed (transient): {exc}", exc) from exc

    candidates = getattr(response, "candidates", None) or []
    finish_reason = getattr(candidates[0], "finish_reason", None) if candidates else None
    if getattr(finish_reason, "name", finish_reason) == "MAX_TOKENS":
        raise _PermanentProviderError(
            "Gemini response was truncated at the output-token limit"
        )
    if not response.text:
        raise _TransientProviderError(
            "Gemini returned an empty response", RuntimeError("empty response")
        )
    return response.text


def _parse_and_validate(content: str, response_model: type[T]) -> T:
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM response was not valid JSON: {exc}") from exc
    try:
        return response_model.model_validate(raw)
    except ValidationError as exc:
        raise SchemaValidationError(
            f"LLM response did not match the expected schema: {exc}", raw
        ) from exc


def request_structured_json(
    system_prompt: str,
    user_prompt: str,
    response_model: type[T],
    max_tokens: int | None = None,
) -> T:
    """Ask the LLM for JSON matching `response_model` and return it parsed.

    The output-token cap is `max_tokens` if given, else the model class's
    own `max_output_tokens` attribute if it declares one, else none. A
    response cut off by the cap is treated like any other provider failure
    (see the module docstring), never parsed as partial JSON.

    See the module docstring for the full retry/fallback policy. Raises
    LLMError if no provider produced a usable response, or
    SchemaValidationError if a response was well-formed JSON but did not
    match the schema.
    """
    limit = max_tokens or getattr(response_model, "max_output_tokens", None)
    # Only passed when set, so a provider call never sees an argument it
    # doesn't need.
    extra = {"max_tokens": limit} if limit else {}
    groq_error: _TransientProviderError | None = None
    raw_content = ""
    try:
        raw_content = _call_groq(system_prompt, user_prompt, **extra)
    except _PermanentProviderError as exc:
        logger.error("LLM provider=groq category=permanent")
        raise LLMError(str(exc)) from exc
    except _TransientProviderError as first_error:
        groq_error = first_error
        logger.warning(
            "LLM provider=groq category=%s",
            "transient" if first_error.retry_same_provider else "rate_limited",
        )
        if first_error.retry_same_provider:
            time.sleep(_retry_delay_seconds(first_error.original))
            try:
                raw_content = _call_groq(system_prompt, user_prompt, **extra)
                groq_error = None
            except _PermanentProviderError as exc:
                logger.error("LLM provider=groq category=permanent")
                raise LLMError(str(exc)) from exc
            except _TransientProviderError as second_error:
                groq_error = second_error

    if groq_error is not None:
        raw_content = _fall_back_to_gemini(system_prompt, user_prompt, groq_error, **extra)

    return _parse_and_validate(raw_content, response_model)


def _fall_back_to_gemini(
    system_prompt: str,
    user_prompt: str,
    groq_error: _TransientProviderError,
    **extra: int,
) -> str:
    if not get_gemini_api_key():
        raise LLMError(
            "Groq failed, and no Gemini fallback is configured "
            f"(set GEMINI_API_KEY to enable it): {groq_error}"
        ) from groq_error

    logger.warning("LLM falling back from provider=groq to provider=gemini")
    try:
        return _call_gemini(system_prompt, user_prompt, **extra)
    except _PermanentProviderError as exc:
        logger.error("LLM provider=gemini category=permanent")
        raise LLMError(
            f"Groq failed ({groq_error}), and Gemini fallback also failed: {exc}"
        ) from exc
    except _TransientProviderError as first_gemini_error:
        logger.warning("LLM provider=gemini category=transient")
        time.sleep(_retry_delay_seconds(first_gemini_error.original))
        try:
            return _call_gemini(system_prompt, user_prompt, **extra)
        except (_TransientProviderError, _PermanentProviderError) as exc:
            logger.error("LLM provider=gemini category=failed_after_retry")
            raise LLMError(
                f"Groq failed ({groq_error}), and Gemini fallback also failed "
                f"after its own retry: {exc}"
            ) from exc
