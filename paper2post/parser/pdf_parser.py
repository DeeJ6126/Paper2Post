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
    return "\n".join(page.get_text("text") for page in doc)


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


def _extract_title(doc, metadata: dict, full_text: str) -> str:
    title = (metadata.get("title") or "").strip()
    if title and not _is_template_title(title):
        return title
    # 回退 1：metadata.title 是 ICLR/NeurIPS 等页眉 → 从正文首部抓真正的标题
    for line in full_text.splitlines():
        s = line.strip()
        if 15 < len(s) < 250 and not _is_template_title(s) and not re.search(
            r"(abstract|doi|received|accepted|keywords|copyright|^\d+\s*$)", s, re.I
        ):
            return s
    return title  # 实在找不到就保留原 metadata.title（含错也比空好）


def _extract_abstract(full_text: str) -> str:
    """智能 abstract 提取。

    关键改进（大论文 root cause）：
    1. AlphaGenome PDF 解析没匹配到 "Abstract" heading → fallback 取全文前 1500 字符
    2. DeepGGL 把正文当 abstract（93K）→ 限制 3000 字符 + 用 abstract heading 精确切分
    3. abstract 缺失/异常时 → 启发式取全文首段（"## Abstract"之前的前 N 字符）
    """
    # 优先按 abstract heading 切分
    m = re.search(r"(?im)^\s*(?:abstract|summary)\b[.:\s]*", full_text)
    if m:
        start = m.end()
        tail = full_text[start:]
        nxt = re.search(
            r"(?im)^\s*(?:introduction|keywords|1\.\s|background)\b", tail
        )
        end = nxt.start() if nxt else 2000
        return tail[:end].strip()[:3000]
    # Fallback 1：取前 1500 字符作为 abstract（AlphaGenome 这种 heading 缺失的）
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
    abstract = _extract_abstract(full_text)
    sections = split_sections(full_text)

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
            "year": _clean_year(metadata.get("creationDate", "") or metadata.get("modDate", "")),
            "publisher": (metadata.get("publisher") or "").strip() or None,
        },
    )
