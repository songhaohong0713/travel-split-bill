const QUEUE='travel:v2:pending-trips'
function read(key,fallback) {
  const value=wx.getStorageSync(key)
  return value && typeof value===typeof fallback ? value : fallback
}
function account() { return read('travel:v2:account','') }
function setAccount(id) { wx.setStorageSync('travel:v2:account',String(id || '')) }
function uuid() { return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const r=Math.floor(Math.random()*16);return(c==='x'?r:(r&3)|8).toString(16)}) }
function pending() { return read(QUEUE,[]).filter(t=>!t.owner_binding || t.owner_binding===account()) }
function listCachedTrips() {
  const cached=read('travel:v2:trips:'+account(),[])
  return Array.from(new Map([...cached,...pending()].map(t=>[t.id,t])).values())
}
function rememberTrips(trips) { const key='travel:v2:trips:'+account(),previous=read(key,[]);wx.setStorageSync(key,trips.map(t=>{const old=previous.find(p=>p.id===t.id)||{};return {...t,_summary:t._summary || old._summary,companion_name:t.companion_name || old.companion_name}}));return listCachedTrips() }
function enqueueTrip(name,currency,dates={},id=uuid()) {
  const trip={id,name:name.trim(),default_currency:currency,start_date:dates.start_date || null,end_date:dates.end_date || null,member_count:1,is_owner:true,offline:true,owner_binding:account(),created_at:new Date().toISOString()}
  const queue=read(QUEUE,[]).filter(t=>t.id!==id)
  wx.setStorageSync(QUEUE,[...queue,trip])
  return trip
}
function isLocalTrip(id) { return pending().some(t=>t.id===id) }
function localTrip(id) { return pending().find(t=>t.id===id) }
function updateDates(id,dates) {
  const trip=localTrip(id)
  if(!trip)throw Error('本地旅行不存在')
  const next={...trip,...dates}
  wx.setStorageSync(QUEUE,read(QUEUE,[]).map(t=>t.id===id?next:t))
  return next
}
let synchronization=null
function syncPending(api) {
  if(synchronization)return synchronization
  synchronization=(async()=>{
    if(!pending().length)return
    const capabilities=await api.request('/v1/capabilities').catch(()=>null)
    if(!capabilities || capabilities.client_trip_id!==true)return
    for(const trip of pending()) {
      if(!trip.owner_binding && account()) {
        trip.owner_binding=account()
        wx.setStorageSync(QUEUE,read(QUEUE,[]).map(t=>t.id===trip.id?trip:t))
      }
      try {
        await api.request('/v1/trips',{method:'POST',data:{client_id:trip.id,name:trip.name,default_currency:trip.default_currency}})
        if(trip.start_date || trip.end_date)await api.request(`/v1/trips/${trip.id}`,{method:'PATCH',data:{start_date:trip.start_date,end_date:trip.end_date}})
        wx.setStorageSync(QUEUE,read(QUEUE,[]).filter(t=>t.id!==trip.id))
      } catch(error) {
        // Keep the same UUID after a lost response; the server verifies owner and payload.
        if(error.statusCode===409)wx.setStorageSync('travel:v2:sync-error','旅行同步冲突，请保留本地记录并重试')
        break
      }
    }
  })().finally(()=>{synchronization=null})
  return synchronization
}
function forgetTrip(id){const key='travel:v2:trips:'+account();wx.setStorageSync(key,read(key,[]).filter(t=>t.id!==id));wx.setStorageSync('travel:v2:home-summary:'+account(),null);wx.setStorageSync('travel:v2:ledger:'+account()+':'+id,null)}
function cacheHomeSummary(summary) { wx.setStorageSync('travel:v2:home-summary:'+account(),summary) }
function readHomeSummary() { return read('travel:v2:home-summary:'+account(),null) }
function cacheLedger(id,ledger) { wx.setStorageSync('travel:v2:ledger:'+account()+':'+id,ledger) }
function readLedger(id) { return read('travel:v2:ledger:'+account()+':'+id,null) }
module.exports={forgetTrip,cacheHomeSummary,readHomeSummary,account,setAccount,pending,listCachedTrips,rememberTrips,enqueueTrip,syncPending,isLocalTrip,localTrip,updateDates,cacheLedger,readLedger}
