"""Science Writer Agent：将 storyline 转为公众号推文（Markdown）。

两段式：
  1) 一次性 LLM 调用拿"文章骨架"（可选，默认用 writer.md 里固定的 8 节清单，
     避免再发一次 LLM；vision 模型多调一次都是风险）
  2) 逐节 LLM 调用写内容（每节 ~500-700 字，输入/输出 <2KB，vision 模型稳定）
  3) 串成 final_article.md
"""

from __future__ import annotations

import os
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


def _safe_figure_filename(figure_label: str) -> str:
    """把 "Figure 1" / "Figure S3" / "Fig. 2A" 等归一化成可作为 PNG 文件名的字符串。

    实际 figures/ 目录里文件是 figure_1.png / figure_2.png 等小写形式，由
    figure_parser.extract_figures 决定。映射规则：
      "Figure 1"  → "figure_1"
      "Fig. 2"    → "figure_2"
      "Figure S3"  → "figure_s3"
    """
    import re as _re
    s = (figure_label or "").lower().strip()
    s = s.replace("fig.", "figure").replace("fig ", "figure")
    m = _re.search(r"figure\s*([a-z]*\s*\d+)", s)
    if m:
        return "figure_" + _re.sub(r"\s+", "", m.group(1))
    return _re.sub(r"[^a-z0-9_]+", "_", s) or "figure"


def _resolve_figure_path(figure_label: str, figures_dir: Optional[str]) -> str:
    """在 figures_dir 实际找图，返回相对路径 figures/figure_N.<ext>。

    实际扩展名可能是 png/jpeg/jpg（figure_parser 看源图 MIME），用文件存在性判断。
    若 figures_dir 未提供或找不到，fallback 用 .png。
    """
    base = _safe_figure_filename(figure_label)
    if figures_dir:
        for ext in ("png", "jpeg", "jpg", "webp"):
            cand = os.path.join(figures_dir, f"{base}.{ext}")
            if os.path.exists(cand):
                return f"figures/{base}.{ext}"
    return f"figures/{base}.png"


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
        figures_dir: Optional[str] = None,
    ):
        super().__init__(llm, prompts, config)
        # 2026-09-03 评审 2 P0-4：注入 figure 引用时要知道 figures/ 实际扩展名
        # （png/jpeg/jpg 取决于源图），所以传目录进来。None 时回退 .png
        self._figures_dir = figures_dir
        # 2026-09-03 P1-1：备用 pro 模型（仅在 flash 兜底失败时启用）
        self._pro_llm: Optional[LLMProvider] = None

    def _get_pro_llm(self) -> Optional[LLMProvider]:
        """懒构建 pro 模型 provider。失败返回 None。"""
        if self._pro_llm is not None:
            return self._pro_llm
        try:
            from paper2post.llm.registry import build as _build
            cfg = dict(self.config or {})
            # 复制当前 provider 的 api_key/base_url，只换 model
            base_url = getattr(self.llm, "base_url", None) or cfg.get("base_url")
            api_key = getattr(self.llm, "api_key", None) or cfg.get("api_key")
            provider = cfg.get("provider", "deepseek")
            self._pro_llm = _build(
                cfg,
                provider_name=provider,
                api_key=api_key,
                base_url=base_url,
                model="deepseek-v4-pro",
            )
            return self._pro_llm
        except Exception:
            return None

    def _try_pro_rewrite(
        self,
        *,
        section_name: str,
        section_role: str,
        analysis: PaperAnalysis,
        evidence: EvidenceMap,
        options: dict,
    ) -> str:
        """P1-1：用 deepseek-v4-pro 重写一个章节。

        当 flash 模型 2 次都给出空/截断/中英混杂的回答时升级调用。Prompt 极简：只给
        abstract + 章节名 + 角色，强制中文改写。返回正文（不含 ## 标题）。
        """
        pro = self._get_pro_llm()
        if pro is None:
            return ""
        # 拼接输入：abstract + paper.analysis 关键字段
        abstract = (
            (analysis.research_question or "").strip()
            + "\n\n背景：" + " / ".join((analysis.background or [])[:3])
            + "\n\n方法：" + " / ".join((analysis.methods or [])[:5])
            + "\n\n发现：" + " / ".join(
                (f.finding or "") for f in (analysis.main_findings or [])[:5]
            )
            + "\n\n意义：" + " / ".join((analysis.innovation or [])[:3])
            + "\n\n局限：" + " / ".join((analysis.limitations or [])[:3])
        ).strip()
        if not abstract or len(abstract) < 50:
            return ""
        # 取 evidence 第 1 条作为锚点
        ev = ""
        for item in (evidence.evidence or [])[:3]:
            if (item.claim or "").strip():
                ev = (item.claim or "").strip()[:200]
                break
        sys_prompt = (
            "你是公众号科普写手。把给定的英文 abstract + paper 关键字段改写成**纯中文**段落。"
            "要求：\n"
            "1. 不要把英文原文整段照抄；按中文学术科普风格重述\n"
            "2. 段落控制在 3-5 个，400-600 字\n"
            "3. 不要使用 markdown 标题，只输出段落正文\n"
            "4. 如果 abstract 不完整，承认信息有限但仍要写出有信息量的段落\n"
            "5. 结尾必须是中文句号 / 问号 / 感叹号"
        )
        user = (
            f"## 章节名\n{section_name}\n\n"
            f"## 章节功能\n{section_role}\n\n"
            f"## 论文标题\n{analysis.title or ''}\n\n"
            f"## 论文关键信息\n{abstract[:1500]}\n\n"
            + (f"## 关键证据\n{ev}\n\n" if ev else "")
            + "## 任务\n用中文改写本节内容，3-5 段，400-600 字。"
        )
        try:
            text = pro.generate(
                system=sys_prompt,
                user=user,
                temperature=0.3,
                max_tokens=1500,
            )
            return (text or "").strip()
        except Exception:
            return ""

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
        paper_abstract: str = "",
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
                # 真没救 → 用 abstract 拼一段降级内容，让文章仍有真实信息而不是空架子
                body = self._fallback_section_body(
                    name, role, analysis, evidence, options, paper_abstract=paper_abstract
                )
                if not body:
                    body = f"（{role} — 未能生成）"
            section_md = f"## {name}\n\n{body.rstrip()}\n"
            written.append(section_md)

        # 2026-09-03 评审 2 P0-4：13 篇里 9 篇 final_article.md 中 `![]()` / "图 X" 引用为 0。
        # 即使 prompt 强约束"必须引用 1 张图"，LLM 经常忽略。兜底：写完后扫一遍，如果
        # 全篇 0 个 figure 引用且 figures 列表非空，把可用图的视觉描述以"如图 X 所示：..."
        # 注入到 §01/§03/§04/§05 等显眼位置。
        article_so_far = "\n".join(written)
        article_so_far = self._inject_figure_refs(article_so_far, figures)

        # 论文信息 footer
        journal = analysis.journal or ""
        year = analysis.year or ""
        doi = "—"
        article_so_far += "\n---\n\n"
        article_so_far += "### 论文信息\n"
        article_so_far += f"**Title:** {analysis.title}  \n"
        article_so_far += f"**Journal:** {journal}  \n"
        article_so_far += f"**Year:** {year}  \n"
        article_so_far += f"**DOI:** {doi}\n\n"

        return article_so_far

    @staticmethod
    def _has_figure_ref(text: str) -> bool:
        """检测正文是否已含 figure 引用（`![]()` / `Figure X` / `图 X`）。"""
        if not text:
            return False
        if "![figure]" in text or "![fig" in text:
            return True
        import re as _re
        if _re.search(r"!\[[^\]]*\]\([^)]*figure", text, _re.I):
            return True
        if _re.search(r"\b[Ff]igure\s*\d+\b", text):
            return True
        if _re.search(r"图\s*\d+\b|图\s*[一二三四五六七八九十]+", text):
            return True
        return False

    def _inject_figure_refs(self, article_md: str, figures: List[FigureAnalysis]) -> str:
        """若全篇无 figure 引用，挑选 2-3 张可用图，注入到 §01/§03/§04。

        评审 2（2026-09-03 13 篇）：9 篇 final_article 引用 0 张图。figure_analysis.json
        实际上有可用图（article_usage=true 或有视觉描述的），但 writer 不会自动嵌入。

        2026-09-03 Fix-2：放宽选取条件 — 任何有非空 summary（包括纯 caption）的图都算可用，
        不强求 article_usage=true。DiffDock/EMSFold/Mol-GDL/TargetDiff/AlphaGenome 这 5 篇
        之前 0 张图就是因为 article_usage 全是 false。优先级：
          1. 视觉描述 + importance=high/core
          2. 视觉描述
          3. summary 非空（≥20 字符，包括 caption）
          4. 前 N 张
        """
        if not figures:
            return article_md
        if self._has_figure_ref(article_md):
            return article_md  # 已有引用，不强加

        # 选可用图：放宽到任意有非空 summary 的图
        usable = []
        for f in figures:
            summary = (f.summary or "").strip()
            has_visual = "【视觉描述】" in summary and "[无视觉描述" not in summary
            importance = (f.importance or "").lower()
            if has_visual and importance in ("high", "core"):
                usable.append(f)  # 优先级 1
            if len(usable) >= 3:
                break
        if len(usable) < 3:
            for f in figures:
                if f in usable:
                    continue
                summary = (f.summary or "").strip()
                has_visual = "【视觉描述】" in summary and "[无视觉描述" not in summary
                if has_visual:
                    usable.append(f)  # 优先级 2
                if len(usable) >= 3:
                    break
        if len(usable) < 3:
            for f in figures:
                if f in usable:
                    continue
                summary = (f.summary or "").strip()
                if len(summary) >= 20:
                    usable.append(f)  # 优先级 3：纯 caption 也算
                if len(usable) >= 3:
                    break
        if not usable:
            usable = [f for f in figures[:2] if (f.figure or "").strip()]
        if not usable:
            return article_md

        # 注入位置：§03 (作者是怎么研究的) / §04 (最重要的发现) / §01 (为什么值得关注)
        inject_targets = ("03 作者是怎么研究的", "04 最重要的发现是什么", "01 为什么值得关注")
        injects_added = 0
        for sec_name in inject_targets:
            if injects_added >= len(usable):
                break
            # 在 "## <sec_name>" 之后插入一段 figure 描述
            marker = f"## {sec_name}\n"
            idx = article_md.find(marker)
            if idx < 0:
                continue
            # 找正文末尾（本节最后一行的下一个 ## 之前）
            next_section = article_md.find("\n## ", idx + len(marker))
            if next_section < 0:
                next_section = len(article_md)
            fig = usable[injects_added]
            fig_label = (fig.figure or "Figure").strip()
            fig_summary = (fig.summary or "").strip()
            # summary 可能含"【视觉描述】xxxxx"或纯 caption；截前 220 字符
            if len(fig_summary) > 220:
                fig_summary = fig_summary[:220] + "…"
            img_path = _resolve_figure_path(fig_label, self._figures_dir)
            insert = (
                f"\n\n（图示：{fig_label}）\n\n"
                f"![{fig_label}]({img_path})\n\n"
                f"*{fig_label}：{fig_summary}*\n"
            )
            article_md = article_md[:next_section] + insert + article_md[next_section:]
            injects_added += 1

        return article_md

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

        评审 3 P1-1（2026-09-03）：flash 模型大量空响应/中英混杂，每次最多 2 次尝试（1 正常 + 1 重试）。
        仍失败时改用 pro 模型 + 改写 prompt 再试 1 次；若仍失败，调用 _fallback_section_body 兜底。
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
                # 2 次 flash 都失败 → 升级到 pro 模型 + 改写 prompt
                rewrite_body = self._try_pro_rewrite(
                    section_name=section_name, section_role=section_role,
                    analysis=analysis, evidence=evidence, options=options,
                )
                if rewrite_body and not self._is_bad_body(rewrite_body, section_role):
                    return rewrite_body
                return ""
            return body
        return ""

    @staticmethod
    def _is_bad_body(body: str, role: str) -> bool:
        """True 表示 body 是"不可用"。

        2026-09-03 P1-2 增强（评审 3 暴露的新失败模式）：
        1. **中英混杂 / 纯英文 dump**：abstract 整段照搬时中文比例 < 30%
        2. **PDF 元数据泄漏**：含 arXiv 戳、Vol.、©、doi:、邮箱等
        3. **作者列表硬贴**：≥3 个 "Lastname, X." 模式连续出现
        4. **URL 片段**：含 github.com / arxiv.org / http://
        5. **mid-word 截断**：末尾是常见英文半截词 (an / ac / to / the / of / at) 而非标点
        6. **PDF 双换行残留**：含 ≥2 个连续 "\n\n" 制造碎段
        7. **截断**：末尾不是句末标点
        8. **占位符模板**：prompt role 描述被原样返回
        9. **meta-disclaimer**："I cannot" / "I'm sorry"
        """
        if not body:
            return True
        body_stripped = body.strip()
        if len(body_stripped) < 60:
            return True
        # meta-disclaimer
        low = body_stripped.lower()
        if any(s in low for s in ("i cannot", "i don't have", "i am unable", "i'm sorry", "i can\u2019t", "as an ai")):
            return True
        # 占位符模板（prompt role 描述被原样返回）
        for hint in _PLACEHOLDER_ROLE_HINTS:
            if hint in body_stripped and len(body_stripped) < 200:
                return True
        # 1. 中文比例 < 30%（基本是英文 dump）
        cn_chars = sum(1 for c in body_stripped if '\u4e00' <= c <= '\u9fff')
        total = len(body_stripped)
        if total > 100 and cn_chars / total < 0.30:
            return True
        # 2. PDF 元数据泄漏
        import re as _re
        pdf_markers = (
            "arxiv:", "doi:", "received ", "accepted ", "© 20",
            "published online", "manuscript", "open access",
            "copyright", "all rights reserved", "no reuse",
        )
        for m in pdf_markers:
            if m in low:
                return True
        if _re.search(r"vol\.\s*\d+", low) or _re.search(r"vol\s*\d+\s*,", low):
            return True
        # 3. 作者列表硬贴（≥3 个 "Lastname, X." 模式）
        import re as _re
        author_dumps = _re.findall(r"[A-Z][a-zA-Z\u00C0-\u017F\-']+,\s*[A-Z]\.?", body_stripped)
        if len(author_dumps) >= 3:
            return True
        # 4. URL 片段
        if _re.search(r"(github\.com|arxiv\.org|http://|https://|www\.)", body_stripped):
            return True
        # 5. mid-word 截断：末尾是常见英文半截词
        last_word = body_stripped.split()[-1] if body_stripped.split() else ""
        last_word_low = last_word.lower().rstrip(".,;:!?")
        if last_word_low in ("an", "ac", "to", "the", "of", "at", "in", "is", "by", "as", "or", "on", "we", "or"):
            return True
        # 6. PDF 双换行残留：≥2 个 "\n\n" 制造碎段
        if body_stripped.count("\n\n") >= 2:
            # 只在长度短的时候拦（否则长文本来就有空行）
            if len(body_stripped) < 800:
                return True
        # 7. 截断：末尾不是句末标点
        if body_stripped[-1] not in _END_PUNCT:
            return True
        return False

    def _fallback_section_body(
        self,
        section_name: str,
        section_role: str,
        analysis: PaperAnalysis,
        evidence: EvidenceMap,
        options: dict,
        paper_abstract: str = "",
    ) -> str:
        """LLM 失败时用 abstract + paper_analysis 拼一段降级正文，至少不是空架子。

        评审 1 + 2026-09-03 probe 验证：reader 经常 LLM empty response，paper_analysis
        全空 → fallback 也没东西。修复：优先用 paper.abstract（parser 永远有 1500 字符），
        然后才退到 analysis 字段。

        评审 2 P0-3：每个 section 强制返回 ≥80 字符的实质内容；5/7/8 即使没 analysis
        也要从 abstract 拆句凑，绝不返回 "" 让外层写出"（…— 未能生成）"占位符。
        """
        abstract = (paper_abstract or analysis.research_question or "").strip()
        if not abstract:
            # 实在连 abstract 都没有：每个 section 给一个固定的、最小化的真实内容块
            return (
                f"本节（{section_name.replace(section_name[:3], '').strip() or section_name}）"
                f"需要依据论文原文相关章节展开。本报告基于论文 {analysis.title or '该论文'} "
                f"自动生成，由于源材料不完整，此节仅作框架性提示，详细内容请参考原文。"
            )
        # 8 个 section 中，01/02/05/06/08 这 5 个最能从 abstract 凑出内容
        sname = section_name or ""
        s = sname.lower()
        if "01" in sname or "why" in s or "关注" in sname:
            return (
                f"本论文以「{analysis.title or '该论文'}」为研究对象。"
                f"其核心问题是：{abstract[:300]}"
                f"该方向对相关领域有重要参考价值。"
            )
        if "02" in sname or "问题" in sname or "problem" in s:
            return (
                f"研究问题：{abstract[:400]}"
            )
        if "03" in sname or "方法" in sname or "method" in s:
            methods = analysis.methods or []
            if methods and any(m.strip() for m in methods):
                return "研究方法：\n" + "\n".join(f"- {str(m)[:200]}" for m in methods[:5] if str(m).strip())
            # methods 空时，启发式从 abstract 抽含技术动词的句子
            tech_lines = [line for line in abstract.replace("。", "。\n").split("\n")
                          if line.strip() and any(kw in line.lower() for kw in
                          ("we ", "use ", "train ", "design ", "build ", "propose",
                           "introduce", "develop", "apply", "adopt", "combine",
                           "本文", "采用", "构建", "训练", "使用"))]
            if tech_lines:
                return "研究方法概述：\n" + "\n".join(f"- {l.strip()[:300]}" for l in tech_lines[:3])
            return f"本文采用的研究路径概述：{abstract[:400]}"
        if "04" in sname or "发现" in sname or "finding" in s or "result" in s:
            findings = analysis.main_findings or []
            if findings and any((f.finding or "").strip() for f in findings):
                return "主要发现：\n" + "\n".join(
                    f"- {(f.finding or '').strip()[:200]}" for f in findings[:5]
                    if (f.finding or "").strip()
                )
            # 兜底：用 abstract 末 2 句作为结果
            sents = [s.strip() for s in abstract.replace("。", "。\n").replace(".", ".\n").split("\n") if s.strip()]
            tail = " ".join(sents[-2:]) if len(sents) >= 2 else abstract
            return f"实验核心发现：{tail[:400]}"
        if "05" in sname or "机制" in sname or "mechanism" in s:
            conclusions = (analysis.authors_conclusion or "").strip()
            if conclusions:
                return f"作者给出的机制解释：{conclusions[:400]}"
            # 兜底：abstract 中部 1 句
            sents = [s.strip() for s in abstract.replace("。", "。\n").replace(".", ".\n").split("\n") if s.strip()]
            mid = sents[len(sents) // 2] if sents else abstract
            return f"机制层面，作者提出：{mid[:400]}"
        if "06" in sname or "验证" in sname or "validation" in s:
            methods = analysis.methods or []
            base = "进一步验证手段：\n" + "\n".join(
                f"- {str(m)[:200]}" for m in methods[:5] if str(m).strip()
            ) if methods and any(str(m).strip() for m in methods) else ""
            if base:
                return base + "\n\n（实验/基准验证的具体设置请参考原文）"
            return f"作者通过实验和基准测试进一步验证：{abstract[:300]}"
        if "07" in sname or "意义" in sname or "significance" in s:
            innovations = analysis.innovation or []
            if innovations and any(x.strip() for x in innovations):
                return "研究意义：\n" + "\n".join(
                    f"- {str(x)[:200]}" for x in innovations[:3] if str(x).strip()
                )
            # 兜底：abstract 前 1 句
            sents = [s.strip() for s in abstract.replace("。", "。\n").replace(".", ".\n").split("\n") if s.strip()]
            head = sents[0] if sents else abstract
            return f"研究意义：{head[:300]}"
        if "08" in sname or "局限" in sname or "limitation" in s:
            limitations = analysis.limitations or []
            if limitations and any(str(x).strip() for x in limitations):
                return "作者承认的局限：\n" + "\n".join(
                    f"- {str(x)[:200]}" for x in limitations[:3] if str(x).strip()
                )
            # 兜底：abstract 末 1 句（论文 abstract 末尾常含 limitation 暗示）
            sents = [s.strip() for s in abstract.replace("。", "。\n").replace(".", ".\n").split("\n") if s.strip()]
            tail = sents[-1] if sents else abstract
            return f"局限性方面：{tail[:300]}"
        # 兜底兜底：返回 abstract 前 300 字符 + role 描述
        return f"{section_role}：{abstract[:300]}"
