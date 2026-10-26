const test = require('node:test')
const assert = require('node:assert/strict')
const ui = require('../services/ledger-view')
test('trip cards omit unknown dates and expose only three statuses', () => {
  const card = ui.tripCard({id:'t',name:'东京',member_count:2}, {day_count:3,pending:[{currency:'CNY',amount:'30.00'}]}, '2026-10-26')
  assert.equal(card.subtitle,'3 天 · 成员 2/2')
  assert.equal(card.statusText,'待结清 ¥30.00')
  assert.equal(ui.tripCard({member_count:1},{day_count:1,has_recording:true,pending:[]}).statusText,'记录中')
  assert.equal(ui.tripCard({member_count:2},{day_count:0,pending:[]}).statusText,'已结清')
})
test('ongoing uses date interval or latest expense in two days', () => {
  assert.equal(ui.inProgress({start_date:'2026-10-25',end_date:'2026-10-28'},'2026-10-26'),true)
  assert.equal(ui.inProgress({latest_expense_date:'2026-10-24'},'2026-10-26'),true)
  assert.equal(ui.inProgress({latest_expense_date:'2026-10-23'},'2026-10-26'),false)
})
test('day view uses only the daily allocation of a combined transfer',()=>{
 const payment={amount:'100.00',currency:'CNY',allocations:[{date:'2026-10-24',amount:'40.00'},{date:'2026-10-25',amount:'60.00'}]}
 assert.equal(ui.dayPayment(payment,'2026-10-24',['a','b']).amount,'40.00')
 assert.equal(ui.dayPayment(payment,'2026-10-25',['a','b']).amount,'60.00')
 assert.equal(ui.dayPayment(payment,'',['a','b']).amount,'100.00')
})
