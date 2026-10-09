"""
LeadAI — Inbound Lead Intent Detector for Social Messages (DMs & InMail).

Provides hybrid evaluation:
1. High-speed, zero-cost Regex & Keyword trigger detection for immediate signals (demo, pricing, consultation, etc.)
2. LLM fallback via complete_json for natural language nuances when keywords are borderline.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from core.observability import traceable
from .llm import complete_json
from ..models import utcnow

_utcnow = utcnow

logger = logging.getLogger("leadai.services.intent_detector")

# Core Commercial Keyword Patterns
DEMO_PATTERNS = [
    r"\bdemo\b",
    r"\bwalkthrough\b",
    r"\bpresentation\b",
    r"\btrial\b",
    r"\bshow me how\b",
    r"\bsee it in action\b",
]

PRICING_PATTERNS = [
    r"\bpricing\b",
    r"\bprice\b",
    r"\bcost\b",
    r"\bquote\b",
    r"\bquotation\b",
    r"\bhow much\b",
    r"\brates\b",
    r"\bsubscription\b",
    r"\bpackages?\b",
    r"\bbudget\b",
    r"\bfees?\b",
    r"\bdiscount\b",
]

MEETING_PATTERNS = [
    r"\bschedule a call\b",
    r"\bbook a call\b",
    r"\bset up a call\b",
    r"\bhop on a call\b",
    r"\bhave a call\b",
    r"\bmeeting\b",
    r"\bdiscuss further\b",
    r"\bconsultation\b",
    r"\bcalendar\b",
    r"\bcalendly\b",
    r"\byour availability\b",
]

PRODUCT_INTEREST_PATTERNS = [
    r"\binterested in (your|the)\b",
    r"\bwant to (use|buy|try|implement|explore)\b",
    r"\blooking for a solution\b",
    r"\bcan (you|your platform|your tool) help\b",
    r"\bwe need a tool\b",
    r"\bwe are looking to\b",
    r"\bhire you\b",
    r"\bpartnership\b",
]


@dataclass
class IntentEvaluationResult:
    is_lead: bool
    score: float
    category: str  # 'demo_request' | 'pricing_inquiry' | 'consultation_request' | 'product_interest' | 'general'
    signals: List[str] = field(default_factory=list)
    rationale: str = ""


class LeadIntentEvaluator:
    """Evaluates inbound text turns from DMs, InMail, and notes for commercial buying intent."""

    @classmethod
    @traceable(name="tool:intent_evaluator", run_type="tool")
    def evaluate_text(
        cls,
        text: str,
        contact_name: Optional[str] = None,
        use_llm_fallback: bool = True,
        company_id: Optional[str] = None,
    ) -> IntentEvaluationResult:
        if not text or not text.strip():
            return IntentEvaluationResult(
                is_lead=False,
                score=0.0,
                category="general",
                signals=[],
                rationale="Empty message"
            )

        clean_text = text.strip()
        lower_text = clean_text.lower()
        matched_signals: List[str] = []
        category = "general"
        base_score = 0.0

        # 1. Fast Pattern Matching
        # Demo check
        for pat in DEMO_PATTERNS:
            if re.search(pat, lower_text, re.IGNORECASE):
                matched_signals.append("demo")
                category = "demo_request"
                base_score = max(base_score, 0.90)
                break

        # Pricing check
        for pat in PRICING_PATTERNS:
            if re.search(pat, lower_text, re.IGNORECASE):
                matched_signals.append("pricing")
                if category == "general":
                    category = "pricing_inquiry"
                base_score = max(base_score, 0.85)
                break

        # Meeting / Call check
        for pat in MEETING_PATTERNS:
            if re.search(pat, lower_text, re.IGNORECASE):
                matched_signals.append("call_meeting")
                if category == "general":
                    category = "consultation_request"
                base_score = max(base_score, 0.88)
                break

        # Product Interest check
        for pat in PRODUCT_INTEREST_PATTERNS:
            if re.search(pat, lower_text, re.IGNORECASE):
                matched_signals.append("product_interest")
                if category == "general":
                    category = "product_interest"
                base_score = max(base_score, 0.80)
                break

        # If strong keyword signals matched, return immediately without LLM overhead
        if matched_signals:
            return IntentEvaluationResult(
                is_lead=True,
                score=base_score,
                category=category,
                signals=matched_signals,
                rationale=f"Commercial signals detected: {', '.join(matched_signals)}"
            )

        # 2. LLM Fallback for ambiguous or implicit inquiries
        if use_llm_fallback and len(clean_text) >= 15:
            try:
                from core.usage_tracker import bind_usage_context

                system_prompt = (
                    "You are an AI sales qualification assistant. Analyze the incoming LinkedIn direct message "
                    "or note from a prospect to determine if they are expressing commercial/buying interest, "
                    "asking for product information, requesting pricing, or asking to connect for business services.\n\n"
                    "Respond with a strict JSON object:\n"
                    "{\n"
                    '  "is_lead": true/false,\n'
                    '  "intent_score": float between 0.0 and 1.0,\n'
                    '  "category": "demo_request" | "pricing_inquiry" | "consultation_request" | "product_interest" | "general",\n'
                    '  "key_signals": ["signal1", "signal2"],\n'
                    '  "rationale": "short explanation"\n'
                    "}"
                )
                user_content = f"Prospect Message:\n\"\"\"{clean_text}\"\"\""
                messages = [{"role": "user", "content": user_content}]

                with bind_usage_context(company_id=company_id, process="intent_evaluation", channel="social"):
                    result, _ = complete_json(system_prompt, messages, temperature=0.1, max_tokens=180)

                if result and isinstance(result, dict) and "is_lead" in result:
                    score = float(result.get("intent_score", 0.0))
                    is_lead = bool(result.get("is_lead", False)) and score >= 0.60
                    return IntentEvaluationResult(
                        is_lead=is_lead,
                        score=score,
                        category=result.get("category", "general"),
                        signals=result.get("key_signals", []),
                        rationale=result.get("rationale", "AI semantic evaluation")
                    )
            except Exception as llm_err:
                logger.debug("LLM intent evaluation fallback notice: %s", llm_err)

        return IntentEvaluationResult(
            is_lead=False,
            score=0.1,
            category="general",
            signals=[],
            rationale="No buying intent detected"
        )
