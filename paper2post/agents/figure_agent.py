"""Figure Agent：图表筛选与解读。"""

from __future__ import annotations

from typing import List, Optional

from paper2post.llm.base import LLMProvider, generate_json
from paper2post.prompts import Prompts
from paper2post.schemas.paper import ParsedPaper
from paper2post.schemas.storyline import Storyline
from paper2post.schemas.figure import FigureAnalysis

from .base import BaseAgent


class FigureAgent(BaseAgent):
    def __init__(
        self,
        llm: LLMProvider,
        prompts: Prompts,
        config: Optional[dict] = None,
    ):
        super().__init__(llm, prompts, config)

    def _draft(self, paper: ParsedPaper) -> List[FigureAnalysis]:
        total = len(paper.figures)
        out: List[FigureAnalysis] = []
        for i, f in enumerate(paper.figures):
            # 图较少时默认高重要度，较多时取前几幅
            importance = "high" if total <= 5 else ("medium" if i < 4 else "low")
            out.append(
                FigureAnalysis(
                    figure=f.label,
                    importance=importance,
                    panels=[],
                    role="main_finding",
                    summary=f.caption or "（待补充：图意解读）",
                    article_usage=f.caption != "",
                )
            )
        return out

    def run(
        self,
        paper: ParsedPaper,
        storyline: Storyline,
        article_type: str,
    ) -> List[FigureAnalysis]:
        draft = self._draft(paper)
        user = self.dump(
            {
                "figures": [
                    {"label": f.label, "caption": f.caption, "page": f.page}
                    for f in paper.figures
                ],
                "storyline": storyline.model_dump(),
                "article_type": article_type,
            }
        )
        data = generate_json(
            self.llm,
            system=self.prompts.figure_agent,
            user=user,
            draft=[x.model_dump() for x in draft],
            temperature=self.temperature(),
            max_tokens=self.max_tokens(),
        )

        items: List[dict] = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("figures") or data.get("figure_analysis") or []
        elif data is None:
            items = []

        if not items:
            return draft
        return [FigureAnalysis(**x) for x in items if isinstance(x, dict)]
