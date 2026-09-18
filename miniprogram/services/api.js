const app = getApp()

function request(path, options = {}) {
  const accessToken = wx.getStorageSync("access_token")
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${app.globalData.apiBaseUrl}${path}`,
      method: options.method || "GET",
      data: options.data,
      header: { Authorization: accessToken ? `Bearer ${accessToken}` : "", ...(options.header || {}) },
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) return resolve(response.data.data)
        reject(response.data.error || { code: "NETWORK_ERROR", message: "请求未完成" })
      },
      fail() { reject({ code: "NETWORK_ERROR", message: "网络不可用，请稍后重试" }) }
    })
  })
}

function login() {
  return new Promise((resolve, reject) => wx.login({ success: resolve, fail: reject }))
    .then(({ code }) => request("/v1/auth/wechat", { method: "POST", data: { code } }))
    .then((tokens) => { wx.setStorageSync("access_token", tokens.access_token); wx.setStorageSync("refresh_token", tokens.refresh_token); return tokens })
}

function createTrip(name, defaultCurrency) { return request("/v1/trips", { method: "POST", data: { name, default_currency: defaultCurrency } }) }
function uuid4() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (token) => {
    const value = Math.floor(Math.random() * 16)
    return (token === "x" ? value : (value & 0x3) | 0x8).toString(16)
  })
}
function createExpense(tripId, occurredAt, payload) { return request(`/v1/trips/${tripId}/expenses`, { method: "POST", data: { occurred_at: occurredAt, payload }, header: { "Idempotency-Key": uuid4() } }) }function previewSettlement(tripId, data) { return request(`/v1/trips/${tripId}/settlements/preview`, { method: "POST", data }) }
function publishSettlement(tripId, data) { return request(`/v1/trips/${tripId}/settlements/publish`, { method: "POST", data }) }
function createShareLink(tripId, expiresInDays) { return request(`/v1/trips/${tripId}/share-links`, { method: "POST", data: { expires_in_days: expiresInDays } }) }
function uploadReceipt(tripId, filePath) {
  return new Promise((resolve, reject) => wx.getFileInfo({ src: filePath, success: resolve, fail: reject }))
    .then((info) => request("/v1/uploads", { method: "POST", data: { trip_id: tripId, mime_type: "image/jpeg", byte_size: info.size, sha256: "0".repeat(64) } }))
    .then((upload) => new Promise((resolve, reject) => wx.uploadFile({ url: upload.upload_url.startsWith("/") ? `${app.globalData.apiBaseUrl}${upload.upload_url}` : upload.upload_url, filePath, name: "file", success: () => resolve(upload), fail: reject })))
    .then((upload) => request(`/v1/uploads/${upload.image_id}/complete`, { method: "POST" }))
    .then((image) => request("/v1/receipt-jobs", { method: "POST", data: { image_id: image.image_id } }))
}

function getReceiptJob(jobId) { return request(`/v1/receipt-jobs/${jobId}`) }
module.exports = { request, login, createTrip, createExpense, previewSettlement, publishSettlement, createShareLink, uploadReceipt, getReceiptJob }