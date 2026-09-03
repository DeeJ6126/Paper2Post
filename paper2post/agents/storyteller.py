"""Story Planner Agent：自动组织故事线。"""

from __future__ import annotations

from typing import Optional

from paper2post.llm.base import LLMProvider, generate_json
from paper2post.prompts import Prompts
from paper2post.schemas.paper import PaperAnalysis
from paper2post.schemas.evidence import EvidenceMap
from paper2post.schemas.storyline import Storyline, StorySection

from .base import BaseAgent


def _short(v, n: int = 150) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _compact_for_storyteller(analysis: PaperAnalysis, evidence: EvidenceMap) -> dict:
    """把 analysis + evidence 压到 ~3KB 内，给 storyteller 用。"""
    return {
        "analysis": {
            "title": analysis.title or "",
            "research_question": _short(analysis.research_question, 300),
            "knowledge_gap": _short(analysis.knowledge_gap, 150),
            "hypothesis": _short(analysis.hypothesis, 150),
            "authors_conclusion": _short(analysis.authors_conclusion, 200),
            "background": [_short(x, 100) for x in (analysis.background or []) if x][:3],
            "methods": [_short(x, 100) for x in (analysis.methods or []) if x][:3],
            "main_findings": [
                {"finding_id": f.finding_id, "finding": _short(f.finding, 150), "importance": f.importance}
                for f in (analysis.main_findings or [])[:3]
            ],
            "innovation": [_short(x, 100) for x in (analysis.innovation or []) if x][:2],
        },
        "evidence": [
            {"claim": _short(e.claim, 150), "source_section": _short(e.source_section, 60)}
            for e in (evidence.evidence or [])[:3]
            if (e.claim or "").strip()
        ],
    }


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
        # 纯文本 user payload（避免 deepseek-v4-flash 在 JSON user 上的空响应问题）
        comp = _compact_for_storyteller(analysis, evidence)
        a = comp.get("analysis", {})
        evs = comp.get("evidence", [])
        ev_text = "\n".join(f"- {e.get('claim', '')}（来源：{e.get('source_section', '')}）" for e in evs) or "（无）"
        user = (
            f"## 模式\n{mode}\n\n"
            f"## 论文标题\n{a.get('title', '')}\n\n"
            f"## 研究问题\n{a.get('research_question', '')}\n\n"
            f"## 知识空白\n{a.get('knowledge_gap', '')}\n\n"
            f"## 假设\n{a.get('hypothesis', '')}\n\n"
            f"## 作者结论\n{a.get('authors_conclusion', '')}\n\n"
            f"## 背景\n" + ("\n".join(f"- {x}" for x in a.get("background", [])) or "（无）") + "\n\n"
            f"## 方法\n" + ("\n".join(f"- {x}" for x in a.get("methods", [])) or "（无）") + "\n\n"
            f"## 主要发现\n" + ("\n".join(f"- {f.get('finding', '')}（{f.get('importance', 'medium')}）" for f in a.get("main_findings", [])) or "（无）") + "\n\n"
            f"## 创新点\n" + ("\n".join(f"- {x}" for x in a.get("innovation", [])) or "（无）") + "\n\n"
            f"## 证据\n{ev_text}"
        )
        data = generate_json(
            self.llm,
            system=self.prompts.storyteller,
            user=user,
            draft=draft.model_dump(),
            temperature=self.temperature(),
            max_tokens=self.max_tokens(),
            _caller="storyteller",
        )
        return Storyline(**data)
