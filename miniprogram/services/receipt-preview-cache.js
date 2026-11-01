// Session-only index of owned downloadFile temporary files; never user-selected originals.
const entries = new Map()
let owner = ''

function remove(scope) {
  const entry = entries.get(scope)
  entries.delete(scope)
  if (entry && typeof wx.getFileSystemManager === 'function') {
    // WeChat may already have collected this temporary file.
    try { wx.getFileSystemManager().unlink({ filePath: entry.path, fail() {} }) }
    catch (_) { console.warn('receipt_preview_cache_cleanup_failed') }
  }
}
function clear() {
  for (const scope of entries.keys()) remove(scope)
  owner = ''
}
function forAccount(account) {
  if (owner !== account) { clear(); owner = account }
}
function get(account, scope, version) {
  forAccount(account)
  const entry = entries.get(scope)
  if (!account || !entry) return ''
  if (entry.version !== version) { remove(scope); return '' }
  entries.delete(scope); entries.set(scope, entry)
  return entry.path
}
function put(account, scope, version, path) {
  forAccount(account)
  if (!account) return
  if (entries.get(scope)?.path !== path) remove(scope)
  entries.delete(scope); entries.set(scope, { version, path })
  while (entries.size > 3) remove(entries.keys().next().value)
}
module.exports = { get, put, remove, clear, forAccount }
