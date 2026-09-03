"""Scientific Reviewer Agent：自动事实核验。"""

from __future__ import annotations

from typing import Optional

from paper2post.llm.base import LLMProvider, generate_json
from paper2post.prompts import Prompts
from paper2post.schemas.paper import ParsedPaper
from paper2post.schemas.evidence import EvidenceMap
from paper2post.schemas.review import FactCheck, ReviewIssue

from .base import BaseAgent


class ReviewerAgent(BaseAgent):
    def __init__(
        self,
        llm: LLMProvider,
        prompts: Prompts,
        config: Optional[dict] = None,
    ):
        super().__init__(llm, prompts, config)

    def _draft(self) -> FactCheck:
        return FactCheck(overall_score=100.0, passed=True, issues=[])

    def run(
        self,
        paper: ParsedPaper,
        evidence: EvidenceMap,
        article: str,
    ) -> FactCheck:
        draft = self._draft()
        # evidence 全 dump 可能 2-3KB；截到前 5 条 + 每条 200 字符
        compact_evidence = {
            "evidence": [
                {
                    "claim": (e.claim or "")[:200],
                    "source_section": (e.source_section or "")[:60],
                    "figure": (e.figure or "")[:60],
                }
                for e in (evidence.evidence or [])[:5]
                if (e.claim or "").strip()
            ]
        }
        user = self.dump(
            {
                # article 截到 2KB 防止 vision 模型空响应。Reviewer 重点核验
                # 前 1-2 段（hook、why important、findings），再长边际收益低。
                "article": article[:2000],
                "evidence": compact_evidence,
                "paper_summary": (paper.abstract or "")[:1000],
            }
        )
        data = generate_json(
            self.llm,
            system=self.prompts.reviewer,
            user=user,
            draft=draft.model_dump(),
            temperature=0.0,
            max_tokens=self.max_tokens(),
            _caller="reviewer",
        )
        return FactCheck(**data)
