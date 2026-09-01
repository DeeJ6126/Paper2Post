"""Science Writer Agent：将 storyline 转为公众号推文（Markdown）。

两段式：
  1) 一次性 LLM 调用拿"文章骨架"（可选，默认用 writer.md 里固定的 8 节清单，
     避免再发一次 LLM；vision 模型多调一次都是风险）
  2) 逐节 LLM 调用写内容（每节 ~500-700 字，输入/输出 <2KB，vision 模型稳定）
  3) 串成 final_article.md
"""

from __future__ import annotations

import re
from typing import List, Optional

from paper2post.llm.base import LLMProvider
from paper2post.prompts import Prompts
from paper2post.schemas.paper import PaperAnalysis
from paper2post.schemas.evidence import EvidenceMap
from paper2post.schemas.storyline import Storyline
from paper2post.schemas.figure import FigureAnalysis

from .base import BaseAgent

# 默认的 8 节文章骨架。每节带 role 描述，喂给 Section Writer。
DEFAULT_SECTIONS = [
    ("01 为什么值得关注",       "阐述该论文的领域意义和为什么读者应该关心。"),
    ("02 作者想解决什么问题",   "该论文要回答的核心科学问题、现有研究空白。"),
    ("03 作者是怎么研究的",     "研究方法 / 数据集 / 模型框架 / 实验设计。"),
    ("04 最重要的发现是什么",   "关键结果，每条 evidence 对应一个 finding。"),
    ("05 背后的机制是什么",     "作者对结果给出的生物学 / 化学 / 物理学解释。"),
    ("06 作者如何进一步验证",   "补充实验、跨数据集验证、消融等。"),
    ("07 这项工作的意义在哪里", "对领域、对下游应用、对临床或产业的影响。"),
    ("08 有哪些局限",           "作者承认的不足、当前方法的边界。"),
]

# 单节内容长度上限
SECTION_MAX_TOKENS = 1200
# 输入里 evidence 列表最多保留前 N 条
EVIDENCE_KEEP = 8
# 输入里 figures 列表最多保留前 N 条
FIGURES_KEEP = 6


def _short_text(v, limit: int = 200) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def _compact_analysis(a: PaperAnalysis) -> dict:
    """把 PaperAnalysis 压到最小可用集合。"""
    return {
        "title": a.title or "",
        "research_question": _short_text(a.research_question, 400),
        "background": [_short_text(x, 150) for x in (a.background or []) if x][:3],
        "knowledge_gap": _short_text(a.knowledge_gap, 200),
        "hypothesis": _short_text(a.hypothesis, 200),
        "methods": [_short_text(x, 150) for x in (a.methods or []) if x][:5],
        "main_findings": [
            {
                "finding_id": (f.finding_id or ""),
                "finding": _short_text(f.finding, 200),
                "evidence": _short_text(f.evidence, 200),
                "figure": _short_text(f.figure, 60),
                "importance": f.importance or "medium",
            }
            for f in (a.main_findings or [])[:6]
        ],
        "innovation": [_short_text(x, 150) for x in (a.innovation or []) if x][:3],
        "limitations": [_short_text(x, 150) for x in (a.limitations or []) if x][:3],
        "authors_conclusion": _short_text(a.authors_conclusion, 250),
    }


def _compact_evidence(e: EvidenceMap) -> list:
    return [
        {"claim": _short_text(item.claim, 200), "source_section": _short_text(item.source_section, 80),
         "figure": _short_text(item.figure, 60), "confidence": float(item.confidence or 0)}
        for item in (e.evidence or [])[:EVIDENCE_KEEP]
        if (item.claim or "").strip()
    ]


def _compact_figures(figs: List[FigureAnalysis]) -> list:
    return [
        {"figure": f.figure, "importance": f.importance, "role": f.role, "summary": _short_text(f.summary, 180)}
        for f in (figs or [])[:FIGURES_KEEP]
    ]


class WriterAgent(BaseAgent):
    def __init__(
        self,
        llm: LLMProvider,
        prompts: Prompts,
        config: Optional[dict] = None,
    ):
        super().__init__(llm, prompts, config)

    # ---- 旧 mock 风格 draft（不再依赖；保留以防无 LLM 调用） ----
    def _mock_draft(
        self,
        analysis: PaperAnalysis,
        evidence: EvidenceMap,
        storyline: Storyline,
        figures: List[FigureAnalysis],
        options: dict,
    ) -> str:
        # 当 LLM 完全不可用时退化成结构化骨架
        lines = [f"# {analysis.title or 'Untitled paper'}", ""]
        for sec, role in DEFAULT_SECTIONS[:5]:
            lines.append(f"## {sec}")
            lines.append(f"（{role}）")
            lines.append("")
        return "\n".join(lines)

    def _call_section_llm(
        self,
        section_name: str,
        section_role: str,
        analysis: PaperAnalysis,
        evidence: EvidenceMap,
        figures: List[FigureAnalysis],
        previous_sections: List[str],
        options: dict,
    ) -> str:
        """单节 LLM 调用。返回 markdown 字符串。"""
        user_payload = {
            "section_name": section_name,
            "section_role": section_role,
            "analysis": _compact_analysis(analysis),
            "evidence": _compact_evidence(evidence),
            "figures": _compact_figures(figures),
            "previous_sections": previous_sections[-2:] if previous_sections else [],
            "target_language": options.get("language", "zh-CN"),
            "target_audience": options.get("target_audience", "biology_graduate"),
        }
        user = self.dump(user_payload)
        text = self.llm.generate(
            system=self.prompts.writer_section,
            user=user,
            temperature=self.temperature(),
            max_tokens=SECTION_MAX_TOKENS,
        )
        return (text or "").strip()

    def _extract_section_body(self, text: str, section_name: str) -> str:
        """LLM 可能直接返回 '## section_name\\n...'，去掉前导标题避免重复。"""
        t = (text or "").strip()
        # 去掉开头可能的 code fence
        if t.startswith("```"):
            t = t.strip("`").strip()
            if t.lower().startswith("markdown"):
                t = t[len("markdown"):].strip()
        # 去掉 LLM 自己加的标题（与我们要的 ## section_name 重复）
        # 匹配首行恰好是该 section_name（容忍微小差异）
        m = re.match(r"^#{1,6}\s*([^\n]+?)\s*\n", t)
        if m and section_name.replace(" ", "").replace("0", "").replace("1", "").replace("2", "").replace("3", "").replace("4", "").replace("5", "").replace("6", "").replace("7", "").replace("8", "").replace("9", "").lower() in m.group(1).replace(" ", "").lower():
            t = t[m.end():].lstrip("\n").lstrip()
        return t

    def run(
        self,
        analysis: PaperAnalysis,
        evidence: EvidenceMap,
        storyline: Storyline,
        figures: List[FigureAnalysis],
        options: dict,
    ) -> str:
        if self.llm.is_mock:
            return self._mock_draft(analysis, evidence, storyline, figures, options)

        # 逐节写
        written: List[str] = [f"# {analysis.title or 'Untitled paper'}"]
        previous_bodies: List[str] = []
        for name, role in DEFAULT_SECTIONS:
            try:
                text = self._call_section_llm(
                    section_name=name,
                    section_role=role,
                    analysis=analysis,
                    evidence=evidence,
                    figures=figures,
                    previous_sections=previous_bodies,
                    options=options,
                )
            except Exception:
                text = ""
            body = self._extract_section_body(text, name)
            if not body:
                # 兜底：占位文字
                body = f"（{role}）"
            section_md = f"## {name}\n\n{body.rstrip()}\n"
            written.append(section_md)
            # 把可用的 body 喂给下一节（去除 markdown 标记）
            previous_bodies.append(re.sub(r"[#*`>\[\]]", "", body)[:400])

        # 论文信息 footer
        journal = analysis.journal or ""
        year = analysis.year or ""
        doi = "—"
        written.append("---")
        written.append("")
        written.append("### 论文信息")
        written.append(f"**Title:** {analysis.title}  ")
        written.append(f"**Journal:** {journal}  ")
        written.append(f"**Year:** {year}  ")
        written.append(f"**DOI:** {doi}")
        written.append("")

        return "\n".join(written)
