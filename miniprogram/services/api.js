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
      header: { "X-Client-Timezone-Offset": String(new Date().getTimezoneOffset()), ...authorizationHeader(), ...(options.header || {}) },
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
  if (typeof wx.base64ToArrayBuffer === 'function') {
    const previousAccount = String(wx.getStorageSync('travel:v2:account') || '')
    try {
      const encoded=tokens.access_token.split('.')[1].replace(/-/g,'+').replace(/_/g,'/')
      const bytes=new Uint8Array(wx.base64ToArrayBuffer(encoded+'='.repeat((4-encoded.length%4)%4)))
      const claims=JSON.parse(Array.from(bytes).map(n=>String.fromCharCode(n)).join(''))
      require('./offline-trips').setAccount(claims.sub)
    } catch (_) { require('./offline-trips').setAccount('') }
    const nextAccount = String(wx.getStorageSync('travel:v2:account') || '')
    if (previousAccount !== nextAccount) require('./receipt-preview-cache').forAccount(nextAccount)
  }
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

async function createTrip(name, defaultCurrency, clientId) {
  const id=clientId || uuid4()
  try {
    const capabilities=await request('/v1/capabilities')
    if(!capabilities || capabilities.client_trip_id!==true)throw {statusCode:409,message:'服务尚未升级，暂不能创建旅行，请稍后重试'}
    return await request('/v1/trips',{method:'POST',data:{client_id:id,name,default_currency:defaultCurrency,timezone_offset_minutes:new Date().getTimezoneOffset()}})
  }
  catch(error) {
    if(error.statusCode && error.statusCode<500)throw error
    return require('./offline-trips').enqueueTrip(name,defaultCurrency,{},id)
  }
}
function getExchangeRate(date, fromCurrency, toCurrency) { return request(`/v1/exchange-rates?date=${encodeURIComponent(date)}&from_currency=${encodeURIComponent(fromCurrency)}&to_currency=${encodeURIComponent(toCurrency)}`) }
async function listTrips() {
  const offline=require('./offline-trips')
  await offline.syncPending(module.exports)
  const trips=await request('/v1/trips')
  return offline.rememberTrips(trips)
}
function listTripMembers(tripId) {
  const offline=require('./offline-trips')
  if(offline.isLocalTrip(tripId))return Promise.resolve([{id:offline.account() || 'local-self',is_current:true,is_owner:true,nickname:'我'}])
  return request(`/v1/trips/${tripId}/members`)
}
function createTripInvite(tripId) {
  if(require('./offline-trips').isLocalTrip(tripId))return Promise.reject({message:'旅行同步后即可邀请同行人'})
  return request(`/v1/trips/${tripId}/invites`, { method: "POST" }) }
async function getTripInvite(token) { const invite=await request(`/v1/trip-invites/${encodeURIComponent(token)}`);if(typeof invite.already_member!=='boolean'){const trips=await request('/v1/trips');if(!Array.isArray(trips))throw {message:'暂时无法核对成员状态，请重试'};invite.already_member=trips.some(t=>t.id===(invite.trip_id || invite.id))}return invite }
function acceptTripInvite(token) { return request(`/v1/trip-invites/${encodeURIComponent(token)}/accept`, { method: "POST" }) }
function uuid4() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (token) => {
    const value = Math.floor(Math.random() * 16)
    return (token === "x" ? value : (value & 0x3) | 0x8).toString(16)
  })
}
async function requireCalculationVersion(payload) {
  if(!(payload.expenses || []).some(b=>b.calculation_version==='v2'))return
  let capabilities
  try { capabilities=await request('/v1/capabilities') }
  catch(e) { if(e.statusCode===404 || e.statusCode===405)throw {message:'记账服务需要更新，请保留当前内容，更新后再保存'};throw e }
  if(capabilities.calculation_version!=='v2')throw {message:'记账服务需要更新，请保留当前内容，更新后再保存'}
}
async function createExpense(tripId, occurredAt, payload, idempotencyKey) {
  if(require('./offline-trips').isLocalTrip(tripId))return Promise.reject({message:'旅行尚未同步，请恢复网络后再保存消费'})
  await requireCalculationVersion(payload)
  return request(`/v1/trips/${tripId}/expenses`, { method: "POST", data: { occurred_at: occurredAt, payload }, header: { "Idempotency-Key": idempotencyKey || uuid4() } }) }function previewSettlement(tripId, data) { return request(`/v1/trips/${tripId}/settlements/preview`, { method: "POST", data }) }
function listExpenses(tripId) {
  if(require('./offline-trips').isLocalTrip(tripId))return Promise.resolve([])
  return request(`/v1/trips/${tripId}/expenses`)
}
async function updateExpense(tripId, expenseId, revision, occurredAt, payload) { await requireCalculationVersion(payload);return request(`/v1/trips/${tripId}/expenses/${expenseId}`, { method: "PATCH", data: { revision, occurred_at: occurredAt, payload } }) }
function deleteExpense(tripId, expenseId) { return request(`/v1/trips/${tripId}/expenses/${expenseId}`, { method: "DELETE" }) }
async function deleteTrip(tripId) { const result=await request(`/v1/trips/${tripId}`, { method: "DELETE" });require("./offline-trips").forgetTrip(tripId);return result }
function publishSettlement(tripId, data) { return request(`/v1/trips/${tripId}/settlements/publish`, { method: "POST", data }) }
function createShareLink(tripId, expiresInDays) { return request(`/v1/trips/${tripId}/share-links`, { method: "POST", data: { expires_in_days: expiresInDays } }) }
async function uploadReceipt(tripId, filePath, options = {}) {
  await ensureAuthenticated()
  return new Promise((resolve, reject) => wx.uploadFile({
    url: `${app.globalData.apiBaseUrl}/v1/receipt-jobs`, filePath, name: "file",
    formData: { trip_id: tripId, recognize: options.recognize === false ? 'false' : 'true' }, header: authorizationHeader(),
    success(response) {
      let body
      try { body = typeof response.data === "string" ? JSON.parse(response.data) : response.data } catch (_) { return reject({ code: "NETWORK_ERROR", message: "识别服务返回异常" }) }
      if (response.statusCode === 202) return resolve(body.data)
      reject({ ...(body.error || { code: "NETWORK_ERROR", message: "识别请求未完成" }), statusCode: response.statusCode })
    },
    fail(error) { console.error("receipt_upload_failed", { errMsg: error.errMsg || "", hasFilePath: Boolean(filePath) }); reject({ code: "NETWORK_ERROR", message: "网络不可用，请稍后重试" }) },
  })).catch(async error => {
    if (error.statusCode === 401 && !options.authRetried) {
      await ensureAuthenticated(true)
      return uploadReceipt(tripId, filePath, { ...options, authRetried: true })
    }
    throw error
  })
}

function getReceiptJob(jobId) { return request(`/v1/receipt-jobs/${jobId}`) }
function getExpenseReceipt(tripId, expenseId) { return request(`/v1/trips/${encodeURIComponent(tripId)}/expenses/${encodeURIComponent(expenseId)}/receipt`) }
async function downloadExpenseReceipt(tripId, expenseId, onProgress, authRetried = false) {
  await ensureAuthenticated()
  return new Promise((resolve, reject) => {
    const task = wx.downloadFile({
      url: `${app.globalData.apiBaseUrl}/v1/trips/${encodeURIComponent(tripId)}/expenses/${encodeURIComponent(expenseId)}/receipt/file`,
      header: authorizationHeader(), timeout: 120000,
      success(r) {
        if (r.statusCode === 200 && r.tempFilePath) return resolve(r.tempFilePath)
        reject({ statusCode: r.statusCode, code: r.statusCode === 404 ? 'RECEIPT_IMAGE_MISSING' : 'RECEIPT_DOWNLOAD_FAILED', message: r.statusCode === 404 ? '原图不存在，请重新上传小票' : '原图暂时无法下载，请稍后重试' })
      },
      fail(e) { reject({ code: 'RECEIPT_DOWNLOAD_FAILED', message: String(e.errMsg || '').includes('timeout') ? '原图下载超时，请重试' : '原图下载失败，请检查网络后重试' }) },
    })
    if (onProgress && task && task.onProgressUpdate) task.onProgressUpdate(onProgress)
  }).catch(async e => {
    if (e.statusCode === 401 && !authRetried) { await ensureAuthenticated(true); return downloadExpenseReceipt(tripId, expenseId, onProgress, true) }
    throw e
  })
}
async function getLedger(tripId) {
  const offline=require('./offline-trips')
  if(offline.isLocalTrip(tripId))return {members:[offline.account() || 'local-self'],days:[],payments:[],payer_marks:[],summary:[],expense_summaries:[],offline:true}
  const result=await request(`/v1/trips/${tripId}/ledger`)
  if(result.contract_version!==2)result.compatibilityError="服务尚未更新，待结清状态暂不可用；账单明细仍可查看"
  offline.cacheLedger(tripId,result)
  return result
}
function closeDay(tripId, date) { return request(`/v1/trips/${tripId}/days/${date}/close`, { method: 'POST' }) }
function previewPayment(tripId, dates = null) { return request(`/v1/trips/${tripId}/payments/preview`, { method: 'POST', data: { dates } }) }
function markPayment(tripId, data) { return request(`/v1/trips/${tripId}/payments`, { method: 'POST', data }) }
function revokePayment(tripId, id) { return request(`/v1/trips/${tripId}/payments/${id}/revoke`, { method: 'POST' }) }
async function ledgerSummary() { const result=await request('/v1/trips/ledger-summary');if(result.contract_version!==2)result.compatibilityError='服务尚未更新，待结清汇总暂不可用';return result }
function updateTripDates(tripId, data) {
  const offline=require('./offline-trips')
  if(offline.isLocalTrip(tripId))return Promise.resolve(offline.updateDates(tripId,data))
  return request(`/v1/trips/${tripId}`, {method:'PATCH',data})
}
function markPaid(tripId,data) { return request(`/v1/trips/${tripId}/payments/mark-paid`,{method:'POST',data}) }
function revokePayerMark(tripId,id) { return request(`/v1/trips/${tripId}/payer-marks/${id}/revoke`,{method:'POST'}) }
module.exports = { markPaid, revokePayerMark, getLedger, closeDay, previewPayment, markPayment, revokePayment, ledgerSummary, updateTripDates }
Object.assign(module.exports, { request, login, ensureAuthenticated, createTrip, getExchangeRate, listTrips, listTripMembers, createTripInvite, getTripInvite, acceptTripInvite, createExpense, listExpenses, updateExpense, deleteExpense, deleteTrip, previewSettlement, publishSettlement, createShareLink, uploadReceipt, getReceiptJob, getExpenseReceipt, downloadExpenseReceipt })

function updateProfile(nickname){return request('/v1/me',{method:'PATCH',data:{nickname}})}
function updateTripName(tripId,name){
 const offline=require('./offline-trips')
 if(offline.isLocalTrip(tripId))return Promise.resolve(offline.updateDates(tripId,{name}))
 return request(`/v1/trips/${tripId}/name`,{method:'PATCH',data:{name}})
}
Object.assign(module.exports,{updateProfile,updateTripName})

function updateTripNickname(tripId,nickname){return request(`/v1/trips/${tripId}/members/me`,{method:"PATCH",data:{nickname}})}
module.exports.updateTripNickname=updateTripNickname

function setTripTimezone(tripId,offset){return request(`/v1/trips/${tripId}/timezone`,{method:"PATCH",data:{timezone_offset_minutes:offset}})}
module.exports.setTripTimezone=setTripTimezone
