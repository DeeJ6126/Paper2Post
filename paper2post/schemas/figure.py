"""Figure Agent Schema：图表筛选与解读。"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class FigureAnalysis(BaseModel):
    figure: str = ""            # 例如 "Figure 3"
    importance: str = "low"     # high | medium | low
    panels: List[str] = Field(default_factory=list)
    role: str = ""              # study_design | overview | main_finding | mechanism | validation | clinical | supplementary
    summary: str = ""
    article_usage: bool = False

    model_config = {"extra": "allow"}
