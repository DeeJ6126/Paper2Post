"""Reader agent 两段式测试：要求 paper_analysis 不再是全占位符。"""
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
from paper2post.agents import ReaderAgent
from paper2post.llm import MockProvider, LLMError


def find_pdf() -> str:
    candidates = [
        ROOT / "data/test_papers/cellreasoner.pdf",
        ROOT / "examples/sample_paper.pdf",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    raise SystemExit("no PDF found (need data/test_papers/cellreasoner.pdf or examples/sample_paper.pdf)")


def main() -> int:
    settings = load_settings()
    try:
        llm = make_provider(
            settings, provider_name="deepseek",
            model=settings.get("model"),
            base_url=settings.get("base_url"),
        )
        print(f"provider: {type(llm).__name__} name={llm.name} is_mock={llm.is_mock}")
    except LLMError as e:
        print(f"LLMError: {e}; falling back to mock")
        llm = MockProvider()

    pdf = find_pdf()
    paper = parse_pdf(pdf, extract_figure_images=False)
    print(f"paper: {paper.title!r}, sections={len(paper.sections)}, figures={len(paper.figures)}")

    prompts = Prompts.load(ROOT)
    assert prompts.reader_section, "reader_section prompt not loaded"

    agent = ReaderAgent(llm, prompts, settings)
    result = agent.run(paper)

    d = result.model_dump()
    print()
    print("--- result ---")
    for k in ("title", "research_question", "main_findings", "methods",
              "background", "knowledge_gap", "authors_conclusion", "year"):
        v = d.get(k)
        if isinstance(v, list):
            print(f"  {k:22s}: N={len(v)}, sample={v[:2]}")
        else:
            print(f"  {k:22s}: {str(v)[:100]!r}")
    print()
    # 断言
    failures = 0
    def fail(msg):
        nonlocal failures
        failures += 1
        print(f"  FAIL: {msg}")

    placeholder_strings = {
        "（待补充：论文核心科学问题）",
        "（待补充：研究背景）",
        "（待补充：研究空白）",
        "（待补充：研究假设）",
        "（待补充：研究方法）",
        "（待补充：创新点）",
        "（待补充：局限）",
        "（待补充：作者结论）",
    }
    for field in ("research_question", "knowledge_gap", "hypothesis", "authors_conclusion"):
        v = (d.get(field) or "").strip()
        if v in placeholder_strings:
            fail(f"{field} still placeholder: {v!r}")
    for field in ("background", "methods", "innovation", "limitations"):
        vals = d.get(field) or []
        if vals and all(v in placeholder_strings for v in vals):
            fail(f"{field} all placeholder")

    print()
    print(f"{'PASS' if failures == 0 else 'FAIL'}: {failures} failures")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
