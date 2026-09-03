"""PDF 解析器：提取文本、章节、图与图注。

V1 采用启发式规则（适配单栏为主）。多栏、参考文献剥离等可在后续里程碑增强。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from paper2post.schemas.paper import ParsedPaper, PaperSection, ParsedFigure

from .figure_parser import extract_figures


SECTION_PATTERN = re.compile(
    r"(?m)^\s*(abstract|introduction|results|methods|"
    r"material(s)? and methods|discussion|conclusion(s)?|"
    r"supplementary|references|acknowledg(e)?ments|appendix)\b",
    re.IGNORECASE,
)

# 主体内容 sections（核心叙事）
CORE_SECTIONS = ("abstract", "introduction", "methods", "results", "discussion", "conclusion")
# 辅助 sections（不是核心叙事，跳过）
SKIP_SECTIONS = ("supplementary", "references", "acknowledgements", "appendix")

# 单 section 文本上限。大论文 24KB 平衡：context window（vision / flash 仍 6KB+ 安全）
# + 12KB 不够（DeepGGL Methods 137K 截后看不到核心）→ 24KB。
MAX_SECTION_TEXT = 24_000


def _read_text(doc) -> str:
    raw = "\n".join(page.get_text("text") for page in doc)
    return _clean_text(raw)


# 2026-09-03 评审 2：13/13 篇正文都泄漏 PDF 残留（Nature 页眉 "Nature | Vol 630 | 13 June 2024"、
# 行号 "15 \nWe present…"、作者名单硬贴 "David Baker, ... 1206 | Nature | Vol 649 | 29 January 2026"、
# "Article\n" 标记）。这些直接被 reader/writer 抄进 final_article。
# 在解析端一次性剥掉，下游不用再各自处理。
_NATURE_HEADER_RE = re.compile(
    r"^\s*\d{1,5}\s*\|\s*Nature\s*\|\s*Vol\s+\d+\s*\|\s*\d{1,2}\s+\w+\s+\d{4}\s*$",
    re.MULTILINE,
)
_NATURE_HEADER_RE2 = re.compile(
    r"^\s*Nature\s*\|\s*Vol\s+\d+\s*\|\s*\d{1,2}\s+\w+\s+\d{4}\s*\|\s*\d+\s*$",
    re.MULTILINE,
)
_ARTICLE_MARKER_RE = re.compile(r"^\s*Article\s*$", re.MULTILINE | re.IGNORECASE)
_PURE_NUMBER_LINE_RE = re.compile(r"^\s*\d{1,4}\s*$", re.MULTILINE)
# 长作者列表（≥3 个名字，逗号分隔，全大写或首字母大写，行内无句末标点）
_AUTHOR_DUMP_RE = re.compile(
    r"^[ \t]*(?:[A-Z][a-zA-Z\u00C0-\u017F\-']+(?:\s+[A-Z][a-zA-Z\u00C0-\u017F\-']+){0,2}"
    r"(?:,\s*[A-Z][a-zA-Z\u00C0-\u017F\-']+(?:\s+[A-Z][a-zA-Z\u00C0-\u017F\-']+){0,2}){2,})\s*$",
    re.MULTILINE,
)
# 其他常见期刊残留（"bioRxiv preprint" / "Under review" / "Page X of Y"）
_PRECPRINT_NOISE_RE = re.compile(
    r"^\s*(?:bioRxiv preprint|medRxiv preprint|Under review|Page \d+ of \d+|"
    r"\d+ of \d+|Received\s+\d{1,2}\s+\w+\s+\d{4}|Accepted\s+\d{1,2}\s+\w+\s+\d{4})\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def _clean_text(raw: str) -> str:
    """剥 PDF 解析残留：期刊页眉 / Article 标记 / 纯行号 / 长作者列表 / 预印本标记。

    评审 2（2026-09-03 13 篇）暴露这些噪声会被 LLM 抄进 final_article。
    """
    if not raw:
        return raw
    s = raw
    s = _NATURE_HEADER_RE.sub("", s)
    s = _NATURE_HEADER_RE2.sub("", s)
    s = _ARTICLE_MARKER_RE.sub("", s)
    s = _PURE_NUMBER_LINE_RE.sub("", s)
    s = _PRECPRINT_NOISE_RE.sub("", s)
    # 作者列表剥：只在行较长（≥ 80 字符）时才剥，避免误伤正文里"Tom, John, and Jack"这种短引用
    cleaned_lines = []
    for line in s.splitlines():
        if len(line) >= 80 and _AUTHOR_DUMP_RE.match(line):
            continue
        cleaned_lines.append(line)
    s = "\n".join(cleaned_lines)
    # 多个连续空行压成一个
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s


# 一些会议 LaTeX 模板把页眉当 metadata.title（如 "Published as a conference paper at ICLR 2023"）。
# 评审 1 显示 DiffDock / Uni-Mol / TargetDiff 都中招。集中识别为"假标题"并回退。
_TEMPLATE_TITLE_HINTS = (
    "published as a conference paper",
    "published as a workshop paper",
    "to appear in",
    "to be published in",
    "under review",
    "preprint",
    "submitted to",
    "iclr 20", "neurips 20", "icml 20", "cvpr 20", "eccv 20", "aaai 20", "ijcai 20",
    "acm isbn",
    # 2026-09-03 P1-3：识别"Supplementary Materials for ..."，这是 PDF 首页的元数据栏标签
    "supplementary materials",
)


def _is_template_title(s: str) -> bool:
    s_low = (s or "").lower()
    return any(h in s_low for h in _TEMPLATE_TITLE_HINTS)


def _clean_year(raw: str) -> str:
    """pymupdf 的 creationDate 形如 "D:20241116104703+05'30'" — 旧逻辑 `[:4]` 截成 "D:20"。

    提 4 位年份：剥 "D:" 前缀，匹配 \\d{4}。失败返回 ""。
    """
    if not raw:
        return ""
    s = raw.strip()
    if s.startswith("D:"):
        s = s[2:]
    m = re.match(r"(\d{4})", s)
    return m.group(1) if m else ""


# 2026-09-03 Fix-3：当 PDF metadata 的 creationDate/modDate 都没有时（如 RSA、某些预印本），
# 从正文 / 首页扫描 4 位年份（1990-2030 之间）。优先级：
#   1) 摘要里出现的 "(2024)" / "© 2024" / "Received 2024" 等显式标记
#   2) 首页 / 头部 metadata 里的 "(20YY)" / "Vol YY" 出版日期
#   3) 最后兜底：取全文第一个 20XX
_YEAR_PATTERN = re.compile(r"\b(19[9]\d|20[0-3]\d)\b")


def _extract_year_from_text(full_text: str) -> str:
    """从 PDF 文本里找出版年份（creationDate 缺失时用）。"""
    if not full_text:
        return ""
    head = full_text[:8000]  # 头部 8KB，够覆盖 arXiv 戳、首页 metadata、abstract
    # 优先级 1：明确的版权/出版标记
    m = re.search(
        r"(?:©|copyright|copyright\s+©|\(c\)|published|received|accepted|"
        r"open\s+access|article\s+published|advance\s+access|online\s+first|"
        r"reference\s+citation|bibliographic|arxiv:.*?(?:\d{4}\.\d{4,5}v\d+\s*\[.*?\]\s*\d{1,2}\s+\w+\s+(\d{4}))|"
        r"中国科学院|版权|收稿日期|修回日期|出版日期)\s*[\(\s©]?\s*((?:19[9]\d|20[0-3]\d))",
        head, re.IGNORECASE,
    )
    if m:
        # 兼容 arXiv 戳里的 group 1 在前
        return m.group(1) if not m.group(2) else m.group(2)
    # 优先级 2：期刊头 "Vol X, 20YY" / "Vol X, Month 20YY"
    m = re.search(
        r"vol(?:ume)?\.?\s*\d+\s*,?\s*(?:[A-Z][a-z]+\s*)?\d{0,2}\s*,?\s*((?:19[9]\d|20[0-3]\d))",
        head, re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m = re.search(r"\(\s*((?:19[9]\d|20[0-3]\d))", head)
    if m:
        return m.group(1)
    # 优先级 3：arXiv 戳 "arXiv:NNNN.NNNNN [...] DD Mon YYYY"（不依赖 group 1 在前）
    m = re.search(r"arxiv:[\w\.\-/]+\s*\[.*?\]\s*\d{1,2}\s+\w+\s+(\d{4})", head, re.IGNORECASE)
    if m:
        return m.group(1)
    # 优先级 4：头里第一个 20XX
    m = _YEAR_PATTERN.search(head)
    if m:
        return m.group(1)
    return ""


def _extract_title(doc, metadata: dict, full_text: str) -> str:
    title = (metadata.get("title") or "").strip()
    if title and not _is_template_title(title):
        # 2026-09-03 P1-3：补一个修正常见截断
        title = _fix_truncated_title(title, full_text)
        if title:
            return title
    # 2026-09-03 P1-3：metadata.title 是模板前缀（"Supplementary Materials for" / "preprint" 等），
    # 真正标题在 full_text 前几行 → 抓最长符合条件的那行
    for line in full_text.splitlines():
        s = line.strip()
        if 15 < len(s) < 250 and not _is_template_title(s) and not re.search(
            r"(abstract|doi|received|accepted|keywords|copyright|^\d+\s*$)", s, re.I
        ):
            s = _fix_truncated_title(s, full_text)
            if s:
                return s
    return _fix_truncated_title(title, full_text) or title  # 实在找不到就保留原 metadata.title


# 2026-09-03 P1-3：常见标题截断模式
#   1) "Supplementary Materials for\nEvolutionary-scale prediction..." → 拼回真正标题
#   2) "DIFFDOCK: DIFFUSION STEPS, TWISTS,\nAND TURNS FOR MOLECULAR..." → 拼回逗号后内容
#   3) "From Local to Global: A GraphRAG Approach to\nQuery-Focused Summarization" → 拼回
#   4) "3D EQUIVARIANT DIFFUSION FOR TARGET-AWARE\n..." → 拼回
def _fix_truncated_title(title: str, full_text: str) -> str:
    """识别并补回标题被换行截掉的部分。"""
    if not title or not full_text:
        return title
    # 1. 去掉 "Supplementary Materials for" 前缀（其实是元数据栏的标签）
    sup = re.match(r"^\s*supplementary\s+materials?\s+for\s*$", title, re.I)
    if sup:
        # 标题应在原 metadata.title 的下一行；先回退 1
        return title  # 留给上层 fallback 处理
    # 2. 标题以逗号 / "to" / 介词结尾 → 找下一行合并
    tail_ends = (
        title.rstrip().endswith((",", ":", "—", " -", " –"))
        or title.rstrip().lower().endswith(" to")
        or title.rstrip().lower().endswith(" and")
        or title.rstrip().lower().endswith(" for ")
        or re.search(r"\b(to|and|for|of|or)$", title.rstrip(), re.I) is not None
    )
    if not tail_ends or len(title) > 200:
        return title
    # 找 title 出现在 full_text 里的位置
    idx = full_text.find(title)
    if idx < 0:
        return title
    after = full_text[idx + len(title):idx + len(title) + 200]
    # 取下一段（直到空行 / 句号 / 标题结束的标点）
    nxt = re.split(r"\n\s*\n", after, maxsplit=1)[0].strip()
    if not nxt or len(nxt) > 200:
        return title
    # 只在 nxt 也是标题风格（全大写 / 含冒号 / 以名词结尾）才合并
    if not (nxt[0].isupper() or nxt[0].isdigit() or nxt[0] in "([{"):
        return title
    # 避免合并了"Abstract"等后续节标题
    if nxt.lower() in ("abstract", "introduction", "1.", "1. introduction", "background"):
        return title
    # 如果原 title 以逗号结尾，nxt 前不补空格
    sep = " " if title.rstrip()[-1] not in ",-:—–" else ""
    merged = (title.rstrip() + sep + nxt).strip()
    return merged


def _extract_abstract(full_text: str, sections: Optional[List[PaperSection]] = None) -> str:
    """智能 abstract 提取。

    关键改进（大论文 root cause）：
    1. AlphaGenome PDF 解析没匹配到 "Abstract" heading → fallback 取全文前 1500 字符
    2. DeepGGL 把正文当 abstract（93K）→ 限制 3000 字符 + 用 abstract heading 精确切分
    3. abstract 缺失/异常时 → 启发式取全文首段（"## Abstract"之前的前 N 字符）
    4. 2026-09-03 Fix-1：BMC Biology 这类结构化 abstract（Background/Results/Conclusions），
       "Background" 出现在 abstract 内部，旧的 stop word 列表会误把它当下一段开头 → 截出 0 字符。
       修复：从 sections 里取 heading=="Abstract" 的整段文本作为 abstract。
    """
    # 优先按 abstract heading 切分
    m = re.search(r"(?im)^\s*(?:abstract|summary)\b[.:\s]*", full_text)
    if m:
        start = m.end()
        tail = full_text[start:]
        # 修复 Fix-1：排除 "Background" 这种 abstract 内部子标题（BMC、Nature Reviews 等）。
        # 只用 "introduction / keywords / 1. / methods / results / discussion / conclusion" 作为 stop。
        nxt = re.search(
            r"(?im)^\s*(?:introduction|keywords|methods|materials\s+and\s+methods|"
            r"results\s+(?:and|&)\s+discussion|discussion|conclusions?|references)\b|"
            r"^\s*1\.\s",
            tail,
        )
        end = nxt.start() if nxt else 2000
        extracted = tail[:end].strip()[:3000]
        if extracted:
            return extracted
    # Fallback 1（Fix-1）：从已切好的 sections 里取 heading=="Abstract" 的整段
    if sections:
        for s in sections:
            if (s.heading or "").lower() == "abstract" and (s.text or "").strip():
                # 去 heading 行本身
                body = re.sub(r"(?im)^\s*abstract\b[.:\s]*\n?", "", s.text).strip()
                if body:
                    return body[:3000]
    # Fallback 2：取前 1500 字符作为 abstract（AlphaGenome 这种 heading 缺失的）
    head = full_text.strip()[:1500]
    if head:
        return head
    return ""


def split_sections(full_text: str) -> List[PaperSection]:
    """借助小节标题关键字切分正文。

    关键改进（大论文稳定性）：
    1. 跳过 Supplementary / References / Acknowledgements / Appendix（辅助内容）
    2. 同 heading（不区分大小写）只保留第一个 section
    3. 每个 section text 截到 MAX_SECTION_TEXT (12KB)
    """
    matches = list(SECTION_PATTERN.finditer(full_text))
    if not matches:
        return [PaperSection(heading="full_text", text=full_text.strip()[:MAX_SECTION_TEXT])]
    raw_sections: List[PaperSection] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        heading = m.group(1).title()
        body = full_text[start:end].strip()
        raw_sections.append(PaperSection(heading=heading, text=body))
    # 去重 + 过滤
    seen: set = set()
    out: List[PaperSection] = []
    for s in raw_sections:
        norm = s.heading.lower()
        if norm in SKIP_SECTIONS:
            continue
        if norm in seen:
            continue
        seen.add(norm)
        if len(s.text) > MAX_SECTION_TEXT:
            s.text = s.text[:MAX_SECTION_TEXT]
        out.append(s)
    return out


def parse_pdf(
    pdf_path: str,
    figures_dir: Optional[str] = None,
    extract_figure_images: bool = True,
) -> ParsedPaper:
    """解析 PDF 为中间结构。

    Args:
        pdf_path: 论文 PDF 路径。
        figures_dir: 图像输出目录（为空则不变更提取图像）。
        extract_figure_images: 是否提取图像文件。
    """
    pdf_path = os.path.abspath(pdf_path)
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF 不存在: {pdf_path}")

    import pymupdf  # 延迟导入，避免无依赖时 import 失败

    doc = pymupdf.open(pdf_path)
    metadata = dict(doc.metadata or {})
    full_text = _read_text(doc)
    title = _extract_title(doc, metadata, full_text)
    authors = (metadata.get("author") or "").strip()
    sections = split_sections(full_text)
    # 2026-09-03 Fix-1：先切 sections 再抽 abstract，BMC Biology 之类结构化 abstract 才能正确
    abstract = _extract_abstract(full_text, sections=sections)

    figures: List[ParsedFigure] = []
    captions: List[str] = []
    if extract_figure_images:
        figures, captions = extract_figures(doc, pdf_path, figures_dir)

    page_count = doc.page_count
    doc.close()

    return ParsedPaper(
        pdf_path=pdf_path,
        title=title,
        authors=authors,
        abstract=abstract,
        full_text=full_text,
        sections=sections,
        figures=figures,
        captions=captions,
        metadata={
            "page_count": page_count,
            "pdf_metadata": metadata,
            # 2026-09-03 评审 1：所有 13 篇 year 都 "D:20"（截前 4 字符），
            # 用 _clean_year 剥 "D:" 前缀并匹配 4 位年份；放平级字段方便 writer 读取
            "year": _clean_year(metadata.get("creationDate", "") or metadata.get("modDate", ""))
            or _extract_year_from_text(full_text),  # Fix-3：creationDate 缺失时从文本提
            "publisher": (metadata.get("publisher") or "").strip() or None,
        },
    )
