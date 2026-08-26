"""Storyline Schema：自动组织的推文故事线。"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class StorySection(BaseModel):
    title: str = ""
    findings: List[str] = Field(default_factory=list)
    figures: List[str] = Field(default_factory=list)
    content: str = ""


class Storyline(BaseModel):
    hook: str = ""
    core_question: str = ""
    sections: List[StorySection] = Field(default_factory=list)
    take_home_message: str = ""
    mode: str = "deep_review"

    model_config = {"extra": "allow"}
