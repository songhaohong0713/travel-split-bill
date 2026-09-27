# Multimodal Receipt Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Convert Japanese and English receipt photos to editable Chinese item candidates, never automatically writing them into a bill.

**Architecture:** The Mini Program sends the compressed JPEG to the authenticated receipt-job endpoint. FastAPI creates the existing job metadata, sends the JPEG held in memory to CloudBase AI, validates strict JSON, and saves candidates in existing \`receipt_jobs.candidates_json\`. This replaces the currently fake \`/storage-upload\` handoff; no image is retained after the request.

**Tech Stack:** WeChat Mini Program, FastAPI, httpx, CloudBase PG REST API, CloudBase AI \`glm-5v-turbo\`, pytest, Node test runner.

## Global Constraints

- Japanese and English are supported; unsupported text remains available for manual review.
- \`CLOUDBASE_ENV_ID\` and \`CLOUDBASE_APIKEY\` are server-only. Never return a key, prompt, or image bytes.
- JPEG only, 5 MB maximum; no new dependencies, tables, or migrations.
- Candidates require explicit user confirmation. Network, parse, and empty-result errors retry three times then persist \`failed/OCR_FAILED\`.
- Preserve the SQLite fallback tests.

---

## File Structure

- \`backend/app/providers/cloudbase_receipt_ai.py\`: AI request and strict response parser.
- \`backend/app/api/v1/uploads.py\`: multipart job endpoint, retry loop, CloudBase/SQLite persistence.
- \`backend/app/main.py\`: provider configuration.
- \`backend/tests/unit/test_cloudbase_receipt_ai.py\`: provider parser/request tests.
- \`backend/tests/integration/test_uploads.py\` and \`backend/tests/integration/test_uploads_cloudbase.py\`: endpoint tests.
- \`miniprogram/services/api.js\`, \`miniprogram/pages/expense/index.js\`, \`miniprogram/tests/ocr-review.integration.test.js\`: upload and review-to-item behavior.
- \`docs/superpowers/specs/2026-09-27-multimodal-receipt-extraction-design.md\`: explain request-scoped image handling.

### Task 1: Add the CloudBase multimodal receipt provider

**Files:**
- Create: \`backend/app/providers/cloudbase_receipt_ai.py\`
- Test: \`backend/tests/unit/test_cloudbase_receipt_ai.py\`

**Interfaces:**
- Produces: \`CloudBaseReceiptAi.from_environment()\`, \`await recognize(image: bytes) -> list[dict[str, str]]\`, and \`CloudBaseReceiptAiError\`.

- [ ] **Step 1: Write failing tests**

\`\`\`python
def test_recognize_returns_only_complete_candidates() -> None:
    provider = CloudBaseReceiptAi("env", "key", client=FakeClient(
        '{"items":[{"source_text":"お茶","translated_text":"茶","amount":"120","currency":"JPY"},{"source_text":"bad","amount":"x","currency":"JPY"}]}'
    ))
    assert asyncio.run(provider.recognize(b"jpeg")) == [
        {"source_text": "お茶", "translated_text": "茶", "amount": "120", "currency": "JPY"}
    ]

def test_recognize_rejects_non_json_or_empty_items() -> None:
    with pytest.raises(CloudBaseReceiptAiError):
        asyncio.run(CloudBaseReceiptAi("env", "key", client=FakeClient("not json")).recognize(b"jpeg"))
\`\`\`

- [ ] **Step 2: Verify failure**

Run: \`cd backend; pytest tests/unit/test_cloudbase_receipt_ai.py -v\`

Expected: import error.

- [ ] **Step 3: Implement minimum provider**

\`\`\`python
class CloudBaseReceiptAi:
    def __init__(self, env_id: str, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self.url = f"https://{env_id}.api.tcloudbasegateway.com/v1/ai/cloudbase/chat/completions"
        self.api_key, self._client = api_key, client

    async def recognize(self, image: bytes) -> list[dict[str, str]]:
        content = await self._completion(base64.b64encode(image).decode("ascii"))
        return parse_receipt_candidates(content)
\`\`\`

Send exactly one instruction requesting \`{"items":[...]}\` and an \`image_url\` block holding \`data:image/jpeg;base64,...\`; use \`glm-5v-turbo\`. Validate nonblank source/translation, uppercase three-letter currency, and amounts with \`decimal_from_string\`. Convert HTTP, JSON, and response-shape failures to \`CloudBaseReceiptAiError\` without retaining response bodies.

- [ ] **Step 4: Verify**

Run: \`cd backend; pytest tests/unit/test_cloudbase_receipt_ai.py -v; ruff check app/providers/cloudbase_receipt_ai.py tests/unit/test_cloudbase_receipt_ai.py\`

Expected: PASS.

- [ ] **Step 5: Commit**

\`\`\`bash
git add backend/app/providers/cloudbase_receipt_ai.py backend/tests/unit/test_cloudbase_receipt_ai.py
git commit -m "feat: add CloudBase multimodal receipt provider"
\`\`\`

### Task 2: Make the receipt job endpoint handle JPEG bytes

**Files:**
- Modify: \`backend/app/main.py\`
- Modify: \`backend/app/api/v1/uploads.py\`
- Modify: \`backend/tests/integration/test_uploads.py\`
- Modify: \`backend/tests/integration/test_uploads_cloudbase.py\`

**Interfaces:**
- Consumes: multipart \`POST /v1/receipt-jobs\` with form \`trip_id\` and JPEG \`file\`.
- Produces: \`202 {"data":{"id":...,"status":"needs_review","candidates":[...]}}\` or \`failed\`.

- [ ] **Step 1: Write failing endpoint tests**

\`\`\`python
def test_receipt_job_processes_jpeg_without_creating_expense(client: TestClient) -> None:
    response = client.post("/v1/receipt-jobs", headers=headers, data={"trip_id": trip_id},
        files={"file": ("receipt.jpg", b"jpeg", "image/jpeg")})
    assert response.status_code == 202
    assert response.json()["data"]["status"] == "needs_review"
    assert response.json()["data"]["candidates"][0]["translated_text"] == "茶"
    assert client.get(f"/v1/trips/{trip_id}/expenses", headers=headers).json()["data"] == []
\`\`\`

Also test PNG and oversize files produce HTTP 400. Mock \`app.state.receipt_ai\`. For CloudBase assert: \`tsb_create_receipt_image\`, \`tsb_mark_receipt_uploaded\`, \`tsb_create_receipt_job\`, then \`PATCH /receipt_jobs?id=eq.<job_id>\` with \`status\`, \`attempts\`, \`candidates_json\`, and \`error_code\`.

- [ ] **Step 2: Verify failure**

Run: \`cd backend; pytest tests/integration/test_uploads.py tests/integration/test_uploads_cloudbase.py -v\`

Expected: existing route expects JSON \`image_id\`.

- [ ] **Step 3: Implement request-scoped recognition**

\`\`\`python
@router.post("/receipt-jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_receipt_job(trip_id: Annotated[str, Form()], file: Annotated[UploadFile, File()], ...) -> dict[str, object]:
    image = await validated_jpeg(file)
    job_id = await create_uploaded_image_and_job(trip_id, user_id, ...)
    status, attempts, candidates, error_code = await recognize_up_to_three_times(receipt_ai, image)
    await persist_receipt_job(job_id, status, attempts, candidates, error_code, ...)
    return {"data": {"id": job_id, "status": status, "candidates": candidates}}
\`\`\`

Delete \`POST /v1/uploads\` and \`POST /v1/uploads/{image_id}/complete\`; those URLs have no implementation. Keep generated object key as metadata only. The CloudBase branch uses existing RPCs then PATCHes existing \`receipt_jobs\` via \`CloudBasePgClient.request\`; SQLite updates \`ReceiptJob\`. Missing AI config saves \`failed/OCR_FAILED\`; retry only \`CloudBaseReceiptAiError\`. Configure \`app.state.receipt_ai\` during lifespan but keep health checks AI-independent.

- [ ] **Step 4: Verify**

Run: \`cd backend; pytest tests/integration/test_uploads.py tests/integration/test_uploads_cloudbase.py tests/unit/test_receipt_worker.py -v; pytest -q; ruff check app tests\`

Expected: PASS.

- [ ] **Step 5: Commit**

\`\`\`bash
git add backend/app/main.py backend/app/api/v1/uploads.py backend/tests/integration/test_uploads.py backend/tests/integration/test_uploads_cloudbase.py
git commit -m "feat: process receipt jobs with multimodal AI"
\`\`\`

### Task 3: Turn a reviewed candidate into an editable item

**Files:**
- Modify: \`miniprogram/services/api.js\`
- Modify: \`miniprogram/pages/expense/index.js\`
- Modify: \`miniprogram/tests/ocr-review.integration.test.js\`

- [ ] **Step 1: Write failing test**

\`\`\`javascript
options.events.ocrCandidateConfirmed({ sourceText: "お茶", translatedText: "茶", amount: "120" })
const added = instance.data.items.at(-1)
assert.equal(added.name, "茶")
assert.equal(added.amount, "120")
assert.equal(instance.data.expandedItemId, added.id)
\`\`\`

- [ ] **Step 2: Verify failure**

Run: \`node --test miniprogram/tests/ocr-review.integration.test.js\`

Expected: candidate is stored only in unused page state.

- [ ] **Step 3: Implement direct upload and insertion**

\`\`\`javascript
function uploadReceipt(tripId, filePath) {
  return new Promise((resolve, reject) => wx.uploadFile({
    url: app.globalData.apiBaseUrl + "/v1/receipt-jobs", filePath, name: "file",
    formData: { trip_id: tripId }, header: authorizationHeader(),
    success: (r) => r.statusCode === 202 ? resolve(JSON.parse(r.data).data) : reject(JSON.parse(r.data).error),
    fail: reject,
  }))
}
\`\`\`

Remove legacy staging calls. Review confirmation appends \`newItem()\` with \`name: translatedText || sourceText\` and confirmed amount via \`syncBill\`, then expands that item. No candidate is added before confirmation.

- [ ] **Step 4: Verify**

Run: \`node --test miniprogram/tests/*.test.js\`

Expected: PASS.

- [ ] **Step 5: Commit**

\`\`\`bash
git add miniprogram/services/api.js miniprogram/pages/expense/index.js miniprogram/tests/ocr-review.integration.test.js
git commit -m "feat: add reviewed receipt candidates to expense items"
\`\`\`

### Task 4: Review and deploy safely

**Files:**
- Modify: \`docs/superpowers/specs/2026-09-27-multimodal-receipt-extraction-design.md\`

- [ ] **Step 1: Update design**

Add: “JPEG bytes exist only during the authenticated receipt-job request. Existing metadata remains for polling; the MVP retains neither a private object URL nor receipt bytes.”

- [ ] **Step 2: Run full checks**

Run: \`cd backend; pytest -q; ruff check app tests; cd ..; node --test miniprogram/tests/*.test.js\`

Expected: all commands exit 0.

- [ ] **Step 3: Run CloudBase code review**

Read rules \`AUTH001\`, \`SEC001\`, \`PG-CR001\`, and \`STORAGE001\`; verify server-only credentials, authenticated trip access, candidate-only persistence, and no image URL in a client response.

- [ ] **Step 4: Commit**

\`\`\`bash
git add docs/superpowers/specs/2026-09-27-multimodal-receipt-extraction-design.md
git commit -m "docs: clarify receipt image handling"
\`\`\`

- [ ] **Step 5: Deploy and smoke test**

Update the existing CloudBase service without replacing existing environment variables. In DevTools compile, test one non-sensitive Japanese and English receipt, verify review candidates, confirm one, edit its name/amount, and verify nothing saves until “查看分账”.

## Self-Review

Tasks 1–2 cover model JSON, credential boundaries, retry and failure states. Task 3 keeps the human confirmation gate. Task 4 checks CloudBase security. Durable receipt archiving is intentionally deferred until the product requires retained originals.
