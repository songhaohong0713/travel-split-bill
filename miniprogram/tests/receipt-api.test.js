const test=require('node:test')
const assert=require('node:assert/strict')
const fs=require('node:fs')
const vm=require('node:vm')
const path=require('node:path')

test('receipt upload refreshes expired JWT once and keeps attachment-only mode and file',async()=>{
 const uploads=[],reads=[],storage={access_token:'old',refresh_token:'refresh'};const module={exports:{}}
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../services/api.js'),'utf8'),{
  getApp:()=>({globalData:{apiBaseUrl:'https://backend.example'}}),module,exports:module.exports,
  require:()=>({isLocalTrip:()=>false}),console,Promise,
  wx:{getStorageSync:k=>storage[k],setStorageSync:(k,v)=>storage[k]=v,
   uploadFile:o=>{uploads.push(o);o.success({statusCode:uploads.length===1?401:202,data:JSON.stringify(uploads.length===1?{error:{code:'TOKEN_EXPIRED'}}:{data:{id:'saved-job'}})})},
   request:o=>{reads.push(o);o.success({statusCode:200,data:{data:o.url.endsWith('/auth/refresh')?{access_token:'fresh',refresh_token:'next'}:{status:'available',url:'https://private.example/short'}}})}},
 })
 const api=module.exports
 assert.equal((await api.uploadReceipt('trip','local-original.jpg',{recognize:false})).id,'saved-job')
 assert.equal(uploads.length,2);assert.equal(uploads[0].header.Authorization,'Bearer old');assert.equal(uploads[1].header.Authorization,'Bearer fresh')
 assert.ok(uploads.every(o=>o.filePath==='local-original.jpg'&&o.formData.recognize==='false'))
 assert.equal((await api.getExpenseReceipt('trip','expense')).status,'available')
 assert.equal(reads[1].url,'https://backend.example/v1/trips/trip/expenses/expense/receipt')
 assert.equal(reads[1].header.Authorization,'Bearer fresh')
})

test('original download uses the existing backend JWT, reports progress and refreshes once',async()=>{
 const files=[],progress=[],storage={access_token:'old',refresh_token:'refresh'},module={exports:{}}
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../services/api.js'),'utf8'),{
  getApp:()=>({globalData:{apiBaseUrl:'https://backend.example'}}),module,exports:module.exports,require:()=>({}),console,Promise,
  wx:{getStorageSync:k=>storage[k],setStorageSync:(k,v)=>storage[k]=v,
   request:o=>o.success({statusCode:200,data:{data:{access_token:'fresh',refresh_token:'next'}}}),
   downloadFile:o=>{files.push(o);o.success({statusCode:files.length===1?401:200,tempFilePath:'/tmp/original.png'});return {onProgressUpdate:cb=>cb({progress:50})}}},
 })
 assert.equal(await module.exports.downloadExpenseReceipt('t','e',p=>progress.push(p.progress)),'/tmp/original.png')
 assert.equal(files.length,2);assert.equal(files[0].header.Authorization,'Bearer old');assert.equal(files[1].header.Authorization,'Bearer fresh');assert.equal(files[1].url,'https://backend.example/v1/trips/t/expenses/e/receipt/file');assert.equal(files[1].timeout,120000);assert.ok(progress.includes(50))
})

test('login clears preview cache on an account change, not on token refresh for the same account',async()=>{
 const values={access_token:'old',refresh_token:'refresh','travel:v2:account':'a'},cleared=[],module={exports:{}}
 let account='b'
 const token=()=> 'header.'+Buffer.from(JSON.stringify({sub:account})).toString('base64url')+'.signature'
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../services/api.js'),'utf8'),{
  getApp:()=>({globalData:{apiBaseUrl:'https://backend.example'}}),module,exports:module.exports,console,Promise,
  require:name=>name.includes('offline-trips')?{setAccount:id=>values['travel:v2:account']=id}:{forAccount:id=>cleared.push(id)},
  wx:{getStorageSync:k=>values[k],setStorageSync:(k,v)=>values[k]=v,base64ToArrayBuffer:s=>Uint8Array.from(Buffer.from(s,'base64')).buffer,
   request:o=>o.success({statusCode:200,data:{data:{access_token:token(),refresh_token:'next'}}})},
 })
 await module.exports.login();await module.exports.login();assert.deepEqual(cleared,['b']);assert.equal(values['travel:v2:account'],'b')
})
