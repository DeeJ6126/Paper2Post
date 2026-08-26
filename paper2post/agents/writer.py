"""Science Writer Agent：将 storyline 转为公众号推文（Markdown）。"""

from __future__ import annotations

from typing import List, Optional

from paper2post.llm.base import LLMProvider
from paper2post.prompts import Prompts
from paper2post.schemas.paper import PaperAnalysis
from paper2post.schemas.evidence import EvidenceMap
from paper2post.schemas.storyline import Storyline
from paper2post.schemas.figure import FigureAnalysis

from .base import BaseAgent


class WriterAgent(BaseAgent):
    def __init__(
        self,
        llm: LLMProvider,
        prompts: Prompts,
        config: Optional[dict] = None,
    ):
        super().__init__(llm, prompts, config)

    def _draft(
        self,
        analysis: PaperAnalysis,
        evidence: EvidenceMap,
        storyline: Storyline,
        figures: List[FigureAnalysis],
        options: dict,
    ) -> str:
        """mock 模式：用已解析数据合成一篇结构完整、可读的推文（而非占位骨架）。"""
        lines: List[str] = []

        def is_ph(x):
            return (not x) or isinstance(x, str) and x.startswith("（待补充")

        def para(items, fallback):
            got = " ".join(x for x in items if not is_ph(x))
            return got if got else fallback

        title = analysis.title or "Untitled paper"
        lines.append(f"# {title}")
        lines.append("")
        summ = self.first_sentence(analysis.research_question) or storyline.hook or storyline.core_question
        lines.append(f"> {summ or '（见正文）'}")
        lines.append("")

        lines.append("## 为什么值得关注")
        lines.append(para(analysis.background, storyline.hook or "该研究围绕一项重要的科学问题展开，具有潜在的临床与应用价值。"))
        lines.append("")

        lines.append("## 作者想解决什么问题")
        lines.append(analysis.research_question or "（研究问题见摘要）")
        if analysis.knowledge_gap and not is_ph(analysis.knowledge_gap):
            lines.append(""); lines.append(analysis.knowledge_gap)
        lines.append("")

        lines.append("## 作者是怎么研究的")
        lines.append(para(analysis.methods, "研究采用了系统性的实验与计算手段。"))
        sp = ", ".join(analysis.samples.species) or "human / mouse"
        if analysis.samples.sample_size or analysis.samples.species:
            lines.append(f"样本信息：{analysis.samples.sample_size or '——'} · {sp} · {', '.join(analysis.samples.tissue) or '——'}。")
        lines.append("")

        findings = [f for f in analysis.main_findings if f.finding]
        lines.append("## 最重要的发现")
        if findings:
            for i, f in enumerate(findings, 1):
                lines.append(f"**发现 {i}｜{f.finding}**")
                if f.evidence:
                    lines.append(f.evidence)
                if f.figure:
                    lines.append(f"（对应 {f.figure}）")
                lines.append("")
        else:
            lines.append("（该论文的关键发现可结合原文与证据阅读。）")
        lines.append("")

        lines.append("## 背后的机制与创新")
        lines.append(para(analysis.innovation, "（机制与创新点详见论文 Discussion。）"))
        lines.append("")

        lines.append("## 意义与局限")
        lines.append(analysis.authors_conclusion if not is_ph(analysis.authors_conclusion) else "（结论见原文）")
        lims = [x for x in analysis.limitations if not is_ph(x)]
        if lims:
            lines.append(""); lines.append("局限：" + "；".join(lims))
        lines.append("")

        used = [f for f in figures if f.article_usage]
        if used:
            lines.append("## 关键图表")
            for f in used[:6]:
                lines.append(f"- **{f.figure}｜{f.summary}**")
            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("### 论文信息")
        lines.append("**Title:** " + (analysis.title or "") + "  \n**Journal:** " + (analysis.journal or "") + "  \n**Year:** " + (analysis.year or "") + "  \n**DOI:** —")
        lines.append("")
        return "\n".join(lines)

    def run(
        self,
        analysis: PaperAnalysis,
        evidence: EvidenceMap,
        storyline: Storyline,
        figures: List[FigureAnalysis],
        options: dict,
    ) -> str:
        if self.llm.is_mock:
            return self._draft(analysis, evidence, storyline, figures, options)

        user = self.dump(
            {
                "analysis": analysis.model_dump(),
                "evidence": evidence.model_dump(),
                "storyline": storyline.model_dump(),
                "figures": [x.model_dump() for x in figures],
                "article_config": options,
            }
        )
        return self.llm.chat(
            system=self.prompts.writer,
            user=user,
            temperature=self.temperature(),
            max_tokens=self.max_tokens(),
        )
