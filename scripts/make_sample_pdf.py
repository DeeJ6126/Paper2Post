"""生成一个示例论文 PDF，用于跑通 Paper2Post 链路。

用法:
    python scripts/make_sample_pdf.py
输出:
    examples/sample_paper.pdf
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
_VENDOR = os.path.join(_ROOT, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.insert(0, _VENDOR)

from pathlib import Path

import pymupdf


def _make_figure_pixmap(width: int, height: int, color: int):
    irect = pymupdf.IRect(0, 0, width, height)
    pix = pymupdf.Pixmap(pymupdf.csRGB, irect, False)
    pix.clear_with(color)
    return pix


def _heading(page, y, text, size=14):
    page.insert_text((72, y), text, fontsize=size, fontname="hebo")


def _paragraph(page, y, text, width=460, fontsize=9):
    rect = pymupdf.Rect(72, y, 72 + width, y + 80)
    page.insert_textbox(rect, text, fontsize=fontsize, fontname="helv")


def build_sample_pdf(path: str = "examples/sample_paper.pdf") -> str:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    doc.set_metadata(
        {
            "title": "Aging impairs APLNR signaling and endothelial function",
            "author": "Jane Doe, John Smith",
            "subject": "Aging and endothelial APLNR signaling",
        }
    )

    # ---- Page 1 ----
    page = doc.new_page()
    _heading(page, 72, "Aging impairs APLNR signaling and endothelial function", size=15)
    _paragraph(
        page,
        110,
        "Jane Doe 1, John Smith 2, Alex Lee 1  |  1 Department of Cardiology. 2 Aging Institute.",
        fontsize=9,
    )
    _heading(page, 170, "Abstract", size=12)
    _paragraph(
        page,
        190,
        "Aging is a major risk factor for cardiovascular disease. We report that the "
        "expression of APLNR (apelin receptor) declines with age in human and mouse "
        "endothelial cells. Loss of APLNR impairs nitric oxide production and leads to "
        "endothelial dysfunction. Overexpression of APLNR in aged mice restores "
        "endothelial function, suggesting a therapeutic avenue.",
    )
    _heading(page, 330, "Introduction", size=12)
    _paragraph(
        page,
        350,
        "The endothelium plays a central role in vascular homeostasis. Apelin and its "
        "receptor APLNR are known regulators of vascular tone; however, how aging "
        "modulates this axis remains incompletely understood.",
    )

    # ---- Page 2 (Results with two figures) ----
    page2 = doc.new_page()
    _heading(page2, 72, "Results", size=12)
    _paragraph(
        page2,
        92,
        "We observed a progressive decline of APLNR expression with age in cultured "
        "endothelial cells (Figure 1A). RNA-seq revealed a 2.3-fold reduction in aged "
        "cells (Figure 1B).",
    )
    # Figure 1 image
    fig1 = _make_figure_pixmap(260, 130, 200)
    page2.insert_image(pymupdf.Rect(72, 180, 72 + 260, 180 + 130), pixmap=fig1)
    _paragraph(page2, 320, "Figure 1. APLNR expression declines with age.", width=460, fontsize=9)

    _paragraph(
        page2,
        360,
        "Overexpression of APLNR increased endothelial nitric oxide production and "
        "improved vasodilation in aged mice (Figure 2).",
    )
    # Figure 2 image
    fig2 = _make_figure_pixmap(260, 130, 80)
    page2.insert_image(pymupdf.Rect(72, 410, 72 + 260, 410 + 130), pixmap=fig2)
    _paragraph(page2, 550, "Figure 2. APLNR restoration improves endothelial function.", width=460, fontsize=9)

    # ---- Page 3 (Methods, Discussion, References) ----
    page3 = doc.new_page()
    _heading(page3, 72, "Methods", size=12)
    _paragraph(
        page3,
        92,
        "Primary endothelial cells were cultured from young (3 months) and aged "
        "(24 months) mice. Gene expression was measured by qPCR and RNA-seq. "
        "Vasodilation was assessed by wire myography.",
    )
    _heading(page3, 210, "Discussion", size=12)
    _paragraph(
        page3,
        230,
        "Our data suggest that the APLNR-apelin axis is a key mediator of age-related "
        "endothelial dysfunction and may represent a target for therapeutic intervention.",
    )
    _heading(page3, 330, "References", size=12)
    _paragraph(page3, 350, "1. Doe J et al. Nature 2023.\\n2. Smith J et al. Cell 2022.", width=460, fontsize=9)

    doc.set_toc(
        [
            [1, "Abstract", 1],
            [1, "Introduction", 1],
            [1, "Results", 2],
            [1, "Methods", 3],
            [1, "Discussion", 3],
            [1, "References", 3],
        ]
    )
    doc.save(str(out), garbage=4, deflate=True)
    doc.close()
    return str(out)


if __name__ == "__main__":
    p = build_sample_pdf()
    print("Generated:", p)
