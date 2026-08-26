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
    r"supplementary|references|acknowledg(e)?ments)\b",
    re.IGNORECASE,
)


def _read_text(doc) -> str:
    return "\n".join(page.get_text("text") for page in doc)


def _extract_title(doc, metadata: dict, full_text: str) -> str:
    title = (metadata.get("title") or "").strip()
    if title:
        return title
    # 回退：取正文中首个非空且较长的行
    for line in full_text.splitlines():
        s = line.strip()
        if len(s) > 30 and not re.search(r"(abstract|doi|received|accepted)", s, re.I):
            return s
    return ""


def _extract_abstract(full_text: str) -> str:
    """按 Abstract 标题切分，提取摘要正文。"""
    m = re.search(r"(?im)^\s*(abstract|summary)\b[.:\s]*", full_text)
    if not m:
        return ""
    start = m.end()
    # 找到下一个疑似小节/段落边界
    tail = full_text[start:]
    nxt = re.search(
        r"(?im)^\s*(introduction|keywords|1\.\s|introduction\b)", tail
    )
    end = nxt.start() if nxt else 2000
    return tail[:end].strip()


def split_sections(full_text: str) -> List[PaperSection]:
    """借助小节标题关键字切分正文。"""
    matches = list(SECTION_PATTERN.finditer(full_text))
    if not matches:
        return [PaperSection(heading="full_text", text=full_text.strip())]
    sections: List[PaperSection] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        heading = m.group(1).title()
        body = full_text[start:end].strip()
        sections.append(PaperSection(heading=heading, text=body))
    return sections


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
        },
    )
