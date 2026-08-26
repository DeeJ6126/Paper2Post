"""命令行入口。"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from .config import PROJECT_ROOT, load_settings
from .llm import LLMProvider, MockProvider, LLMError, all_provider_names
from .pipeline import Pipeline, make_provider


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paper2post",
        description="Paper2Post - AI 论文推文自动生成系统",
    )
    p.add_argument("pdf", help="论文 PDF 路径")
    p.add_argument(
        "--article-type",
        default=None,
        choices=["deep_review", "speed", "methods", "resource"],
        help="推文类型",
    )
    p.add_argument("--audience", default=None, help="目标受众")
    p.add_argument("--length", default=None, type=int, help="推文长度（字）")
    p.add_argument("--language", default=None, help="语言（如 zh-CN）")
    p.add_argument("--style", default=None, help="写作风格")
    p.add_argument(
        "--provider",
        default=None,
        choices=["mock"] + all_provider_names(),
        help="LLM Provider: " + " | ".join(["mock"] + all_provider_names()),
    )
    p.add_argument("--model", default=None, help="模型名")
    p.add_argument("--api-key", default=None, help="API Key（优先于环境变量）")
    p.add_argument("--base-url", default=None, help="OpenAI 兼容 base_url")
    p.add_argument("--config", default=None, help="配置文件路径")
    p.add_argument("--out", default="outputs", help="输出目录")
    p.add_argument("--mock", action="store_true", help="使用 mock LLM，无需 API Key")
    p.add_argument("--no-figures", action="store_true", help="不提取图像")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config)

    options = {
        "article_type": args.article_type or settings.get("article_type", "deep_review"),
        "target_audience": args.audience or settings.get("target_audience", ""),
        "article_length": args.length or settings.get("article_length", 2500),
        "language": args.language or settings.get("language", "zh-CN"),
        "style": args.style or settings.get("style", "academic_popularization"),
        "extract_figures": not args.no_figures,
    }

    # ---- Provider ----
    llm: LLMProvider
    if args.mock:
        llm = MockProvider()
    else:
        try:
            llm = make_provider(
                settings,
                provider_name=args.provider,
                model=args.model,
                api_key=args.api_key,
                base_url=args.base_url,
            )
        except LLMError as exc:
            print("[warn] 无法初始化真实 LLM Provider，已回退到 mock 模式。原因：", exc)
            llm = MockProvider()

    pipeline = Pipeline(settings=settings, llm=llm)
    paths = pipeline.run(args.pdf, output_dir=args.out, options=options)

    print("=== 生成完成 ===")
    for k, v in paths.items():
        print(f"  {k} -> {v}")
    print("生成报告:", os.path.join(args.out, "generation_report.md"))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
