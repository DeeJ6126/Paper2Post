"""Story Planner Agent：自动组织故事线。"""

from __future__ import annotations

from typing import Optional

from paper2post.llm.base import LLMProvider, generate_json
from paper2post.prompts import Prompts
from paper2post.schemas.paper import PaperAnalysis
from paper2post.schemas.evidence import EvidenceMap
from paper2post.schemas.storyline import Storyline, StorySection

from .base import BaseAgent


class StorytellerAgent(BaseAgent):
    def __init__(
        self,
        llm: LLMProvider,
        prompts: Prompts,
        config: Optional[dict] = None,
    ):
        super().__init__(llm, prompts, config)

    def _draft(self, analysis: PaperAnalysis, mode: str = "deep_review") -> Storyline:
        findings = analysis.main_findings
        titles = {
            "deep_review": [
                "为什么值得关注",
                "作者想解决什么问题",
                "作者是怎么研究的",
                "最重要的发现",
            ],
            "speed": ["核心发现是什么", "为什么重要"],
            "methods": ["问题背景", "新方法", "基准验证", "应用与局限"],
            "resource": ["数据库解决什么", "包含哪些数据", "如何构建", "如何使用"],
        }.get(mode, ["背景", "问题", "方法", "发现", "意义"])

        sections = []
        for i, t in enumerate(titles):
            sec_findings = [f.finding_id for f in findings] if i >= 2 else []
            sections.append(
                StorySection(title=t, findings=sec_findings, figures=[], content="")
            )
        return Storyline(
            hook=self.first_sentence(analysis.research_question) or "（待补充：开篇钩子）",
            core_question=analysis.research_question,
            sections=sections,
            take_home_message=analysis.authors_conclusion,
            mode=mode,
        )

    def run(self, analysis: PaperAnalysis, evidence: EvidenceMap, mode: str) -> Storyline:
        draft = self._draft(analysis, mode)
        user = self.dump(
            {
                "analysis": analysis.model_dump(),
                "evidence": evidence.model_dump(),
                "mode": mode,
            }
        )
        data = generate_json(
            self.llm,
            system=self.prompts.storyteller,
            user=user,
            draft=draft.model_dump(),
            temperature=self.temperature(),
            max_tokens=self.max_tokens(),
        )
        return Storyline(**data)
