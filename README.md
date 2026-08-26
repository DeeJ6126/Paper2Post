# Paper2Post

> **AI 论文推文自动生成系统** — 将学术论文 PDF 自动转化为经过结构化解读、证据约束、图表筛选与事实核验的公众号科研推文。

## 目标

尽可能实现 **上传论文 → 自动生成可发布推文**，人工只负责最后审核与发布。

V1 不追求全自动发布，优先实现：

```text
论文 PDF → 高质量推文初稿 → 自动事实核验 → 可直接人工审核发布
```

## 已实现（Milestone 0 + 脚手架）

- [x] 工程骨架与目录结构
- [x] 可替换 LLM Provider（OpenAI / Mock）
- [x] PDF 解析：文本、分节、图表抽取、图注识别（PyMuPDF）
- [x] 结构化 Schema（paper / evidence / storyline / figure / review）
- [x] 六 Agent：Reader / Storyteller / Figure / Writer / Reviewer / Editor
- [x] 端到端 Pipeline 与 CLI
- [x] Markdown / HTML / JSON / 图 全量产出
- [ ] 真实 LLM 效果验证（Phase 0 Prompt Benchmark）
- [ ] Milestone 1：多栏正文、参考文献剥离、图注精准匹配

## 快速开始

### 准备依赖（二选一）

**方式 A：使用工作区内的 vendor/（无需 pip 安装）**
本仓库已把第三方依赖放入 `vendor/`，入口脚本会自动把它加入 sys.path。

**方式 B：按标准方式安装**
```bash
pip install -r requirements.txt
```

生成示例论文 PDF：
```bash
python scripts/make_sample_pdf.py
```

### 方式一：Mock 模式（无需 API Key，跑通全链路）

```bash
python paper2post.py examples/sample_paper.pdf --mock
# 等价于
python -m paper2post examples/sample_paper.pdf --mock
```

运行后结果写入 `outputs/` 目录。

### 方式二：真实 LLM 模式

```bash
# 配置密钥
copy .env.example .env   # 填入 OPENAI_API_KEY
python -m paper2post examples/sample_paper.pdf --provider openai --model gpt-4o-mini
```

若未配置密钥，CLI 会自动警告并回退到 mock 模式，保证可运行。

### 冒烟测试

```bash
python tests/smoke_test.py
```

## 输出结构

```text
outputs/
├── metadata.json          # 论文元信息
├── paper_analysis.json    # 结构化论文理解
├── evidence.json          # 结论 ↔ 证据 ↔ 图 映射
├── storyline.json         # 自动故事线
├── figure_analysis.json   # 图表筛选与解读
├── fact_check.json        # 事实核验报告
├── draft_article.md
├── final_article.md
├── final_article.html
├── captions.json
├── figures/               # 抽取的图（PNG）
└── generation_report.md
```

## 架构

```text
PDF Parser → Paper Reader → Evidence → Story Planner
          → Figure Agent → Writer → Reviewer → Editor → Output(MD/HTML/图)
```

各阶段中间结果写入磁盘（JSON），支持缓存与局部重跑；只改文章风格时无需重新解析 PDF 与理解论文。

## 模型调用原则（多 Call）

```text
CALL 1 论文结构化    → 强模型
CALL 2 证据提取      → 强模型
CALL 3 故事线        → 强模型
CALL 4 Figure 分析   → 小/中模型
CALL 5 文章生成      → 强模型
CALL 6 事实核验      → 强模型
CALL 7 修改
CALL 8 编辑排版      → 中等模型
```

## 已知局限（下一步优化）

- Mock 模式产出的是「结构完整但含占位符」的骨架，真实生成依赖 LLM。
- PDF 解析目前以单栏为主；多栏、参考文献剥离、图注与图精准绑定仍在 Milestone 1 规划中。
- 事实核验当前由 Reviewer 在 LLM 模式下执行；Mock 模式下默认全部通过。

## 使用 Docker（快速运行）

已提供 `Dockerfile`、`docker-compose.yml` 与 `.dockerignore`，一条命令即可构建并跑通。

### 构建镜像
```bash
docker build -t paper2post:latest .
```

### 一键运行（Compose，默认 mock 处理内置示例）
```bash
docker compose up --build
```
结果写入宿主机 `outputs/` 目录。

### 处理你自己的论文
把 PDF 放到 `data/` 目录，然后：
```bash
docker compose run --rm paper2post /app/data/your_paper.pdf --article-type deep_review
```
如需真实 LLM，先在 `.env` 填入 `OPENAI_API_KEY`（已只读挂载进容器）。

`config/` 与 `prompts/` 已挂载到容器，可直接热改而无需重 build。

> 注：本环境未运行 Docker daemon（`docker info`/`docker ps` 不可达），故未能在此实测构建；编排文件已按标准最佳实践编写。

## 可视化 Web App（V1）

无需命令行，直接在浏览器里上传 PDF、选类型、点生成。基于标准库实现，零额外依赖。

```bash
python run_web.py                # 默认 http://127.0.0.1:8000
python run_web.py --port 8000 --no-browser
```
启动后自动打开浏览器（或手动访问 http://127.0.0.1:8000）。

页面组成：
- 左侧：拖拽/选择 PDF，配置推文类型、长度、受众、LLM 引擎（Mock / OpenAI）
- 右侧：推文预览（HTML）、Markdown 源码、论文分析、故事线、图表、事实核验、下载

后端把一个 run 的结果写入 `outputs_web/<run_id>/`，通过 `/files/` 端点安全地提供下载（含目录穿越防护）。


## 支持的 LLM Provider

内置**可替换 Provider 架构**，可接入主流模型 API。

**OpenAI 兼容协议**（只要填 base_url + model 即可，理论上可接任意兼容网关）：

- OpenAI、DeepSeek、Qwen(DashScope)、Moonshot(Kimi)、Groq、Mistral、OpenRouter、Ollama(本地)、vLLM(本地)

**原生协议 Provider**：

- Anthropic(Claude)、Google Gemini

配置密钥（`.env`）：

```text
OPENAI_API_KEY=...            # OpenAI
ANTHROPIC_API_KEY=...         # Anthropic (Claude)
GEMINI_API_KEY=...            # Google Gemini
DEEPSEEK_API_KEY=...          # DeepSeek
DASHSCOPE_API_KEY=...         # Qwen/DashScope
MOONSHOT_API_KEY=...          # Moonshot(Kimi)
GROQ_API_KEY=...              # Groq
MISTRAL_API_KEY=...           # Mistral
OPENROUTER_API_KEY=...        # OpenRouter
```

CLI 与 Web 均可选择 Provider：

```bash
python paper2post.py paper.pdf --provider deepseek --model deepseek-chat
python paper2post.py paper.pdf --provider anthropic --model claude-3-5-sonnet-latest
python paper2post.py paper.pdf --provider gemini --model gemini-2.0-flash
```

Web 页面里的 LLM 引擎下拉框同样可选；未配置对应 key 时自动回退到 mock。

## 桌面端应用

零依赖启动：内置 HTTP 服务 + 原生窗口（pywebview），不可用时自动回退到系统浏览器：

```bash
python desktop.py             # 原生窗口
python desktop.py --browser   # 强制用系统浏览器
```

打包成单文件桌面程序（.exe）：

```bash
pip install -r requirements.txt pyinstaller
python build_exe.py           # 产物 dist/Paper2Post(.exe)
```

> **重要：请在 Python 3.11 / 3.12 上构建。** 本仓库在 Python 3.13 + PyInstaller 6.22 下，哪怕是最小的 onefile 程序也无法启动（PyInstaller 冻结运行时与 3.13 存在兼容性问题），无头/受限环境会加剧这一点；在 3.11/3.12 上即可稳定产出可运行的 .exe。
> 若要支持 Claude/Gemini，先 `pip install anthropic google-genai` 再构建；若要原生窗口而非浏览器回退，先 `pip install pywebview` 再构建。

已在本环境成功构建出 `dist/Paper2Post.exe`（约 67MB，已排除 numpy/pandas/scipy 等无关重型库）。
## License

MIT