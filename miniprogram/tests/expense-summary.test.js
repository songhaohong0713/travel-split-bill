const test = require('node:test')
const assert = require('node:assert/strict')
const {expenseSummary} = require('../services/expense-summary')

test('expense display includes exclusive tax and refunds and prioritizes actual payment',()=>{
  const record={id:'e',revision:1,occurred_at:'2026-10-07',payload:{title:'药店',expenses:[{payer_id:'a',items:[{amount:{amount:'1000',currency:'JPY'},tax_included:false,tax_amount:{amount:'100',currency:'JPY'}}],adjustments:[{amount:{amount:'-50',currency:'JPY'}}]}]}}
  assert.equal(expenseSummary(record,'CNY').total,'1050')
  record.payload.expenses[0].actual_payment={amount:'49.80',currency:'CNY'}
  const result=expenseSummary(record,'CNY',{a:'我'})
  assert.equal(result.total,'49.80')
  assert.equal(result.currency,'CNY')
  assert.equal(result.originalTotal,'1050')
  assert.equal(result.payer,'我')
})
