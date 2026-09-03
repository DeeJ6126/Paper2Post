"""单批 runner：跑指定论文 N 篇，超出 _summary_v2 已 OK 的。
用法：python _run_batch.py
"""
import sys
import time
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from paper2post.config import load_settings
from paper2post.pipeline import make_provider, Pipeline


def safe_name(p: Path) -> str:
    name = p.stem
    for ch in '/\\:*?"<>|()（）':
        name = name.replace(ch, "_")
    return name[:60]


def metrics_for(out_dir: Path) -> dict:
    art_path = out_dir / "final_article.md"
    analysis_path = out_dir / "paper_analysis.json"
    figs_path = out_dir / "figure_analysis.json"
    article = art_path.read_text(encoding="utf-8") if art_path.exists() else ""
    analysis = json.loads(analysis_path.read_text(encoding="utf-8")) if analysis_path.exists() else {}
    figs = json.loads(figs_path.read_text(encoding="utf-8")) if figs_path.exists() else []
    placeholders = []
    for k in ("knowledge_gap", "hypothesis", "authors_conclusion"):
        v = analysis.get(k, "")
        if isinstance(v, str) and "（待补充" in v:
            placeholders.append(k)
    inn = analysis.get("innovation", [])
    if isinstance(inn, str) and "（待补充" in inn:
        placeholders.append("innovation")
    elif isinstance(inn, list) and any("（待补充" in (x or "") for x in inn):
        placeholders.append("innovation")
    figs_with_visual = sum(1 for f in figs if "视觉描述" in (f.get("summary") or ""))
    return {
        "article_chars": len(article),
        "h2_count": article.count("\n## "),
        "figs_total": len(figs),
        "figs_with_visual": figs_with_visual,
        "placeholders_left": placeholders,
    }


def run_one(pdf: Path, model: str, out_dir: Path, text_model: str = "deepseek-v4-flash", skip_vision: bool = False) -> dict:
    settings = load_settings()
    t0 = time.time()
    try:
        llm = make_provider(settings, provider_name="deepseek", model=model, base_url=settings.get("base_url"))
        pipeline = Pipeline(settings=settings, llm=llm)
        pipeline.run(str(pdf), output_dir=str(out_dir), options={
            "article_type": "deep_review",
            "target_audience": "biology_graduate",
            "article_length": 2500,
            "language": "zh-CN",
            "style": "academic_popularization",
            "text_model": text_model,  # reader/writer/reviewer/editor 用的模型
            "skip_vision": skip_vision,  # 大论文可跳过 vision 防止 figure_agent 卡死
        })
        elapsed = time.time() - t0
        m = metrics_for(out_dir)
        m.update({"ok": True, "elapsed_s": round(elapsed, 1), "pdf": pdf.name, "size_mb": round(pdf.stat().st_size / 1e6, 2)})
        return m
    except Exception as e:
        elapsed = time.time() - t0
        return {"ok": False, "elapsed_s": round(elapsed, 1), "pdf": pdf.name, "size_mb": round(pdf.stat().st_size / 1e6, 2), "error": str(e)[:300]}


def main():
    pdf_dir = ROOT / "data" / "test_papers"
    out_root = ROOT / "outputs_test_run"
    out_root.mkdir(parents=True, exist_ok=True)
    summary_path = out_root / "_summary_v2.json"
    partial_path = out_root / "_summary_v2_partial.json"
    pdfs = sorted(pdf_dir.glob("*.pdf"), key=lambda p: p.stat().st_size)
    # 加载已有 OK 记录
    summary = []
    done_names = set()
    for src in (summary_path, partial_path):
        if src.exists():
            try:
                items = json.loads(src.read_text(encoding="utf-8"))
                if not isinstance(items, list):
                    continue
                for m in items:
                    if m.get("ok") and m["pdf"] not in done_names:
                        summary.append(m)
                        done_names.add(m["pdf"])
            except Exception:
                pass
    print(f"loaded {len(summary)} ok records", flush=True)
    model = "deepseek-v4-flash-vision-exp"
    pending = [p for p in pdfs if p.name not in done_names]
    print(f"pending: {len(pending)} papers: {[p.name for p in pending]}", flush=True)
    # argv[1] 是"只跑这些 PDF"的 substring 列表（用 substring 匹配）。空 = 跑全部。
    only_pdfs = sys.argv[1:] if len(sys.argv) > 1 else []
    if only_pdfs:
        before = len(pending)
        pending = [p for p in pending if any(s in p.name for s in only_pdfs)]
        print(f"filtered {before - len(pending)} papers; running only {len(pending)}", flush=True)
    # 黑名单：综述书等不需要跑的论文
    BLACKLIST = ("DeepGGL", "深度几何图学习")
    bl_filtered = [p for p in pending if not any(b in p.name for b in BLACKLIST)]
    if len(bl_filtered) < len(pending):
        print(f"blacklist filtered {len(pending) - len(bl_filtered)} papers (综述书等)", flush=True)
        pending = bl_filtered
    for i, pdf in enumerate(pending, 1):
        out_dir = out_root / safe_name(pdf)
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n[{i}/{len(pending)}] {pdf.name} ({pdf.stat().st_size/1e6:.1f}MB)", flush=True)
        # 中大型论文（>= 5MB）默认跳过 vision 防止 figure_agent 卡死
        # 经验：5MB+ 的论文 figure 数量 30+ 张，vision 8s × 6 = 48s 但限流累计经常卡 5+ 分钟
        skip = pdf.stat().st_size > 5 * 1024 * 1024
        if skip:
            print(f"  [skip_vision=True] for {pdf.stat().st_size/1e6:.1f}MB paper", flush=True)
        m = run_one(pdf, model, out_dir, skip_vision=skip)
        summary.append(m)
        if m.get("ok"):
            print(f"  OK {m['elapsed_s']}s | chars={m['article_chars']} h2={m['h2_count']} figs={m['figs_total']} (visual={m['figs_with_visual']}) ph={m['placeholders_left']}", flush=True)
        else:
            print(f"  FAIL {m['elapsed_s']}s | {m.get('error','')[:200]}", flush=True)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== batch done, {sum(1 for x in summary if x.get('ok'))}/{len(summary)} ok ===", flush=True)


if __name__ == "__main__":
    main()
