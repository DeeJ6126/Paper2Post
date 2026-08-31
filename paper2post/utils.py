"""通用工具：JSON 落盘、Evidence 构建、Markdown 转 HTML。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, List

from paper2post.schemas.paper import PaperAnalysis
from paper2post.schemas.evidence import Evidence, EvidenceMap


def _to_dict(obj: Any) -> Any:
    """递归把 Pydantic 对象转换为可 JSON 序列化的 dict。"""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, (list, tuple)):
        return [_to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    return obj


def dump_json(obj: Any, path: str) -> None:
    """把对象（Pydantic 或 dict 或嵌套结构）写入 JSON。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = _to_dict(obj)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_evidence_map(analysis: PaperAnalysis) -> EvidenceMap:
    """从 main_findings 派生 Evidence。

    Todo(里程碑 3): 升级为独立的 LLM Evidence Agent，从原文抽取证据。
    """
    evs: List[Evidence] = []
    for f in analysis.main_findings:
        evs.append(
            Evidence(
                claim=f.finding,
                source_section="Results" if f.evidence else "",
                figure=f.figure,
                evidence_text=f.evidence,
                confidence=0.9 if f.evidence else 0.5,
            )
        )
    return EvidenceMap(evidence=evs)


# ---------- Markdown -> HTML（最小实现） ----------


def _inline(md: str) -> str:
    s = md
    s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r'<img src="\2" alt="\1" />', s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", s)
    return s


def markdown_to_html(markdown: str) -> str:
    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    out: List[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for line in lines:
        s = line.rstrip()
        if not s.strip():
            close_list()
            out.append("")
            continue
        if re.match(r"^#{1,6}\s+", s):
            close_list()
            level = len(s) - len(s.lstrip("#"))
            body = _inline(s.lstrip("#").strip())
            out.append(f"<h{level}>{body}</h{level}>")
        elif re.match(r"^>\s?", s):
            close_list()
            out.append(f"<blockquote>{_inline(s.lstrip('>').strip())}</blockquote>")
        elif re.match(r"^(-{3,}|\*{3,}|_{3,})\s*$", s):
            close_list()
            out.append("<hr />")
        elif re.match(r"^[-*+]\s+", s):
            if not in_list:
                out.append("<ul>")
                in_list = True
            body = re.sub(r"^[-*+]\s+", "", s)
            out.append(f"<li>{_inline(body)}</li>")
        elif re.match(r"^\d+[.)]\s+", s):
            body = re.sub(r"^\d+[.)]\s+", "", s)
            out.append(f"<p>{_inline(body)}</p>")
        else:
            close_list()
            out.append(f"<p>{_inline(s)}</p>")

    close_list()
    body = "\n".join(out)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Paper2Post 生成</title>
<style>
body{{max-width:760px;margin:2em auto;padding:0 1.2em;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;line-height:1.75;color:#1f2328}}
h1,h2,h3{{line-height:1.3}}
img{{max-width:100%;height:auto}}
blockquote{{border-left:4px solid #d0d7de;margin:1em 0;padding-left:1em;color:#57606a}}
ul{{padding-left:1.5em}}
code{{background:#f6f8fa;padding:.1em .35em;border-radius:4px}}
</style>
</head>
<body>
{body}
</body>
</html>"""
