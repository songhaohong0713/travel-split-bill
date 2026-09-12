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
function previewSettlement(tripId, data) { return request(`/v1/trips/${tripId}/settlements/preview`, { method: "POST", data }) }
module.exports = { request, login, createTrip, previewSettlement }