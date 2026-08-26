"""Evidence Schema：结论 ↔ 证据 ↔ Figure 映射。

每个核心结论必须记录其原文依据，Writer 不允许生成无 Evidence 支持的重要结论。
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    claim: str = ""
    source_section: str = ""
    figure: str = ""
    evidence_text: str = ""
    confidence: float = 0.5

    model_config = {"extra": "allow"}


class EvidenceMap(BaseModel):
    evidence: List[Evidence] = Field(default_factory=list)

    model_config = {"extra": "allow"}
