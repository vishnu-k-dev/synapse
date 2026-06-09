from __future__ import annotations

import time
import uuid
from typing import Any, TypeVar

import anthropic
import openai
from pydantic import BaseModel, ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import LLMError
from app.llm.registry import PromptRegistry, get_prompt_registry

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

# ── Token cost table (USD per 1K tokens) ──────────────────────────────────────
_COST_TABLE: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.000150, "output": 0.000600},
    "gpt-4o-mini-2024-07-18": {"input": 0.000150, "output": 0.000600},
    "text-embedding-3-small": {"input": 0.000020, "output": 0.0},
    "claude-haiku-4-5-20251001": {"input": 0.000800, "output": 0.004000},
}


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = _COST_TABLE.get(model, {"input": 0.0, "output": 0.0})
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1000.0


class StructuredResponse(BaseModel, extra="allow"):
    model_used: str
    prompt_key: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


class LLMClient:
    """Centralized LLM client — handles routing, retries, validation, and cost logging.

    All structured calls go through complete_structured().
    All embedding calls go through embed_batch().
    Nothing else should call OpenAI/Anthropic directly.
    """

    def __init__(
        self,
        registry: PromptRegistry | None = None,
    ) -> None:
        settings = get_settings()
        self._openai = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        self._anthropic = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._settings = settings
        self._registry = registry or get_prompt_registry()

    async def complete_structured(
        self,
        prompt_key: str,
        prompt_vars: dict[str, Any],
        response_model: type[T],
        job_id: str | None = None,
        stage: str = "unknown",
        experiment_id: str | None = None,
    ) -> T:
        """Call LLM with a registered prompt, validate against response_model, log the call.

        Retries up to 3 times on validation failure before raising LLMError.
        Falls back to Anthropic if OpenAI raises a non-validation error.
        """
        prompt_def = self._registry.get(prompt_key)
        system_text, user_text = self._registry.render(prompt_key, prompt_vars)

        last_error: Exception | None = None

        for attempt in range(3):
            start = time.monotonic()
            model = prompt_def.model
            input_tokens = output_tokens = 0
            success = False
            error_type: str | None = None

            try:
                raw_json, input_tokens, output_tokens = await self._call_openai_json(
                    model=model,
                    system=system_text,
                    user=user_text,
                    max_tokens=prompt_def.max_tokens,
                    temperature=prompt_def.temperature,
                )
                result = response_model.model_validate_json(raw_json)
                success = True
                return result

            except ValidationError as exc:
                last_error = exc
                error_type = "validation_error"
                logger.warning(
                    "llm_validation_failed",
                    prompt_key=prompt_key,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                # On last attempt, try Anthropic fallback before giving up
                if attempt == 2 and prompt_def.fallback_model:
                    return await self._anthropic_fallback(
                        prompt_def.fallback_model, system_text, user_text,
                        prompt_def.max_tokens, response_model,
                        job_id, stage, prompt_key, prompt_def.version,
                    )

            except openai.APIError as exc:
                last_error = exc
                error_type = "api_error"
                logger.error("openai_api_error", error=str(exc), attempt=attempt + 1)
                if prompt_def.fallback_model:
                    return await self._anthropic_fallback(
                        prompt_def.fallback_model, system_text, user_text,
                        prompt_def.max_tokens, response_model,
                        job_id, stage, prompt_key, prompt_def.version,
                    )

            finally:
                latency_ms = int((time.monotonic() - start) * 1000)
                cost = _compute_cost(model, input_tokens, output_tokens)
                await self._log_call(
                    job_id=job_id,
                    stage=stage,
                    prompt_key=prompt_key,
                    prompt_version=prompt_def.version,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                    latency_ms=latency_ms,
                    success=success,
                    error_type=error_type,
                    experiment_id=experiment_id,
                )

        raise LLMError(
            f"LLM call '{prompt_key}' failed after 3 attempts. Last error: {last_error}"
        )

    async def embed_batch(
        self,
        texts: list[str],
        job_id: str | None = None,
        stage: str = "compression",
    ) -> list[list[float]]:
        """Embed a list of texts in a single batched API call."""
        model = self._settings.openai_embedding_model
        start = time.monotonic()

        try:
            response = await self._openai.embeddings.create(
                model=model,
                input=texts,
            )
            embeddings = [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
            input_tokens = response.usage.total_tokens
            latency_ms = int((time.monotonic() - start) * 1000)
            cost = _compute_cost(model, input_tokens, 0)

            await self._log_call(
                job_id=job_id,
                stage=stage,
                prompt_key="embed_batch",
                prompt_version="v1",
                model=model,
                input_tokens=input_tokens,
                output_tokens=0,
                cost_usd=cost,
                latency_ms=latency_ms,
                success=True,
                error_type=None,
                experiment_id=None,
            )
            return embeddings

        except openai.APIError as exc:
            raise LLMError(f"Embedding call failed: {exc}") from exc

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _call_openai_json(
        self,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, int, int]:
        response = await self._openai.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content = response.choices[0].message.content or "{}"
        input_t = response.usage.prompt_tokens if response.usage else 0
        output_t = response.usage.completion_tokens if response.usage else 0
        return content, input_t, output_t

    async def _anthropic_fallback(
        self,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
        response_model: type[T],
        job_id: str | None,
        stage: str,
        prompt_key: str,
        prompt_version: str,
    ) -> T:
        start = time.monotonic()
        try:
            response = await self._anthropic.messages.create(
                model=model,
                system=system,
                messages=[{"role": "user", "content": user}],
                max_tokens=max_tokens,
            )
            raw = response.content[0].text if response.content else "{}"
            result = response_model.model_validate_json(raw)
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            latency_ms = int((time.monotonic() - start) * 1000)
            await self._log_call(
                job_id=job_id, stage=stage, prompt_key=prompt_key,
                prompt_version=prompt_version, model=model,
                input_tokens=input_tokens, output_tokens=output_tokens,
                cost_usd=_compute_cost(model, input_tokens, output_tokens),
                latency_ms=latency_ms, success=True, error_type=None, experiment_id=None,
            )
            return result
        except Exception as exc:
            raise LLMError(f"Anthropic fallback also failed: {exc}") from exc

    async def _log_call(
        self,
        job_id: str | None,
        stage: str,
        prompt_key: str,
        prompt_version: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        latency_ms: int,
        success: bool,
        error_type: str | None,
        experiment_id: str | None,
    ) -> None:
        from app.db.engine import get_session_factory
        from app.db.models import LLMCallLog
        import uuid as _uuid

        try:
            factory = get_session_factory()
            async with factory() as session:
                log = LLMCallLog(
                    id=_uuid.uuid4(),
                    job_id=uuid.UUID(job_id) if job_id else None,
                    stage=stage,
                    prompt_key=prompt_key,
                    prompt_version=prompt_version,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    latency_ms=latency_ms,
                    success=success,
                    error_type=error_type,
                    experiment_id=experiment_id,
                )
                session.add(log)
                await session.commit()
        except Exception as exc:
            logger.warning("llm_log_failed", error=str(exc))


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
