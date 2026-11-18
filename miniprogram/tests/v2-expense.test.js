const test=require('node:test');const assert=require('node:assert/strict');const f=require('../services/expense-form');
const members=[{id:'me',label:'我'},{id:'friend',label:'小周'}];
const base={tripId:'t',amount:'1281',currency:'JPY',settlementCurrency:'CNY',referenceRate:'0.04236',payer:'me',memberOptions:members,splitMode:'even',items:[],adjustments:[]};
test('solo saves automatically with no invented friend',()=>{const d={...base,memberOptions:members.slice(0,1)};assert.equal(f.validate(d),'');assert.equal(f.previewRows(d).length,1)});
test('JPY refuses fractions and custom amounts must add to original total',()=>{assert.match(f.validate({...base,amount:'1.5'}),/最小单位/);assert.equal(f.validate({...base,splitMode:'custom',customShares:{me:'641',friend:'640'}}),'');assert.match(f.validate({...base,splitMode:'custom',customShares:{me:'640',friend:'640'}}),/合计/)});
test('preview gives original remainder and converted remainder to payer',()=>{const rows=f.previewRows(base);assert.equal(rows[0].amount,'641');assert.equal(rows[1].amount,'640');assert.equal(rows[0].convertedAmount,'27.15');assert.equal(rows[1].convertedAmount,'27.11')});
test('new expense carries v2 and custom amount weights',()=>{const bill=f.buildPayload({...base,splitMode:'custom',customShares:{me:'641',friend:'640'}}).expenses[0];assert.equal(bill.calculation_version,'v2');assert.deepEqual(bill.items[0].allocation_amounts,{me:'641',friend:'640'})});
test('item ownership goes directly into allocation without changing tax',()=>{const p=f.buildPayload({...base,splitMode:'items',items:[{id:'1',name:'tea',amount:'1281',owner:'friend',ownerChanged:true,raw:{tax_included:true}}]});assert.deepEqual(p.expenses[0].items[0].allocation,{friend:'1'});assert.equal(p.expenses[0].items[0].tax_included,true)});

test('custom item amounts coexist with shared adjustments and additional tax',()=>{
 const d={...base,amount:'1000',splitMode:'custom',splitChanged:true,customShares:{me:'600',friend:'400'},adjustments:[{id:'discount',amount:'-100'}],items:[{id:'1',amount:'1000',raw:{allocation:{me:'0.5',friend:'0.5'},tax_amount:{currency:'JPY',amount:'100'},tax_included:false}}]};
 assert.equal(f.validate(d),'');
 const payload=f.buildPayload(d),bill=payload.expenses[0];
 assert.deepEqual(bill.adjustments[0].allocation,{me:'0.5',friend:'0.5'});
 const rows=f.previewRows(d);assert.equal(rows[0].amount,'610');assert.equal(rows[1].amount,'390');
});

test('editing custom item values rebuilds exact allocations and untouched reload retains them',()=>{
 const original=f.buildPayload({...base,amount:'3',splitMode:'custom',customShares:{me:'2',friend:'1'},items:[{id:'1',amount:'1'},{id:'2',amount:'2'}]});
 const data={...base,...f.hydrate({id:'r',payload:original}),financialChanged:true,items:[{...f.hydrate({payload:original}).items[0],amount:'2'},{...f.hydrate({payload:original}).items[1],amount:'1'}]};
 const items=f.buildPayload(data).expenses[0].items;
 assert.equal(items[0].allocation_amounts.friend,'0');assert.equal(items[0].allocation_amounts.me,'2');
 assert.equal(items[1].allocation_amounts.friend,'1');assert.equal(items[1].allocation_amounts.me,'0');
 const rows=f.previewRows({...base,...f.hydrate({payload:original})});assert.equal(rows[1].amount,'1');
});

test('same-currency previews do not repeat original amounts as conversions',()=>{
 const rows=f.previewRows({...base,currency:'CNY',amount:'123',referenceRate:'1'});
 assert.equal(rows[0].originalText,'¥61.50');assert.equal(rows[0].convertedText,'');
});

test('receipt date range guard and latest original currency are separate from settlement',()=>{
 assert.equal(f.dateOutsideTrip('2024-11-26',{start_date:'2026-10-26',end_date:'2026-10-29'}),true);
 assert.equal(f.dateOutsideTrip('2026-10-28',{start_date:'2026-10-26',end_date:'2026-10-29'}),false);
 assert.equal(f.latestCurrency([{payload:{expenses:[{items:[{amount:{currency:'USD'}}]}]}},{payload:{expenses:[{items:[{amount:{currency:'JPY'}}]}]}}]),'JPY');
});

test('rate display is short without changing the actual-payment calculation',()=>{
 const m=require('../services/money');assert.equal(m.formatRate('0.0458430458'),'0.045843');assert.equal(m.formatRate('0.047104'),'0.047104');
});
