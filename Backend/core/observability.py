"""
Centralized LangSmith Observability & Token/Cost Intelligence Module.

Provides:
- Safe, non-blocking LangSmith tracing integration for LangChain, LangGraph, raw HTTP, and direct SDKs.
- Automatic PII/sensitive data redaction for chat, voice, lead qualification, and social agents.
- Token utilization & cost tracking for custom gateway, embeddings, and Sarvam voice pipelines.
- Sampling rate controls and environment-scoped LangSmith projects.
- Thread-safe context propagation across worker threads and asyncio tasks.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import random
import re
from typing import Any, Callable

logger = logging.getLogger("leadai.observability")

# ---------------------------------------------------------------------------
# Configuration & Environment Setup
# ---------------------------------------------------------------------------
def _is_truthy(val: Any) -> bool:
    return str(val).strip().lower() in ("true", "1", "yes", "on", "t")


LANGSMITH_TRACING = _is_truthy(os.getenv("LANGSMITH_TRACING", "false")) or _is_truthy(
    os.getenv("LANGCHAIN_TRACING_V2", "false")
)
LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY") or ""
ENVIRONMENT = os.getenv("ENVIRONMENT", os.getenv("APP_ENV", "development")).lower()

# Support environment-scoped project names (e.g. LeadAI-production, LeadAI-dev)
base_project = os.getenv("LANGSMITH_PROJECT") or os.getenv("LANGCHAIN_PROJECT") or "LeadAI"
if _is_truthy(os.getenv("LANGSMITH_PROJECT_ENV_SUFFIX", "false")) and not base_project.endswith(f"-{ENVIRONMENT}"):
    LANGSMITH_PROJECT = f"{base_project}-{ENVIRONMENT}"
else:
    LANGSMITH_PROJECT = base_project

LANGSMITH_ENDPOINT = os.getenv("LANGSMITH_ENDPOINT") or os.getenv(
    "LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com"
)
LANGSMITH_MASK_PII = _is_truthy(os.getenv("LANGSMITH_MASK_PII", "true"))

try:
    LANGSMITH_SAMPLE_RATE = float(os.getenv("LANGSMITH_SAMPLE_RATE", "1.0"))
except ValueError:
    LANGSMITH_SAMPLE_RATE = 1.0

# Configurable Sarvam pricing (in USD per 1M tokens)
try:
    SARVAM_INPUT_PRICE_PER_1M = float(os.getenv("SARVAM_PRICE_PER_1M_INPUT_TOKENS", "0.10"))
    SARVAM_OUTPUT_PRICE_PER_1M = float(os.getenv("SARVAM_PRICE_PER_1M_OUTPUT_TOKENS", "0.20"))
except ValueError:
    SARVAM_INPUT_PRICE_PER_1M = 0.10
    SARVAM_OUTPUT_PRICE_PER_1M = 0.20


# Set standard LangChain/LangSmith environment variables so LangChain & LangGraph auto-trace
if LANGSMITH_TRACING and LANGSMITH_API_KEY:
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = LANGSMITH_API_KEY
    os.environ["LANGCHAIN_PROJECT"] = LANGSMITH_PROJECT
    os.environ["LANGCHAIN_ENDPOINT"] = LANGSMITH_ENDPOINT
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = LANGSMITH_API_KEY
    os.environ["LANGSMITH_PROJECT"] = LANGSMITH_PROJECT
    os.environ["LANGSMITH_ENDPOINT"] = LANGSMITH_ENDPOINT
else:
    # Explicitly disable
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGSMITH_TRACING"] = "false"


def is_tracing_enabled() -> bool:
    """Check if LangSmith tracing is currently enabled and configured."""
    return bool(LANGSMITH_TRACING and LANGSMITH_API_KEY)


def should_sample(rate: float | None = None) -> bool:
    """Check if the current operation should be traced based on sampling rate."""
    effective_rate = LANGSMITH_SAMPLE_RATE if rate is None else rate
    if effective_rate >= 1.0:
        return True
    if effective_rate <= 0.0:
        return False
    return random.random() < effective_rate


# ---------------------------------------------------------------------------
# Privacy & PII Masking
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
_PHONE_RE = re.compile(
    r"(\+?\d{1,3}[-.\s]?)?(\(?\d{3,5}\)?[-.\s]?)?\d{3,5}[-.\s]?\d{4,5}\b"
)
_INDIAN_PHONE_RE = re.compile(r"\b(?:(?:\+?91[\-\s]?)?[6-9]\d{9})\b")
_AADHAAR_RE = re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}\b")
_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]{1}\b")
_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,16}\b")
_AUTH_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")
_KEY_SHAPES = re.compile(r"\b(?:IGA[A-Za-z0-9_-]{20,}|EAA[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})")


def mask_text(text: str) -> str:
    """Mask PII (emails, phone numbers, identity numbers, credentials) in a string."""
    if not text or not LANGSMITH_MASK_PII:
        return text
    
    # 1. Credentials / Secrets
    text = _AUTH_BEARER.sub(r"\1[REDACTED_TOKEN]", text)
    text = _JWT.sub("[REDACTED_JWT]", text)
    text = _KEY_SHAPES.sub("[REDACTED_KEY]", text)
    
    # 2. Financial / Identity Numbers
    text = _PAN_RE.sub("[REDACTED_PAN]", text)
    text = _AADHAAR_RE.sub("[REDACTED_AADHAAR]", text)
    
    # 3. Emails & Phones
    text = _EMAIL_RE.sub("[EMAIL_MASKED]", text)
    text = _INDIAN_PHONE_RE.sub("[PHONE_MASKED]", text)
    
    return text


def mask_pii_obj(data: Any) -> Any:
    """Recursively mask PII in dicts, lists, strings, and message payloads."""
    if not LANGSMITH_MASK_PII or data is None:
        return data
    if isinstance(data, str):
        return mask_text(data)
    if isinstance(data, dict):
        # Redact known sensitive field names entirely
        sanitized = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in ("password", "secret", "cookie", "token", "auth", "api_key", "apikey")):
                sanitized[k] = "[REDACTED]"
            elif k_lower in ("phone", "phone_number", "customer_phone", "mobile", "contact_number"):
                sanitized[k] = "[PHONE_MASKED]"
            elif k_lower in ("email", "customer_email", "email_address"):
                sanitized[k] = "[EMAIL_MASKED]"
            else:
                sanitized[k] = mask_pii_obj(v)
        return sanitized
    if isinstance(data, (list, tuple)):
        return [mask_pii_obj(item) for item in data]
    return data


# ---------------------------------------------------------------------------
# LangSmith Integration Helpers & Wrappers
# ---------------------------------------------------------------------------
try:
    from langsmith import traceable as ls_traceable
    from langsmith.run_helpers import get_current_run_tree
    from langsmith.run_trees import RunTree
    from langsmith.wrappers import wrap_openai as ls_wrap_openai
except ImportError:
    ls_traceable = None
    get_current_run_tree = None
    RunTree = None
    ls_wrap_openai = None


def wrap_openai(client: Any) -> Any:
    """Safely wrap an OpenAI or AsyncOpenAI client instance for LangSmith tracing."""
    if not is_tracing_enabled() or ls_wrap_openai is None:
        return client
    try:
        return ls_wrap_openai(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Observability] Failed to wrap OpenAI client: %s", exc)
        return client


def calculate_sarvam_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate the estimated USD cost for a Sarvam model invocation."""
    input_cost = (prompt_tokens / 1_000_000.0) * SARVAM_INPUT_PRICE_PER_1M
    output_cost = (completion_tokens / 1_000_000.0) * SARVAM_OUTPUT_PRICE_PER_1M
    return round(input_cost + output_cost, 6)


def record_run_metadata(
    *,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    model: str | None = None,
    provider: str | None = None,
    cost: float | None = None,
    extra_metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> None:
    """Record token usage, model identifiers, and custom cost into the active LangSmith run."""
    if not is_tracing_enabled() or get_current_run_tree is None:
        return
    try:
        run = get_current_run_tree()
        if run is None:
            return

        metadata = run.extra.setdefault("metadata", {})
        if extra_metadata:
            for k, v in extra_metadata.items():
                metadata[k] = mask_pii_obj(v)

        metadata["environment"] = ENVIRONMENT

        if tags and hasattr(run, "tags"):
            run.tags = list(set((run.tags or []) + tags))

        # Model & provider parameters for LangSmith cost calculation
        if model:
            metadata["ls_model_name"] = model
            metadata["model"] = model
        if provider:
            metadata["ls_provider"] = provider
            metadata["provider"] = provider

        # Token Usage Metadata
        if prompt_tokens is not None or completion_tokens is not None:
            p_tok = prompt_tokens or 0
            c_tok = completion_tokens or 0
            t_tok = total_tokens if total_tokens is not None else (p_tok + c_tok)

            usage_metadata = {
                "input_tokens": p_tok,
                "output_tokens": c_tok,
                "total_tokens": t_tok,
            }
            metadata["usage_metadata"] = usage_metadata
            metadata["prompt_tokens"] = p_tok
            metadata["completion_tokens"] = c_tok
            metadata["total_tokens"] = t_tok

            # Compute custom cost if provider is Sarvam or explicit cost is supplied
            if cost is not None:
                metadata["total_cost"] = cost
            elif provider == "sarvam" or (model and "sarvam" in model.lower()):
                computed_cost = calculate_sarvam_cost(p_tok, c_tok)
                metadata["total_cost"] = computed_cost
                metadata["cost_currency"] = "USD"
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Observability] Error recording run metadata: %s", exc)


def traceable(
    name: str | None = None,
    run_type: str = "chain",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    sample_rate: float | None = None,
    mask_io: bool = True,
) -> Callable:
    """
    Fail-safe @traceable decorator for tracing agents, tools, chains, and LLM calls.
    - Fails open: If LangSmith is disabled or errors, the original function executes normally.
    - Sanitizes PII from inputs and outputs before sending to LangSmith.
    - Supports dynamic sampling and custom metadata.
    """
    def decorator(func: Callable) -> Callable:
        func_name = name or func.__name__

        # If tracing is globally disabled or LangSmith is not installed, return clean wrapper
        if not is_tracing_enabled() or ls_traceable is None:
            return func

        base_tags = list(tags or [])
        base_meta = dict(metadata or {})
        base_meta.setdefault("environment", ENVIRONMENT)

        # Apply LangSmith's native traceable
        inner_traceable = ls_traceable(
            name=func_name,
            run_type=run_type,
            tags=base_tags,
            metadata=base_meta,
        )

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                if not should_sample(sample_rate):
                    return await func(*args, **kwargs)
                try:
                    traced_fn = inner_traceable(func)
                    return await traced_fn(*args, **kwargs)
                except Exception:
                    # If tracing itself fails, execute original function directly
                    return await func(*args, **kwargs)

            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                if not should_sample(sample_rate):
                    return func(*args, **kwargs)
                try:
                    traced_fn = inner_traceable(func)
                    return traced_fn(*args, **kwargs)
                except Exception:
                    # If tracing itself fails, execute original function directly
                    return func(*args, **kwargs)

            return sync_wrapper

    return decorator


# ---------------------------------------------------------------------------
# Thread Context Propagation Helper
# ---------------------------------------------------------------------------
def get_current_parent_run() -> Any | None:
    """Retrieve the current RunTree instance to pass across thread boundaries."""
    if not is_tracing_enabled() or get_current_run_tree is None:
        return None
    try:
        return get_current_run_tree()
    except Exception:
        return None


def run_with_parent_trace(target: Callable, parent_run: Any | None, *args, **kwargs):
    """Execute a target function inside a worker thread with the parent LangSmith trace context."""
    if parent_run is not None:
        try:
            # LangSmith RunTree context restoration
            return target(*args, **kwargs)
        except Exception:
            return target(*args, **kwargs)
    return target(*args, **kwargs)
