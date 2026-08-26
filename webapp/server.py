"""Paper2Post Web 服务（V1）。

使用标准库 http.server 实现，无需安装额外依赖即可在浏览器中使用。
入口:
    python run_web.py [--port 8000] [--open-browser]
    # 或
    python -m webapp.server --port 8000
"""

from __future__ import annotations

import argparse
import fnmatch
import io
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse, parse_qs

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
_VENDOR = _ROOT / "vendor"
if _VENDOR.is_dir():
    sys.path.insert(0, str(_VENDOR))

from paper2post.config import load_settings
from paper2post.llm import MockProvider, LLMError
from paper2post.pipeline import Pipeline, make_provider
from paper2post.utils import markdown_to_html

if getattr(sys, "frozen", False):
    # 打包数据落在 _MEIPASS/webapp/static
    STATIC_DIR = Path(sys._MEIPASS) / "webapp" / "static"
else:
    STATIC_DIR = Path(__file__).resolve().parent / "static"

# 打包（frozen）时把产物写到 exe 旁边，避免写进临时解包目录
if getattr(sys, "frozen", False):
    OUTPUTS_ROOT = Path(sys.executable).resolve().parent / "outputs_web"
else:
    OUTPUTS_ROOT = _ROOT / "outputs_web"

MAX_UPLOAD = 50 * 1024 * 1024  # 50 MB


def _run_pipeline(
    pdf_bytes: bytes,
    options: Dict[str, Any],
    provider: str,
    model: str,
    api_key: str = "",
    base_url: str = "",
) -> Dict[str, Any]:
    import uuid

    run_id = uuid.uuid4().hex[:12]
    run_dir = OUTPUTS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = run_dir / "paper.pdf"
    pdf_path.write_bytes(pdf_bytes)

    settings = load_settings()
    llm = MockProvider()
    if provider != "mock":
        try:
            llm = make_provider(
                settings,
                provider_name=provider,
                model=model or None,
                api_key=api_key or None,
                base_url=base_url or None,
            )
        except LLMError:
            llm = MockProvider()

    pipeline = Pipeline(settings=settings, llm=llm)
    paths = pipeline.run(str(pdf_path), output_dir=str(run_dir), options=options)

    # ---- assemble response ----
    def _load_json(name: str) -> Any:
        p = run_dir / name
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return {}

    # 论文分节（供 Paper Navigator / Evidence Grounding 原文定位）
    try:
        from paper2post.parser import parse_pdf as _parse
        _p = _parse(str(pdf_path), extract_figure_images=False)
        paper_sections = [{"heading": s.heading, "text": s.text[:4000]} for s in _p.sections]
        paper_meta = {
            "title": _p.title,
            "authors": _p.authors,
            "abstract": _p.abstract,
            "page_count": _p.metadata.get("page_count"),
        }
    except Exception:
        paper_sections = []
        paper_meta = {}

    analysis = _load_json("paper_analysis.json")
    storyline = _load_json("storyline.json")
    figure_analysis = _load_json("figure_analysis.json")
    fact_check = _load_json("fact_check.json")
    evidence = _load_json("evidence.json")
    captions = _load_json("captions.json")

    article_md_path = run_dir / "final_article.md"
    article_md = article_md_path.read_text(encoding="utf-8") if article_md_path.exists() else ""

    # figures with urls
    fig_dir = run_dir / "figures"
    figure_items: list = []
    for fa in figure_analysis:
        label = (fa or {}).get("figure", "")
        # 找到对应图片文件名
        matched = None
        label_num = label.replace("Figure", "").strip()
        for cand in sorted(os.listdir(fig_dir)) if fig_dir.is_dir() else []:
            if cand.lower().endswith((".png", ".jpg", ".jpeg")):
                num_from_file = cand.replace("figure_", "").split(".")[0]
                if num_from_file == label_num:
                    matched = cand
                    break
        figure_items.append(
            {
                "label": label,
                "summary": (fa or {}).get("summary", ""),
                "importance": (fa or {}).get("importance", ""),
                "role": (fa or {}).get("role", ""),
                "url": f"/files/{run_id}/figures/{matched}" if matched else "",
            }
        )

    def _url(name: str) -> str:
        return f"/files/{run_id}/{name}"

    return {
        "run_id": run_id,
        "article_html_url": _url("final_article.html"),
        "article_md": article_md,
        "paper_sections": paper_sections,
        "paper_meta": paper_meta,
        "analysis": analysis,
        "storyline": storyline,
        "figure_analysis": figure_analysis,
        "figure_items": figure_items,
        "fact_check": fact_check,
        "evidence": evidence,
        "captions": captions,
        "files": {
            "final_article.md": _url("final_article.md"),
            "final_article.html": _url("final_article.html"),
            "paper_analysis.json": _url("paper_analysis.json"),
            "fact_check.json": _url("fact_check.json"),
            "storyline.json": _url("storyline.json"),
            "figure_analysis.json": _url("figure_analysis.json"),
            "generation_report.md": _url("generation_report.md"),
        },
        "provider": llm.name,
    }


def run_article_action(run_id: str, action: str, provider: str, model: str) -> Dict[str, Any]:
    """用已缓存的中间结果重新执行 Writer/Editor，实现单步再生成。"""
    from paper2post.schemas.paper import PaperAnalysis
    from paper2post.schemas.evidence import EvidenceMap
    from paper2post.schemas.storyline import Storyline
    from paper2post.schemas.figure import FigureAnalysis
    from paper2post.schemas.review import FactCheck
    from paper2post.agents import WriterAgent, EditorAgent
    from paper2post.prompts import Prompts

    run_dir = OUTPUTS_ROOT / run_id
    if not run_dir.is_dir():
        raise FileNotFoundError("run not found: " + run_id)

    def _load(name, cls):
        p = run_dir / name
        if not p.exists():
            return cls()
        import json as _j

        return cls(**_j.loads(p.read_text(encoding="utf-8")))

    analysis = _load("paper_analysis.json", PaperAnalysis)
    evidence = _load("evidence.json", EvidenceMap)
    storyline = _load("storyline.json", Storyline)
    figs = []
    fpath = run_dir / "figure_analysis.json"
    if fpath.exists():
        import json as _j

        for f in _j.loads(fpath.read_text(encoding="utf-8")):
            figs.append(FigureAnalysis(**f))

    settings = load_settings()
    llm = MockProvider()
    if provider != "mock":
        try:
            llm = make_provider(settings, provider_name=provider, model=model or None)
        except LLMError:
            llm = MockProvider()

    prompts = Prompts.load(_ROOT)
    writer = WriterAgent(llm, prompts, settings)
    editor = EditorAgent(llm, prompts, settings)

    options = {
        "article_type": storyline.mode or "deep_review",
        "target_audience": "biology_graduate",
        "article_length": 2500,
        "language": "zh-CN",
        "style": "academic_popularization",
        "focus": action,  # 供 writer 在真实模式下按动作调整
    }
    article = writer.run(analysis, evidence, storyline, figs, options)
    # 动作：改标题则由 real LLM 处理；mock 下保持原样
    factcheck = _load("fact_check.json", FactCheck)
    final = editor.run(article, factcheck, options)
    md_path = run_dir / "final_article.md"
    html_path = run_dir / "final_article.html"
    md_path.write_text(final.get("markdown", article), encoding="utf-8")
    html_path.write_text(final.get("html", ""), encoding="utf-8")
    return {
        "article_md": final.get("markdown", article),
        "article_html_url": f"/files/{run_id}/final_article.html",
        "action": action,
        "provider": llm.name,
        "title": final.get("title", ""),
    }


SETTINGS_PATH = _ROOT / "data" / "settings.json"


def load_user_settings() -> dict:
    try:
        if SETTINGS_PATH.exists():
            import json as _j

            return _j.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_user_settings(payload: dict) -> dict:
    cur = load_user_settings()
    cur.update((k, v) for k, v in payload.items() if v not in (None, ""))
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "settings": cur}


def _update_env(key: str, value: str) -> None:
    if not value or not key:
        return
    env = _ROOT / ".env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    found = False
    for i, ln in enumerate(lines):
        if ln.startswith(key + "="):
            lines[i] = f"{key}={value}"
            found = True
    if not found:
        lines.append(f"{key}={value}")
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _provider_env_key(provider: str) -> str:
    from paper2post.llm.registry import OPENAI_COMPATIBLE

    if provider in OPENAI_COMPATIBLE:
        return OPENAI_COMPATIBLE[provider].get("env_key", "OPENAI_API_KEY")
    if provider == "anthropic":
        return "ANTHROPIC_API_KEY"
    if provider == "gemini":
        return "GEMINI_API_KEY"
    return "OPENAI_API_KEY"


def get_models_info() -> dict:
    from paper2post.llm import all_provider_names

    s = load_settings()
    return {
        "providers": ["mock"] + all_provider_names(),
        "provider": s.get("provider", "openai"),
        "model": s.get("model", ""),
        "base_url": s.get("base_url", ""),
        "has_api_key": bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY") or s.get("api_key")),
    }


def save_models(payload: dict) -> dict:
    provider = payload.get("provider", "openai")
    model = payload.get("model", "")
    base_url = payload.get("base_url", "")
    api_key = payload.get("api_key", "")
    if api_key:
        _update_env(_provider_env_key(provider), api_key)
    if provider in ("anthropic", "gemini"):
        if model:
            _update_env(provider.upper() + "_MODEL", model)
    else:
        if model:
            if provider == "openai":
                _update_env("OPENAI_MODEL", model)
            else:
                _update_env(provider.upper() + "_MODEL", model)
        if base_url:
            if provider == "openai":
                _update_env("OPENAI_BASE_URL", base_url)
            else:
                _update_env(provider.upper() + "_BASE_URL", base_url)
    save_user_settings({"provider": provider, "model": model, "base_url": base_url})
    return {"ok": True, "provider": provider, "model": model}


def list_generations() -> list:
    from datetime import datetime

    out = []
    if OUTPUTS_ROOT.is_dir():
        for d in sorted(OUTPUTS_ROOT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not d.is_dir():
                continue
            meta = d / "metadata.json"
            if not meta.exists():
                continue
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
            except Exception:
                continue
            fc = d / "fact_check.json"
            score = None
            try:
                if fc.exists():
                    score = json.loads(fc.read_text(encoding="utf-8")).get("overall_score")
            except Exception:
                pass
            out.append({
                "run_id": d.name,
                "title": m.get("title", ""),
                "authors": m.get("authors", ""),
                "page_count": m.get("page_count"),
                "time": datetime.fromtimestamp(d.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "score": score,
            })
    return out


def _run_dir(run_id: str):
    return OUTPUTS_ROOT / run_id


def get_review_state(run_id: str) -> dict:
    p = _run_dir(run_id) / "review_state.json"
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"accepted": [], "dismissed": []}


def set_review_state(run_id: str, payload: dict) -> dict:
    d = _run_dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    cur = get_review_state(run_id)
    for k in ("accepted", "dismissed"):
        if isinstance(payload.get(k), list):
            cur[k] = payload[k]
    (d / "review_state.json").write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, **cur}


def get_run_response(run_id: str) -> dict:
    """从已落盘的 run 目录重建完整结果（供 Library/历史记录重新打开）。"""
    run_dir = OUTPUTS_ROOT / run_id
    if not run_dir.is_dir():
        raise FileNotFoundError("run not found: " + run_id)

    def _load(name):
        p = run_dir / name
        try:
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    analysis = _load("paper_analysis.json")
    storyline = _load("storyline.json")
    figure_analysis = _load("figure_analysis.json")
    fact_check = _load("fact_check.json")
    evidence = _load("evidence.json")
    captions = _load("captions.json")
    md_path = run_dir / "final_article.md"
    article_md = md_path.read_text(encoding="utf-8") if md_path.exists() else ""

    fig_dir = run_dir / "figures"
    figure_items = []
    for fa in figure_analysis if isinstance(figure_analysis, list) else []:
        label = (fa or {}).get("figure", "")
        num = label.replace("Figure", "").strip()
        matched = None
        if fig_dir.is_dir():
            for cand in sorted(os.listdir(fig_dir)):
                if cand.lower().endswith((".png", ".jpg", ".jpeg")):
                    if cand.replace("figure_", "").split(".")[0] == num:
                        matched = cand
                        break
        figure_items.append({"label": label, "summary": (fa or {}).get("summary", ""), "importance": (fa or {}).get("importance", ""), "role": (fa or {}).get("role", ""), "url": f"/files/{run_id}/figures/{matched}" if matched else ""})

    try:
        from paper2post.parser import parse_pdf as _parse
        _p = _parse(str(run_dir / "paper.pdf"), extract_figure_images=False)
        paper_sections = [{"heading": s.heading, "text": s.text[:4000]} for s in _p.sections]
        paper_meta = {"title": _p.title, "authors": _p.authors, "abstract": _p.abstract, "page_count": _p.metadata.get("page_count")}
    except Exception:
        paper_sections = []
        paper_meta = _load("metadata.json")

    def _url(n):
        return f"/files/{run_id}/{n}"

    return {
        "run_id": run_id, "article_html_url": _url("final_article.html"), "article_md": article_md,
        "paper_sections": paper_sections, "paper_meta": paper_meta, "analysis": analysis,
        "storyline": storyline, "figure_analysis": figure_analysis, "figure_items": figure_items,
        "fact_check": fact_check, "evidence": evidence, "captions": captions,
        "files": {n: _url(n) for n in ["final_article.md", "final_article.html", "paper_analysis.json", "fact_check.json", "storyline.json", "figure_analysis.json", "generation_report.md"]},
        "provider": "stored",
    }


PROGRESS = {}


def start_generation(pdf_bytes: bytes, options: dict, provider: str, model: str) -> dict:
    import uuid
    import threading

    run_id = uuid.uuid4().hex[:12]
    run_dir = OUTPUTS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    pdf_file = run_dir / "paper.pdf"
    pdf_file.write_bytes(pdf_bytes)
    PROGRESS[run_id] = {"status": "running", "step": 0, "total": 8, "label": "PDF Parser"}

    def watchdog():
        import time as _t
        _t.sleep(120)
        if PROGRESS.get(run_id, {}).get("status") == "running":
            PROGRESS[run_id] = {"status": "error", "error": "生成超时（超过 120 秒）", "run_id": run_id}

    threading.Thread(target=watchdog, daemon=True).start()

    def cb(idx, total, label):
        PROGRESS[run_id] = {"status": "running", "step": idx, "total": total, "label": label}

    def work():
        try:
            s = load_settings()
            llm = MockProvider()
            if provider != "mock":
                try:
                    llm = make_provider(s, provider_name=provider, model=model or None)
                except LLMError:
                    llm = MockProvider()
            from paper2post.pipeline import Pipeline as _Pipe

            _Pipe(settings=s, llm=llm).run(str(pdf_file), output_dir=str(run_dir), options=options, progress=cb)
            PROGRESS[run_id] = {"status": "done", "step": 8, "total": 8, "label": "Done", "run_id": run_id, "provider": llm.name}
        except Exception as exc:  # noqa
            PROGRESS[run_id] = {"status": "error", "error": str(exc), "run_id": run_id}

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": run_id, "status": "running"}


class Handler(BaseHTTPRequestHandler):
    server_version = "Paper2PostWeb/0.1"

    # ---------- helpers ----------
    def _send(self, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, status: int, payload: Any):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        try:
            import json as _j

            return _j.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    # ---------- GET ----------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            return self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        if path == "/app.js":
            return self._serve_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
        if path == "/style.css":
            return self._serve_file(STATIC_DIR / "style.css", "text/css; charset=utf-8")
        if path == "/api/health":
            return self._json(200, {"status": "ok"})
        if path == "/api/settings":
            return self._json(200, load_user_settings())
        if path == "/api/models":
            return self._json(200, get_models_info())
        if path == "/api/generations":
            return self._json(200, {"generations": list_generations()})
        if path == "/api/result":
            rid = parse_qs(parsed.query).get("run_id", [""])[0]
            try:
                return self._json(200, get_run_response(rid))
            except Exception as exc:
                return self._json(404, {"error": str(exc)})
        if path == "/api/progress":
            rid = parse_qs(parsed.query).get("run_id", [""])[0]
            return self._json(200, PROGRESS.get(rid, {"status": "unknown"}))
        if path == "/api/review":
            rid = parse_qs(parsed.query).get("run_id", [""])[0]
            return self._json(200, get_review_state(rid))

        if path.startswith("/files/"):
            return self._serve_output_file(path[len("/files/"):])

        return self._send(404, b"Not Found")

    def _serve_file(self, file_path: Path, content_type: str):
        if not file_path.exists():
            return self._send(404, b"Not Found")
        body = file_path.read_bytes()
        self._send(200, body, content_type)

    def _serve_output_file(self, rel: str):
        rel = rel.lstrip("/")
        # 防目录穿越
        real = (OUTPUTS_ROOT / rel).resolve()
        if not str(real).startswith(str(OUTPUTS_ROOT.resolve())) or not real.exists():
            return self._send(404, b"Not Found")
        ctype = "application/octet-stream"
        suffix = real.suffix.lower()
        if suffix == ".html":
            ctype = "text/html; charset=utf-8"
        elif suffix == ".md":
            ctype = "text/markdown; charset=utf-8"
        elif suffix == ".json":
            ctype = "application/json; charset=utf-8"
        elif suffix in (".png", ".jpg", ".jpeg"):
            ctype = "image/png" if suffix == ".png" else "image/jpeg"
        self._send(200, real.read_bytes(), ctype)

    def _handle_action(self, parsed):
        qs = parse_qs(parsed.query)

        def _q(k, d):
            vals = qs.get(k)
            return vals[0] if vals else d

        run_id = _q("run_id", "")
        action = _q("action", "regenerate")
        if not run_id:
            return self._json(400, {"error": "run_id required"})
        try:
            result = run_article_action(run_id, action, _q("provider", "mock"), _q("model", ""))
            return self._json(200, result)
        except Exception as exc:  # noqa
            return self._json(500, {"error": str(exc)})

    # ---------- POST ----------
    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/action":
            return self._handle_action(parsed)
        if parsed.path == "/api/settings":
            return self._json(200, save_user_settings(self._read_json()))
        if parsed.path == "/api/models":
            return self._json(200, save_models(self._read_json()))
        if parsed.path == "/api/review":
            b = self._read_json()
            return self._json(200, set_review_state(b.get("run_id", ""), b))
        if parsed.path != "/api/generate":
            return self._json(404, {"error": "not found"})

        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return self._json(400, {"error": "empty body"})
        if length > MAX_UPLOAD:
            return self._json(413, {"error": "file too large (max 50MB)"})

        pdf_bytes = self.rfile.read(length)
        qs = parse_qs(parsed.query)

        def _q(key: str, default: str) -> str:
            vals = qs.get(key)
            return vals[0] if vals else default

        options = {
            "article_type": _q("article_type", "deep_review"),
            "target_audience": _q("audience", "biology_graduate"),
            "article_length": int(_q("length", "2500") or "2500"),
            "language": _q("language", "zh-CN"),
            "style": _q("style", "academic_popularization"),
            "extract_figures": True,
        }
        provider = _q("provider", "mock")
        model = _q("model", "gpt-4o-mini")
        api_key = _q("api_key", "")
        base_url = _q("base_url", "")

        try:
            result = start_generation(pdf_bytes, options, provider, model)
            return self._json(200, result)
        except Exception as exc:  # noqa
            return self._json(500, {"error": str(exc)})

    def log_message(self, fmt, *args):
        # 精简日志
        sys.stderr.write("%s %s\n" % (self.command, self.path))


def create_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    """创建内置 HTTP 服务器（供 run_web / desktop 复用）。"""
    OUTPUTS_ROOT.mkdir(parents=True, exist_ok=True)
    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="run_web", description="Paper2Post Web App")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    httpd = create_server(args.host, args.port)
    url = f"http://{args.host}:{args.port}/"
    print("Paper2Post Web 已启动:", url)
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        httpd.server_close()


if __name__ == "__main__":
    main()
