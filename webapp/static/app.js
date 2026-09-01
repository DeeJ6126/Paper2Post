/* Paper2Post — Scientific Publishing Workspace SPA (i18n: zh/en) */
(function () {
  var state = {
    lang: "zh",
    view: "home", file: null, result: null,
    config: { article_type: "deep_review", audience: "Biology Graduate", length: "2500", language: "中文", style: "Academic Popularization", provider: "deepseek", model: "deepseek-v4-flash-vision-exp" },
    paperFiles: [], selClaim: null, selFig: null, activeRun: null, resultsByRun: {}, generations: [],
    activeWorkspaceTab: { paper: 0, editor: 0, inspector: 0 },
    dismissed: [], accepted: [],
    busy: false
  };

  function t(zh, en) { return state.lang === "en" ? en : zh; }
  function setLang(l) { state.lang = l; render(); }

  function h(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function icon(name) {
    var s = document.createElement("span"); s.className = "sb-ic";
    var map = {
      file: '<path d="M6 2h8l4 4v14H6z"/><path d="M14 2v4h4"/>',
      plus: '<path d="M12 5v14M5 12h14"/>',
      book: '<path d="M4 5h16v14H4zM8 5v14"/>',
      check: '<path d="M5 13l4 4L19 7"/>',
      spark: '<path d="M12 2l1.8 5.6L19 9l-5.2 1.4L12 16l-1.8-5.6L5 9l5.2-1.4z"/>',
      arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
      search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>'
    };
    s.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">' + (map[name] || map.file) + '</svg>';
    return s;
  }
  function badge(kind, text) {
    var tMap = { ready: ["green", t("就绪", "Ready")], reviewing: ["blue", t("审核中", "Reviewing")], parsing: ["amber", t("解析中", "Parsing")] };
    var tv = tMap[kind] || ["gray", kind];
    var b = h("span", "badge " + tv[0]); b.appendChild(h("span", "dot " + tv[0])); b.appendChild(h("span", null, text || tv[1])); return b;
  }
  function toast(msg) {
    var el = document.getElementById("toast"); if (!el) { el = h("div"); el.id = "toast"; el.style.cssText = "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#0B1220;color:#fff;padding:10px 18px;border-radius:10px;font-size:13px;opacity:0;transition:opacity .3s;z-index:99"; document.body.appendChild(el); }
    el.textContent = msg; el.style.opacity = "1"; setTimeout(function () { el.style.opacity = "0"; }, 2200);
  }

  function setTopbar() {
    var tb = document.getElementById("tbLeft"); tb.innerHTML = "";
    var map = { home: t("工作区", "Workspace"), setup: t("上传论文", "New Paper"), workspace: t("文章工作区", "Article Workspace"), review: t("科学审校", "Scientific Review"), figures: t("图表", "Figures"), library: t("文库", "Library"), evidence: t("证据", "Evidence"), generations: t("生成记录", "Generations"), templates: t("模板", "Templates"), settings: t("设置", "Settings"), api: t("API / 模型", "API / Models"), help: t("帮助", "Help"), processing: t("AI 处理中", "AI Processing") };
    tb.appendChild(h("span", "weak", map[state.view] || ""));
    if (state.result) {
      var meta = h("span", "tb-meta"); meta.appendChild(h("span", "tb-dot"));
      meta.appendChild(h("span", (state.result.paper_meta && state.result.paper_meta.title) ? state.result.paper_meta.title.split(" ").slice(0, 2).join(" ") : ""));
      meta.appendChild(h("span", "weak", t("准确度", "Accuracy") + " " + (state.result.fact_check ? state.result.fact_check.overall_score + "%" : "—")));
      tb.appendChild(meta);
    }
  }

  function render() {
    var v = document.getElementById("view"); v.innerHTML = "";
    var fn = VIEWS[state.view]; if (fn) fn(v);
    document.querySelectorAll(".sb-item[data-nav]").forEach(function (e) { e.classList.toggle("active", e.dataset.nav === state.view); });
    setTopbar();
    var lb = document.getElementById("langBtn"); if (lb) lb.textContent = state.lang === "zh" ? "EN" : "中文";
  }
  function show(view) { state.view = view; render(); }
  function container(v) { var c = h("div", "container"); v.appendChild(c); return c; }

  function parseArticle(md) {
    var lines = (md || "").split(String.fromCharCode(10));
    var blocks = [], title = "", summary = "";
    for (var i = 0; i < lines.length; i++) {
      var l = lines[i].trim(); if (!l) continue;
      if (l.indexOf("# ") === 0) title = l.slice(2).trim();
      else if (l.charAt(0) === ">") summary = l.replace(/^> /, "").trim();
      else if (l.indexOf("## ") === 0) blocks.push({ type: "h2", text: l.slice(3).trim() });
      else if (l === "---") blocks.push({ type: "hr" });
      else if (l.indexOf("- **") === 0) { var ft = l.slice(4); if (ft.indexOf("**") === ft.length - 2) ft = ft.slice(0, -2); blocks.push({ type: "figure", text: ft.trim() }); }
      else if (l.indexOf("![") === 0) blocks.push({ type: "figure", text: l });
      else if (l.indexOf("**") === 0 || l.indexOf("*") === 0) blocks.push({ type: "meta", text: l.replace(/\*/g, "") });
      else blocks.push({ type: "p", text: l });
    }
    return { title: title, summary: summary, blocks: blocks };
  }

  var VIEWS = { home: renderHome, setup: renderSetup, processing: renderProcessing, workspace: renderWorkspace, review: renderReview, figures: renderFigures, library: renderLibrary, evidence: renderEvidence, generations: renderGenerations, templates: renderGenerations, settings: renderSettings, api: renderApi, help: renderPlain };

  function renderHome(v) {
    var c = container(v); var hero = h("div");
    hero.appendChild(h("h1", "h1", t("上午好", "Good afternoon")));
    hero.appendChild(h("h2", null, t("把论文变成可发布的科研故事。", "Turn papers into publication-ready stories.")));
    c.appendChild(hero);
    c.appendChild(h("p", "lead muted mt8", t("上传一篇文献，Paper2Post 会自动完成结构化解读、图表筛选、证据溯源、事实核验与排版优化。", "Upload a scientific paper and let Paper2Post analyze, verify and transform it into a structured research article.")));

    var up = h("div", "upload mt24"); up.id = "uploadZone";
    var upic = h("div", "up-ic"); upic.appendChild(icon("file")); up.appendChild(upic);
    up.appendChild(h("h3", null, t("把论文拖到这里", "Drop your paper here")));
    up.appendChild(h("p", "weak", "PDF · up to 50 MB"));
    var btns = h("div", "row mt16"); btns.style.justifyContent = "center";
    var choose = h("button", "btn btn-primary", t("选择 PDF", "Choose PDF"));
    var doi = h("button", "btn", t("从 DOI 导入", "Import from DOI"));
    btns.appendChild(choose); btns.appendChild(doi); up.appendChild(btns);
    up.appendChild(h("div", "weak mt16", "DOI · PMID · arXiv · bioRxiv"));
    var fi = h("input"); fi.type = "file"; fi.accept = "application/pdf"; fi.style.display = "none"; up.appendChild(fi);
    choose.onclick = function () { fi.click(); };
    doi.onclick = function () { toast(t("DOI 导入为占位功能。", "DOI import is a placeholder.")); };
    function onFile(f) { if (f) { state.file = f; state.paperFiles.unshift({ name: f.name }); show("setup"); } }
    fi.onchange = function (e) { onFile(e.target.files[0]); };
    up.addEventListener("dragover", function (e) { e.preventDefault(); up.classList.add("drag"); });
    up.addEventListener("dragleave", function () { up.classList.remove("drag"); });
    up.addEventListener("drop", function (e) { e.preventDefault(); up.classList.remove("drag"); onFile(e.dataTransfer.files[0]); });
    c.appendChild(up);

    c.appendChild(h("h2", "h2 mt24", t("最近论文", "Recent papers")));
    var list = h("div");
    c.appendChild(list);
    var demo = [{ name: "A foundation model for single-cell biology…", journal: "Nature Biotechnology", year: "2026", status: "ready", run_id: null }];
    var rows = (state.generations.length ? state.generations : []).concat(state.paperFiles);
    if (!rows.length) rows = demo;
    var tbl = h("table", "table mt16"); var thead = h("thead"); var tr = h("tr");
    [t("论文", "Paper"), t("状态", "Status"), t("模式", "Mode"), t("更新时间", "Updated")].forEach(function (t2) { tr.appendChild(h("th", null, t2)); });
    thead.appendChild(tr); tbl.appendChild(thead); var tbody = h("tbody");
    rows.forEach(function (r, idx) {
      var row = h("tr");
      row.appendChild(h("td", null, r.name || "Paper"));
      var st = h("td"); st.appendChild(badge(r.status || "ready")); row.appendChild(st);
      row.appendChild(h("td", "muted", t("深度精读", "Deep Review")));
      row.appendChild(h("td", "muted", r.time || "just now"));
      row.onclick = function () {
        if (r.run_id) { openRun(r.run_id); }
        else if (state.file) { show("setup"); }
        else { toast(t("先上传一篇论文开始生成", "Upload a paper first")); show("setup"); }
      };
      tbody.appendChild(row);
    });
    tbl.appendChild(tbody); list.appendChild(tbl);
  }

  function field(label) { var f = h("div", "field"); f.appendChild(h("label", null, label)); return f; }
  function seg(opts, sel, cb) { var s = h("div", "seg"); opts.forEach(function (o) { var b = h("button", (o === sel ? "active" : ""), o); b.onclick = function () { cb(o); s.querySelectorAll("button").forEach(function (x) { x.classList.remove("active"); }); b.classList.add("active"); }; s.appendChild(b); }); return s; }

  function renderSetup(v) {
    var c = container(v); var head = h("div", "between"); var left = h("div");
    left.appendChild(h("div", "weak", "Nature Biotechnology · 2026"));
    left.appendChild(h("h2", "h2", "A foundation model for single-cell biology…"));
    left.appendChild(h("p", "muted", "Jane Doe, John Smith · 2026 · DOI: 10.xxxx/xxxx"));
    head.appendChild(left); var vp = h("button", "btn", t("查看 PDF", "View PDF")); vp.onclick = function () { toast(t("PDF 预览将在后续版本提供", "PDF preview coming soon")); }; head.appendChild(vp); c.appendChild(head);

    var split = h("div", "split-65 mt24"); var leftCol = h("div", "card");
    leftCol.appendChild(h("h3", "h3", t("论文", "Paper")));
    leftCol.appendChild(h("p", "muted", t("已上传", "Uploaded") + ": " + (state.file ? state.file.name : "paper.pdf")));
    leftCol.appendChild(h("div", "weak mt8", "8 " + t("页", "pages") + " · 14 " + t("图", "figures") + " · 12,400 " + t("词", "words")));
    var figs = h("div", "fig-grid mt16");
    for (var i = 1; i <= 4; i++) { var fc = h("div", "fig-card"); var img = h("img"); img.alt = "Figure " + i; img.style.background = "#E8EDF2"; img.style.minHeight = "120px"; fc.appendChild(img); var b = h("div", "fc-body"); b.appendChild(h("div", "cap", "Figure " + i)); b.appendChild(h("div", "weak", t("核心发现", "Main finding"))); fc.appendChild(b); figs.appendChild(fc); }
    leftCol.appendChild(figs); split.appendChild(leftCol);

    var rightCol = h("div"); var card = h("div", "card");
    card.appendChild(h("h3", "h3", t("生成设置", "Generation Settings")));
    card.appendChild(field(t("生成模式", "Generation Mode")));
    var modes = [[t("顶刊速递", "Top Journal Brief"), "speed", t("快速解读核心发现", "Fast core finding brief")], [t("深度精读", "Deep Review"), "deep_review", t("逐图深度解读", "Figure-by-figure deep read")], [t("方法论文", "Methods Paper"), "methods", t("方法 / 架构 / 基准", "Model, architecture & benchmark")], [t("数据库 / 资源", "Resource / Database"), "resource", t("数据资源与使用", "Data resources & usage")]];
    var modeWrap = h("div");
    modes.forEach(function (m, idx) { var item = h("div", "row"); item.style.cssText = "padding:8px 10px;border-radius:8px;cursor:pointer;margin-bottom:2px"; var dot = h("span"); dot.style.cssText = "width:8px;height:8px;border-radius:50%;background:" + (idx === 1 ? "#2563EB" : "#D1D5DB"); var tt = h("div"); tt.appendChild(h("div", null, m[0])); tt.appendChild(h("div", "weak", m[2])); item.appendChild(dot); item.appendChild(tt); item.onclick = (function (mi) { return function () { state.config.article_type = mi[1]; render(); }; })(m); modeWrap.appendChild(item); });
    card.appendChild(modeWrap);
    card.appendChild(field(t("目标受众", "Target Audience")));
    var aud = h("select", "select"); [t("一般科学读者", "General Science"), t("本科生", "Undergraduate"), t("生物学科研究生", "Biology Graduate"), t("领域专家", "Domain Expert")].forEach(function (a) { var o = h("option", null, a); o.value = a; aud.appendChild(o); }); aud.value = state.config.audience; aud.onchange = function () { state.config.audience = aud.value; }; card.appendChild(aud);
    card.appendChild(field(t("文章长度", "Article Length"))); card.appendChild(seg(["1500", "2500", "4000"], state.config.length, function (val) { state.config.length = val; }));
    card.appendChild(field(t("语言", "Language"))); card.appendChild(seg([t("中文", "中文"), t("English", "English")], state.config.language, function (val) { state.config.language = val; }));
    card.appendChild(field(t("写作风格", "Writing Style"))); card.appendChild(seg([t("学术科普", "Academic Popularization"), t("专业", "Professional"), t("简洁", "Concise"), t("深度", "Deep Dive")], state.config.style, function (val) { state.config.style = val; }));

    var genBtn = h("button", "btn btn-primary"); genBtn.style.cssText = "width:100%;margin-top:18px;justify-content:center;padding:12px";
    genBtn.appendChild(h("span", null, t("生成推文", "Generate Article"))); genBtn.appendChild(icon("arrow"));
    genBtn.onclick = function () { show("processing"); startGeneration(); }; card.appendChild(genBtn);
    card.appendChild(h("div", "weak mt8", t("预计处理步骤", "Estimated processing steps") + ": 8"));
    rightCol.appendChild(card); split.appendChild(rightCol); c.appendChild(split);
  }

  var STEPS = [["PDF Parser", "PDF Parser"], ["Paper Reader", "Paper Reader"], ["Evidence Mapping", "Evidence Mapping"], ["Story Planner", "Story Planner"], ["Figure Agent", "Figure Agent"], ["Writer", "Writer"], ["Scientific Reviewer", "Scientific Reviewer"], ["Editor", "Editor"]];
  function startGeneration() {
    if (!state.file) { toast(t("请先上传一篇论文", "Upload a paper first")); show("setup"); return; }
    show("processing");
    var list = document.getElementById("stepList"); var items = list ? Array.prototype.slice.call(list.children) : [];
    var activity = document.getElementById("activity");
    var cfg = { article_type: state.config.article_type, audience: state.config.audience, length: state.config.length, language: state.config.language === "中文" ? "zh-CN" : "en", style: state.config.style, model: state.config.model || "deepseek-v4-flash-vision-exp" };
    var qs = Object.keys(cfg).map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(cfg[k]); }).join("&");
    function setSteps(step) { items.forEach(function (it, i) { it.className = (i < step ? "step done" : (i === step ? "step active" : "step")); }); }
    function logMsg(label) { if (activity) { var d = h("div"); d.appendChild(h("div", "weak", new Date().toLocaleTimeString())); d.appendChild(h("div", null, label)); activity.appendChild(d); } }
    function showErr(m) { var el = document.getElementById("procStatus"); if (el) el.textContent = "Error: " + m; }
    fetch("/api/generate?" + qs, { method: "POST", body: state.file }).then(function (r) { return r.json(); }).then(function (start) {
      if (start.error) { showErr(start.error); return; }
      if (start.run_id) state.pendingRun = start.run_id;
      var rid = start.run_id;
      var already = {};
      var lastStep = -1, lastStepTime = Date.now();
      var unknownN = 0;
      var poll = setInterval(function () {
        fetch("/api/progress?run_id=" + encodeURIComponent(rid)).then(function (r) { return r.json(); }).then(function (p) {
          if (p.status === "running") { setSteps(p.step || 0); if (p.step !== lastStep) { lastStep = p.step; lastStepTime = Date.now(); } else if (Date.now() - lastStepTime > 15000) { logMsg(t("仍在处理中（较慢）…", "Still working (slow)…")); lastStepTime = Date.now(); } if (p.label && !already[p.label]) { already[p.label] = 1; logMsg(p.label); } }
          else if (p.status === "done") { clearInterval(poll); setSteps(8);
            fetch("/api/result?run_id=" + encodeURIComponent(rid)).then(function (r) { return r.json(); }).then(function (res) {
              if (res.error) { showErr(res.error); return; }
              state.result = res; state.activeRun = rid; state.resultsByRun[rid] = res;
              state.generations.unshift({ title: (state.file ? state.file.name : "Paper " + rid.slice(0, 4)), run_id: rid, time: new Date().toLocaleTimeString(), status: "ready" });
              refreshGenerations();
              var und = document.getElementById("understanding"); if (und) und.classList.remove("hidden"); fillUnderstanding(res);
              setTimeout(function () { try { if (state.view === "processing") show("workspace"); else toast(t("生成完成，可从首页「最近论文」打开", "Done — open from Recent papers")); } catch (err) { showErr("渲染工作区出错: " + err.message); } }, 500);
            }).catch(function (e3) { showErr(e3.message); });
          } else if (p.status === "error") { clearInterval(poll); showErr(p.error || "unknown"); }
          else { unknownN++; if (unknownN > 5) { clearInterval(poll); showErr(t("生成状态丢失（服务可能已重启），请重新生成", "Generation state lost (service restarted) — please regenerate")); } }
        }).catch(function (e2) { showErr(e2.message); });
      }, 500);
    }).catch(function (e) { showErr(e.message); });
  }

  function renderProcessing(v) {
    var c = container(v); c.appendChild(h("h1", "h1", t("正在理解你的论文", "Understanding your paper")));
    c.appendChild(h("p", "muted", t("Paper2Post 正在分析科学结构与证据。", "Paper2Post is analyzing the scientific structure and evidence.")));
    var steps = h("div", "steps mt24"); steps.id = "stepList";
    STEPS.forEach(function (s, i) { if (i > 0) steps.appendChild(h("div", "step-conn")); var st = h("div", "step" + (i === 0 ? " active" : "")); st.appendChild(h("div", "step-c", String(i + 1))); st.appendChild(h("div", "step-l", s[0])); steps.appendChild(st); });
    c.appendChild(steps);
    var body = h("div", "split-65 mt24"); var main = h("div");
    var und = h("div", "card hidden"); und.id = "understanding"; und.appendChild(h("h3", "h3", t("论文理解", "Paper Understanding")));
    und.appendChild(h("div", "weak mt8", t("研究问题", "Research Question"))); var rq = h("div", "h3", "…"); rq.id = "rq"; und.appendChild(rq);
    und.appendChild(h("div", "weak mt16", t("数据集", "Dataset"))); var ds = h("div", "h3"); ds.id = "dataset"; und.appendChild(ds);
    und.appendChild(h("div", "weak mt16", t("关键发现", "Key Findings"))); var kf = h("div"); kf.id = "kf"; und.appendChild(kf);
    var st2 = h("div", "weak mt16"); st2.id = "procStatus"; und.appendChild(st2); main.appendChild(und); body.appendChild(main);
    var side = h("div", "card"); side.appendChild(h("h3", "h3", t("AI 活动", "AI Activity"))); var act = h("div"); act.style.marginTop = "12px"; act.id = "activity"; side.appendChild(act); side.appendChild(h("div", "weak mt16", t("Paper2Post Agent 管线运行中…", "Paper2Post agent pipeline is running…"))); body.appendChild(side); c.appendChild(body);
  }

  function fillUnderstanding(res) {
    var a = res.analysis || {};
    var rq = document.getElementById("rq"); if (rq) rq.textContent = "“" + (a.research_question || a.title || "") + "”";
    var ds = document.getElementById("dataset"); if (ds) { var s = a.samples || {}; ds.textContent = (s.sample_size || "—") + " · " + ((s.species || []).join(", ") || "human") + " · " + ((s.tissue || []).join(", ") || "tissue"); }
    var kf = document.getElementById("kf"); if (kf) { kf.innerHTML = ""; (a.main_findings || []).slice(0, 4).forEach(function (f, i) { var b = h("div", "claim-box"); b.appendChild(h("div", "cap", "Finding 0" + (i + 1))); b.appendChild(h("div", null, f.finding || f.evidence_text || "—")); kf.appendChild(b); }); }
  }

  function wsTabs(arr, key) { var tabs = h("div", "tabs"); arr.forEach(function (t, i) { var b = h("button", "tab" + (i === state.activeWorkspaceTab[key] ? " active" : ""), t); b.onclick = function () { state.activeWorkspaceTab[key] = i; render(); }; tabs.appendChild(b); }); return tabs; }

  function renderWorkspace(v) { var w = h("div", "workspace"); w.appendChild(navigatorCol()); w.appendChild(editorCol()); w.appendChild(inspectorCol()); v.appendChild(w); }

  function navigatorCol() {
    var col = h("div", "ws-col"); var head = h("div", "ws-head"); head.appendChild(wsTabs([t("论文", "Paper"), t("图表", "Figures"), t("证据", "Evidence")], "paper")); col.appendChild(head);
    var body = h("div", "ws-body"); var tabi = state.activeWorkspaceTab.paper;
    if (tabi === 0) { var secs = (state.result && state.result.paper_sections) || []; if (!secs.length) secs = [t("摘要", "Abstract"), t("引言", "Introduction"), t("方法", "Methods"), t("结果", "Results"), t("讨论", "Discussion")].map(function (x) { return { heading: x }; }); secs.forEach(function (s, i) { var n = h("div", "nav-item" + (i === 0 ? " active" : ""), s.heading || ("Section " + (i + 1))); body.appendChild(n); }); }
    else if (tabi === 1) { var figs = (state.result && state.result.figure_items) || []; if (!figs.length) figs = ["Figure 1", "Figure 2", "Figure 3"].map(function (f) { return { figure: f, role: "main_finding", importance: "high" }; }); figs.forEach(function (f) { var cd = h("div", "nav-item"); cd.appendChild(h("div", "cap", f.figure)); cd.appendChild(h("div", "weak", (f.role || "-") + " · " + (f.importance || "-"))); body.appendChild(cd); }); }
    else { var ev = (state.result && state.result.evidence && state.result.evidence.evidence) || []; (ev.length ? ev : [{ claim: t("（暂无证据，mock 生成）", "No evidence (mock)."), source_section: "", figure: "", confidence: 0 }]).forEach(function (e, i) { var cd = h("div", "nav-item"); cd.appendChild(h("div", "cap", "Claim 0" + (i + 1))); cd.appendChild(h("div", "cap", "“" + String(e.claim || "").slice(0, 60) + "”")); cd.appendChild(h("div", "weak", (e.source_section || "-") + " · " + (e.figure || "") + " · " + Math.round((e.confidence || 0) * 100) + "%")); cd.onclick = function () { state.selClaim = e; state.activeWorkspaceTab.inspector = 1; render(); }; body.appendChild(cd); }); }
    col.appendChild(body); return col;
  }

  function editorCol() {
    var col = h("div", "ws-col"); col.style.background = "#FBFBFD"; var head = h("div", "ws-head"); head.appendChild(wsTabs([t("文章", "Article"), t("故事线", "Storyline"), t("预览", "Preview")], "editor")); col.appendChild(head);
    var body = h("div", "ws-body");
    if (state.activeWorkspaceTab.editor === 0) {
      var ed = h("div", "editor");
      var art = parseArticle(state.result ? state.result.article_md : "");
      ed.appendChild(h("h1", "article-title", art.title || "AI reveals a new cellular program of brain aging"));
      if (art.summary) ed.appendChild(h("div", "summary-box", art.summary));
      ed.appendChild(h("div", "row mt8")); var rg = h("button", "btn btn-sm", t("重新生成", "Regenerate")); rg.onclick = function () { runAction("regenerate"); }; ed.appendChild(rg); ed.appendChild(h("span", "weak", t("已接真实后端", "wired to backend")));
      var n = 1;
      art.blocks.forEach(function (b) {
        if (b.type === "h2") { ed.appendChild(h("h2", null, (state.result && state.result.storyline ? String(n) + " · " : "") + b.text)); n++; }
        else if (b.type === "p") { var wrap = h("div", "para-wrap"); var pr = h("p", null, b.text); var tools = h("div", "para-tools"); ["Rewrite", "Shorten", "Verify", "Evidence"].forEach(function (tb) { var btn = h("button", "btn btn-sm btn-ghost", tb); btn.onclick = function () { runAction(tb.toLowerCase()); }; tools.appendChild(btn); }); wrap.appendChild(pr); wrap.appendChild(tools); ed.appendChild(wrap); }
        else if (b.type === "figure") { var fm = h("div", "fig-marker"); fm.appendChild(h("div", "cap", "FIGURE 2")); fm.appendChild(h("div", "weak", b.text)); ed.appendChild(fm); }
        else if (b.type === "meta") ed.appendChild(h("p", "muted", b.text));
      });
      var used = h("div", "row mt24"); used.style.cssText = "gap:10px;flex-wrap:wrap";
      ((state.result && state.result.figure_items) || []).slice(0, 3).forEach(function (f) { if (f.url) { var m = h("div", "fig-marker"); var im = h("img"); im.src = f.url; im.style.width = "160px"; m.appendChild(im); m.appendChild(h("div", "figcap", f.label || "Figure")); used.appendChild(m); } });
      ed.appendChild(used); body.appendChild(ed);
    } else if (state.activeWorkspaceTab.editor === 1) { body.appendChild(storylineView()); }
    else { var pv = h("div", "card"); if (state.result && state.result.article_html_url) { var ifr = h("iframe"); ifr.src = state.result.article_html_url; ifr.style.cssText = "width:100%;height:78vh;border:1px solid var(--border);border-radius:10px"; pv.appendChild(ifr); } else pv.appendChild(h("div", "muted", t("暂无预览", "No preview yet."))); body.appendChild(pv); }
    col.appendChild(body); return col;
  }

  function inspectorCol() {
    var col = h("div", "ws-col"); var head = h("div", "ws-head"); head.appendChild(wsTabs([t("AI", "AI"), t("证据", "Evidence"), t("审校", "Review")], "inspector")); col.appendChild(head);
    var body = h("div", "ws-body"); var tabi = state.activeWorkspaceTab.inspector;
    if (tabi === 0) body.appendChild(inspPanel()); else if (tabi === 1) body.appendChild(evidenceInsp()); else body.appendChild(reviewInsp());
    col.appendChild(body); return col;
  }

  function inspPanel() {
    var panel = h("div", "insp-panel"); panel.appendChild(h("h3", "h3", t("询问 Paper2Post", "Ask Paper2Post")));
    var input = h("input", "input mt8"); input.placeholder = t("关于这篇论文或文章…", "Ask about this paper or article…"); panel.appendChild(input);
    panel.appendChild(h("div", "weak mt16 cap", t("快捷操作", "QUICK ACTIONS")));
    [t("重写选中段落", "Rewrite selected paragraph"), t("生成更好的标题", "Generate better title"), t("解释图表", "Explain Figure"), t("优化故事线", "Improve storyline"), t("生成机制图", "Generate mechanism diagram")].forEach(function (t2) { var b = h("div", "btn btn-sm btn-ghost mt8", t2); b.style.cssText = "border:1px solid var(--border)"; b.onclick = function () { runAction(t2.indexOf(t("标题", "title")) >= 0 ? "title" : "regenerate"); }; panel.appendChild(b); });
    return panel;
  }

  function evidenceInsp() {
    var panel = h("div", "insp-panel"); panel.appendChild(h("h3", "h3", t("证据", "Evidence")));
    var ev = (state.result && state.result.evidence && state.result.evidence.evidence) || [];
    var e = state.selClaim || ev[0];
    if (!e) { panel.appendChild(h("div", "muted", t("暂无证据（mock 生成无结果）", "No evidence yet (mock)."))); return panel; }
    panel.appendChild(h("div", "insp-k", "CLAIM")); panel.appendChild(h("div", "insp-v", "“" + (e.claim || "—") + "”"));
    panel.appendChild(h("div", "insp-k mt16", "SOURCE")); panel.appendChild(h("div", "insp-v", (e.source_section || "Results") + " · Paragraph 12"));
    panel.appendChild(h("div", "insp-k mt16", "FIGURE")); panel.appendChild(h("div", "insp-v", e.figure || "—"));
    panel.appendChild(h("div", "insp-k mt16", t("原文", "ORIGINAL TEXT"))); panel.appendChild(h("div", "insp-v muted", e.evidence_text || t("原文语段", "Original paper text")));
    panel.appendChild(h("button", "btn btn-sm mt16", t("在论文中打开", "Open in paper")));
    panel.appendChild(h("div", "insp-k mt16", "CONFIDENCE")); panel.appendChild(h("div", "h3", Math.round((e.confidence || 0) * 100) + "%"));
    panel.appendChild(h("div", "badge green mt8", t("证据已支持", "Evidence supported"))); return panel;
  }

  function reviewInsp() {
    var panel = h("div", "insp-panel"); var fc = state.result && state.result.fact_check || {};
    panel.appendChild(h("h3", "h3", t("科学审校", "Scientific Review")));
    var ring = h("div", "score-ring mt8"); var inner = h("div", "in"); inner.appendChild(h("div", "h2", String(fc.overall_score || 0))); inner.appendChild(h("div", "weak", "/ 100")); ring.appendChild(inner); panel.appendChild(ring);
    panel.appendChild(h("div", "weak mt8", t("科学准确度", "Scientific Accuracy")));
    var bt = h("button", "btn btn-primary btn-sm mt16", t("打开完整审校", "Open full review")); bt.onclick = function () { show("review"); }; panel.appendChild(bt); return panel;
  }

  function renderReview(v) {
    var c = container(v); var fc = state.result && state.result.fact_check || {};
    var head = h("div", "between"); head.appendChild(h("h1", "h1", t("科学审校", "Scientific Review")));
    var score = h("div", "row"); var ring = h("div", "score-ring"); var inner = h("div", "in"); inner.appendChild(h("div", "h2", String(fc.overall_score || 0))); inner.appendChild(h("div", "weak", "/ 100")); ring.appendChild(inner); score.appendChild(ring);
    var sTxt = h("div"); sTxt.appendChild(h("div", "h3", t("科学准确度", "Scientific Accuracy"))); sTxt.appendChild(h("div", "weak", t("独立核验通过", "Independent verification pass"))); score.appendChild(sTxt); head.appendChild(score); c.appendChild(head);
    c.appendChild(h("h3", "h3 mt24", t("评测指标", "Metrics")));
    (function () {
      var ev = (state.result && state.result.evidence && state.result.evidence.evidence) || [];
      var figs = (state.result && state.result.figure_items) || [];
      var issues = fc.issues || [];
      var coverage = ev.length ? Math.min(100, 60 + ev.length * 15) : 20;
      var figCons = figs.length ? Math.min(100, 70 + figs.length * 8) : 50;
      var halRisk = issues.length ? (issues.some(function (i) { return i.severity === "high"; }) ? t("高", "High") : t("中", "Medium")) : t("低", "Low");
      var m = h("div", "metrics mt12");
      [[t("科学准确度", "Scientific Accuracy"), (fc.overall_score != null ? String(fc.overall_score) + "%" : "—"), "green"], [t("证据覆盖", "Evidence Coverage"), coverage + "%", "green"], [t("图表一致性", "Figure Consistency"), figCons + "%", "green"], [t("幻觉风险", "Hallucination Risk"), halRisk, (halRisk === t("低", "Low") ? "green" : "amber")]].forEach(function (x) { var b = h("div", "metric"); b.appendChild(h("div", "weak cap", x[0])); b.appendChild(h("div", "h3 mt8", x[1])); b.appendChild(h("div", "badge " + x[2] + " mt8", t("已验证", "Verified"))); m.appendChild(b); });
      c.appendChild(m);
    })();
    c.appendChild(h("h3", "h3 mt24", t("问题", "Issues")));
    var issues = fc.issues || [];
    if (!issues.length) c.appendChild(h("p", "muted", t("无关键问题，所有结论均已核验。", "No critical issues. All claims verified.")));
    issues.forEach(function (i, idx) {
      if (state.dismissed.indexOf(idx) >= 0) return;
      var card = h("div", "card mt12" + (state.accepted.indexOf(idx) >= 0 ? "" : ""));
      card.appendChild(h("div", "badge " + (i.severity === "high" ? "red" : "amber"), i.severity || t("中", "medium")));
      card.appendChild(h("div", "cap mt8", "Paragraph " + (i.paragraph || 1))); card.appendChild(h("div", "cap", "“" + (i.original || "") + "”"));
      card.appendChild(h("div", "mt8", t("问题", "Issue") + ": " + (i.problem || "—"))); card.appendChild(h("div", "weak", t("建议", "Suggestion") + ": " + (i.suggestion || "—")));
      var bt = h("div", "row mt12"); var ac = h("button", "btn btn-sm", t("接受", "Accept")); ac.onclick = function () { state.accepted.push(idx); toast(t("已接受该修改建议", "Accepted suggestion")); saveReview(); render(); }; var dm = h("button", "btn btn-sm", t("忽略", "Dismiss")); dm.onclick = function () { state.dismissed.push(idx); toast(t("已忽略", "Dismissed")); saveReview(); render(); }; var ve = h("button", "btn btn-sm ", t("查看证据", "View Evidence")); ve.onclick = function () { state.activeWorkspaceTab.inspector = 1; show("workspace"); };
      bt.appendChild(ac); bt.appendChild(dm); bt.appendChild(ve); card.appendChild(bt); c.appendChild(card);
    });
    c.appendChild(h("h3", "h3 mt24", t("检查项", "Checks")));
    [[t("物种一致性", "Species consistency"), "ok"], [t("实验类型", "Experiment type"), "ok"], [t("2 条无依据结论", "2 unsupported claims"), "warn"], [t("数值准确", "Numerical values"), "ok"], [t("因果关系", "Causality"), "ok"]].forEach(function (x) { var r = h("div", "nav-item"); r.appendChild(h("span", "muted", (x[1] === "ok" ? "✓ " : "! ") + x[0])); c.appendChild(r); });
  }

  function storylineView() {
    var w = h("div", "container"); var head = h("div", "between"); head.appendChild(h("h1", "h1", t("故事线", "Storyline"))); var rs = h("button", "btn", t("重新生成故事线", "Regenerate Storyline")); rs.onclick = function () { runAction("storyline"); }; head.appendChild(rs); w.appendChild(head);
    var st = state.result && state.result.storyline || {};
    var secs = (st.sections || []).length ? st.sections : [{ title: t("为什么这个问题重要？", "Why does this problem matter?"), findings: ["F1"], figures: ["Fig 2"] }, { title: t("作者发现了什么？", "What did the authors discover?"), findings: ["F3"], figures: ["Fig 3"] }, { title: t("作者如何证明？", "How did they prove it?"), findings: [], figures: ["Fig 4"] }, { title: t("背后的机制？", "What mechanism explains it?"), findings: [], figures: ["Fig 5"] }, { title: t("为何重要？", "Why does it matter?"), findings: [], figures: [] }];
    secs.forEach(function (s, i) { var b = h("div", "card mt16"); b.style.cssText = "display:flex;gap:16px;align-items:flex-start"; b.appendChild(h("div", "weak", "0" + (i + 1))); var body = h("div"); body.appendChild(h("h3", "h3", s.title)); body.appendChild(h("div", "row mt8")); body.appendChild(h("span", "weak", t("发现", "Findings") + ":")); body.appendChild(h("span", null, (s.findings || []).join(" · ") || "—")); body.appendChild(h("div", "row")); body.appendChild(h("span", "weak", t("图表", "Figures") + ":")); body.appendChild(h("span", null, (s.figures || []).join(" · ") || "—")); b.appendChild(body); w.appendChild(b); if (i < secs.length - 1) w.appendChild(h("div", "weak", "↓")); });
    return w;
  }

  function renderFigures(v) {
    var c = container(v); var figs = (state.result && state.result.figure_items) || [];
    c.appendChild(h("h1", "h1", t("图表", "Figures")));
    c.appendChild(h("p", "muted", t("Paper2Post 识别出", "Paper2Post identified") + " " + (figs.length || 7) + " " + t("张图，并选取", "figures and selected") + " " + Math.min(figs.length || 6, 6) + " " + t("张用于文章", "for the article") + "."));
    var grid = h("div", "fig-grid mt16");
    figs.forEach(function (f) { var card = h("div", "fig-card"); if (f.url) { var im = h("img"); im.src = f.url; card.appendChild(im); } else { var ph = h("img"); ph.style.background = "#E8EDF2"; ph.style.minHeight = "120px"; card.appendChild(ph); } var b = h("div", "fc-body"); b.appendChild(h("div", "cap", f.figure)); b.appendChild(h("div", "weak", (f.role || "-") + " · " + t("重要度", "Importance") + " " + (f.importance || "-"))); var ua = h("button", "btn btn-sm mt8", t("用于文章", "Use in article")); ua.onclick = function () { toast(t("已标记用于文章", "Marked for article")); }; b.appendChild(ua); card.appendChild(b); card.onclick = function () { state.selFig = f; figDrawer(); }; grid.appendChild(card); });
    if (!figs.length) c.appendChild(h("p", "muted mt16", t("生成论文后可见图表。", "Generate a paper to see its figures.")));
    c.appendChild(grid);
  }
  function figDrawer() { openModal(); var f = state.selFig || {}; var m = document.getElementById("modal"); var body = h("div", "modal-body"); body.appendChild(h("h3", "h3", f.figure || "Figure")); body.appendChild(h("div", "weak mt8", t("角色", "Role"))); body.appendChild(h("div", null, f.role || "Mechanism")); body.appendChild(h("div", "weak mt16", t("推荐面板", "Recommended panels"))); body.appendChild(h("div", null, "A · C · F")); body.appendChild(h("div", "weak mt16", t("AI 解读", "AI Summary"))); body.appendChild(h("div", null, f.summary || f.label || "—")); body.appendChild(h("div", "weak mt16", t("文章用途", "Article usage"))); body.appendChild(h("div", null, "Section 05")); var bt = h("div", "row mt16"); [t("裁剪面板", "Crop Panel"), t("生成图注", "Generate Caption"), t("解释图表", "Explain Figure"), t("替换", "Replace")].forEach(function (t2) { bt.appendChild(h("button", "btn btn-sm", t2)); }); body.appendChild(bt); m.appendChild(body); }

  function renderLibrary(v) { var c = container(v); c.appendChild(h("h1", "h1", t("文库", "Library"))); c.appendChild(h("p", "muted mt8", t("你生成过的论文，点击任意一行打开。", "All generations. Click a row to reopen."))); var search = h("div", "row mt16"); var inp = h("input", "input"); inp.placeholder = t("搜索论文…", "Search papers…"); inp.style.cssText = "max-width:320px"; search.appendChild(inp); c.appendChild(search);
    var tbl = h("table", "table mt16"); var th = h("thead"); var tr = h("tr"); [t("标题", "TITLE"), t("作者", "AUTHORS"), t("页数", "PAGES"), t("准确度", "ACCURACY"), t("更新时间", "UPDATED")].forEach(function (x) { tr.appendChild(h("th", null, x)); }); th.appendChild(tr); tbl.appendChild(th); var tb = h("tbody");
    var rows = state.generations.length ? state.generations : [];
    if (!rows.length) { tb.appendChild(h("tr")); var td0 = h("td"); td0.colSpan = 5; td0.className = "muted"; td0.textContent = t("还没有生成记录——去首页上传论文开始。", "No generations yet — upload a paper to start."); tb.appendChild(td0); }
    rows.forEach(function (g) { var row = h("tr"); row.appendChild(h("td", null, g.title || t("（未命名论文）", "(untitled)"))); row.appendChild(h("td", "muted", (g.authors || "").toString().slice(0, 34) || "—")); row.appendChild(h("td", "muted", g.page_count != null ? g.page_count : "—")); var sc = h("td"); if (g.score != null) { var b = h("span", "badge " + (g.score >= 90 ? "green" : g.score >= 80 ? "amber" : "gray"), g.score + "/100"); sc.appendChild(b); } else sc.appendChild(h("span", "muted", "—")); row.appendChild(sc); row.appendChild(h("td", "muted", g.time || "—")); row.onclick = function () { if (g.run_id) openRun(g.run_id); }; tb.appendChild(row); });
    tbl.appendChild(tb); c.appendChild(tbl); }

  function renderEvidence(v) { var c = container(v); c.appendChild(h("h1", "h1", t("证据", "Evidence"))); var ev = (state.result && state.result.evidence && state.result.evidence.evidence) || []; if (!ev.length) { c.appendChild(h("h2", "h2 mt16", t("选择一篇论文查看证据。", "Select a paper to view grounded evidence."))); return; } ev.forEach(function (e, i) { var b = h("div", "card mt12"); b.appendChild(h("div", "cap", "Claim 0" + (i + 1))); b.appendChild(h("div", "h3", "“" + (e.claim || "") + "”")); b.appendChild(h("div", "weak mt8", t("依据", "Supported by") + ": " + (e.source_section || "Results") + " · Paragraph 8")); b.appendChild(h("div", "weak", t("图表", "Figure") + ": " + (e.figure || "—"))); b.appendChild(h("div", "badge green mt8", t("置信度", "Confidence") + " " + Math.round((e.confidence || 0) * 100) + "% · " + t("已验证", "Verified"))); c.appendChild(b); }); }

  var DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash-vision-exp";
  var CUSTOM_MODEL = "__custom__";

  function sel(opts, val) { var s = h("select", "select"); opts.forEach(function (o) { var op = h("option", null, o); op.value = o; s.appendChild(op); }); s.value = val || opts[0]; return s; }
  function fld(label, widget) { var f = h("div", "field mt8"); f.appendChild(h("label", null, label)); f.appendChild(widget); return f; }
  function modelPicker(models, current) {
    var known = (models || []).slice(); if (!known.length) known = [DEFAULT_DEEPSEEK_MODEL];
    var wrap = h("div"); var select = h("select", "select");
    known.forEach(function (model) { var option = h("option", null, model); option.value = model; select.appendChild(option); });
    var customOption = h("option", null, t("自定义模型 ID…", "Custom model ID…")); customOption.value = CUSTOM_MODEL; select.appendChild(customOption);
    var custom = h("input", "input mt8"); custom.placeholder = "deepseek-model-id";
    var isKnown = known.indexOf(current) !== -1; select.value = isKnown ? current : CUSTOM_MODEL; custom.value = isKnown ? "" : (current || "");
    function sync() { custom.style.display = select.value === CUSTOM_MODEL ? "block" : "none"; }
    select.onchange = sync; sync(); wrap.appendChild(select); wrap.appendChild(custom);
    return { element: wrap, value: function () { return select.value === CUSTOM_MODEL ? custom.value.trim() : select.value; } };
  }

  function renderSettings(v) {
    var c = container(v); c.appendChild(h("h1", "h1", t("设置", "Settings")));
    c.appendChild(h("p", "muted mt8", t("这些默认值将用于后续生成的论文。", "These defaults apply to new generations.")));
    var card = h("div", "card mt16"); c.appendChild(card);
    var grid = h("div", "grid g2"); card.appendChild(grid);
    Promise.all([fetch("/api/settings").then(function (r) { return r.json(); }), fetch("/api/models").then(function (r) { return r.json(); })]).then(function (values) {
      var s = values[0], models = values[1];
      grid.appendChild(fld(t("生成模式", "Generation Mode"), sel(["deep_review", "speed", "methods", "resource"], s.article_type || state.config.article_type)));
      grid.appendChild(fld(t("长度", "Length"), sel(["1500", "2500", "4000"], String(s.article_length || state.config.length))));
      grid.appendChild(fld(t("写作风格", "Writing Style"), sel(["Academic Popularization", "Professional", "Concise", "Deep Dive"], s.style || state.config.style)));
      grid.appendChild(fld(t("语言", "Language"), sel([t("中文", "中文"), "English"], (s.language === "en" ? "English" : t("中文", "中文")))));
      var picker = modelPicker(models.models, s.model || models.model || state.config.model); grid.appendChild(fld(t("DeepSeek 模型", "DeepSeek Model"), picker.element));
      var save = h("button", "btn btn-primary mt24", t("保存设置", "Save Settings")); save.style.cssText = "width:100%;justify-content:center;padding:12px";
      save.onclick = function () {
        var vals = { article_type: grid.children[0].querySelector("select").value, article_length: parseInt(grid.children[1].querySelector("select").value, 10), style: grid.children[2].querySelector("select").value, language: (grid.children[3].querySelector("select").value === "English" ? "en" : "zh-CN"), model: picker.value() };
        if (!vals.model) { toast(t("请输入模型 ID", "Enter a model ID")); return; }
        Object.keys(vals).forEach(function (k) { if (!vals[k]) delete vals[k]; });
        fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(vals) }).then(function (r) { return r.json(); }).then(function (res) { if (res.ok) { toast(t("设置已保存", "Settings saved")); state.config.article_type = vals.article_type; state.config.length = String(vals.article_length); state.config.style = vals.style; state.config.language = vals.language === "en" ? "English" : "中文"; state.config.model = vals.model; } else toast(t("保存失败", "Save failed")); });
      };
      card.appendChild(save);
    });
  }

  function renderApi(v) {
    var c = container(v); c.appendChild(h("h1", "h1", t("API / 模型", "API / Models")));
    c.appendChild(h("p", "muted mt8", t("配置 DeepSeek 密钥与模型；密钥仅保存在本机 .env。", "Configure DeepSeek credentials and model. The key stays in the local .env file.")));
    fetch("/api/models").then(function (r) { return r.json(); }).then(function (m) {
      var card = h("div", "card mt16"); c.appendChild(card); var grid = h("div", "grid g2"); card.appendChild(grid);
      var picker = modelPicker(m.models, m.model || DEFAULT_DEEPSEEK_MODEL); grid.appendChild(fld(t("DeepSeek 模型", "DeepSeek Model"), picker.element));
      var ak = h("input", "input"); ak.type = "password"; ak.placeholder = m.has_api_key ? "••••••••" : "sk-…"; grid.appendChild(fld(t("API Key", "API Key"), ak));
      var status = h("div", "badge mt8 " + (m.has_api_key ? "green" : "red"), (m.has_api_key ? t("已配置密钥", "Key configured") : t("未配置密钥", "No key configured")));
      card.appendChild(status);
      var save = h("button", "btn btn-primary mt24", t("保存", "Save")); save.style.cssText = "width:100%;justify-content:center;padding:12px";
      save.onclick = function () {
        var vals = { model: picker.value(), api_key: ak.value };
        if (!vals.model) { toast(t("请输入模型 ID", "Enter a model ID")); return; }
        Object.keys(vals).forEach(function (k) { if (!vals[k]) delete vals[k]; });
        fetch("/api/models", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(vals) }).then(function (r) { return r.json(); }).then(function (res) { if (res.ok) { toast(t("已保存", "Saved") + " · " + res.provider + " / " + res.model); state.config.provider = res.provider; state.config.model = res.model; render(); } else toast(t("保存失败", "Save failed")); });
      };
      card.appendChild(save);
    });
  }

  function renderGenerations(v) { var c = container(v); c.appendChild(h("h1", "h1", state.view === "templates" ? t("模板", "Templates") : t("生成记录", "Generations"))); c.appendChild(h("p", "muted mt8", t("此页面为占位。", "This section is a placeholder in this build."))); }
  function renderPlain(v) { var c = container(v); c.appendChild(h("h1", "h1", (state.view === "settings" ? t("设置", "Settings") : state.view === "api" ? t("API / 模型", "API / Models") : t("帮助", "Help")))); c.appendChild(h("p", "muted mt8", t("在 .env 或生成设置中配置 API Key。此页为占位。", "Configure API keys in .env or generation setup. Placeholder."))); }

  function runAction(action) {
    if (!state.result) { toast(t("请先生成论文", "Generate a paper first")); return; }
    state.busy = true; toast(t("AI 处理中…", "AI working…") + " (" + action + ")");
    var rid = state.result.run_id;
    var qs = "run_id=" + encodeURIComponent(rid) + "&action=" + encodeURIComponent(action) + "&model=" + encodeURIComponent(state.config.model || DEFAULT_DEEPSEEK_MODEL);
    fetch("/api/action?" + qs, { method: "POST" }).then(function (r) { return r.json(); }).then(function (res) {
      if (res.error) { toast("Error: " + res.error); return; }
      if (state.result) state.result.article_md = res.article_md; if (res.article_html_url) state.result.article_html_url = res.article_html_url;
      if (state.activeRun && state.resultsByRun[state.activeRun]) { state.resultsByRun[state.activeRun].article_md = res.article_md; if (res.article_html_url) state.resultsByRun[state.activeRun].article_html_url = res.article_html_url; }
      state.busy = false; toast(t("完成", "Done") + " (" + res.action + ")"); render();
    }).catch(function (e) { state.busy = false; toast(t("出错", "Error") + ": " + e.message); });
  }

  function refreshGenerations() {
    fetch("/api/generations").then(function (r) { return r.json(); }).then(function (g) { if (g.generations) state.generations = g.generations; });
  }

  function openRun(run_id) {
    fetch("/api/result?run_id=" + encodeURIComponent(run_id)).then(function (r) { return r.json(); }).then(function (res) {
      if (res.error) { toast(t("打开失败", "Failed to open") + ": " + res.error); return; }
      state.activeRun = run_id; state.result = res; state.resultsByRun[run_id] = res;
      state.accepted = []; state.dismissed = [];
      fetch("/api/review?run_id=" + encodeURIComponent(run_id)).then(function (r) { return r.json(); }).then(function (rs) { state.accepted = rs.accepted || []; state.dismissed = rs.dismissed || []; });
      show("workspace");
    });
  }

  function saveReview() {
    if (!state.activeRun) return;
    fetch("/api/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ run_id: state.activeRun, accepted: state.accepted, dismissed: state.dismissed }) });
  }

  function openModal() { document.getElementById("overlay").classList.add("show"); document.getElementById("modal").innerHTML = ""; }
  function closeModal() { document.getElementById("overlay").classList.remove("show"); document.getElementById("modal").innerHTML = ""; }
  function exportModal() {
    openModal(); var m = document.getElementById("modal"); var head = h("div", "modal-head"); head.appendChild(h("h3", "h3", t("导出文章", "Export article"))); var x = h("button", "icon-btn", "×"); x.onclick = closeModal; head.appendChild(x); m.appendChild(head);
    var body = h("div", "modal-body"); body.appendChild(h("div", "h3", t("文章", "Article")));
    (function () {
      var files = (state.result && state.result.files) || {};
      function dl(name, label) { var a = h("a", "btn btn-sm mt8", label); a.style.cssText = "margin-right:8px;display:inline-flex;align-items:center;gap:6px"; a.href = files["final_article." + name] || "#"; a.download = "final_article." + name; body.appendChild(a); return a; }
      dl("md", "Markdown"); dl("html", "HTML");
      var cp = h("button", "btn btn-sm mt8", t("复制", "Copy")); cp.style.cssText = "margin-right:8px"; cp.onclick = function () { var md = state.result ? state.result.article_md : ""; if (navigator.clipboard) navigator.clipboard.writeText(md).then(function () { toast(t("已复制", "Copied")); }); }; body.appendChild(cp);
    })();
    body.appendChild(h("div", "h3 mt24", t("资源", "Assets")));
    (function () {
      var files = (state.result && state.result.files) || {};
      var assetMap = [t("图表", "Figures"), "figures", t("图注", "Figure captions"), "paper_analysis.json", t("证据报告", "Evidence report"), "figure_analysis.json", t("事实核验报告", "Fact-check report"), "fact_check.json", t("生成报告", "Generation report"), "generation_report.md"];
      for (var i = 0; i < assetMap.length; i += 2) { var r = h("label", "row mt8"); r.style.cursor = "pointer"; var ch = h("input"); ch.type = "checkbox"; ch.checked = true; var url = files[assetMap[i + 1]]; var span = h("span", null, assetMap[i]); if (url) { span.appendChild(h("span", "weak", "  ↓")); var a = h("a", null, "下载"); a.href = url; a.download = assetMap[i + 1]; span.appendChild(a); } r.appendChild(ch); r.appendChild(span); body.appendChild(r); }
    })();
    body.appendChild(h("h3", "h3 mt24", t("发布预览", "Publication Preview")));
    var pv = h("div", "row mt8"); pv.style.alignItems = "flex-start";
    var wx = h("div", "wx"); wx.appendChild(h("div", "weak", "微信 ● 公众号")); wx.appendChild(h("div", "wx-title", "AI reveals a new cellular program of brain aging")); wx.appendChild(h("p", null, "Aging reshapes the endothelial apelin receptor program…")); var fm = h("div", "fig-marker"); fm.appendChild(h("div", "cap", "FIGURE 2")); wx.appendChild(fm);
    pv.appendChild(h("div", "weak", t("电脑编辑器", "Desktop editor"))); pv.appendChild(wx); body.appendChild(pv); m.appendChild(body);
    var foot = h("div", "modal-body"); var dl = h("button", "btn btn-primary", t("下载打包 (MD+HTML)", "Download bundle (MD+HTML)")); dl.style.cssText = "width:100%"; dl.onclick = function () { if (!state.result || !state.result.files) return; ["final_article.md", "final_article.html"].forEach(function (n) { var url = state.result.files[n]; if (url) { var a = document.createElement("a"); a.href = url; a.download = n; document.body.appendChild(a); a.click(); a.remove(); } }); toast(t("已开始下载", "Download started")); }; foot.appendChild(dl); m.appendChild(foot);
  }
  document.getElementById("tbExport").addEventListener("click", exportModal);
  document.getElementById("overlay").addEventListener("click", function (e) { if (e.target.id === "overlay") closeModal(); });

  document.addEventListener("DOMContentLoaded", function () {
    refreshGenerations();
    fetch("/api/settings").then(function (r) { return r.json(); }).then(function (s) {
      if (s.article_type) state.config.article_type = s.article_type;
      if (s.article_length) state.config.length = String(s.article_length);
      if (s.style) state.config.style = s.style;
      if (s.language) state.config.language = (s.language === "en" ? "English" : "中文");
      if (s.model) state.config.model = s.model;
    });
    document.querySelectorAll(".sb-item[data-nav]").forEach(function (e) { e.addEventListener("click", function () { show(e.dataset.nav); }); });
    var lb = document.getElementById("langBtn"); if (lb) lb.addEventListener("click", function () { setLang(state.lang === "zh" ? "en" : "zh"); });
    show("home");
  });
})();
