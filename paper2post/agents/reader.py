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

# 单节正文字数上限：vision 模型 ~1.5KB 输入下空响应频繁。**按 token 估算更稳**——
# 中文 1 字符 ≈ 1 token。600 字符 ≈ 600 token，加上 system prompt (~600 token) + 输出
# (~800 token) + 包装 = 总 ~2200 token，在 vision 模型的稳定区间内。
SECTION_TEXT_LIMIT = 600
# 单节抽取输出上限：紧凑 JSON，800 tokens 足够。
SECTION_MAX_TOKENS = 800
# 总聚合输入上限：聚合 prompt 含 6+ section notes 时通常超过这个阈值，触发空响应。
# 默认走 _degrade_from_notes，不需要聚合 LLM call。
AGG_INPUT_LIMIT = 6000
# 一次最多抽取多少节：限制 vision 模型下的总耗时
MAX_SECTIONS = 3


def _split_abstract(abstract: str) -> List[str]:
    """把 abstract 拆成句。容忍中英文句号、换行。"""
    text = (abstract or "").strip()
    if not text:
        return []
    for sep in ("\n", "。", "."):
        text = text.replace(sep, "\n")
    return [s.strip() for s in text.split("\n") if s.strip() and len(s.strip()) > 12]


def _abstract_fallback(abstract: str, kind: str) -> str:
    """4 字段的最后兜底：直接从 abstract 拆句取对应位置，**绝不返回字面 placeholder**。

    kind 取值: gap / hyp / conc
    """
    sents = _split_abstract(abstract)
    if not sents:
        return f"（来自 abstract：{(abstract or '')[:200]}）"
    if kind == "gap":
        # 优先末句（论文 abstract 末尾常含 limitation 暗示）
        cand = next((s for s in reversed(sents) if any(
            kw in s.lower() for kw in ("however", "but", "yet", "although", "remain", "limitation", "challenge"))), None)
        return cand or sents[-1][:300]
    if kind == "hyp":
        # 优先第一句（介绍性主张）
        cand = next((s for s in sents if any(
            kw in s.lower() for kw in ("we present", "we propose", "we introduce", "we develop", "we design", "we build", "本文", "我们"))), None)
        return cand or sents[0][:300]
    if kind == "conc":
        # 优先末句
        return sents[-1][:300]
    return sents[0][:300]


def _abstract_fallback_list(abstract: str) -> List[str]:
    """innovation 字段的兜底：取 abstract 前 1 句。"""
    sents = _split_abstract(abstract)
    if sents:
        return [sents[0][:300]]
    return [(abstract or "")[:200]] if abstract else []


class ReaderAgent(BaseAgent):
    """读取论文，输出结构化 paper_analysis.json。不直接写推文。"""

    MAX_SECTIONS = MAX_SECTIONS

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
            "gap_phrasing": "",
            "hypothesis_phrasing": "",
            "conclusion_phrasing": "",
            "innovation_phrases": [],
        }

    def _extract_section(self, idx: int, heading: str, text: str) -> Dict[str, Any]:
        """Step 1: 对单节做要点抽取，输入小、输出小。

        temperature 用 0.2 强制 JSON 稳定：vision 模型 + 长 prompt 下 0.7 经常
        在字符串里加多余引号导致 JSON parse 失败。

        关键（2026-09-03 probe）：deepseek-v4-flash 对 JSON 格式的 user prompt
        会卡 14s 后返回空字符串。改用纯文本 / Markdown 格式即可 10s 内返回。
        输出端 response_format 仍然强约束 JSON，所以 model 还是按 JSON 输出。
        """
        # 纯文本 user payload（不再用 JSON dict）
        truncated = (text or "")[:SECTION_TEXT_LIMIT]
        user = (
            f"## 第 {idx} 节\n"
            f"标题：{heading}\n\n"
            f"## 原文（≤{SECTION_TEXT_LIMIT} 字）\n{truncated}"
        )
        data = generate_json(
            self.llm,
            system=self.prompts.reader_section,
            user=user,
            draft=self._empty_section_note(),
            temperature=0.2,
            max_tokens=SECTION_MAX_TOKENS,
            _caller="reader_section",
        )
        # 容错：补齐缺省字段
        if not isinstance(data, dict):
            data = {}
        data.setdefault("role", self._infer_role_from_heading(heading))
        for k, v in self._empty_section_note().items():
            data.setdefault(k, v)
        return data

    _HEADING_ROLE_HINTS = (
        ("abstract", "abstract"),
        ("introduction", "introduction"),
        ("intro", "introduction"),
        ("background", "background"),
        ("related work", "related_work"),
        ("related_work", "related_work"),
        ("method", "methods"),
        ("approach", "methods"),
        ("experiment", "results"),
        ("result", "results"),
        ("evaluation", "results"),
        ("discussion", "discussion"),
        ("conclusion", "conclusion"),
        ("summary", "conclusion"),
    )

    def _infer_role_from_heading(self, heading: str) -> str:
        h = (heading or "").lower()
        for kw, role in self._HEADING_ROLE_HINTS:
            if kw in h:
                return role
        return "unknown"

    def _extract_all_sections(self, paper: ParsedPaper) -> List[Dict[str, Any]]:
        """逐节调一次 LLM；任何单节失败不影响后续。

        长论文只取前 MAX_SECTIONS 节：13 节 × 30s × 3 retries 在 vision 模型下要 20 分钟，
        6 节大约 5 分钟，能拿到够用的覆盖。

        选 section 策略：
        1. 优先 abstract（论文核心）
        2. 优先 methods / results（实验）
        3. 跳过 references / appendix（已在 parser 层去）
        4. 同一 heading 只取第一个
        """
        # 优先级：abstract > methods > results > introduction > discussion > conclusion
        priority = {
            "abstract": 0, "introduction": 4, "methods": 1, "results": 2,
            "discussion": 3, "conclusion": 5,
        }
        sorted_sections = sorted(
            paper.sections,
            key=lambda s: (priority.get(s.heading.lower(), 99), len(s.text or "")),
        )
        # 同一 heading 只取第一个
        seen: set = set()
        deduped: List = []
        for s in sorted_sections:
            norm = (s.heading or "").lower()
            if norm in seen:
                continue
            seen.add(norm)
            deduped.append(s)
        sections_to_process = deduped[: self.MAX_SECTIONS]
        notes: List[Dict[str, Any]] = []
        for i, sec in enumerate(sections_to_process):
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
        #   注：vision 模型在 6KB+ 输入下偶发返回空响应且重试成本高。聚合 prompt
        #   含 6+ section notes 时通常超过这个阈值。改为：默认走 _degrade_from_notes
        #   直接用 section_notes 拼出 PaperAnalysis；如果 LLM 真的不依赖 vision
        #   （flash/pro），可以传 aggregate=True 启用二次合成。
        if self.config.get("aggregate", False):
            user = self._build_aggregate_user(paper, section_notes)
            data = generate_json(
                self.llm,
                system=self.prompts.reader,
                user=user,
                draft=draft.model_dump(),
                temperature=self.temperature(),
                max_tokens=self.max_tokens(),
                _caller="reader_aggregate",
            )
            try:
                return PaperAnalysis(**data)
            except Exception:
                pass
        return self._degrade_from_notes(paper, section_notes)

    def _degrade_from_notes(self, paper: ParsedPaper, notes: List[Dict[str, Any]]) -> PaperAnalysis:
        """当 LLM 聚合返回的 JSON 不满足 schema 时，从 section notes 直接拼一个可用版。

        优先使用 section notes 里的 gap_phrasing / conclusion_phrasing / innovation_phrases
        （这些字段由 prompts/reader_section.md 引导 LLM 在抽 section 时直接给出）。
        缺失时再用基于关键词的启发式从 claims 里抓，最后才用占位符。
        当所有 notes 都 fallback（LLM 全程空响应）时，用 paper.abstract 直接分句兜底。
        """
        all_claims: List[str] = []
        all_methods: List[str] = []
        all_findings: List[Dict[str, Any]] = []
        all_evidence: List[str] = []
        all_figures: List[str] = []
        all_limitations: List[str] = []
        all_innovations: List[str] = []
        gaps: List[str] = []
        hypotheses: List[str] = []
        conclusions: List[str] = []
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
            gap = (n.get("gap_phrasing") or "").strip()
            if gap:
                gaps.append(gap)
            hyp = (n.get("hypothesis_phrasing") or "").strip()
            if hyp:
                hypotheses.append(hyp)
            conclusion = (n.get("conclusion_phrasing") or "").strip()
            if conclusion:
                conclusions.append(conclusion)
            for ip in n.get("innovation_phrases", []) or []:
                ip_clean = (ip or "").strip()
                if ip_clean:
                    all_innovations.append(ip_clean)

        # 兜底层：当所有 notes 都没 claims（LLM 全程空响应）时，从 paper.abstract 拆句作为
        # 抽象级的 background/claims 来源。background 至少有内容，启发式关键词也能用上。
        if not any(n.get("claims") for n in notes) and paper.abstract:
            abstract_sents = [
                s.strip() for s in
                paper.abstract.replace("。", "。\n").replace(".", ".\n").split("\n")
                if s.strip() and len(s.strip()) > 12
            ][:6]
            if abstract_sents:
                notes = list(notes) + [
                    {"role": "abstract", "claims": abstract_sents[:3], "gap_phrasing": "", "hypothesis_phrasing": "", "conclusion_phrasing": "", "innovation_phrases": []},
                ]
                for s in abstract_sents:
                    all_claims.append(s)

        # 启发式 fallback：当 section notes 没显式提供时，从 claims 里抓
        if not gaps:
            # 关键改进：去掉 "difficult"（会被 "differential" 误匹配）、"challenge"
            # （会被 "challen" 误匹到 differential） 等太宽的词
            gap_keywords = ("未解决", "尚未", "缺乏", "空白", "不足", "未充分", "未知的",
                            "remained unclear", "remains unknown", "lacking", "unresolved",
                            "open question", "limitation",
                            "however,", "but ", "yet ", "although ")
            for n in notes:
                role = n.get("role", "unknown")
                if role in ("background", "introduction", "related_work", "discussion", "abstract"):
                    for c in n.get("claims", []):
                        cl = c.lower()
                        if any(kw in cl for kw in gap_keywords):
                            gaps.append(c)
                            break
                if gaps:
                    break
            # 终极兜底：abstract 倒数第一句
            if not gaps and paper.abstract:
                sents = [s.strip() for s in paper.abstract.replace("。", "。\n").replace(".", ".\n").split("\n") if s.strip() and len(s.strip()) > 12]
                if sents:
                    last = sents[-1][:300]
                    if any(kw in last.lower() for kw in ("however", "but", "yet", "although", "remain", "challenge", "limitation")):
                        gaps.append(last)
                    else:
                        gaps.append(last)

        if not hypotheses:
            hyp_keywords = ("假设", "假说", "提出", "认为", "预测", "猜想",
                            "hypothesis", "hypothesize", "we propose", "we hypothesize",
                            "we posit", "we assume", "we predict",
                            "we present", "we introduce", "we develop", "we design", "we build",
                            "本文", "我们提出", "本工作")
            for n in notes:
                role = n.get("role", "unknown")
                if role in ("abstract", "introduction", "background"):
                    for c in n.get("claims", []):
                        if any(kw in c.lower() for kw in hyp_keywords):
                            hypotheses.append(c)
                            break
                if hypotheses:
                    break
            # 兜底：abstract 中含"we present / we propose / we introduce"等介绍性主张的句子
            if not hypotheses and paper.abstract:
                sents = [s.strip() for s in paper.abstract.replace("。", "。\n").replace(".", ".\n").split("\n") if s.strip() and len(s.strip()) > 12]
                for s in sents:
                    if any(kw in s.lower() for kw in ("we present", "we propose", "we introduce", "we develop", "we design", "we build", "we describe", "we develop")):
                        hypotheses.append(s)
                        break
                # 最终兜底：abstract 前 2 句（论文 abstract 开头常介绍目的/方法）
                if not hypotheses and sents:
                    hypotheses.append(sents[0][:300])

        if not conclusions:
            # 优先取 conclusion 节的 claims
            for n in notes:
                if n.get("role") == "conclusion":
                    cs = n.get("claims", [])
                    if cs:
                        conclusions.append(cs[0])
                        break
            # fallback：abstract 的第一条 claim
            if not conclusions:
                for n in notes:
                    if n.get("role") == "abstract" and n.get("claims"):
                        conclusions.append(n["claims"][0])
                        break
            # 终极兜底：abstract 末 1 句（论文 abstract 末尾通常给总结）
            if not conclusions and paper.abstract:
                sents = [s.strip() for s in paper.abstract.replace("。", "。\n").replace(".", ".\n").split("\n") if s.strip() and len(s.strip()) > 12]
                if sents:
                    conclusions.append(sents[-1][:300])

        if not all_innovations:
            # 启发式关键词扩展（大论文 root cause）：技术论文"novel"标志词
            # 比通用关键词更广，加入 allow / efficient / achieve / demonstrate 等
            innovation_keywords = (
                "首次", "新", "原创", "创新", "novel", "first", "new approach",
                "we present", "we propose", "we introduce", "we develop", "we propose",
                "we design", "we build", "we show", "we demonstrate", "we achieve",
                "enables", "allows", "allows for",
                "本文", "我们提出", "本工作",
            )
            for n in notes:
                role = n.get("role", "unknown")
                if role in ("abstract", "introduction", "background", "methods"):
                    for c in n.get("claims", []):
                        if any(kw in c.lower() for kw in innovation_keywords):
                            all_innovations.append(c)
                        if len(all_innovations) >= 3:
                            break
                if len(all_innovations) >= 3:
                    break
            if not all_innovations and paper.abstract:
                # 兜底：取 abstract 前 200 字
                all_innovations = [paper.abstract[:200]]

        # 2026-09-03 评审 1：year 字段全是 "D:20"（PDF metadata.creationDate 截前 4 字符）。
        # pdf_parser 已经把干净的 year 放在 paper.metadata["year"]，优先用。
        # journal 同理：用 pdf_parser 抽出的 publisher（即使为空也保留空串，不伪造）。
        clean_year = (
            paper.metadata.get("year")
            or paper.metadata.get("pdf_metadata", {}).get("year")
            or ""
        )
        clean_journal = (
            paper.metadata.get("publisher")
            or paper.metadata.get("pdf_metadata", {}).get("publisher")
            or ""
        )
        return PaperAnalysis(
            title=paper.title,
            journal=clean_journal,
            year=clean_year,
            research_question=self.first_sentence(paper.abstract) or "",
            background=all_claims[:5] or [],
            knowledge_gap=(gaps[0] if gaps else _abstract_fallback(paper.abstract, kind="gap")),
            hypothesis=(hypotheses[0] if hypotheses else _abstract_fallback(paper.abstract, kind="hyp")),
            methods=all_methods[:5] or [],
            main_findings=all_findings[:6],
            innovation=(all_innovations[:3] if all_innovations else _abstract_fallback_list(paper.abstract)),
            limitations=all_limitations[:3] or [],
            authors_conclusion=(conclusions[0] if conclusions else _abstract_fallback(paper.abstract, kind="conc")),
        )
