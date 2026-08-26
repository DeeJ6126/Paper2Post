"""Paper2Post 原生桌面应用（自绘现代 UI，零依赖 Tkinter）。

用法:
    python desktop.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if (_ROOT / "paper2post").is_dir():
    sys.path.insert(0, str(_ROOT))
_VENDOR = _ROOT / "vendor"
if _VENDOR.is_dir():
    sys.path.insert(0, str(_VENDOR))

import tkinter as tk
from tkinter import filedialog, scrolledtext

from paper2post.config import load_settings
from paper2post.llm import MockProvider, LLMError, all_provider_names
from paper2post.pipeline import Pipeline, make_provider

OUTPUTS_ROOT = Path(os.environ.get("PAPER2POST_OUTPUT", ".paper2post_native"))

# ---------- 设计令牌 ----------
BG = "#eef1f6"
SURFACE = "#ffffff"
INK = "#1f2937"
MUTED = "#7a8394"
BORDER = "#e4e8ef"
ACCENT = "#6366f1"
ACCENT_DARK = "#4f46e5"
ACCENT_HOVER = "#818cf8"
VIOLET = "#8b5cf6"
SUCCESS = "#10b981"
ERROR = "#ef4444"
GRAD_TOP = "#6366f1"
GRAD_BOTTOM = "#8b5cf6"
FONT = "Segoe UI"
DPI_SCALE = 1.0

LANG = "zh"
UI_MAP = {
    "① 论文 PDF": "① Paper PDF", "② 推文设置": "② Article Settings", "③ LLM 引擎": "③ LLM Engine",
    "论文与推文设置": "Paper & Generation Settings", "尚未选择 PDF": "No PDF selected",
    "请选择论文": "Select a paper", "请先选择 PDF": "Please choose a PDF first",
    "推文类型": "Article type", "长度（字）": "Length (chars)", "目标受众": "Audience", "语言": "Language",
    "模型名（可选）": "Model (optional)", "LLM 引擎": "LLM Engine",
    "API Key（可选，留空则读 .env）": "API Key (optional)", "Base URL（可选，覆盖默认）": "Base URL (optional)",
    "推文预览": "Article", "论文分析": "Analysis", "故事线": "Storyline", "图表": "Figures",
    "事实核验": "Review", "输出文件": "Files", "打开输出目录": "Open Output", "选择论文 PDF": "Choose PDF",
    "生成推文": "Generate Article", "已选择，点击「生成推文」": "Selected. Click Generate.",
}


def t(zh, en):
    return en if LANG == "en" else zh


def _enable_dpi():
    """Windows 高 DPI 感知：防止界面被系统放大导致模糊。"""
    if os.name != "nt":
        return
    import ctypes
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _compute_dpi(root):
    global DPI_SCALE
    try:
        dpi = root.winfo_fpixels("1i")
        DPI_SCALE = max(1.0, dpi / 96.0)
        root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        DPI_SCALE = 1.0


def _draw_round(canvas, x1, y1, x2, y2, r, **kw):
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    canvas.create_polygon(pts, smooth=True, **kw)


class Pill(tk.Canvas):
    """圆角胶囊按钮。"""

    def __init__(self, parent, text, command=None, *, fill=ACCENT, hover=ACCENT_HOVER,
                 fg="#ffffff", bg=SURFACE, w=180, h=40, r=12, bold=True, on_leave=None):
        w = max(1, int(round(w * DPI_SCALE)))
        h = max(1, int(round(h * DPI_SCALE)))
        r = max(1, int(round(r * DPI_SCALE)))
        super().__init__(parent, width=w, height=h, bg=bg, highlightthickness=0, cursor="hand2")
        self._text = text
        self._cmd = command
        self._fill, self._hover, self._fg = fill, hover, fg
        self.cw, self.ch, self._r, self._bold = w, h, r, bold
        self._on_leave = on_leave
        self._draw(fill)
        self.bind("<Enter>", lambda e: self._draw(hover))
        self.bind("<Leave>", lambda e: (self._draw(self._fill), self._on_leave() if self._on_leave else None))
        self.bind("<Button-1>", lambda e: self._cmd() if self._cmd else None)

    def _draw(self, fill):
        self.delete("all")
        _draw_round(self, 1, 1, self.cw - 1, self.ch - 1, self._r, fill=fill, outline="")
        weight = "bold" if self._bold else "normal"
        self.create_text(self.cw / 2, self.ch / 2, text=self._text, fill=self._fg, font=(FONT, 11, weight))

    def set_text(self, text):
        self._text = text
        self._draw(self._fill)

    def set_fill(self, fill):
        self._fill = fill
        self._draw(fill)


class GradientHeader(tk.Canvas):
    """顶栏渐变。"""

    def __init__(self, parent, height=76, top=GRAD_TOP, bottom=GRAD_BOTTOM):
        super().__init__(parent, height=height, bg=top, highlightthickness=0)
        self._h = height
        self._top, self._bottom = top, bottom
        self.bind("<Configure>", lambda e: self._paint(e.width, e.height))

    def _paint(self, w, h):
        self.delete("all")
        if w <= 0:
            return
        steps = max(1, h)
        top_rgb = self.winfo_rgb(self._top)
        bot_rgb = self.winfo_rgb(self._bottom)
        for i in range(steps):
            t = i / max(1, steps - 1)
            col = "#%02x%02x%02x" % tuple(int(top_rgb[c] + (bot_rgb[c] - top_rgb[c]) * t) // 256 for c in range(3))
            self.create_line(0, i, w, i, fill=col)


def lighten(hex_color, factor=0.12):
    rgb = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    rgb = [min(255, int(c + (255 - c) * factor)) for c in rgb]
    return "#%02x%02x%02x" % tuple(rgb)


def tint(hex_color, other, t=0.5):
    a = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[c] + (b[c] - a[c]) * t) for c in range(3))


def card(parent, **kw):
    fr = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, **kw)
    return fr


def accent_strip(parent, color=ACCENT, height=4):
    return tk.Frame(parent, bg=color, height=height)


class Paper2PostApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Paper2Post")
        root.geometry("1320x860")
        root.minsize(1080, 720)
        root.configure(bg=BG)
        self.pdf_path = None
        self.settings = load_settings()
        self._photo_refs = []
        self._last_run = None
        self._tabs = {}
        self._current_tab = None
        self._pills = []
        self._secs = []
        self._build()
        self._paint_header()
        root.title(t("Paper2Post — AI 论文推文自动生成", "Paper2Post — AI Scientific Publishing"))

    # ---------- 顶部 ----------
    def _paint_header(self):
        s = DPI_SCALE
        self.header = GradientHeader(self.root, height=int(84 * s))
        self.header.pack(fill="x", side="top")
        logo = tk.Canvas(self.header, width=int(44 * s), height=int(44 * s), bg=GRAD_TOP, highlightthickness=0)
        logo.place(x=int(26 * s), y=int(20 * s))
        _draw_round(logo, 1, 1, int(43 * s), int(43 * s), int(22 * s), fill="#ffffff", outline="")
        logo.create_text(int(22 * s), int(22 * s), text="P", fill=ACCENT, font=(FONT, 20, "bold"))
        self.header.create_text(int(84 * s), int(36 * s), text="Paper2Post", fill="#ffffff", anchor="w",
                                font=(FONT, 17, "bold"))
        self.header.create_text(int(84 * s), int(60 * s),
                                text="学术论文 → 公众号科研推文  ·  自动生成 / 事实核验 / 图表解读",
                                fill="#dbe4ff", anchor="w", font=(FONT, 11))

    def _build(self):
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)
        self.side = card(body, width=int(340 * DPI_SCALE))
        self.side.pack(side="left", fill="y", padx=(18, 0), pady=18)
        self.side.pack_propagate(False)
        accent_strip(self.side, ACCENT).pack(fill="x")
        self._build_controls()

        self.result = tk.Frame(body, bg=BG)
        self.result.pack(side="left", fill="both", expand=True, padx=18, pady=18)
        self._build_result()

    # ---------- 左控制卡 ----------
    def _build_controls(self):
        pad = {"padx": 22, "pady": (0, 4)}

        def section(t):
            row = tk.Frame(self.side, bg=SURFACE)
            row.pack(fill="x", pady=(16, 6))
            tk.Label(row, text=t, bg=SURFACE, fg=ACCENT, font=(FONT, 11, "bold")).pack(side="left")
            line = tk.Frame(row, bg=BORDER, height=1)
            line.pack(side="left", fill="x", expand=True, padx=10)

        ttk_title = tk.Label(self.side, text=t("论文与推文设置", "Paper & Generation Settings"), bg=SURFACE, fg=INK, font=(FONT, 14, "bold"))
        ttk_title.pack(anchor="w", padx=22, pady=(20, 4))

        section("① 论文 PDF")
        self.file_label = tk.Label(self.side, text=t("尚未选择 PDF", "No PDF selected"), bg=SURFACE, fg=MUTED, font=(FONT, 11), wraplength=290, justify="left")
        self.file_label.pack(anchor="w", **pad)
        self.pick_btn = Pill(self.side, t("选择论文 PDF", "Choose PDF"), self.pick_pdf, fill=SURFACE, hover=lighten(ACCENT, 0.85), fg=ACCENT, w=290, h=38, r=10, bold=False)
        self.pick_btn.pack(padx=22, pady=(4, 2))

        section("② 推文设置")
        for label, widget in self._form_fields():
            tk.Label(self.side, text=label, bg=SURFACE, fg=MUTED, font=(FONT, 11)).pack(anchor="w", **pad)
            widget.pack(fill="x", **pad)

        section("③ LLM 引擎")
        self._chat_fields()

        self.generate_btn = Pill(self.side, t("生成推文", "Generate Article"), self.start, fill=ACCENT, hover=ACCENT_DARK, w=290, h=46)
        self.generate_btn.pack(padx=22, pady=(16, 6))
        self.status = tk.Label(self.side, text=t("请选择论文", "Select a paper"), bg=SURFACE, fg=MUTED, font=(FONT, 11))
        self.status.pack(anchor="w", padx=22, pady=(0, 6))
        self.lang_btn = tk.Button(self.side, text="EN" if LANG == "zh" else "中文", command=self.toggle_lang, bg=SURFACE, fg=ACCENT, relief="flat", bd=0, cursor="hand2", font=(FONT, 10))
        self.lang_btn.pack(anchor="e", padx=22, pady=(0, 18))

    def _form_fields(self):
        self.article_type = self._combo(["deep_review", "speed", "methods", "resource"])
        self.article_type.set("deep_review")
        self.length = self._combo(["1500", "2500", "4000"])
        self.length.set("2500")
        self.audience = self._entry("biology_graduate")
        self.language = self._entry("zh-CN")
        return [("推文类型", self.article_type), ("长度（字）", self.length),
                ("目标受众", self.audience), ("语言", self.language)]

    def _chat_fields(self):
        self.provider = self._combo(["mock"] + all_provider_names())
        self.provider.set("mock")
        self.model = self._entry("")
        self.api_key = self._entry("")
        self.base_url = self._entry("")
        for lbl, w in [
            ("LLM 引擎", self.provider),
            ("模型名（可选）", self.model),
            ("API Key（可选，留空则读 .env）", self.api_key),
            ("Base URL（可选，覆盖默认）", self.base_url),
        ]:
            tk.Label(self.side, text=lbl, bg=SURFACE, fg=MUTED, font=(FONT, 11)).pack(anchor="w", padx=22, pady=(0, 4))
            w.pack(fill="x", padx=22, pady=(0, 4))

    def _combo(self, values):
        import tkinter.ttk as ttk
        w = ttk.Combobox(self.side, values=values, state="readonly", font=(FONT, 11))
        w.configure(style="Modern.TCombobox")
        return w

    def _entry(self, value):
        import tkinter.ttk as ttk
        w = ttk.Entry(self.side, font=(FONT, 11))
        if value:
            w.insert(0, value)
        w.configure(style="Modern.TEntry")
        return w

    # ---------- 结果区（胶囊页签） ----------
    def _build_result(self):
        bar = tk.Frame(self.result, bg=BG)
        bar.pack(fill="x")
        self._tabbar = bar
        # 页签
        tabs = [("article", "推文预览"), ("analysis", "论文分析"), ("story", "故事线"),
                ("figures", "图表"), ("facts", "事实核验"), ("files", "输出文件")]
        self._tab_btns = {}
        for key, label in tabs:
            btn = Pill(bar, label, lambda k=key: self._show_tab(k),
                       fill=SURFACE, hover=lighten(ACCENT, 0.88), fg=MUTED, w=110, h=34, r=17, bold=False)
            btn.pack(side="left", padx=(0, 8), pady=(0, 10))
            self._tab_btns[key] = btn

        self._pages = {}
        container = tk.Frame(self.result, bg=BG)
        container.pack(fill="both", expand=True)

        self._pages["article"] = self._text_page(container, "markdown")
        self._pages["analysis"] = self._text_page(container, "json")
        self._pages["story"] = self._text_page(container, "json")
        self._pages["facts"] = self._text_page(container, "json")
        self._pages["figures"] = self._figures_page(container)
        self._pages["files"] = self._files_page(container)
        self._show_tab("article")

    def _show_tab(self, key):
        for k, p in self._pages.items():
            if k == key:
                p.pack(fill="both", expand=True)
            else:
                p.pack_forget()
        for k, b in self._tab_btns.items():
            if k == key:
                b._fill = ACCENT
                b._fg = "#ffffff"
                b._draw(ACCENT)
            else:
                b._fill = SURFACE
                b._fg = MUTED
                b._draw(SURFACE)

    def _text_page(self, parent, kind):
        frame = card(parent)
        txt = scrolledtext.ScrolledText(frame, wrap="word", font=("Consolas", 11), bg=SURFACE, fg=INK, highlightthickness=0, bd=0)
        txt.pack(fill="both", expand=True, padx=2, pady=2)
        return frame

    def _figures_page(self, parent):
        frame = card(parent)
        return frame

    def _files_page(self, parent):
        frame = card(parent)
        self._files_text = scrolledtext.ScrolledText(frame, wrap="word", font=("Consolas", 11), bg=SURFACE, fg=INK, highlightthickness=0, bd=0)
        self._files_text.pack(fill="both", expand=True, padx=12, pady=(12, 4))
        Pill(frame, "打开输出目录", self.open_dir, fill=ACCENT, hover=ACCENT_DARK, fg="#ffffff", w=150, h=34, r=10).pack(anchor="w", padx=12, pady=(4, 12))
        return frame

    # ---------- actions ----------
    def pick_pdf(self):
        path = filedialog.askopenfilename(title="选择论文 PDF", filetypes=[("PDF", "*.pdf"), ("All", "*.*")])
        if path:
            self.pdf_path = path
            self.file_label.configure(text=os.path.basename(path))
            self._status("已选择，点击「生成推文」", "")

    def start(self):
        if not self.pdf_path or not os.path.exists(self.pdf_path):
            self._status("请先选择 PDF", "err")
            return
        self.generate_btn.set_fill(lighten(ACCENT, 0.35))
        options = {
            "article_type": self.article_type.get(),
            "article_length": int(self.length.get() or "2500"),
            "target_audience": self.audience.get().strip() or "",
            "language": self.language.get().strip() or "zh-CN",
            "style": "academic_popularization",
            "extract_figures": True,
        }
        provider = self.provider.get()
        model = self.model.get().strip()
        api_key = self.api_key.get().strip() or None
        base_url = self.base_url.get().strip() or None
        self._status("生成中，请稍候…", "")
        threading.Thread(target=self._worker, args=(self.pdf_path, options, provider, model, api_key, base_url), daemon=True).start()

    def _worker(self, pdf_path, options, provider, model, api_key, base_url):
        try:
            run_id = uuid.uuid4().hex[:8]
            run_dir = OUTPUTS_ROOT / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "paper.pdf").write_bytes(Path(pdf_path).read_bytes())

            llm = MockProvider()
            if provider != "mock":
                try:
                    llm = make_provider(self.settings, provider_name=provider, model=model or None,
                                        api_key=api_key, base_url=base_url)
                except LLMError as exc:
                    self._status("LLM 出错已回退 mock：" + str(exc)[:60], "err")
            Pipeline(settings=self.settings, llm=llm).run(str(run_dir / "paper.pdf"), output_dir=str(run_dir), options=options)
            self._status("完成（" + llm.name + "）", "")
            self.root.after(0, lambda d=str(run_dir): self._render(d))
        except Exception as exc:
            self._status("生成失败：" + str(exc)[:130], "err")
            self.root.after(0, lambda: self.generate_btn.set_fill(ACCENT))

    def _render(self, run_dir):
        rd = Path(run_dir)
        self._fill(self._pages["article"], self._read(rd / "final_article.md"))
        self._fill(self._pages["analysis"], self._read_json(rd / "paper_analysis.json"))
        self._fill(self._pages["story"], self._read_json(rd / "storyline.json"))
        self._fill(self._pages["facts"], self._read_json(rd / "fact_check.json"))

        for child in self._pages["figures"].winfo_children():
            child.destroy()
        self._photo_refs = []
        fig_dir = rd / "figures"
        figs = sorted([f for f in fig_dir.iterdir() if f.suffix.lower() in (".png", ".jpg", ".jpeg")]) if fig_dir.is_dir() else []
        if not figs:
            tk.Label(self._pages["figures"], text="未提取到图表", bg=SURFACE, fg=MUTED).pack(padx=20, pady=20)
        else:
            wrap = tk.Frame(self._pages["figures"], bg=SURFACE)
            wrap.pack(fill="both", expand=True, padx=12, pady=12)
            for f in figs:
                try:
                    img = tk.PhotoImage(file=str(f))
                    self._photo_refs.append(img)
                    tk.Label(wrap, image=img, bg=SURFACE).pack(padx=6, pady=8)
                    tk.Label(wrap, text=f.name, bg=SURFACE, fg=MUTED, font=(FONT, 11)).pack()
                except Exception:
                    tk.Label(wrap, text="（无法预览）" + f.name, bg=SURFACE, fg=MUTED).pack(padx=6, pady=8)

        self._files_text.delete("1.0", "end")
        for name in ["paper_analysis.json", "storyline.json", "figure_analysis.json", "fact_check.json",
                     "draft_article.md", "final_article.md", "final_article.html", "generation_report.md"]:
            if (rd / name).exists():
                self._files_text.insert("end", name + "\n")
        self._files_text.insert("end", "\n输出目录: " + run_dir)
        self._last_run = run_dir
        self.root.after(0, lambda: self.generate_btn.set_fill(ACCENT))

    def open_dir(self):
        if self._last_run and os.path.isdir(self._last_run):
            try:
                os.startfile(self._last_run)
            except Exception:
                pass

    def _fill(self, page, text):
        child = page.winfo_children()
        txt = None
        for w in child:
            if isinstance(w, scrolledtext.ScrolledText):
                txt = w
                break
        home = txt.winfo_children() if txt else None
        if txt:
            txt.delete("1.0", "end")
            txt.insert("1.0", text or "")

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _read_json(self, path: Path) -> str:
        txt = self._read(path)
        try:
            return json.dumps(json.loads(txt), ensure_ascii=False, indent=2)
        except Exception:
            return txt

    def _walk(self, root):
        for w in root.winfo_children():
            yield w
            for x in self._walk(w):
                yield x

    def _apply_lang(self):
        rev = {v: k for k, v in UI_MAP.items()}
        for w in self._walk(self.root):
            if hasattr(w, "set_text"):
                cur = getattr(w, "_text", "")
                if LANG == "en" and cur in UI_MAP:
                    w.set_text(UI_MAP[cur])
                elif LANG == "zh" and cur in rev:
                    w.set_text(rev[cur])
            elif isinstance(w, tk.Label):
                cur = w.cget("text")
                if LANG == "en" and cur in UI_MAP:
                    w.config(text=UI_MAP[cur])
                elif LANG == "zh" and cur in rev:
                    w.config(text=rev[cur])
        self.root.title(t("Paper2Post — AI 论文推文自动生成", "Paper2Post — AI Scientific Publishing"))
        if hasattr(self, "lang_btn"):
            self.lang_btn.config(text="EN" if LANG == "zh" else "中文")

    def toggle_lang(self):
        global LANG
        LANG = "en" if LANG == "zh" else "zh"
        self._apply_lang()

    def _status(self, msg, kind):
        self.root.after(0, lambda: self.status.configure(text=msg, fg=ERROR if kind == "err" else SUCCESS))


def run_app():
    _enable_dpi()
    root = tk.Tk()
    _compute_dpi(root)
    import tkinter.ttk as ttk
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.configure("Modern.TCombobox", fieldbackground=SURFACE, background=SURFACE, foreground=INK,
                 borderwidth=0, arrowsize=int(14 * DPI_SCALE), padding=int(7 * DPI_SCALE))
    st.configure("Modern.TEntry", fieldbackground=SURFACE, foreground=INK, borderwidth=0, padding=int(7 * DPI_SCALE))
    Paper2PostApp(root)
    root.mainloop()


if __name__ == "__main__":
    run_app()
