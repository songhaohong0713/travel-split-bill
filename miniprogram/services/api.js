const app = getApp()
let authenticationPromise = null

function authorizationHeader() {
  const accessToken = wx.getStorageSync("access_token")
  return { Authorization: accessToken ? `Bearer ${accessToken}` : "" }
}

function rawRequest(path, options = {}) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${app.globalData.apiBaseUrl}${path}`,
      method: options.method || "GET",
      data: options.data,
      header: { ...authorizationHeader(), ...(options.header || {}) },
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) return resolve(response.data.data)
        reject({ ...(response.data && response.data.error || { code: "NETWORK_ERROR", message: "请求未完成" }), statusCode: response.statusCode })
      },
      fail() { reject({ code: "NETWORK_ERROR", message: "网络不可用，请稍后重试" }) }
    })
  })
}

function storeTokens(tokens) {
  wx.setStorageSync("access_token", tokens.access_token)
  wx.setStorageSync("refresh_token", tokens.refresh_token)
  return tokens
}

function wechatLogin() {
  return new Promise((resolve, reject) => wx.login({ success: resolve, fail: reject }))
    .then(({ code }) => rawRequest("/v1/auth/wechat", { method: "POST", data: { code } }))
    .then(storeTokens)
}

function ensureAuthenticated(force = false) {
  if (!force && wx.getStorageSync("access_token")) return Promise.resolve()
  if (authenticationPromise) return authenticationPromise
  const refreshToken = wx.getStorageSync("refresh_token")
  const authenticate = refreshToken
    ? rawRequest("/v1/auth/refresh", { method: "POST", data: { refresh_token: refreshToken } }).then(storeTokens).catch((error) => error.statusCode === 401 ? wechatLogin() : Promise.reject(error))
    : wechatLogin()
  authenticationPromise = authenticate.finally(() => { authenticationPromise = null })
  return authenticationPromise
}

function request(path, options = {}) {
  return rawRequest(path, options).catch((error) => {
    if (error.statusCode !== 401 || options.authRetried || path.startsWith("/v1/auth/")) throw error
    return ensureAuthenticated(true).then(() => rawRequest(path, { ...options, authRetried: true }))
  })
}

function login() { return ensureAuthenticated(true) }

function createTrip(name, defaultCurrency) { return request("/v1/trips", { method: "POST", data: { name, default_currency: defaultCurrency } }) }
function getExchangeRate(date, fromCurrency, toCurrency) { return request(`/v1/exchange-rates?date=${encodeURIComponent(date)}&from_currency=${encodeURIComponent(fromCurrency)}&to_currency=${encodeURIComponent(toCurrency)}`) }
function listTrips() { return request("/v1/trips") }
function listTripMembers(tripId) { return request(`/v1/trips/${tripId}/members`) }
function createTripInvite(tripId) { return request(`/v1/trips/${tripId}/invites`, { method: "POST" }) }
function getTripInvite(token) { return request(`/v1/trip-invites/${encodeURIComponent(token)}`) }
function acceptTripInvite(token) { return request(`/v1/trip-invites/${encodeURIComponent(token)}/accept`, { method: "POST" }) }
function uuid4() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (token) => {
    const value = Math.floor(Math.random() * 16)
    return (token === "x" ? value : (value & 0x3) | 0x8).toString(16)
  })
}
function createExpense(tripId, occurredAt, payload) { return request(`/v1/trips/${tripId}/expenses`, { method: "POST", data: { occurred_at: occurredAt, payload }, header: { "Idempotency-Key": uuid4() } }) }function previewSettlement(tripId, data) { return request(`/v1/trips/${tripId}/settlements/preview`, { method: "POST", data }) }
function listExpenses(tripId) { return request(`/v1/trips/${tripId}/expenses`) }
function updateExpense(tripId, expenseId, revision, occurredAt, payload) { return request(`/v1/trips/${tripId}/expenses/${expenseId}`, { method: "PATCH", data: { revision, occurred_at: occurredAt, payload } }) }
function deleteExpense(tripId, expenseId) { return request(`/v1/trips/${tripId}/expenses/${expenseId}`, { method: "DELETE" }) }
function deleteTrip(tripId) { return request(`/v1/trips/${tripId}`, { method: "DELETE" }) }
function publishSettlement(tripId, data) { return request(`/v1/trips/${tripId}/settlements/publish`, { method: "POST", data }) }
function createShareLink(tripId, expiresInDays) { return request(`/v1/trips/${tripId}/share-links`, { method: "POST", data: { expires_in_days: expiresInDays } }) }
function uploadReceipt(tripId, filePath) {
  return new Promise((resolve, reject) => wx.uploadFile({
    url: `${app.globalData.apiBaseUrl}/v1/receipt-jobs`, filePath, name: "file",
    formData: { trip_id: tripId }, header: authorizationHeader(),
    success(response) {
      let body
      try { body = typeof response.data === "string" ? JSON.parse(response.data) : response.data } catch (_) { return reject({ code: "NETWORK_ERROR", message: "识别服务返回异常" }) }
      if (response.statusCode === 202) return resolve(body.data)
      reject(body.error || { code: "NETWORK_ERROR", message: "识别请求未完成" })
    },
    fail(error) { console.error("receipt_upload_failed", { errMsg: error.errMsg || "", hasFilePath: Boolean(filePath) }); reject({ code: "NETWORK_ERROR", message: "网络不可用，请稍后重试" }) },
  }))
}

function getReceiptJob(jobId) { return request(`/v1/receipt-jobs/${jobId}`) }
module.exports = { request, login, ensureAuthenticated, createTrip, getExchangeRate, listTrips, listTripMembers, createTripInvite, getTripInvite, acceptTripInvite, createExpense, listExpenses, updateExpense, deleteExpense, deleteTrip, previewSettlement, publishSettlement, createShareLink, uploadReceipt, getReceiptJob }
