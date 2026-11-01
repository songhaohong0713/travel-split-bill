const test = require('node:test')
const assert = require('node:assert/strict')
const m = require('../services/money')
test('decimal conversion and per expense totals match design', () => {
  assert.equal(m.convert('1280','0.04236'),'54.22')
  assert.equal(m.convert('2964','0.04236'),'125.56')
  assert.equal(m.convert('5727','0.04236'),'242.60')
  assert.equal(m.sum(['54.22','125.56','242.60'],'CNY'),'422.38')
})
test('currency precision and payer remainder are independent of member order', () => {
  assert.equal(m.formatMoney('JPY','1280'),'1,280 JPY')
  assert.equal(m.formatMoney('CNY','1204.5'),'¥1,204.50')
  assert.deepEqual(m.split('111','JPY',['a','b'],'b'),[{id:'a',amount:'55'},{id:'b',amount:'56'}])
  assert.deepEqual(m.split('4.71','CNY',['a','b'],'a'),[{id:'a',amount:'2.36'},{id:'b',amount:'2.35'}])
})
test('item owners and custom fixed amounts conserve every minor unit',()=>{
  const items=[{amount:'1280',owner:'a'},{amount:'836',owner:'equal'},{amount:'528',owner:'b'},{amount:'220',owner:'equal'},{amount:'100',owner:'equal'}]
  assert.deepEqual(m.split('2964','JPY',['a','b'],'a','items',items),[{id:'a',amount:'1858'},{id:'b',amount:'1106'}])
  assert.deepEqual(m.convertShares(m.split('2964','JPY',['a','b'],'a','items',items),'0.04236','a'),[{id:'a',amount:'78.71'},{id:'b',amount:'46.85'}])
  assert.throws(()=>m.split('100','CNY',['a','b'],'a','custom',[],{a:'30',b:'60'}))
})
