const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const test = require("node:test")
const vm = require("node:vm")

function loadPage(relativePath, extra = {}) {
  let definition
  const source = fs.readFileSync(path.join(__dirname, "..", relativePath), "utf8")
  vm.runInNewContext(source, {
    Page(value) { definition = value },
    require: extra.require || require,
    wx: extra.wx || {},
    setTimeout: extra.setTimeout || (() => {}),
  })
  return definition
}

function pageInstance(definition, data = {}) {
  return {
    ...definition,
    data: { ...definition.data, ...data },
    setData(value) { this.data = { ...this.data, ...value } },
  }
}

test("OCR confirmation returns original text, translation, and amount to the expense page", () => {
  const review = loadPage("pages/ocr-review/index.js", { wx: { navigateBack() {}, showToast() {} } })
  const emitted = []
  const instance = pageInstance(review)
  instance.getOpenerEventChannel = () => ({ emit: (name, value) => emitted.push({ name, value }) })
  instance.onLoad({ candidates: encodeURIComponent(JSON.stringify([{ text: "お茶 120", translated_text: "茶 120" }])) })
  instance.chooseCandidate({ currentTarget: { dataset: { id: "0" } } })
  instance.applyManualItem()

  assert.deepEqual(JSON.parse(JSON.stringify(emitted)), [{
    name: "ocrCandidateConfirmed",
    value: { sourceText: "お茶 120", translatedText: "茶 120", amount: "120" },
  }])
})

test("expense page opens OCR review and applies a confirmed candidate", () => {
  const api = { previewSettlement() {}, uploadReceipt() {}, getReceiptJob() { return Promise.resolve({}) } }
  const calls = []
  const expense = loadPage("pages/expense/index.js", { require: () => api, wx: { navigateTo(value) { calls.push(value) } } })
  const instance = pageInstance(expense, { ocrCandidates: [{ text: "お茶 120", translated_text: "茶 120" }] })
  instance.openOcrReview()
  const options = calls[0]
  assert.match(options.url, /pages\/ocr-review\/index/)
  options.events.ocrCandidateConfirmed({ sourceText: "お茶 120", translatedText: "茶 120", amount: "120" })
  assert.equal(instance.data.sourceText, "お茶 120")
  assert.equal(instance.data.translatedText, "茶 120")
  assert.equal(instance.data.amount, "120")
})

test("OCR review page is registered", () => {
  const app = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "app.json"), "utf8"))
  assert.ok(app.pages.includes("pages/ocr-review/index"))
})
