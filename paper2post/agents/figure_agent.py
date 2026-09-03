"""Figure Agent：图表筛选与解读。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from paper2post.llm.base import LLMProvider, generate_json
from paper2post.prompts import Prompts
from paper2post.schemas.paper import ParsedPaper
from paper2post.schemas.storyline import Storyline
from paper2post.schemas.figure import FigureAnalysis

from .base import BaseAgent

# 喂给 Figure Agent 的图上限。AlphaFold3 这种大论文会有 100+ 张图，全喂进去
# 会让 vision 模型超时（prompt 超 50KB）。取前 30 张够用，超出的图在 _draft
# 里仍按 low importance 给出，保证 figure_items 列表完整。
MAX_FIGURES_FOR_LLM = 30

# 真正用 vision 模型看图的图上限：避免 1MB+ 论文图多时 base64 payload 爆炸
# 8 张图 × ~200KB base64 ≈ 1.6MB 文本，安全。
MAX_FIGURES_VISION = 6  # 5-6 张足够代表论文（之前 8 张 + DeepSeek 限流经常卡 5+ 分钟）

# 视觉描述 prompt：要求简洁，1-3 句，抓住"图里画了什么 / 表达什么关系 / 关键数据点"
_VISION_PROMPT = (
    "这是一张科研论文的配图。请用 1-3 句中文描述你**实际看到**的内容，"
    "重点说明：(1) 图的类型（流程图/架构图/可视化结果/对比表/示意图/统计图）；"
    "(2) 关键的视觉元素（坐标轴、颜色编码、子图分布）；"
    "(3) 如果能看出具体数字/趋势，简要列出。"
    "禁止编造图中没有的内容。描述要客观、中性，2-3 句话内。"
)


class FigureAgent(BaseAgent):
    MAX_FIGURES_FOR_LLM = MAX_FIGURES_FOR_LLM
    MAX_FIGURES_VISION = MAX_FIGURES_VISION

    def __init__(
        self,
        llm: LLMProvider,
        prompts: Prompts,
        config: Optional[dict] = None,
    ):
        super().__init__(llm, prompts, config)

    def _draft(self, paper: ParsedPaper) -> List[FigureAnalysis]:
        total = len(paper.figures)
        out: List[FigureAnalysis] = []
        for i, f in enumerate(paper.figures):
            # 图较少时默认高重要度，较多时取前几幅
            importance = "high" if total <= 5 else ("medium" if i < 4 else "low")
            out.append(
                FigureAnalysis(
                    figure=f.label,
                    importance=importance,
                    panels=[],
                    role="main_finding",
                    summary=f.caption or "（待补充：图意解读）",
                    article_usage=f.caption != "",
                )
            )
        return out

    def run(
        self,
        paper: ParsedPaper,
        storyline: Storyline,
        article_type: str,
        skip_vision: bool = False,
    ) -> List[FigureAnalysis]:
        draft = self._draft(paper)
        # 只把前 N 张喂给 LLM，超出的图仍出现在 draft（带 low importance）里。
        # 同时截断每张 caption 长度，避免单条 1000+ 字符 caption 撑爆。
        figs_for_llm = paper.figures[: self.MAX_FIGURES_FOR_LLM]
        user = self.dump(
            {
                "figures": [
                    {
                        "label": f.label,
                        "caption": (f.caption or "")[:200],  # 截断 200 字符
                        "page": f.page,
                    }
                    for f in figs_for_llm
                ],
                "storyline": {
                    "mode": storyline.mode,
                    "core_question": (storyline.core_question or "")[:200],
                    "sections": [s.title for s in (storyline.sections or [])][:5],
                    "take_home_message": (storyline.take_home_message or "")[:200],
                },
                "article_type": article_type,
            }
        )
        data = generate_json(
            self.llm,
            system=self.prompts.figure_agent,
            user=user,
            draft=[x.model_dump() for x in draft[: self.MAX_FIGURES_FOR_LLM]],
            temperature=self.temperature(),
            max_tokens=self.max_tokens(),
            _caller="figure_agent",
        )

        items: List[dict] = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("figures") or data.get("figure_analysis") or []
        elif data is None:
            items = []

        if not items:
            items = [x.model_dump() for x in draft[: self.MAX_FIGURES_FOR_LLM]]
        # 把 LLM 给的 items 截到 MAX_FIGURES_FOR_LLM，剩余的图用 draft 兜底
        from_paper_labels = {f.label for f in paper.figures}
        seen_labels = set()
        merged: List[FigureAnalysis] = []
        for x in items:
            if not isinstance(x, dict):
                continue
            fa = FigureAnalysis(**x)
            seen_labels.add(fa.figure)
            merged.append(fa)
        for d in draft[len(merged):]:
            if d.figure not in seen_labels:
                merged.append(d)
        # Vision 增强：选 top-N 关键图让 vision 模型"看"一下，把视觉描述合并到 summary
        if skip_vision:
            return merged
        merged = self._enrich_with_vision(paper, merged)
        return merged

    def _enrich_with_vision(
        self, paper: ParsedPaper, items: List[FigureAnalysis]
    ) -> List[FigureAnalysis]:
        """对前 N 张图调 analyze_image，把视觉描述合并到 summary 末尾。

        - 仅当 LLM 支持 vision 且图片实际存在时才启用
        - 单图失败不影响其它图
        - 任何异常都被吞掉，最坏情况退化为"只用 caption"
        """
        # 一些 provider / model 走 LLMError 早抛，所以提前判断
        try:
            supports = bool(getattr(self.llm, "supports_vision", lambda: False)())
        except Exception:
            supports = False
        if not supports:
            return items

        # 把 items 按 importance 排序，优先看 high > medium > low
        importance_rank = {"high": 0, "medium": 1, "low": 2}
        ranked = sorted(
            range(len(items)),
            key=lambda i: (importance_rank.get(items[i].importance, 1), i),
        )
        top_idx = ranked[: self.MAX_FIGURES_VISION]

        # 构造 paper.figures 的 label -> ParsedFigure 索引，便于找到图片文件
        label_to_fig = {f.label: f for f in paper.figures}

        # 并行调 8 张图的 vision 模型。max_workers=4 防 DeepSeek 限流。
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _vision_one(i: int) -> tuple:
            fa = items[i]
            pf = label_to_fig.get(fa.figure)
            if not pf or not pf.path or not Path(pf.path).exists():
                return (i, "")
            try:
                return (i, self.llm.analyze_image(
                    pf.path, _VISION_PROMPT, temperature=0.3, max_tokens=400
                ).strip())
            except Exception:
                return (i, "")

        # 2026-09-03 评审 1 暴露：vision 模型返回"纯黑色矩形图像，无法判断图表类型"等
        # placeholder 文字被原样塞进 figure summary，正文 writer 不知道图是"看不到的"，
        # 就硬描述图里画了什么（"散点图和密度等高线"）— 实质是 hallucinate 视觉内容。
        # 防御：vision 描述如果是 placeholder / 长度太短 / 关键词触发"看不见"，标记为
        # [无视觉描述]，让 writer 知道不能扩展视觉细节。
        _VISION_PLACEHOLDER_HINTS = (
            "纯黑色", "纯白色", "无法判断", "看不到", "无法识别", "无法看清",
            "占位符", "待补充", "无视觉", "未识别", "无法描述", "无法提取",
            "黑色矩形", "白色矩形", "all black", "all white",
        )

        def _is_placeholder_vision(desc: str) -> bool:
            d = (desc or "").strip()
            if not d:
                return True
            if len(d) < 8:  # 太短
                return True
            low = d.lower()
            return any(h in low for h in _VISION_PLACEHOLDER_HINTS)

        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(_vision_one, i) for i in top_idx]
            for fut in as_completed(futures):
                i, vision_desc = fut.result()
                if not vision_desc:
                    continue
                fa = items[i]
                existing = (fa.summary or "").strip()
                if "视觉描述" in existing or "【视觉】" in existing:
                    continue
                if _is_placeholder_vision(vision_desc):
                    # 不要把 placeholder 描述塞进 summary。改成显式"无视觉描述"标记。
                    if existing:
                        fa.summary = existing + "\n\n[无视觉描述 — vision 未能识别该图]"
                    else:
                        fa.summary = "[无视觉描述 — vision 未能识别该图]"
                else:
                    if existing:
                        fa.summary = existing + "\n\n【视觉描述】" + vision_desc
                    else:
                        fa.summary = "【视觉描述】" + vision_desc
        return items
