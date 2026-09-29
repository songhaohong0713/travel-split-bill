function decodeCandidates(value) {
  if (!value) return []
  try {
    const parsed = JSON.parse(decodeURIComponent(value))
    return Array.isArray(parsed) ? parsed : []
  } catch (_) {
    return []
  }
}

function amountFromText(value) {
  const matches = String(value).replace(/,/g, "").match(/(?:^|\s)(\d+(?:\.\d{1,2})?)(?:\s|$)/g)
  if (!matches || !matches.length) return ""
  return matches[matches.length - 1].trim()
}

function candidateFrom(value, index) {
  const sourceText = typeof value === "string" ? value.trim() : String(value && (value.source_text || value.text) || "").trim()
  const translatedText = typeof value === "string" ? "" : String(value && (value.translated_text || value.translation) || "").trim()
  return {
    id: String(index),
    sourceText,
    translatedText,
    amount: typeof value === "string" ? amountFromText(sourceText) : String(value && value.amount || amountFromText(sourceText)).trim(),
    currency: typeof value === "string" ? "" : String(value && value.currency || "").trim().toUpperCase(),
    selected: true,
  }
}

Page({
  data: {
    candidates: [],
    sourceText: "",
    translatedText: "",
    amount: "",
    status: "needs_review",
    errorCode: "",
    selectedId: "",
  },

  onLoad(query) {
    const candidates = decodeCandidates(query.candidates).map(candidateFrom).filter((item) => item.sourceText)
    this.setData({
      candidates,
      status: query.status || "needs_review",
      errorCode: query.errorCode || "",
    })
  },

  chooseCandidate(event) {
    const item = this.data.candidates.find((candidate) => candidate.id === event.currentTarget.dataset.id)
    if (!item) return
    this.setData({ selectedId: item.id, sourceText: item.sourceText, translatedText: item.translatedText, amount: item.amount })
  },

  toggleCandidate(event) {
    const id = event.currentTarget.dataset.id
    this.setData({ candidates: this.data.candidates.map((item) => item.id === id ? { ...item, selected: !item.selected } : item) })
  },

  updateCandidate(event) {
    const { id, field } = event.currentTarget.dataset
    this.setData({ candidates: this.data.candidates.map((item) => item.id === id ? { ...item, [field]: event.detail.value } : item) })
  },

  onSourceText(event) { this.setData({ sourceText: event.detail.value }) },
  onTranslatedText(event) { this.setData({ translatedText: event.detail.value }) },
  onAmount(event) { this.setData({ amount: event.detail.value }) },

  applyManualItem() {
    const sourceText = this.data.sourceText.trim()
    const amount = this.data.amount.trim()
    if (!sourceText || !amount) {
      wx.showToast({ title: "请填写商品原文和金额", icon: "none" })
      return
    }
    const channel = this.getOpenerEventChannel && this.getOpenerEventChannel()
    if (channel && channel.emit) channel.emit("ocrCandidateConfirmed", { sourceText, translatedText: this.data.translatedText.trim(), amount })
    wx.navigateBack()
  },

  confirmSelected() {
    const selected = this.data.candidates.filter((item) => item.selected && item.sourceText.trim() && item.translatedText.trim() && item.amount.trim()).map((item) => ({ source_text: item.sourceText.trim(), translated_text: item.translatedText.trim(), amount: item.amount.trim(), currency: item.currency.trim().toUpperCase() }))
    if (!selected.length) return wx.showToast({ title: "请至少保留一件完整商品", icon: "none" })
    const channel = this.getOpenerEventChannel && this.getOpenerEventChannel()
    if (channel && channel.emit) channel.emit("ocrCandidatesConfirmed", selected)
    wx.navigateBack()
  },

  returnToManualEntry() { wx.navigateBack() },
})
