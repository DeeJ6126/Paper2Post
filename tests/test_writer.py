"""Writer 两段式端到端测试：要求 final_article.md 内容是真实中文（≥2KB）且非占位。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper2post.config import load_settings
from paper2post.parser import parse_pdf
from paper2post.pipeline import make_provider
from paper2post.prompts import Prompts
from paper2post.llm import MockProvider, LLMError
from paper2post.agents import (
    ReaderAgent, StorytellerAgent, FigureAgent, WriterAgent, EditorAgent,
)
from paper2post.utils import build_evidence_map


def find_pdf() -> str:
    candidates = [
        ROOT / "data/test_papers/cellreasoner.pdf",
        ROOT / "examples/sample_paper.pdf",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    raise SystemExit("no PDF found")


def main() -> int:
    settings = load_settings()
    try:
        llm = make_provider(
            settings, provider_name="deepseek",
            model=settings.get("model"), base_url=settings.get("base_url"),
        )
    except LLMError as e:
        print(f"LLMError: {e}; using mock")
        llm = MockProvider()
    print(f"LLM: {type(llm).__name__} name={llm.name} is_mock={llm.is_mock}")

    pdf = find_pdf()
    paper = parse_pdf(pdf, extract_figure_images=False)
    print(f"paper: {paper.title[:60]!r}, sections={len(paper.sections)}, figures={len(paper.figures)}")

    prompts = Prompts.load(ROOT)
    assert prompts.writer_section, "writer_section prompt not loaded"

    # 跑 reader
    reader = ReaderAgent(llm, prompts, settings)
    analysis = reader.run(paper)
    print(f"  analysis: findings={len(analysis.main_findings)} methods={len(analysis.methods)}")

    evidence = build_evidence_map(analysis)
    print(f"  evidence: {len(evidence.evidence)} items")

    # 跑 storyteller + figure
    storyline = StorytellerAgent(llm, prompts, settings).run(analysis, evidence, "deep_review")
    fig_analysis = FigureAgent(llm, prompts, settings).run(paper, storyline, "deep_review")
    print(f"  storyline: {len(storyline.sections)} sections")
    print(f"  figures: {len(fig_analysis)} items")

    # 跑 writer
    options = {"article_type": "deep_review", "language": "zh-CN", "target_audience": "biology_graduate"}
    writer = WriterAgent(llm, prompts, settings)
    article = writer.run(analysis, evidence, storyline, fig_analysis, options)

    # 断言
    print()
    print("--- article length:", len(article), "chars ---")
    print(article[:1200])
    print("---")

    failures = 0
    if len(article) < 1500:
        print(f"  FAIL: article too short ({len(article)} chars; expect >=1500)")
        failures += 1

    # 至少有 4 个二级标题
    h2 = sum(1 for line in article.splitlines() if line.startswith("## "))
    if h2 < 4:
        print(f"  FAIL: only {h2} H2 sections (expect >=4)")
        failures += 1
    else:
        print(f"  H2 sections: {h2}")

    # 不能是 Editor 抱怨 "draft_article 为空" 这种元消息
    if "draft_article" in article and "为空" in article:
        print(f"  FAIL: article is Editor meta-message about empty draft")
        failures += 1

    # 不能大量是 placeholder
    placeholder_strings = {
        "（待补充：论文核心科学问题）", "（待补充：研究背景）",
        "（待补充：研究空白）", "（待补充：研究假设）",
        "（待补充：研究方法）", "（待补充：创新点）",
        "（待补充：局限）", "（待补充：作者结论）",
    }
    ph_count = sum(article.count(s) for s in placeholder_strings)
    if ph_count > 3:
        print(f"  FAIL: {ph_count} placeholder strings in article (expect <=3)")
        failures += 1

    print()
    print("PASS" if failures == 0 else f"FAIL: {failures} failures")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
