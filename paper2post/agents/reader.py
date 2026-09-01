"""Paper Reader Agent：结构化论文理解。

两段式：
  1) Section Notes：逐节抽取 ≤1.5KB 的要点 JSON（vision 模型友好）
  2) Aggregate：把所有 section notes + 图注 + 摘要合成为完整 PaperAnalysis
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from paper2post.llm.base import LLMProvider, generate_json
from paper2post.prompts import Prompts
from paper2post.schemas.paper import PaperAnalysis, ParsedPaper

from .base import BaseAgent

# 单节正文字数上限：vision 模型在 ~1.5KB 中文/英文文本输入下能稳定返回 JSON
SECTION_TEXT_LIMIT = 1500
# 单节抽取输出上限：要点 JSON 极小，给 800 tokens 足够
SECTION_MAX_TOKENS = 800
# 总聚合输入上限：vision 上下文里塞 ≤6KB 比较稳
AGG_INPUT_LIMIT = 6000


class ReaderAgent(BaseAgent):
    """读取论文，输出结构化 paper_analysis.json。不直接写推文。"""

    def __init__(
        self,
        llm: LLMProvider,
        prompts: Prompts,
        config: Optional[dict] = None,
    ):
        super().__init__(llm, prompts, config)

    # ---- helpers ----
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

    def _empty_section_note(self) -> Dict[str, Any]:
        return {
            "role": "unknown",
            "claims": [],
            "key_evidence": [],
            "key_numbers": [],
            "related_figures": [],
        }

    def _extract_section(self, idx: int, heading: str, text: str) -> Dict[str, Any]:
        """Step 1: 对单节做要点抽取，输入小、输出小。"""
        user = self.dump(
            {
                "section_index": idx,
                "heading": heading,
                "text": text[:SECTION_TEXT_LIMIT],
            }
        )
        data = generate_json(
            self.llm,
            system=self.prompts.reader_section,
            user=user,
            draft=self._empty_section_note(),
            temperature=self.temperature(),
            max_tokens=SECTION_MAX_TOKENS,
        )
        # 容错：补齐缺省字段
        if not isinstance(data, dict):
            return self._empty_section_note()
        for k, v in self._empty_section_note().items():
            data.setdefault(k, v)
        return data

    def _extract_all_sections(self, paper: ParsedPaper) -> List[Dict[str, Any]]:
        """逐节调一次 LLM；任何单节失败不影响后续。"""
        notes: List[Dict[str, Any]] = []
        for i, sec in enumerate(paper.sections):
            note = self._extract_section(i + 1, sec.heading, sec.text)
            notes.append({"section_index": i + 1, "heading": sec.heading, **note})
        return notes

    def _truncate(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 30] + "…(truncated)…"

    def _build_aggregate_user(self, paper: ParsedPaper, notes: List[Dict[str, Any]]) -> str:
        """把 notes + 标题/摘要/图注打包，控制总长。"""
        payload: Dict[str, Any] = {
            "title": paper.title,
            "abstract": self._truncate(paper.abstract, 1500),
            "section_notes": notes,
            "figures": [
                {"label": f.label, "caption": self._truncate(f.caption, 300)}
                for f in paper.figures[:60]  # 极端长论文也截断
            ],
        }
        s = self.dump(payload)
        if len(s) > AGG_INPUT_LIMIT:
            # 进一步压缩：逐节截断 notes 内容
            for n in payload["section_notes"]:
                if "key_evidence" in n and isinstance(n["key_evidence"], list):
                    n["key_evidence"] = [self._truncate(str(x), 200) for x in n["key_evidence"][:3]]
                if "claims" in n and isinstance(n["claims"], list):
                    n["claims"] = [self._truncate(str(x), 200) for x in n["claims"][:4]]
            s = self.dump(payload)
        return s

    def run(self, paper: ParsedPaper) -> PaperAnalysis:
        draft = self._draft(paper)

        # Step 1: 逐节要点（vision 友好）
        section_notes = self._extract_all_sections(paper)

        # Step 2: 合成
        user = self._build_aggregate_user(paper, section_notes)
        data = generate_json(
            self.llm,
            system=self.prompts.reader,
            user=user,
            draft=draft.model_dump(),
            temperature=self.temperature(),
            max_tokens=self.max_tokens(),
        )

        try:
            return PaperAnalysis(**data)
        except Exception:
            # 聚合失败时，用 section_notes 构造一个降级 PaperAnalysis
            return self._degrade_from_notes(paper, section_notes)

    def _degrade_from_notes(self, paper: ParsedPaper, notes: List[Dict[str, Any]]) -> PaperAnalysis:
        """当 LLM 聚合返回的 JSON 不满足 schema 时，从 section notes 直接拼一个可用版。"""
        all_claims: List[str] = []
        all_methods: List[str] = []
        all_findings: List[Dict[str, Any]] = []
        all_evidence: List[str] = []
        all_figures: List[str] = []
        all_limitations: List[str] = []
        role_map = {
            "background": "background",
            "introduction": "background",
            "related_work": "background",
            "methods": "methods",
            "results": "findings",
            "discussion": "findings",
            "conclusion": "findings",
            "abstract": "background",
        }
        for n in notes:
            role = n.get("role", "unknown")
            for c in n.get("claims", []):
                if c:
                    if role_map.get(role) == "methods":
                        all_methods.append(c)
                    elif role_map.get(role) == "findings":
                        all_findings.append({"finding_id": f"F{len(all_findings) + 1}", "finding": c, "evidence": "", "figure": "", "importance": "medium"})
                    elif role_map.get(role) == "background":
                        all_claims.append(c)
            for e in n.get("key_evidence", []):
                if e:
                    all_evidence.append(e)
            for f in n.get("related_figures", []):
                if f and f not in all_figures:
                    all_figures.append(f)
        return PaperAnalysis(
            title=paper.title,
            journal=paper.metadata.get("pdf_metadata", {}).get("publisher", "") or "",
            year=str(paper.metadata.get("pdf_metadata", {}).get("creationDate", ""))[:4] or "",
            research_question=self.first_sentence(paper.abstract) or "",
            background=all_claims[:5] or ["（待补充：研究背景）"],
            knowledge_gap="（待补充：研究空白）",
            hypothesis="（待补充：研究假设）",
            methods=all_methods[:5] or ["（待补充：研究方法）"],
            main_findings=all_findings[:6],
            innovation=["（待补充：创新点）"],
            limitations=all_limitations[:3] or ["（待补充：局限）"],
            authors_conclusion="（待补充：作者结论）",
        )
