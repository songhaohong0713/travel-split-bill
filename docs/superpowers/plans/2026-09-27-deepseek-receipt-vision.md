# DeepSeek 小票视觉识别 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让云托管后端使用 DeepSeek 视觉模型把 JPEG 小票转换为现有的中文候选账单条目。

**Architecture:** 以一个 `DeepSeekReceiptAi` 提供方替代 CloudBase AI 网关实现。它只从服务端环境变量读取密钥，向 DeepSeek OpenAI 兼容 Chat Completions 接口发送 Data URL 图片，复用现有 JSON 候选条目解析和上传端点重试机制。

**Tech Stack:** Python 3.12、FastAPI、httpx、pytest、ruff、DeepSeek OpenAI-compatible API。

## Global Constraints

- 只读取 `DEEPSEEK_API_KEY` 和可选 `DEEPSEEK_MODEL`；默认模型是 `deepseek-flash`。
- 不修改小程序、数据库、上传 API 或候选条目 JSON 格式。
- 不记录 API Key、供应商原始响应或小票图片。
- 不新增第三方 Python 依赖；使用现有 `httpx`。

---

### Task 1: 验证 DeepSeek 提供方请求契约

**Files:**
- Create: `backend/tests/unit/test_deepseek_receipt_ai.py`
- Create: `backend/app/providers/deepseek_receipt_ai.py`

**Interfaces:**
- Produces: `DeepSeekReceiptAi(api_key: str, model: str = "deepseek-flash", client: httpx.AsyncClient | None = None)`。
- Produces: `DeepSeekReceiptAi.from_environment() -> DeepSeekReceiptAi`。
- Produces: `await DeepSeekReceiptAi.recognize(image: bytes) -> list[dict[str, str]]`。

- [ ] **Step 1: Write the failing test**

```python
def test_recognize_sends_a_jpeg_to_the_deepseek_vision_endpoint() -> None:
    client = FakeClient('{"items":[{"source_text":"Tea","translated_text":"茶","amount":"12","currency":"JPY"}]}')
    provider = DeepSeekReceiptAi("deepseek-key", client=client)

    assert asyncio.run(provider.recognize(b"jpeg"))[0]["translated_text"] == "茶"
    assert client.calls[0]["headers"] == {"Authorization": "Bearer deepseek-key"}
    assert client.calls[0]["json"]["model"] == "deepseek-flash"
    assert client.calls[0]["json"]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_deepseek_receipt_ai.py -v`

Expected: FAIL because `app.providers.deepseek_receipt_ai` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
class DeepSeekReceiptAi:
    url = "https://api.deepseek.com/v1/chat/completions"

    @classmethod
    def from_environment(cls) -> "DeepSeekReceiptAi":
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise DeepSeekReceiptAiError("DeepSeek API key is not configured")
        return cls(api_key, os.getenv("DEEPSEEK_MODEL", "deepseek-flash"))
```

Implement `recognize` with the existing strict receipt prompt and `parse_receipt_candidates`; map request, non-200, malformed JSON and empty candidates to `DeepSeekReceiptAiError` without including provider details.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_deepseek_receipt_ai.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/providers/deepseek_receipt_ai.py backend/tests/unit/test_deepseek_receipt_ai.py
git commit -m "feat: add DeepSeek receipt vision provider"
```

### Task 2: 将应用启动配置切换为 DeepSeek

**Files:**
- Modify: `backend/app/main.py:17-61`
- Modify: `backend/app/api/v1/uploads.py:15,124-133`
- Modify: `backend/tests/unit/test_external_providers.py`

**Interfaces:**
- Consumes: `DeepSeekReceiptAi.from_environment()` 和 `DeepSeekReceiptAiError`。
- Produces: `app.state.receipt_ai` 仅在 `DEEPSEEK_API_KEY` 存在时指向 DeepSeek 提供方。

- [ ] **Step 1: Write the failing test**

```python
def test_configure_receipt_ai_uses_deepseek_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "receipt-model")
    if hasattr(app.state, "receipt_ai"):
        delattr(app.state, "receipt_ai")
    configure_receipt_ai()

    assert app.state.receipt_ai.model == "receipt-model"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_external_providers.py::test_configure_receipt_ai_uses_deepseek_environment -v`

Expected: FAIL because startup still instantiates `CloudBaseReceiptAi`.

- [ ] **Step 3: Write minimal implementation**

```python
from app.providers.deepseek_receipt_ai import DeepSeekReceiptAi, DeepSeekReceiptAiError

def configure_receipt_ai() -> None:
    if getattr(app.state, "receipt_ai", None) is not None:
        return
    try:
        app.state.receipt_ai = DeepSeekReceiptAi.from_environment()
    except DeepSeekReceiptAiError:
        app.state.receipt_ai = None
```

Update the upload module to catch `DeepSeekReceiptAiError` so existing three-attempt handling remains intact. Delete CloudBase receipt-AI imports and source file only after their callers are gone.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_deepseek_receipt_ai.py tests/integration/test_uploads.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/app/api/v1/uploads.py backend/tests/unit/test_external_providers.py
git rm backend/app/providers/cloudbase_receipt_ai.py backend/tests/unit/test_cloudbase_receipt_ai.py
git commit -m "feat: use DeepSeek for receipt extraction"
```

### Task 3: 全量验证与部署交接

**Files:**
- Modify: `docs/superpowers/specs/2026-09-27-deepseek-receipt-vision-design.md` only if implementation differs from its contract.

- [ ] **Step 1: Run complete backend tests**

Run from `backend/`: `python -m pytest`

Expected: PASS with zero failures.

- [ ] **Step 2: Run static checks**

Run from `backend/`: `python -m ruff check app tests`

Expected: `All checks passed!`.

- [ ] **Step 3: Inspect the staged change**

Run: `git diff --check HEAD~2..HEAD && git status --short`

Expected: no whitespace errors; only intended backend and documentation changes are staged or committed.

- [ ] **Step 4: Commit verification-only documentation adjustment if needed**

```bash
git add docs/superpowers/specs/2026-09-27-deepseek-receipt-vision-design.md
git commit -m "docs: finalize DeepSeek receipt integration"
```

- [ ] **Step 5: Deploy handoff**

Tell the user to update the existing CloudRun service from the pushed revision, keep `DEEPSEEK_API_KEY` secret, then upload one JPEG receipt. Verify that it returns reviewable Chinese item candidates rather than `OCR_FAILED`.
