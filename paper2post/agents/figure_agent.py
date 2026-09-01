"""Figure Agent：图表筛选与解读。"""

from __future__ import annotations

from typing import List, Optional

from paper2post.llm.base import LLMProvider, generate_json
from paper2post.prompts import Prompts
from paper2post.schemas.paper import ParsedPaper
from paper2post.schemas.storyline import Storyline
from paper2post.schemas.figure import FigureAnalysis

from .base import BaseAgent

# 喂给 Figure Agent 的图上限。AlphaFold3 这种大论文会有 100+ 张图，全喂进去
# 会让 vision 模型超时（prompt 超 50KB）。取前 30 张够用，超出的图在 _draft
# 里仍按 low importance 给出，保证 figure_items 列表完整。
MAX_FIGURES_FOR_LLM = 30


class FigureAgent(BaseAgent):
    MAX_FIGURES_FOR_LLM = MAX_FIGURES_FOR_LLM

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
        # 只把前 N 张喂给 LLM，超出的图仍出现在 draft（带 low importance）里。
        figs_for_llm = paper.figures[: self.MAX_FIGURES_FOR_LLM]
        user = self.dump(
            {
                "figures": [
                    {"label": f.label, "caption": f.caption, "page": f.page}
                    for f in figs_for_llm
                ],
                "storyline": storyline.model_dump(),
                "article_type": article_type,
            }
        )
        data = generate_json(
            self.llm,
            system=self.prompts.figure_agent,
            user=user,
            draft=[x.model_dump() for x in draft[: self.MAX_FIGURES_FOR_LLM]],
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
        # 把 LLM 给的 items 截到 MAX_FIGURES_FOR_LLM，剩余的图用 draft 兜底
        from_paper_labels = {f.label for f in paper.figures}
        seen_labels = set()
        merged: List[FigureAnalysis] = []
        for x in items:
            if not isinstance(x, dict):
                continue
            fa = FigureAnalysis(**x)
            seen_labels.add(fa.figure)
            merged.append(fa)
        for d in draft[len(merged):]:
            if d.figure not in seen_labels:
                merged.append(d)
        return merged
