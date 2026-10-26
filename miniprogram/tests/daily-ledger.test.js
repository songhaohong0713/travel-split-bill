const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const path = require('node:path')

function page(api, wx = {}) {
  let definition
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../pages/day-bill/index.js'), 'utf8'), {
    require(name) { if(name.includes('ledger-view')) { const source=fs.readFileSync(path.join(__dirname,'../services/ledger-view.js'),'utf8'); const module={exports:{}}; vm.runInNewContext(source,{module,wx:{showModal(o){o.success({confirm:true})}},Date,Promise}); return module.exports }; return name.includes('expense-summary') ? require('../services/expense-summary') : name.includes('member-labels') ? require('../services/member-labels') : api },
    Page(value) { definition = value }, wx: { showToast() {}, ...wx }, Date, Promise,
  })
  return { ...definition, data: { ...definition.data }, setData(changes) { Object.assign(this.data, changes) } }
}

test('daily page submits only server preview token and reloads remaining balance', async () => {
  const calls = []
  let paid = false
  const api = {
    listTripMembers: async () => [{id:'a', is_current:true}, {id:'b'}],
    listExpenses: async () => [],
    getLedger: async () => ({days:[{date:'2026-10-07',status:paid?'settled':'pending',groups:[],pending:[]}],payments:[]}),
    previewPayment: async (_, dates) => { calls.push(dates); return {token:'server-token',dates:['2026-10-07'],groups:[{currency:'CNY',amount:'30.00',from_participant_id:'b',to_participant_id:'a'}]} },
    markPayment: async (_, value) => { calls.push(value); paid = true },
  }
  const p = page(api, {showModal(options) { options.success({confirm:true}) }})
  await p.onLoad({tripId:'t',date:'2026-10-07'})
  await p.confirmPayment()
  const submitted = calls.find(value => value && value.token)
  assert.equal(submitted.token, 'server-token')
  assert.equal(submitted.currency, 'CNY')
  assert.equal(p.data.day.status, 'settled')
})

test('payment conflict refreshes without automatically resubmitting', async () => {
  let submissions = 0
  const p = page({
    listTripMembers:async()=>[], listExpenses:async()=>[],
    getLedger:async()=>({days:[],payments:[]}),
    previewPayment:async()=>({token:'old',dates:[],groups:[{currency:'CNY',amount:'30'}]}),
    markPayment:async()=>{ submissions++; throw {code:'LEDGER_CONFLICT',message:'账单已变化'} },
  }, {showModal(options) {options.success({confirm:true})}})
  await p.onLoad({tripId:'t'})
  await p.confirmPayment()
  assert.equal(submissions, 1)
  assert.equal(p.data.busy, false)
  assert.match(p.data.error, /账单已变化/)
})

test('failed refresh clears financial preview and successful retry clears error', async () => {
  let failing=false
  const p=page({listTripMembers:async()=>[],listExpenses:async()=>[],getLedger:async()=>({days:[{date:'2026-10-07',status:'pending'}],payments:[]}),previewPayment:async()=>{if(failing) throw {message:'无法计算'}; return {dates:['2026-10-07'],groups:[{currency:'CNY',amount:'200'}]}}})
  await p.onLoad({tripId:'t'})
  assert.equal(p.data.previewGroups.length,1)
  failing=true
  await p.reload()
  assert.equal(p.data.previewGroups.length,0)
  assert.equal(p.data.scopeDates.length,0)
  assert.equal(p.data.error,'无法计算')
  failing=false
  await p.reload()
  assert.equal(p.data.error,'')
  assert.equal(p.data.previewGroups.length,1)
})
