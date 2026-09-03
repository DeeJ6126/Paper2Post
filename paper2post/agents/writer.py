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

# 单节内容长度上限（2026-09-03：1200 太短，多篇出现"停在 '而' / 'AF3' / '综合' 截断"；
# 提到 2000 给 800-1200 字符的输出更多缓冲）
SECTION_MAX_TOKENS = 2000
# 输入里 evidence 列表最多保留前 N 条
# 经验：vision 模型在 ~3KB 输入下稳定；evidence 8×200B=1.6KB 加上其他字段就超。
# 砍到 3 条保 vision 稳定。flash / pro 等纯文本模型可以更大但当前默认 vision。
EVIDENCE_KEEP = 3
# 输入里 figures 列表最多保留前 N 条
FIGURES_KEEP = 2

# 占位符检测：writer prompt 的 role 描述 / 输出空时的兜底，特征是 "（xxx）" 整段被原样返回。
# 评审 1 显示 cellreasoner/EMSFold/AI for drug/DiffDock/RSA/Uni-Mol/AlphaFold3/AlphaGenome
# 都把这种 template 文字直接写进 final_article.md。这里集中拦截。
_PLACEHOLDER_ROLE_HINTS = (
    "阐述该论文的领域意义",
    "该论文要回答的核心科学问题",
    "研究方法 / 数据集 / 模型框架 / 实验设计",
    "关键结果，每条 evidence 对应一个 finding",
    "作者对结果给出的生物学 / 化学 / 物理学解释",
    "补充实验、跨数据集验证、消融等",
    "对领域、对下游应用、对临床或产业的影响",
    "作者承认的不足、当前方法的边界",
    "（本节",
    "（详见",
    "（见原文",
    "I cannot extract limitations",
    "I cannot",
    "I'm sorry",
)

# 截断检测：段落不以句末标点（。！？…）结尾，且尾字符是中文字符 → 算"中途截断"。
_END_PUNCT = set("。！？…!?.\n\r")


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
            for f in (a.main_findings or [])[:3]
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
    """压缩图列表 + 给无视觉描述的图打标记，让 writer 知道不能扩展视觉细节。"""
    out: list = []
    for f in (figs or [])[:FIGURES_KEEP]:
        summary = (f.summary or "").strip()
        has_visual = "【视觉描述】" in summary
        # 显式无视觉描述（vision 失败被 figure_agent 标了）
        is_no_visual = ("[无视觉描述" in summary) or (not has_visual and len(summary) < 30)
        out.append(
            {
                "figure": f.figure,
                "importance": f.importance,
                "role": f.role,
                "summary": _short_text(summary, 180) if not is_no_visual else "[无视觉描述 — 仅 caption 可用]",
                "has_visual": has_visual,
            }
        )
    return out


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

    def _format_user_as_text(
        self,
        section_name: str,
        section_role: str,
        analysis: PaperAnalysis,
        evidence: EvidenceMap,
        figures: List[FigureAnalysis],
        previous_sections: List[str],
        options: dict,
    ) -> str:
        """把 section 调用 payload 渲染成纯 Markdown 文本。

        关键发现（2026-09-03 probe）：deepseek-v4-flash 对 JSON 格式的 user prompt
        会卡 14s 后返回空字符串（疑似 content filter / tokenization 触发）。
        同样的内容用纯文本 / Markdown 格式就 10s 内返回 800+ 字符。
        改用纯文本 prompt 后 writer per-section 终于能产出实际内容。
        """
        a = _compact_analysis(analysis)
        ev = _compact_evidence(evidence)
        figs = _compact_figures(figures)
        lines = [
            f"## 当前 section: {section_name}",
            f"（{section_role}）",
            "",
            "## 论文信息",
            f"- 标题：{a.get('title', '')}",
            f"- 研究问题：{a.get('research_question', '')}",
            f"- 知识空白：{a.get('knowledge_gap', '')}",
            f"- 假设：{a.get('hypothesis', '')}",
            f"- 结论：{a.get('authors_conclusion', '')}",
        ]
        bg = a.get('background', [])
        if bg:
            lines.append("- 背景：")
            for b in bg:
                lines.append(f"  - {b}")
        methods = a.get('methods', [])
        if methods:
            lines.append("- 方法：")
            for m in methods:
                lines.append(f"  - {m}")
        findings = a.get('main_findings', [])
        if findings:
            lines.append("- 主要发现：")
            for f in findings:
                lines.append(f"  - {f.get('finding', '')}（重要性：{f.get('importance', 'medium')}）")
        inn = a.get('innovation', [])
        if inn:
            lines.append("- 创新点：")
            for x in inn:
                lines.append(f"  - {x}")
        lim = a.get('limitations', [])
        if lim:
            lines.append("- 局限：")
            for x in lim:
                lines.append(f"  - {x}")
        if ev:
            lines.append("")
            lines.append("## 证据")
            for e in ev:
                lines.append(f"- {e.get('claim', '')}（来源：{e.get('source_section', '')}）")
        if figs:
            lines.append("")
            lines.append("## 可用图")
            for f in figs:
                if f.get("has_visual"):
                    lines.append(f"- {f.get('figure', '')}（{f.get('role', '')}）：{f.get('summary', '')}")
                else:
                    # 显式标记"无视觉描述"防止 writer 编造视觉内容
                    lines.append(f"- {f.get('figure', '')}（{f.get('role', '')}）：[无视觉描述 — 只能引用 caption，不要扩展视觉细节]")
        if previous_sections:
            lines.append("")
            lines.append("## 已有上文（避免重复，保持语气一致）")
            for ps in previous_sections[-2:]:
                lines.append(ps[:400] + ("…" if len(ps) > 400 else ""))
        lines.append("")
        lines.append("## 目标")
        lines.append(f"- 语言：{options.get('language', 'zh-CN')}")
        lines.append(f"- 受众：{options.get('target_audience', 'biology_graduate')}")
        lines.append("- 长度：500-800 字")
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
        user = self._format_user_as_text(
            section_name, section_role, analysis, evidence, figures,
            previous_sections, options,
        )
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

    def _call_oneshot_llm(
        self,
        analysis: PaperAnalysis,
        evidence: EvidenceMap,
        figures: List[FigureAnalysis],
        options: dict,
    ) -> str:
        """1 次 LLM 调用写完整篇文章。避开 8 sections × 多次空响应的累计风险。

        输出 ~6000 token 中文（8 sections × 700 字符）。max_tokens 6000 给 vision 也会卡，
        改用 flash 模型输出 6000 token（如果用 vision 模型则用 5000 上限）。
        """
        user_payload = {
            "sections": [{"name": n, "role": r} for n, r in DEFAULT_SECTIONS],
            "analysis": _compact_analysis(analysis),
            "evidence": _compact_evidence(evidence),
            "figures": _compact_figures(figures),
            "target_language": options.get("language", "zh-CN"),
            "target_audience": options.get("target_audience", "biology_graduate"),
        }
        user = self.dump(user_payload)
        # flash 模型输出 6000 token OK；vision 模型输出 ~1500 token 安全
        is_vision = bool(getattr(self.llm, "supports_vision", lambda: False)())
        text = self.llm.generate(
            system=self.prompts.writer_section + "\n\n## 一次性写完整篇\n一次性输出 8 个 ## section_name 子节，每节 500-800 字中文 Markdown。**严格按顺序写所有 sections**，不要省略。",
            user=user,
            temperature=self.temperature(),
            max_tokens=1500 if is_vision else 6000,
        )
        return (text or "").strip()

    def _split_oneshot_to_sections(self, text: str) -> dict:
        """把 oneshot 全文按 '## 01 xxx' / '## 02 xxx' 等标题切成 sections。"""
        sections = {}
        if not text:
            return sections
        # 找所有 "## N " 标题位置
        import re
        pattern = re.compile(r"^##\s*(\d+)\s+(.+)$", re.M)
        matches = list(pattern.finditer(text))
        if not matches:
            return sections
        for i, m in enumerate(matches):
            num = m.group(1)
            title = m.group(2).strip()
            # 名字去掉末尾的中文标点和数字等做归一化
            content_start = m.end()
            content_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            content = text[content_start:content_end].strip()
            # 用 num + title 一起做 key
            sections[f"{num} {title}"] = content
        return sections

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

        # 逐节 LLM 调用：每节 1 次小调用，max_tokens 2000，flash 模型稳定
        # 经验（2026-09-03）：oneshot 6000 token 调用 deepseek-v4-flash 在 writer payload 下
        # 20s timeout 不够，模型生成 6000 token 速度跟不上；改回逐节 8 × 2000 token。
        # 提 1200 → 2000 防"中途停在 '而' / 'AF3' / '综合'" 截断（评审 1 普遍问题）。
        written: List[str] = [f"# {analysis.title or 'Untitled paper'}"]
        for name, role in DEFAULT_SECTIONS:
            body = self._generate_section_body(
                section_name=name, section_role=role,
                analysis=analysis, evidence=evidence, figures=figures,
                previous_sections=written[-2:], options=options,
            )
            if not body:
                # 真没救 → 显式标记"模型未能生成"而不是把 role 描述塞进去冒充内容
                body = f"（{role} — 未能生成）"
            section_md = f"## {name}\n\n{body.rstrip()}\n"
            written.append(section_md)

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

    def _generate_section_body(
        self,
        *,
        section_name: str,
        section_role: str,
        analysis: PaperAnalysis,
        evidence: EvidenceMap,
        figures: List[FigureAnalysis],
        previous_sections: List[str],
        options: dict,
    ) -> str:
        """单节 LLM 调用 + 占位符 / 截断防御。

        评审 1（2026-09-03）暴露 3 个失败模式：
        1. **占位符泄漏**：LLM 把 `（阐述该论文的领域意义和为什么读者应该关心。）` 这类
           prompt 模板文字原样返回 → 拦截，重试一次。
        2. **截断**：段落停在 "而" / "AF3" / "综合" 等中间字 → 拦截，重试一次。
        3. **meta-disclaimer**：LLM 返回 "I cannot extract limitations" 等元消息 → 拦截，
           当作空返回，让外层兜底写"未能生成"。

        每次最多 2 次尝试（1 正常 + 1 重试）。仍失败就空，外层用显式占位（不再用 role 描述冒充）。
        """
        last_text = ""
        for attempt in (1, 2):
            try:
                text = self._call_section_llm(
                    section_name=section_name, section_role=section_role,
                    analysis=analysis, evidence=evidence, figures=figures,
                    previous_sections=previous_sections, options=options,
                )
            except Exception:
                text = ""
            last_text = text
            body = self._extract_section_body(text, section_name)
            if self._is_bad_body(body, section_role):
                # 占位符 / 截断 / meta-disclaimer → 重试或放弃
                if attempt == 1:
                    import time as _t
                    _t.sleep(0.3)
                    continue
                return ""
            return body
        return ""

    @staticmethod
    def _is_bad_body(body: str, role: str) -> bool:
        """True 表示 body 是"不可用"：空、占位符模板、截断、meta-disclaimer。"""
        if not body:
            return True
        body_stripped = body.strip()
        if len(body_stripped) < 60:
            return True  # 太短，判定为没写出东西
        # meta-disclaimer / "I cannot" / "I don't have"
        low = body_stripped.lower()
        if any(s in low for s in ("i cannot", "i don't have", "i am unable", "i'm sorry", "i can\u2019t")):
            return True
        # 占位符模板（prompt role 描述被原样返回）
        for hint in _PLACEHOLDER_ROLE_HINTS:
            if hint in body_stripped and len(body_stripped) < 200:
                return True
        # 截断：最后 1 个字符是中文/字母且不是句末标点
        if body_stripped and body_stripped[-1] not in _END_PUNCT:
            return True
        return False
