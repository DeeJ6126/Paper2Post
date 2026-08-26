"""端到端冒烟测试：在 mock 模式下跑通 PDF -> 推文，校验产物齐全。

用法:
    python tests/smoke_test.py
"""

import os
import sys
import shutil
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
_VENDOR = os.path.join(_ROOT, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.insert(0, _VENDOR)

from paper2post.llm import MockProvider
from paper2post.pipeline import Pipeline
from paper2post.parser import parse_pdf


REQUIRED = [
    "metadata.json",
    "paper_analysis.json",
    "evidence.json",
    "storyline.json",
    "figure_analysis.json",
    "fact_check.json",
    "draft_article.md",
    "final_article.md",
    "final_article.html",
    "generation_report.md",
]


def ensure_sample_pdf() -> str:
    pdf = str(Path(_ROOT) / "examples" / "sample_paper.pdf")
    if not os.path.exists(pdf):
        from scripts.make_sample_pdf import build_sample_pdf

        build_sample_pdf(pdf)
    return pdf


def main() -> int:
    pdf = ensure_sample_pdf()

    paper = parse_pdf(pdf)
    assert paper.title, "解析标题失败"
    assert paper.full_text, "解析全文失败"
    print(f"[ok] parse_pdf: title={paper.title!r}, sections={len(paper.sections)}, figures={len(paper.figures)}")

    out = str(Path(_ROOT) / "outputs_test")
    if os.path.isdir(out):
        shutil.rmtree(out, ignore_errors=True)
    try:
        pipeline = Pipeline(settings={}, llm=MockProvider())
        options = {"article_type": "deep_review", "language": "zh-CN"}
        paths = pipeline.run(pdf, output_dir=out, options=options)

        for rel in REQUIRED:
            fp = os.path.join(out, rel)
            assert os.path.exists(fp), f"缺少产物: {rel}"
            assert os.path.getsize(fp) > 0, f"产物为空: {rel}"
            print(f"[ok] generated: {rel}")

        fig_dir = os.path.join(out, "figures")
        figs = [f for f in os.listdir(fig_dir) if f.endswith((".png", ".jpg"))] if os.path.isdir(fig_dir) else []
        assert figs, "未能提取任何 figure"
        print(f"[ok] figures extracted: {figs}")

        fa_text = Path(os.path.join(out, "final_article.md")).read_text(encoding="utf-8")
        assert paper.title in fa_text, "final_article.md 缺少论文标题"
        print("[ok] final_article.md 包含论文标题")

        print()
        print("SMOKE TEST PASSED")
        return 0
    finally:
        if os.path.isdir(out):
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
