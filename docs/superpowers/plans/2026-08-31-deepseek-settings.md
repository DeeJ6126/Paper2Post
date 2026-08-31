# DeepSeek Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Paper2Post Web UI use only DeepSeek, with a repository-local API key field and `deepseek-v4-flash-vision-exp` as the default selectable model.

**Architecture:** Keep the DeepSeek catalog in the LLM registry and expose a secret-free settings payload from the existing standard-library HTTP server. Persist only the model in `data/settings.json` and the key in the ignored `.env`; the browser never receives the stored key or controls the provider endpoint.

**Tech Stack:** Python 3.13, standard-library `http.server`, OpenAI-compatible SDK, vanilla JavaScript, `unittest`-style script tests.

---

### Task 1: DeepSeek Catalog And Defaults

**Files:**
- Modify: `paper2post/llm/registry.py`
- Modify: `config/default.yaml`
- Modify: `.env.example`
- Create: `tests/test_deepseek_settings.py`

- [ ] **Step 1: Write a failing catalog test**

Create a test that imports `DEEPSEEK_MODELS` and `DEEPSEEK_DEFAULT_MODEL`, then asserts the default is `deepseek-v4-flash-vision-exp` and all three official model IDs are present.

- [ ] **Step 2: Run the test and confirm the missing constants fail**

Run `python tests/test_deepseek_settings.py`; expect an import failure for the catalog constants.

- [ ] **Step 3: Add the registry-owned catalog and defaults**

Add:

```python
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash-vision-exp"
DEEPSEEK_MODELS = [
    "deepseek-v4-flash-vision-exp",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
]
```

Use these constants in the `deepseek` registry entry. Set `provider: deepseek`, the vision model, and the fixed endpoint in `config/default.yaml`. Update `.env.example` with `DEEPSEEK_API_KEY` and the default model name.

- [ ] **Step 4: Run the catalog test**

Run `python tests/test_deepseek_settings.py`; expect the catalog checks to pass.

### Task 2: Secret-Safe Backend Settings

**Files:**
- Modify: `webapp/server.py`
- Modify: `tests/test_deepseek_settings.py`

- [ ] **Step 1: Add failing backend tests**

Using `tempfile.TemporaryDirectory`, temporarily replace `server._ROOT` and `server.SETTINGS_PATH`. Assert:

```python
info = server.get_models_info()
assert info["provider"] == "deepseek"
assert info["model"] == "deepseek-v4-flash-vision-exp"
assert info["models"][0] == "deepseek-v4-flash-vision-exp"
assert "api_key" not in info
```

Save a key and assert it exists only in the temporary `.env`, the response contains no key, and a second empty-key save preserves it.

- [ ] **Step 2: Run tests and confirm the old multi-provider response fails**

Run `python tests/test_deepseek_settings.py`; expect failures for the provider list, DeepSeek key detection, and response fields.

- [ ] **Step 3: Implement fixed DeepSeek settings**

Make `get_models_info()` return:

```python
{
    "provider": "deepseek",
    "model": selected_model,
    "models": DEEPSEEK_MODELS,
    "allow_custom_model": True,
    "has_api_key": bool(os.environ.get("DEEPSEEK_API_KEY")),
}
```

Make `save_models()` ignore client provider/base URL values, reject an empty model, write only a non-empty `DEEPSEEK_API_KEY`, and persist the fixed provider/endpoint plus selected model. Update Web generation and article actions to always construct `deepseek` at `DEEPSEEK_BASE_URL`.

- [ ] **Step 4: Run backend tests**

Run `python tests/test_deepseek_settings.py`; expect all backend checks to pass.

### Task 3: DeepSeek-Only Settings UI

**Files:**
- Modify: `webapp/static/app.js`
- Modify: `tests/test_deepseek_settings.py`

- [ ] **Step 1: Add failing frontend source assertions**

Read `webapp/static/app.js` and assert it contains the vision default and custom-model option, while the API settings renderer does not create provider or base URL inputs.

- [ ] **Step 2: Replace provider controls with model selection**

Set the state default to DeepSeek and the vision model. In both settings views, render a model select sourced from `/api/models`. In the API view, render the API key password input and status badge. Reveal a custom model ID input only when the custom option is selected. POST only `model` and a non-empty `api_key`.

Remove provider/base URL parameters from generation and article-action URLs; the server owns those values.

- [ ] **Step 3: Run settings tests**

Run `python tests/test_deepseek_settings.py`; expect all catalog, backend, and frontend checks to pass.

### Task 4: Regression And Browser Verification

**Files:**
- Modify: none unless verification reveals a defect

- [ ] **Step 1: Run Python verification**

Run:

```powershell
python tests\test_deepseek_settings.py
python tests\smoke_test.py
python -m py_compile paper2post\llm\registry.py webapp\server.py
```

Expect zero failures and `SMOKE TEST PASSED`.

- [ ] **Step 2: Restart the Web server and verify APIs**

Start `python -u run_web.py --port 8000 --no-browser`. Verify `/api/models` returns DeepSeek, the vision default, the model catalog, and no secret field.

- [ ] **Step 3: Verify the browser UI**

Open `http://127.0.0.1:8000`, confirm there is no provider/base URL control, the vision model is selected, key status is visible, and choosing the custom option reveals exactly one text input.

- [ ] **Step 4: Review the diff and commit implementation**

Run `git diff --check` and `git status --short`, then commit only source, tests, and documentation. Do not commit `.env` or `data/settings.json`.
