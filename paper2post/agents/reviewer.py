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
        user = self.dump(
            {
                "article": article,
                "evidence": evidence.model_dump(),
                "full_text": paper.full_text[:20000],
            }
        )
        data = generate_json(
            self.llm,
            system=self.prompts.reviewer,
            user=user,
            draft=draft.model_dump(),
            temperature=0.0,
            max_tokens=self.max_tokens(),
        )
        return FactCheck(**data)
