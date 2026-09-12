const { previewSettlement } = require("../../services/api")
Page({
  data: { tripId: "", currency: "CNY", amount: "", payer: "我", friend: "", allocationIndex: 0, allocationLabels: ["付款人自己买", "同行人自己买", "两人均分"], receiptPath: "", ocrStatus: "未上传" },
  onLoad(q) { this.setData({ tripId: q.tripId, currency: q.currency }) },
  chooseReceipt() { wx.chooseMedia({ count: 1, mediaType: ["image"], sourceType: ["camera", "album"], success: ({ tempFiles }) => wx.compressImage({ src: tempFiles[0].tempFilePath, quality: 80, success: ({ tempFilePath }) => { this.setData({ receiptPath: tempFilePath, ocrStatus: "等待上传识别" }); wx.showToast({ title: "已选择，等待上传识别", icon: "none" }) } }) }) },
  onAmount(e) { this.setData({ amount: e.detail.value }) }, onPayer(e) { this.setData({ payer: e.detail.value }) }, onFriend(e) { this.setData({ friend: e.detail.value }) }, onAllocation(e) { this.setData({ allocationIndex: Number(e.detail.value) }) },
  preview() {
    const { tripId, currency, amount, payer, friend, allocationIndex } = this.data
    if (!amount || !payer) return wx.showToast({ title: "请填写金额和付款人", icon: "none" })
    if (allocationIndex > 0 && !friend.trim()) return wx.showToast({ title: "请填写同行人", icon: "none" })
    const allocation = allocationIndex === 0 ? { [payer]: "1" } : allocationIndex === 1 ? { [friend]: "1" } : { [payer]: "0.5", [friend]: "0.5" }
    const participants = friend.trim() ? [payer, friend] : [payer]
    previewSettlement(tripId, { participants, settlement_currency: currency, expenses: [{ expense_id: "manual", payer_id: payer, items: [{ item_id: "manual-item", amount: { currency, amount }, allocation }], actual_payment: { currency, amount } }] }).then((result) => { const transfer = result.transfers[0]; wx.showModal({ title: "分账预览", content: transfer ? `${transfer.from_participant_id} 需转给 ${transfer.to_participant_id} ${transfer.amount.amount} ${transfer.amount.currency}` : "无需转账", showCancel: false }) }).catch((e) => wx.showToast({ title: e.message || "预览失败", icon: "none" }))
  }
})