import operator
from typing import Annotated, Any, List, Optional, Tuple, TypedDict
from .schemas import EvidenceItem, ImageSpec, Plan


class State(TypedDict):
    topic: str
    tone: Optional[str]
    target_audience: Optional[str]
    keywords: Optional[List[str]]
    blog_type: Optional[str]
    include_images: Optional[bool]
    num_images: Optional[int]
    target_words: Optional[int]
    target_length: Optional[str]
    language: Optional[str]
    cta_text: Optional[str]
    cta_url: Optional[str]

    mode: str
    needs_research: bool
    queries: List[str]
    evidence: List[EvidenceItem]
    plan: Optional[Plan]

    as_of: str
    recency_days: int

    sections: Annotated[List[Tuple[int, str]], operator.add]
    merged_md: str
    md_with_placeholders: str
    image_specs: List[ImageSpec]
    final: str
