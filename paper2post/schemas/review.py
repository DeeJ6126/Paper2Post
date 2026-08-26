"""Reviewer Schema：事实核验报告。"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class ReviewIssue(BaseModel):
    severity: str = "low"       # low | medium | high
    paragraph: int = 0
    original: str = ""
    problem: str = ""
    suggestion: str = ""


class FactCheck(BaseModel):
    overall_score: float = 0.0
    passed: bool = True
    issues: List[ReviewIssue] = Field(default_factory=list)

    model_config = {"extra": "allow"}
