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
        # 纯文本 user payload（避免 deepseek-v4-flash 在 JSON user 上的空响应问题）
        ev_lines = []
        for e in (evidence.evidence or [])[:5]:
            if not (e.claim or "").strip():
                continue
            ev_lines.append(f"- claim: {e.claim[:200]}")
            if e.source_section:
                ev_lines.append(f"  source: {e.source_section[:60]}")
            if e.figure:
                ev_lines.append(f"  figure: {e.figure[:60]}")
        user = (
            "## 待审核文章（≤2000 字）\n"
            f"{article[:2000]}\n\n"
            "## 论文摘要\n"
            f"{(paper.abstract or '')[:1000]}\n\n"
            "## 证据\n"
            + ("\n".join(ev_lines) if ev_lines else "（无）")
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
