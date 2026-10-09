"""
AI Model Pricing Catalog & Cost Calculation Engine.

Provides authoritative token and image pricing for all supported providers:
- OpenAI (GPT-4o, GPT-4o-mini, GPT-4, GPT-3.5, embeddings, DALL-E)
- Sarvam AI (Sarvam 2b, Sarvam translate, speech)
- Local embeddings (all-MiniLM-L6-v2) -> Free ($0.00)

Handles:
- Missing/unknown models by returning price_status="price_unknown" and cost_usd=None (never 0).
- Configurable overrides via AI_USAGE_PRICES_JSON environment variable.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

PROCESS_CATALOGUE: dict[str, dict] = {
    # Customer Chat & Assistant
    "chat_answer": {"display_name": "Customer Chat Reply", "category": "chat", "is_customer_facing": True},
    "lead_qualification": {"display_name": "Lead Qualification & Scoring", "category": "chat", "is_customer_facing": True},
    "lead_summary": {"display_name": "Lead Conversation Summary", "category": "chat", "is_customer_facing": True},
    "intent_evaluation": {"display_name": "Lead Intent Evaluation", "category": "chat", "is_customer_facing": True},

    # Knowledge Base / RAG Embeddings
    "kb_indexing_embedding": {"display_name": "Knowledge Base Document Indexing", "category": "knowledge_base", "is_customer_facing": True},
    "kb_query_embedding": {"display_name": "Knowledge Base Search Query", "category": "knowledge_base", "is_customer_facing": True},

    # Social Media & Copywriting
    "comment_reply_generation": {"display_name": "Social Comment Reply", "category": "social", "is_customer_facing": True},
    "social_copy_draft": {"display_name": "Social Post Draft Copywriter", "category": "social", "is_customer_facing": True},
    "social_agent": {"display_name": "Social Media AI Agent", "category": "social", "is_customer_facing": True},
    "crag_retrieval_grade": {"display_name": "CRAG Document Relevance Grader", "category": "social", "is_customer_facing": True},

    # Blog Content Studio
    "blog_router": {"display_name": "Blog Workflow Router", "category": "blog", "is_customer_facing": True},
    "blog_planner": {"display_name": "Blog Article Outline Planner", "category": "blog", "is_customer_facing": True},
    "blog_worker": {"display_name": "Blog Section Writer", "category": "blog", "is_customer_facing": True},
    "blog_reducer": {"display_name": "Blog Editor & Assembly", "category": "blog", "is_customer_facing": True},
    "blog_researcher": {"display_name": "Blog Web Evidence Synthesizer", "category": "blog", "is_customer_facing": True},
    "blog_image_generation": {"display_name": "Blog AI Image Generation", "category": "blog", "is_customer_facing": True},

    # Voice Calls & Phone Agents
    "voice_call": {"display_name": "AI Voice Call Turn", "category": "voice", "is_customer_facing": True},
    "voice_opening_line": {"display_name": "Voice Call Opening Line", "category": "voice", "is_customer_facing": True},
    "voice_translation": {"display_name": "Voice Real-Time Translation", "category": "voice", "is_customer_facing": True},
    "voice_party_detector": {"display_name": "Voice Answering Machine / Party Detector", "category": "voice", "is_customer_facing": True},
    "voice_transcript_compress": {"display_name": "Voice Transcript Compression", "category": "voice", "is_customer_facing": True},
    "voice_transcript_compressor": {"display_name": "Voice Transcript Compression", "category": "voice", "is_customer_facing": True},
    "bot_extraction": {"display_name": "Outbound Bot Field Extraction", "category": "voice", "is_customer_facing": True},

    # Platform / System Jobs (Allowed NULL ClientId)
    "blog_topic_picker": {"display_name": "Daily Blog Topic Generator (System Job)", "category": "system", "is_customer_facing": False},
    "pre_warm": {"display_name": "LLM Connection Cache Pre-Warm (System Job)", "category": "system", "is_customer_facing": False},
    "system_warmup": {"display_name": "System Warmup", "category": "system", "is_customer_facing": False},
}
AI_PROCESS_CATALOGUE = PROCESS_CATALOGUE



@dataclass(frozen=True)
class ModelPrice:
    input_per_1m: float
    output_per_1m: float
    is_free: bool = False
    image_unit_cost: Optional[float] = None  # Price per generated image if image model
    price_status: str = "ok"  # "ok", "placeholder", "free"


# Base Catalog: Only models actually called in the codebase
# Source URLs:
# - OpenAI Pricing: https://openai.com/api/pricing/ (Verified 2026-10-09 for gpt-4o, gpt-4o-mini, text-embedding-3-small, dall-e-3)
# - Sarvam AI Pricing: Enterprise Quote (Unverified / marked as placeholder)
DEFAULT_PRICES: dict[str, ModelPrice] = {
    # OpenAI Chat Models (Used in LeadAI Gateway, AI Engine, Blog, Social, Outbound Bot)
    "gpt-4o": ModelPrice(input_per_1m=2.50, output_per_1m=10.00, price_status="ok"),                  # Verified: https://openai.com/api/pricing/
    "gpt-4o-mini": ModelPrice(input_per_1m=0.15, output_per_1m=0.60, price_status="ok"),             # Verified: https://openai.com/api/pricing/
    # OpenAI Embeddings (Used in LeadAI KB embeddings)
    "text-embedding-3-small": ModelPrice(input_per_1m=0.02, output_per_1m=0.0, price_status="ok"),    # Verified: https://openai.com/api/pricing/
    # OpenAI Image Generation (Used in Blog Banner DALL-E 3)
    "dall-e-3": ModelPrice(input_per_1m=0.0, output_per_1m=0.0, image_unit_cost=0.040, price_status="ok"), # Verified: https://openai.com/api/pricing/
    # Sarvam AI LLM Models (Used in Outbound Streaming Voice - Placeholder Enterprise Rates)
    "sarvam-105b": ModelPrice(input_per_1m=0.20, output_per_1m=0.20, price_status="placeholder"),             # Placeholder (Sarvam Enterprise Tier)
    "sarvam-2b": ModelPrice(input_per_1m=0.10, output_per_1m=0.10, price_status="placeholder"),               # Placeholder (Sarvam Dev Tier)
    "sarvam-m-v1": ModelPrice(input_per_1m=0.10, output_per_1m=0.10, price_status="placeholder"),             # Placeholder
    # Local & Free Models (Used in fallback offline mode)
    "all-minilm-l6-v2": ModelPrice(input_per_1m=0.0, output_per_1m=0.0, is_free=True, price_status="free"),
    "sentence-transformers": ModelPrice(input_per_1m=0.0, output_per_1m=0.0, is_free=True, price_status="free"),
    "local-hashed-ngram-384": ModelPrice(input_per_1m=0.0, output_per_1m=0.0, is_free=True, price_status="free"),
    "builtin-extractive": ModelPrice(input_per_1m=0.0, output_per_1m=0.0, is_free=True, price_status="free"),
}




def _load_price_catalog() -> dict[str, ModelPrice]:
    catalog = dict(DEFAULT_PRICES)
    raw_env = os.getenv("AI_USAGE_PRICES_JSON", "").strip()
    if raw_env:
        try:
            custom_prices = json.loads(raw_env)
            for model_name, p in custom_prices.items():
                catalog[model_name.lower()] = ModelPrice(
                    input_per_1m=float(p.get("input_per_1m", 0.0)),
                    output_per_1m=float(p.get("output_per_1m", 0.0)),
                    is_free=bool(p.get("is_free", False)),
                    image_unit_cost=float(p.get("image_unit_cost")) if p.get("image_unit_cost") is not None else None,
                    price_status=str(p.get("price_status", "ok")),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AI Pricing] Failed to parse AI_USAGE_PRICES_JSON: %s", exc)
    return catalog


def calculate_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    *,
    is_image: bool = False,
    image_count: int = 1,
) -> Tuple[Optional[float], Optional[float], Optional[float], str]:
    """Calculate cost for an AI call.

    Returns:
        (cost_usd, unit_input_price_per_1m, unit_output_price_per_1m, price_status)

    price_status is one of:
      - 'ok': Price successfully matched and calculated
      - 'placeholder': Estimated / unverified enterprise price
      - 'free': Model is explicitly zero-cost/local
      - 'price_unknown': No price configured for model (cost_usd is None)
    """
    catalog = _load_price_catalog()
    clean_model = (model or "").strip().lower()
    
    # 1. Exact match first
    price_info = catalog.get(clean_model)

    # 2. Longest prefix / variant matching (e.g. gpt-4o-mini-2024-07-18 -> gpt-4o-mini, not gpt-4o)
    if not price_info:
        # Sort catalog keys by descending length so 'gpt-4o-mini' is checked BEFORE 'gpt-4o'
        sorted_keys = sorted(catalog.keys(), key=len, reverse=True)
        for known_key in sorted_keys:
            # Match dated snapshot or provider-prefixed names
            if (
                clean_model == known_key
                or clean_model.startswith(f"{known_key}-")
                or clean_model.startswith(f"{known_key}:")
                or clean_model.startswith(f"{known_key}/")
                or clean_model.endswith(f"/{known_key}")
                or clean_model.startswith(f"openai/{known_key}")
                or clean_model.startswith(f"sarvam/{known_key}")
            ):
                price_info = catalog[known_key]
                break

    if not price_info:
        # Unknown model — never fake as 0.00
        return None, None, None, "price_unknown"

    if price_info.is_free:
        return 0.0, 0.0, 0.0, "free"

    if is_image:
        unit_cost = price_info.image_unit_cost if price_info.image_unit_cost is not None else 0.040
        cost = round(unit_cost * max(1, image_count), 6)
        return cost, 0.0, 0.0, "ok"

    cost_in = (max(0, input_tokens) / 1_000_000.0) * price_info.input_per_1m
    cost_out = (max(0, output_tokens) / 1_000_000.0) * price_info.output_per_1m
    total_cost = round(cost_in + cost_out, 7)
    status = price_info.price_status if getattr(price_info, "price_status", None) else "ok"
    return total_cost, price_info.input_per_1m, price_info.output_per_1m, status


def normalize_model_name(model: str) -> str:
    """Normalize snapshot / dated model names to canonical model family name.

    Examples:
      - 'gpt-4o-mini-2024-07-18' -> 'gpt-4o-mini'
      - 'gpt-4o-2024-05-13' -> 'gpt-4o'
      - 'openai/gpt-4o' -> 'gpt-4o'
    """
    clean_model = (model or "").strip().lower()
    if not clean_model:
        return "unknown"
    if "/" in clean_model:
        clean_model = clean_model.split("/")[-1]

    import re
    catalog = _load_price_catalog()
    sorted_keys = sorted(catalog.keys(), key=len, reverse=True)
    for known_key in sorted_keys:
        if (
            clean_model == known_key
            or clean_model.startswith(f"{known_key}-")
            or clean_model.startswith(f"{known_key}:")
        ):
            return known_key

    # Generic date strip: model-YYYY-MM-DD or model-YYYYMMDD or model-MMDD
    clean_model = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", clean_model)
    clean_model = re.sub(r"-\d{8}$", "", clean_model)
    clean_model = re.sub(r"-\d{4}$", "", clean_model)
    return clean_model

