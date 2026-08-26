"""Paper Reader Agent：结构化论文理解。"""

from __future__ import annotations

from typing import Optional

from paper2post.llm.base import LLMProvider, generate_json
from paper2post.prompts import Prompts
from paper2post.schemas.paper import PaperAnalysis, ParsedPaper

from .base import BaseAgent


class ReaderAgent(BaseAgent):
    """读取论文，输出结构化 paper_analysis.json。不直接写推文。"""

    def __init__(
        self,
        llm: LLMProvider,
        prompts: Prompts,
        config: Optional[dict] = None,
    ):
        super().__init__(llm, prompts, config)

    def _draft(self, paper: ParsedPaper) -> PaperAnalysis:
        """mock 模式默认输出：尽量回填已解析到的信息。"""
        abstract_first = self.first_sentence(paper.abstract)
        return PaperAnalysis(
            title=paper.title,
            journal=paper.metadata.get("pdf_metadata", {}).get("publisher", ""),
            year="",
            research_question=abstract_first or "（待补充：论文核心科学问题）",
            background=["（待补充：研究背景）"],
            knowledge_gap="（待补充：研究空白）",
            hypothesis="（待补充：研究假设）",
            methods=["（待补充：研究方法）"],
            main_findings=[],
            innovation=["（待补充：创新点）"],
            limitations=["（待补充：局限）"],
            authors_conclusion="（待补充：作者结论）",
        )

    def run(self, paper: ParsedPaper) -> PaperAnalysis:
        draft = self._draft(paper)
        user = self.dump(
            {
                "title": paper.title,
                "abstract": paper.abstract,
                "sections": [
                    {"heading": s.heading, "text": s.text[:6000]}
                    for s in paper.sections
                ],
                "figures": [
                    {"label": f.label, "caption": f.caption} for f in paper.figures
                ],
            }
        )
        data = generate_json(
            self.llm,
            system=self.prompts.reader,
            user=user,
            draft=draft.model_dump(),
            temperature=self.temperature(),
            max_tokens=self.max_tokens(),
        )
        return PaperAnalysis(**data)
