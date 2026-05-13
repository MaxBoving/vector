import os
import re
import json
import asyncio
import random
import time
import uuid
import contextvars
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import openai
import anthropic
from dotenv import load_dotenv

load_dotenv()

DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
DEFAULT_ANTHROPIC_MODELS = [
    "claude-sonnet-4-20250514",
    "claude-3-7-sonnet-20250219",
    "claude-3-5-sonnet-20241022",
]
DEFAULT_ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODELS[0])
DEFAULT_ANTHROPIC_SIMPLE_MODEL = os.getenv("ANTHROPIC_SIMPLE_MODEL", "claude-3-haiku-20240307")
TRANSPORT_RETRY_ATTEMPTS = max(1, int(os.getenv("LLM_TRANSPORT_RETRY_ATTEMPTS", "3")))
TRANSPORT_RETRY_BASE_DELAY_SECONDS = float(os.getenv("LLM_TRANSPORT_RETRY_BASE_DELAY_SECONDS", "0.25"))
TRANSPORT_RETRY_MAX_DELAY_SECONDS = float(os.getenv("LLM_TRANSPORT_RETRY_MAX_DELAY_SECONDS", "2.5"))
_LLM_TRACE_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("llm_trace_context", default={})
_LLM_TELEMETRY_EVENTS: list[dict[str, Any]] = []
_LLM_TELEMETRY_LIMIT = 20000


def _resolve_default_model() -> str:
    explicit = os.getenv("LLM_DEFAULT_MODEL")
    if explicit:
        return explicit
    if os.getenv("ANTHROPIC_API_KEY"):
        return DEFAULT_ANTHROPIC_MODEL
    return DEFAULT_OPENAI_MODEL


def set_llm_trace_context(**fields: Any):
    context = dict(_LLM_TRACE_CONTEXT.get() or {})
    for key, value in fields.items():
        if value is not None:
            context[key] = value
    return _LLM_TRACE_CONTEXT.set(context)


def reset_llm_trace_context(token: Any) -> None:
    _LLM_TRACE_CONTEXT.reset(token)


def get_llm_trace_context() -> dict[str, Any]:
    return dict(_LLM_TRACE_CONTEXT.get() or {})


def reset_llm_telemetry() -> None:
    _LLM_TELEMETRY_EVENTS.clear()


def get_llm_telemetry_events() -> list[dict[str, Any]]:
    return list(_LLM_TELEMETRY_EVENTS)


def _record_llm_telemetry(event: dict[str, Any]) -> None:
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        **get_llm_trace_context(),
        **event,
    }
    _LLM_TELEMETRY_EVENTS.append(payload)
    if len(_LLM_TELEMETRY_EVENTS) > _LLM_TELEMETRY_LIMIT:
        del _LLM_TELEMETRY_EVENTS[: len(_LLM_TELEMETRY_EVENTS) - _LLM_TELEMETRY_LIMIT]


def _serialize_exception(error: Exception) -> dict[str, Any]:
    details: dict[str, Any] = {
        "error_type": type(error).__name__,
        "message": str(error),
    }
    for attr in ("status_code", "request_id", "body"):
        if hasattr(error, attr):
            try:
                details[attr] = getattr(error, attr)
            except Exception:
                details[attr] = "<unavailable>"
    if error.__cause__ is not None:
        details["cause_type"] = type(error.__cause__).__name__
        details["cause_message"] = str(error.__cause__)
    return details

class LLMClient:
    def __init__(self, model: Optional[str] = None):
        self.model = model or _resolve_default_model()
        self.total_tokens_used = 0
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        
        if self.openai_key:
            self.openai_client = openai.OpenAI(api_key=self.openai_key)
            self.openai_async = openai.AsyncOpenAI(api_key=self.openai_key)
        else:
            self.openai_client = self.openai_async = None

        if self.anthropic_key:
            self.anthropic_client = anthropic.Anthropic(api_key=self.anthropic_key)
            self.anthropic_async = anthropic.AsyncAnthropic(api_key=self.anthropic_key)
        else:
            self.anthropic_client = self.anthropic_async = None

    def _anthropic_candidates(self) -> List[str]:
        ordered = [self.model, DEFAULT_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_SIMPLE_MODEL, *DEFAULT_ANTHROPIC_MODELS]
        deduped: List[str] = []
        for candidate in ordered:
            if candidate and "claude" in candidate.lower() and candidate not in deduped:
                deduped.append(candidate)
        return deduped

    def _classify_anthropic_error(self, error: Exception) -> str:
        message = str(error).lower()
        if isinstance(error, anthropic.NotFoundError):
            return "missing_model_access"
        if isinstance(error, anthropic.AuthenticationError):
            return "auth_error"
        if isinstance(error, anthropic.PermissionDeniedError):
            return "permission_denied"
        if isinstance(error, anthropic.RateLimitError):
            return "rate_limit"
        if isinstance(error, anthropic.APITimeoutError):
            return "timeout"
        if isinstance(error, anthropic.APIConnectionError):
            return "connection_error"
        if "credit balance" in message or "insufficient credit" in message or "billing" in message:
            return "billing_or_credit"
        if "overloaded" in message or "overload" in message or "capacity" in message:
            return "service_overload"
        if isinstance(error, anthropic.InternalServerError):
            return "internal_server_error"
        if isinstance(error, anthropic.BadRequestError):
            return "bad_request"
        return "api_error"

    def _classify_provider_error(self, provider: str, error: Exception) -> str:
        if provider == "anthropic":
            return self._classify_anthropic_error(error)
        message = str(error).lower()
        if isinstance(error, openai.RateLimitError) or "rate limit" in message or "429" in message:
            return "rate_limit"
        if isinstance(error, openai.AuthenticationError):
            return "auth_error"
        if isinstance(error, openai.PermissionDeniedError):
            return "permission_denied"
        if isinstance(error, openai.APIConnectionError):
            return "connection_error"
        if isinstance(error, openai.APITimeoutError):
            return "timeout"
        if isinstance(error, openai.BadRequestError):
            return "bad_request"
        if isinstance(error, openai.NotFoundError):
            return "missing_model_access"
        return "api_error"

    def _log_first_failure(self, *, provider: str, model: str, error: Exception, async_mode: bool = False) -> None:
        reason = self._classify_provider_error(provider, error)
        mode_label = "async " if async_mode else ""
        print(
            f"[LLM_ERROR]: provider={provider} {mode_label}model='{model}' "
            f"reason={reason} details={json.dumps(_serialize_exception(error), ensure_ascii=True)}"
        )

    def _is_transport_error(self, *, provider: str, error: Exception) -> bool:
        if provider == "anthropic":
            if isinstance(error, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
                return True
        if provider == "openai":
            if isinstance(error, (openai.APIConnectionError, openai.APITimeoutError)):
                return True
        message = str(error).lower()
        transport_markers = (
            "connection error",
            "connecterror",
            "connection reset",
            "temporary failure in name resolution",
            "nodename nor servname provided",
            "name or service not known",
            "timed out",
        )
        return any(marker in message for marker in transport_markers)

    def _retry_delay(self, attempt_index: int) -> float:
        # Exponential backoff + jitter. attempt_index starts at 1 for first retry.
        delay = min(
            TRANSPORT_RETRY_MAX_DELAY_SECONDS,
            TRANSPORT_RETRY_BASE_DELAY_SECONDS * (2 ** max(0, attempt_index - 1)),
        )
        jitter = random.uniform(0.0, delay * 0.2)
        return delay + jitter

    def _log_anthropic_fallback(self, *, model: str, error: Exception, async_mode: bool = False) -> None:
        reason = self._classify_anthropic_error(error)
        mode_label = "async " if async_mode else ""
        print(
            f"[FALLBACK]: Anthropic {mode_label}request failed for model '{model}'. "
            f"reason={reason} error_type={type(error).__name__} message={error}"
        )
        _record_llm_telemetry(
            {
                "event": "provider_error",
                "provider": "anthropic",
                "model": model,
                "async_mode": async_mode,
                "reason": reason,
                "error": _serialize_exception(error),
            }
        )

    def _complete_anthropic_with_fallbacks(self, prompt: str, system_prompt: str) -> str:
        last_not_found: Optional[Exception] = None
        for candidate in self._anthropic_candidates():
            try:
                self.model = candidate
                res = self.anthropic_client.messages.create(
                    model=self.model, max_tokens=4096, system=system_prompt, messages=[{"role":"user","content":prompt}]
                )
                return res.content[0].text
            except anthropic.NotFoundError as e:
                last_not_found = e
                _record_llm_telemetry(
                    {
                        "event": "provider_error",
                        "provider": "anthropic",
                        "model": candidate,
                        "async_mode": False,
                        "reason": self._classify_anthropic_error(e),
                        "error": _serialize_exception(e),
                    }
                )
                continue
        if last_not_found:
            self._log_anthropic_fallback(model=self.model, error=last_not_found)
            return self._fallback_to_openai(prompt, system_prompt)
        raise RuntimeError("No Anthropic models available for fallback.")

    async def _complete_anthropic_async_with_fallbacks(self, prompt: str, system_prompt: str) -> str:
        last_not_found: Optional[Exception] = None
        for candidate in self._anthropic_candidates():
            try:
                self.model = candidate
                res = await self.anthropic_async.messages.create(
                    model=self.model, max_tokens=4096, system=system_prompt, messages=[{"role":"user","content":prompt}]
                )
                return res.content[0].text
            except anthropic.NotFoundError as e:
                last_not_found = e
                _record_llm_telemetry(
                    {
                        "event": "provider_error",
                        "provider": "anthropic",
                        "model": candidate,
                        "async_mode": True,
                        "reason": self._classify_anthropic_error(e),
                        "error": _serialize_exception(e),
                    }
                )
                continue
        if last_not_found:
            self._log_anthropic_fallback(model=self.model, error=last_not_found, async_mode=True)
            return await self._fallback_to_openai_async(prompt, system_prompt)
        raise RuntimeError("No Anthropic models available for async fallback.")

    def _fallback_to_openai(self, prompt: str, system_prompt: str) -> str:
        print(f"[FALLBACK]: Anthropic model '{self.model}' unavailable. Switching to {DEFAULT_OPENAI_MODEL}...")
        _record_llm_telemetry(
            {
                "event": "fallback",
                "from_provider": "anthropic",
                "to_provider": "openai",
                "from_model": self.model,
                "to_model": DEFAULT_OPENAI_MODEL,
                "async_mode": False,
            }
        )
        original_model = self.model
        self.model = DEFAULT_OPENAI_MODEL
        try:
            return self._complete_openai(prompt, system_prompt)
        finally:
            self.model = original_model

    async def _fallback_to_openai_async(self, prompt: str, system_prompt: str) -> str:
        print(f"[FALLBACK]: Anthropic model '{self.model}' unavailable async. Switching to {DEFAULT_OPENAI_MODEL}...")
        _record_llm_telemetry(
            {
                "event": "fallback",
                "from_provider": "anthropic",
                "to_provider": "openai",
                "from_model": self.model,
                "to_model": DEFAULT_OPENAI_MODEL,
                "async_mode": True,
            }
        )
        original_model = self.model
        self.model = DEFAULT_OPENAI_MODEL
        try:
            return await self._complete_openai_async(prompt, system_prompt)
        finally:
            self.model = original_model

    def complete(self, prompt: str, system_prompt: str = "You are the Brain Router.") -> str:
        provider = "anthropic" if "claude" in self.model.lower() else "openai"
        call_id = str(uuid.uuid4())
        started = time.perf_counter()
        _record_llm_telemetry(
            {
                "event": "request_start",
                "call_id": call_id,
                "provider": provider,
                "model": self.model,
                "async_mode": False,
            }
        )
        attempt = 0
        while True:
            attempt += 1
            try:
                if provider == "anthropic":
                    output = self._complete_anthropic(prompt, system_prompt)
                else:
                    output = self._complete_openai(prompt, system_prompt)
                _record_llm_telemetry(
                    {
                        "event": "request_success",
                        "call_id": call_id,
                        "provider": provider,
                        "model": self.model,
                        "async_mode": False,
                        "attempt": attempt,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    }
                )
                return output
            except Exception as error:
                retryable = self._is_transport_error(provider=provider, error=error)
                if retryable and attempt < TRANSPORT_RETRY_ATTEMPTS:
                    delay = self._retry_delay(attempt)
                    print(
                        f"[LLM_RETRY]: provider={provider} model='{self.model}' "
                        f"attempt={attempt}/{TRANSPORT_RETRY_ATTEMPTS} delay_s={delay:.2f} "
                        f"reason={self._classify_provider_error(provider, error)}"
                    )
                    _record_llm_telemetry(
                        {
                            "event": "request_retry",
                            "call_id": call_id,
                            "provider": provider,
                            "model": self.model,
                            "async_mode": False,
                            "attempt": attempt,
                            "delay_seconds": round(delay, 3),
                            "reason": self._classify_provider_error(provider, error),
                            "error": _serialize_exception(error),
                        }
                    )
                    time.sleep(delay)
                    continue
                self._log_first_failure(provider=provider, model=self.model, error=error, async_mode=False)
                _record_llm_telemetry(
                    {
                        "event": "request_error",
                        "call_id": call_id,
                        "provider": provider,
                        "model": self.model,
                        "async_mode": False,
                        "attempt": attempt,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                        "reason": self._classify_provider_error(provider, error),
                        "error": _serialize_exception(error),
                    }
                )
                raise

    async def complete_async(self, prompt: str, system_prompt: str = "You are the Brain.") -> str:
        provider = "anthropic" if "claude" in self.model.lower() else "openai"
        call_id = str(uuid.uuid4())
        started = time.perf_counter()
        _record_llm_telemetry(
            {
                "event": "request_start",
                "call_id": call_id,
                "provider": provider,
                "model": self.model,
                "async_mode": True,
            }
        )
        attempt = 0
        while True:
            attempt += 1
            try:
                if provider == "anthropic":
                    output = await self._complete_anthropic_async(prompt, system_prompt)
                else:
                    output = await self._complete_openai_async(prompt, system_prompt)
                _record_llm_telemetry(
                    {
                        "event": "request_success",
                        "call_id": call_id,
                        "provider": provider,
                        "model": self.model,
                        "async_mode": True,
                        "attempt": attempt,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    }
                )
                return output
            except Exception as error:
                retryable = self._is_transport_error(provider=provider, error=error)
                if retryable and attempt < TRANSPORT_RETRY_ATTEMPTS:
                    delay = self._retry_delay(attempt)
                    print(
                        f"[LLM_RETRY]: provider={provider} async model='{self.model}' "
                        f"attempt={attempt}/{TRANSPORT_RETRY_ATTEMPTS} delay_s={delay:.2f} "
                        f"reason={self._classify_provider_error(provider, error)}"
                    )
                    _record_llm_telemetry(
                        {
                            "event": "request_retry",
                            "call_id": call_id,
                            "provider": provider,
                            "model": self.model,
                            "async_mode": True,
                            "attempt": attempt,
                            "delay_seconds": round(delay, 3),
                            "reason": self._classify_provider_error(provider, error),
                            "error": _serialize_exception(error),
                        }
                    )
                    await asyncio.sleep(delay)
                    continue
                self._log_first_failure(provider=provider, model=self.model, error=error, async_mode=True)
                _record_llm_telemetry(
                    {
                        "event": "request_error",
                        "call_id": call_id,
                        "provider": provider,
                        "model": self.model,
                        "async_mode": True,
                        "attempt": attempt,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                        "reason": self._classify_provider_error(provider, error),
                        "error": _serialize_exception(error),
                    }
                )
                raise

    def _complete_openai(self, prompt: str, system_prompt: str) -> str:
        if not self.openai_client: return "MOCK: No OpenAI Key"
        res = self.openai_client.chat.completions.create(
            model=self.model, messages=[{"role":"system","content":system_prompt},{"role":"user","content":prompt}], temperature=0.1
        )
        if res.usage: self.total_tokens_used += res.usage.total_tokens
        return res.choices[0].message.content

    async def _complete_openai_async(self, prompt: str, system_prompt: str) -> str:
        if not self.openai_async: return "MOCK: No OpenAI Key"
        res = await self.openai_async.chat.completions.create(
            model=self.model, messages=[{"role":"system","content":system_prompt},{"role":"user","content":prompt}], temperature=0.1
        )
        if res.usage: self.total_tokens_used += res.usage.total_tokens
        return res.choices[0].message.content

    def _complete_anthropic(self, prompt: str, system_prompt: str) -> str:
        if not self.anthropic_client: return "MOCK: No Anthropic Key"
        # Force the Agent Eval format if not already present in response instructions
        if "AGENT EVAL" in system_prompt and "MANDATORY" not in system_prompt:
            system_prompt += "\n\nMANDATORY: You MUST include the '--- AGENT EVAL ---' section exactly as specified in the FORMAT instructions."
        
        try:
            return self._complete_anthropic_with_fallbacks(prompt, system_prompt)
        except (
            anthropic.InternalServerError,
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
            anthropic.RateLimitError,
            anthropic.BadRequestError,
            anthropic.APIError,
        ) as e:
            self._log_anthropic_fallback(model=self.model, error=e)
            err_msg = str(e).lower()
            if (
                "overloaded" in err_msg
                or "credit balance" in err_msg
                or isinstance(e, (anthropic.RateLimitError, anthropic.APIConnectionError))
            ):
                return self._fallback_to_openai(prompt, system_prompt)
            raise e

    async def _complete_anthropic_async(self, prompt: str, system_prompt: str) -> str:
        if not self.anthropic_async: return "MOCK: No Anthropic Key"
        # Force the Agent Eval format
        if "AGENT EVAL" in system_prompt and "MANDATORY" not in system_prompt:
            system_prompt += "\n\nMANDATORY: You MUST include the '--- AGENT EVAL ---' section exactly as specified in the FORMAT instructions."

        try:
            return await self._complete_anthropic_async_with_fallbacks(prompt, system_prompt)
        except (
            anthropic.InternalServerError,
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
            anthropic.RateLimitError,
            anthropic.BadRequestError,
            anthropic.APIError,
        ) as e:
            self._log_anthropic_fallback(model=self.model, error=e, async_mode=True)
            err_msg = str(e).lower()
            if (
                "overloaded" in err_msg
                or "credit balance" in err_msg
                or isinstance(e, (anthropic.RateLimitError, anthropic.APIConnectionError))
            ):
                return await self._fallback_to_openai_async(prompt, system_prompt)
            raise e

    def complete_structured(self, prompt: str, response_model: Any, system_prompt: str) -> Any:
        schema = response_model.model_json_schema()
        json_prompt = f"{prompt}\n\nReturn ONLY valid JSON matching this schema: {schema}"
        raw = self.complete(json_prompt, system_prompt)

        result, parse_error, raw_json = self._try_parse_structured(raw, response_model)
        if result is not None:
            return result

        # Repair pass: send the broken JSON + validation error back and ask for a fix
        if raw_json and parse_error:
            repair_prompt = (
                f"The following JSON failed validation.\n\n"
                f"Error: {parse_error}\n\n"
                f"Broken JSON:\n{raw_json}\n\n"
                f"Fix the JSON so it exactly matches this schema and return ONLY the corrected JSON:\n{schema}"
            )
            repaired_raw = self.complete(repair_prompt, system_prompt)
            result, _, _ = self._try_parse_structured(repaired_raw, response_model)
            if result is not None:
                return result
            print(f"[structured_completion] Repair pass also failed. Schema: {response_model.__name__}")

        return None

    def _try_parse_structured(
        self, raw: str, response_model: Any
    ) -> tuple[Any, Optional[str], Optional[str]]:
        """Extract, parse, and validate JSON from a raw LLM response.

        Returns (parsed_model | None, error_message | None, raw_json_str | None).
        """
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            return None, "No JSON object found in response", None
        raw_json = match.group(0)
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            return None, f"JSONDecodeError: {exc}", raw_json
        try:
            return response_model(**data), None, raw_json
        except Exception as exc:
            return None, f"Validation error: {exc}", raw_json
