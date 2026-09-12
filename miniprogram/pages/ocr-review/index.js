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
  const sourceText = typeof value === "string" ? value.trim() : String(value && value.text || "").trim()
  return {
    id: String(index),
    sourceText,
    amount: amountFromText(sourceText),
  }
}

Page({
  data: {
    candidates: [],
    sourceText: "",
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
    this.setData({ selectedId: item.id, sourceText: item.sourceText, amount: item.amount })
  },

  onSourceText(event) { this.setData({ sourceText: event.detail.value }) },
  onAmount(event) { this.setData({ amount: event.detail.value }) },

  applyManualItem() {
    const sourceText = this.data.sourceText.trim()
    const amount = this.data.amount.trim()
    if (!sourceText || !amount) {
      wx.showToast({ title: "请填写商品原文和金额", icon: "none" })
      return
    }
    const channel = this.getOpenerEventChannel && this.getOpenerEventChannel()
    if (channel && channel.emit) channel.emit("ocrCandidateConfirmed", { sourceText, amount })
    wx.navigateBack()
  },

  returnToManualEntry() { wx.navigateBack() },
})
