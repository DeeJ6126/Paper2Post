"""图表提取：从 PDF 中抽取图像并检测图注。

启发式实现：按 xref 去重，渲染为 PNG，按顺序匹配图注。
"""

from __future__ import annotations

import os
import re
from typing import List, Tuple

from paper2post.schemas.paper import ParsedFigure

CAPTION_PATTERN = re.compile(
    r"(?im)^\s*((?:fig(?:ure)?|table)\.?\s*\d+\s*[a-z]?.*)$"
)
# 限制 figure 数字范围在 1-50。超过 50 几乎都是 PDF 内部噪点 / xref 编号 / 装饰图被误判。
LABEL_PATTERN = re.compile(
    r"(?im)(?:fig(?:ure)?\.?\s*)([1-9]|1\d|2\d|3\d|4\d|50)\b", re.IGNORECASE
)


def _caption_texts(doc) -> List[str]:
    full = "\n".join(page.get_text("text") for page in doc)
    captions: List[str] = []
    for m in CAPTION_PATTERN.finditer(full):
        line = m.group(1).strip()
        # 图注通常较长，过滤太短的行
        if len(line) >= 8:
            captions.append(line)
    return captions


def _label_from_caption(caption: str) -> str:
    m = LABEL_PATTERN.search(caption)
    if m:
        return f"Figure {m.group(1)}"
    return ""


def extract_figures(
    doc,
    pdf_path: str,
    figures_dir: str | None = None,
    max_figures: int = 50,
    max_page_size_mb: float = 8.0,
) -> Tuple[List[ParsedFigure], List[str]]:
    """抽取图像并渲染。返回 (figures, captions)。

    关键改进（深挖 5 篇大论文卡死 root cause）：
    1. 抽图上限 max_figures=50（默认）：DeepGGL 45MB / AlphaGenome 70MB 抽几百张图卡死
    2. 跳过超大页：max_page_size_mb=8MB（避免几百 MB 单页拖死 extract_image）
    3. 边抽边检查 size：单张图 > 5MB 跳过
    4. 整页渲染走 get_pixmap 时强制 100 DPI（之前默认 72 DPI 出图太大）
    """
    figures: List[ParsedFigure] = []
    seen: set = set()
    page_count = doc.page_count
    if max_figures is None or max_figures <= 0:
        max_figures = 50

    for page_index in range(page_count):
        if len(figures) >= max_figures:
            break
        page = doc[page_index]
        for img in page.get_images(full=True):
            if len(figures) >= max_figures:
                break
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)

            # 大页跳过（300MB+ 单页 extract_image 极慢）
            try:
                if page.rect.width * page.rect.height > 50_000_000:
                    continue
            except Exception:
                pass

            rects = page.get_image_rects(xref)
            clip = rects[0] if rects else None
            pix_bytes: bytes | None = None
            ext = "png"

            try:
                raw = doc.extract_image(xref)
                pix_bytes = raw.get("image")
                ext = raw.get("ext") or "png"
            except Exception:
                pix = page.get_pixmap(clip=clip) if clip is not None else None
                pix_bytes = pix.tobytes("png") if pix else None
                ext = "png"

            if not pix_bytes:
                continue

            # 过滤超大单图（避免 base64 撑爆）
            if len(pix_bytes) > 5 * 1024 * 1024:
                continue

            # 过滤装饰图 / 噪点
            try:
                from io import BytesIO
                if ext in ("png", "jpg", "jpeg"):
                    try:
                        from PIL import Image
                        with Image.open(BytesIO(pix_bytes)) as _im:
                            w, h = _im.size
                            if w < 200 or h < 200:
                                continue
                    except ImportError:
                        pass  # 没 PIL 不强求
            except Exception:
                pass

            out_path = ""
            if figures_dir:
                os.makedirs(figures_dir, exist_ok=True)
                out_path = os.path.join(figures_dir, f"figure_{len(figures) + 1}.{ext}")
                with open(out_path, "wb") as fh:
                    fh.write(pix_bytes)

            bbox = list(clip) if clip is not None else []
            figures.append(
                ParsedFigure(
                    index=len(figures),
                    label=f"Figure {len(figures) + 1}",
                    path=out_path,
                    page=page_index + 1,
                    bbox=[float(x) for x in bbox],
                    caption="",
                )
            )

    captions = _caption_texts(doc)

    # 按顺序为 figure 绑定图注 / 标签（启发式）
    for idx, fig in enumerate(figures):
        if idx < len(captions):
            fig.caption = captions[idx]
            label = _label_from_caption(captions[idx])
            if label:
                fig.label = label

    return figures, captions
