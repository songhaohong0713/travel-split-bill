const test = require('node:test')
const assert = require('node:assert/strict')
const f = require('../services/expense-form')
const members = [{id:'a',label:'我'},{id:'b',label:'同行人'}]
const base = {amount:'100',currency:'CNY',settlementCurrency:'CNY',payer:'a',memberOptions:members,splitMode:'even',selfPercent:'50',items:[],title:''}
test('simple entry builds one line and equal stable-ID shares',()=>{const p=f.buildPayload(base);assert.equal(p.expenses[0].items[0].amount.amount,'100');assert.deepEqual(p.expenses[0].items[0].allocation,{a:'0.5',b:'0.5'})})
test('solo entry previews only self and saves without extra choices',()=>{const d={...base,memberOptions:members.slice(0,1)};assert.equal(f.previewRows(d).length,1);assert.equal(f.validate(d),'')})
test('itemization must equal top amount',()=>assert.match(f.validate({...base,items:[{id:'1',name:'饭',amount:'90'}]}),/合计/))
test('editing retains every tax, allocation, adjustment, actual payment and receipt reference',()=>{const payload={title:'商户',participants:['a','b'],settlement_currency:'CNY',receipt_job_id:'job',expenses:[{expense_id:'e',payer_id:'a',actual_payment:{amount:'130',currency:'CNY'},items:[{item_id:'1',name:'a',amount:{amount:'100',currency:'CNY'},allocation:{a:'0.2',b:'0.8'},tax_amount:{amount:'3',currency:'CNY'},tax_included:false},{item_id:'2',name:'b',amount:{amount:'20',currency:'CNY'},allocation:{a:'1'}}],adjustments:[{adjustment_id:'x',amount:{amount:'-5',currency:'CNY'},allocation:{b:'1'},reason:'personal_coupon'}]}]};const d={...base,...f.hydrate({id:'r',payload})};assert.deepEqual(f.buildPayload(d),payload)})
test('trip defaults use dates or recent expense, and reject stale history',()=>{const trips=[{id:'old',latest_expense_date:'2026-01-01'},{id:'recent',latest_expense_date:'2026-10-25'}];assert.equal(f.defaultTrip(trips,'2026-10-26').id,'recent');assert.equal(f.defaultTrip([trips[0]],'2026-10-26').id,'old')})
function pageHarness(overrides={}) {
 const apiPath=require.resolve('../services/api');const saved=require.cache[apiPath];require.cache[apiPath]={id:apiPath,filename:apiPath,loaded:true,exports:overrides};let config;global.Page=p=>{config=p};global.getApp=()=>({globalData:{}});global.wx={redirectTo:o=>{config.redirect=o.url}};delete require.cache[require.resolve('../pages/expense/index')];require('../pages/expense/index');config.data={...config.data,...base,tripId:'t',tripName:'东京',occurredAt:'2026-10-26'};config.setData=function(p){Object.assign(this.data,p)};if(saved)require.cache[apiPath]=saved;else delete require.cache[apiPath];return config
}
test('save uses top amount and navigates to dated bill with trip name',async()=>{let payload;const page=pageHarness({createExpense:async(t,d,p)=>{payload=p;return {occurred_at:d}}});await page.save();assert.equal(payload.expenses[0].items[0].amount.amount,'100');assert.match(page.redirect,/day-bill\/index\?tripId=t&date=2026-10-26&name=/)})
test('solo save persists only the real member',async()=>{let payload;const page=pageHarness({createExpense:async(t,d,p)=>{payload=p;return {}}});page.data.memberOptions=members.slice(0,1);await page.save();assert.deepEqual(payload.participants,['a']);assert.deepEqual(payload.expenses[0].items[0].allocation,{a:'1'})})
test('OCR prefills merchant date amount and all items without review step',()=>{const page=pageHarness({});page.refreshRate=()=>{};page.applyCandidates([{merchant:'咖啡店',occurred_at:'2026-10-25'},{source_text:'coffee',translated_text:'咖啡',amount:'30',currency:'CNY'},{source_text:'cake',amount:'20',currency:'CNY'}]);assert.equal(page.data.title,'咖啡店');assert.equal(page.data.amount,'50.00');assert.equal(page.data.items.length,2);assert.equal(page.data.dateFromReceipt,true)})
test('discount direction input produces negative amount without minus keyboard',()=>{const page=pageHarness({});page.data.adjustments=[{id:'a',direction:'discount',amount:''}];page.onAdjustment({currentTarget:{dataset:{id:'a'}},detail:{value:'12.50'}});assert.equal(page.data.adjustments[0].amount,'-12.50');page.adjustmentDirection({currentTarget:{dataset:{id:'a',direction:'add'}}});assert.equal(page.data.adjustments[0].amount,'12.50')})
test('FX stale response cannot overwrite current currency and save cannot use old 1:1 rate',async()=>{
 const pending=[];let saved=0;const page=pageHarness({getExchangeRate:()=>new Promise(resolve=>pending.push(resolve)),createExpense:async()=>{saved++;return {}}});
 page.data.currency='JPY';page.data.referenceRate='1';const first=page.refreshRate();assert.equal(page.data.referenceRate,'');await page.save();assert.equal(saved,0);
 page.data.currency='USD';const second=page.refreshRate();pending[1]({rate:'7',provider:'test',effective_date:'2026-10-26'});await second;pending[0]({rate:'0.05',provider:'test',effective_date:'2026-10-26'});await first;assert.equal(page.data.referenceRate,'7');
})
test('blank custom amounts reject instead of assigning all to companion',()=>{assert.match(f.validate({...base,splitMode:'custom',customShares:{a:'',b:'100'}}),/填写金额/)})
test('actual paid currency becomes authoritative and stale historical rate is removed',()=>{const p=f.buildPayload({...base,currency:'JPY',actualPaymentAmount:'5',actualPaymentCurrency:'USD',referenceRate:'',originalPayload:{expenses:[{reference_rate:'0.05',payment_to_settlement_rate:'7'}]}});assert.equal(p.expenses[0].settlement_currency,'USD');assert.equal(p.settlement_currency,'USD');assert.equal(p.expenses[0].reference_rate,undefined);assert.equal(p.expenses[0].payment_to_settlement_rate,undefined)})
test('custom multi-item reload restores whole-bill amounts, not first item amounts',()=>{
 const d={...base,tripId:'t',amount:'100',splitMode:'custom',customShares:{a:'40',b:'60'},items:[{id:'1',name:'a',amount:'50'},{id:'2',name:'b',amount:'50'}]}
 const payload=f.buildPayload(d), hydrated=f.hydrate({id:'r',payload})
 assert.deepEqual(hydrated.customShares,{a:'40.00',b:'60.00'})
 assert.equal(f.validate({...base,...hydrated,tripId:'t'}),'')
})

test('legacy JPY zero fractional digits load losslessly and unchanged saves preserve payload',()=>{
 const payload={participants:['a','b'],settlement_currency:'CNY',expenses:[{expense_id:'old',payer_id:'a',reference_rate:'0.05',reference_rate_source:'manual',adjustments:[],items:[{item_id:'i',name:'消费',amount:{currency:'JPY',amount:'2500.00'},allocation:{a:'0.5',b:'0.5'}}]}],title:'旧消费'};
 const d={...base,...f.hydrate({id:'r',payload}),tripId:'t'};
 assert.equal(d.amount,'2500');assert.equal(f.validate(d),'');assert.deepEqual(f.buildPayload(d),payload);
 assert.throws(()=>f.units('2500.10','JPY'),/最小单位/);
});

test('adding an item keeps item mode and solo preview assigns the whole total',()=>{
 const p=pageHarness({});p.data={...p.data,splitMode:'items',amount:'5000',currency:'JPY',settlementCurrency:'JPY',memberOptions:members.slice(0,1),payer:'a',items:[{id:'i',amount:'2500',owner:'equal'}]};
 p.addItem();assert.equal(p.data.splitMode,'items');assert.match(p.data.itemDifferenceText,/2500|2,500/);assert.match(p.data.shareNote,/未分配|明细/);
});

test('out-of-range receipt date offers keep-receipt or today before saving',async()=>{
 const p=pageHarness({});p.refreshRate=async()=>{};p.setData({today:'2026-10-28',tripStartDate:'2026-10-26',tripEndDate:'2026-10-29'});
 let modals=0;global.wx.showModal=o=>{modals++;assert.equal(o.confirmText,'用今天');assert.equal(o.cancelText,'保留日期');assert.ok(o.cancelText.length<=4);o.success({confirm:false})};
 await p.applyCandidates([{merchant:'LAWSON',occurred_at:'2024-11-26'},{amount:'1287',currency:'JPY'}]);
 assert.equal(p.data.occurredAt,'2024-11-26');assert.match(p.data.dateWarning,/2024-11-26/);await p.confirmReceiptDate();assert.equal(modals,1);
 p.receiptDateAcknowledgement=null;global.wx.showModal=o=>o.success({confirm:true});await p.confirmReceiptDate();assert.equal(p.data.occurredAt,'2026-10-28');assert.equal(p.data.dateFromReceipt,false);
});

test('manual default uses the latest original currency while settlement remains CNY',async()=>{
 const p=pageHarness({listTripMembers:async()=>[{id:'a',is_current:true}],listExpenses:async()=>[{payload:{expenses:[{items:[{amount:{currency:'JPY',amount:'10'}}]}]}}]});p.refreshRate=async()=>{};
 await p.selectTrip({id:'t',name:'东京',default_currency:'CNY'});assert.equal(p.data.currency,'JPY');assert.equal(p.data.settlementCurrency,'CNY');
});

test('moved expense returns past the now-empty source day to its trip',()=>{
 const p=pageHarness({});let delta;p.setData({expenseId:'e',originalOccurredAt:'2024-11-26',occurredAt:'2026-10-28'});
 global.getCurrentPages=()=>[{route:'pages/trip-detail/index',data:{tripId:'t'}},{route:'pages/day-bill/index',data:{}},{route:'pages/expense/index',data:{}}];global.wx.navigateBack=o=>delta=o.delta;
 p.returnSource();assert.equal(delta,2);delete global.getCurrentPages;
});

test('double save cannot create duplicate expenses while date confirmation resolves',async()=>{
 let writes=0;const p=pageHarness({createExpense:async()=>{writes++;return {}}});await Promise.all([p.save(),p.save()]);assert.equal(writes,1);
});

test('manual continuation prevents a late OCR result overwriting typed values',async()=>{
 let finish;const p=pageHarness({uploadReceipt:async()=>({id:'job'}),getReceiptJob:()=>new Promise(r=>finish=r)});
 const running=p.startOcr('receipt');await Promise.resolve();p.continueManual();p.sync({amount:'500'});finish({status:'completed',candidates:[{amount:'999',currency:'CNY'}]});await running;assert.equal(p.data.amount,'500');assert.equal(p.data.reading,false);p.onUnload();
});

function legacyTax(){return {title:'汤咖喱',participants:['a','b'],settlement_currency:'CNY',expenses:[{expense_id:'bill',payer_id:'b',reference_rate:'0.04259',reference_rate_source:'manual',items:[{item_id:'i1',name:'咖喱',amount:{currency:'JPY',amount:'1680.00'},tax_amount:{currency:'JPY',amount:'10.00'},tax_included:false,allocation:{b:'1'}},{item_id:'i2',name:'咖喱',amount:{currency:'JPY',amount:'1680.00'},allocation:{a:'1'}}],adjustments:[]}]}}
test('legacy exclusive tax contributes once to top total, validation and preview',()=>{
 const payload=legacyTax(),d={...base,...f.hydrate({id:'r',payload}),tripId:'t'};
 assert.equal(d.amount,'3370');assert.equal(d.items[0].amount,'1680');assert.equal(d.items[0].taxDisplay,'10');assert.equal(f.validate(d),'');
 assert.deepEqual(f.previewRows(d).map(r=>r.amount),['1680','1690']);
 assert.deepEqual(f.buildPayload(d),payload);
 const p=pageHarness({});p.sync(d);assert.equal(p.data.itemDifferenceText,'');assert.match(p.data.shareNote,/3,370/);
 d.amount='3380';assert.match(f.validate(d),/合计/);
});
test('top total includes ordinary discounts but not separately received refunds',()=>{
 const payload=legacyTax();payload.expenses[0].adjustments=[{adjustment_id:'d',amount:{currency:'JPY',amount:'-70.00'},allocation:{a:'0.5',b:'0.5'},reason:'discount'}];
 const d={...base,...f.hydrate({id:'r',payload}),tripId:'t'};assert.equal(d.amount,'3300');assert.equal(f.validate(d),'');assert.deepEqual(f.buildPayload(d),payload);
});
test('missing FX still explains the original-currency companion share',()=>{
 const p=pageHarness({});p.sync({...base,currency:'JPY',amount:'1287',referenceRate:'',rateLoading:false});
 assert.match(p.data.shareNote,/643 JPY/);assert.match(p.data.shareNote,/汇率待补充/);assert.doesNotMatch(p.data.shareNote,/只有我/);
});
test('new entry does not wait for the reference-rate network before displaying fields',async()=>{
 let resolve;const p=pageHarness({ensureAuthenticated:async()=>{},listTrips:async()=>[{id:'t',name:'旅行',default_currency:'CNY'}],ledgerSummary:async()=>({trips:[]}),listTripMembers:async()=>[{id:'a',is_current:true}],listExpenses:async()=>[{payload:{expenses:[{items:[{amount:{currency:'JPY',amount:'10'}}]}]}}],getExchangeRate:()=>new Promise(r=>resolve=r)});
 let ready=false;const load=p.onLoad({tripId:'t'}).then(()=>ready=true);for(let i=0;i<15;i++)await Promise.resolve();
 assert.equal(ready,true);assert.equal(p.data.loading,false);assert.equal(p.data.rateLoading,true);resolve({rate:'0.05',provider:'test',effective_date:'2026-10-30'});await load;
});
test('old receipt year requires explicit confirmation even without trip dates',async()=>{
 const p=pageHarness({});p.refreshRate=async()=>{};p.setData({today:'2026-10-30',tripStartDate:'',tripEndDate:''});let asked=0;
 global.wx.showModal=o=>{asked++;assert.match(o.content,/2024-11-26/);o.success({confirm:false})};await p.applyCandidates([{merchant:'旧小票',occurred_at:'2024-11-26'},{amount:'100',currency:'JPY'}]);assert.equal(asked,1);assert.match(p.data.dateWarning,/年份/);
});

test('failed FX request exposes its reason and can be retried without losing fields',async()=>{
 let attempts=0;const p=pageHarness({getExchangeRate:async()=>{if(!attempts++)throw {message:'历史汇率暂不可用'};return {rate:'0.047104',provider:'frankfurter',effective_date:'2024-11-26'}}});p.setData({currency:'JPY',settlementCurrency:'CNY',occurredAt:'2024-11-26',amount:'1287'});await p.refreshRate();assert.match(p.data.rateError,/历史汇率/);await p.refreshRate();assert.equal(p.data.referenceRate,'0.047104');assert.equal(p.data.rateError,'');assert.equal(p.data.amount,'1287');
});
test('unallocated detail prevents any save even when actual payment is filled',async()=>{
 let writes=0;const p=pageHarness({createExpense:async()=>writes++});p.setData({amount:'1287',currency:'JPY',actualPaymentAmount:'59',actualPaymentCurrency:'CNY',items:[{id:'i',amount:'1282',owner:'equal'}]});await p.save();assert.equal(writes,0);assert.match(p.data.error,/合计/);
});
test('amount without explicit items remains all-in when adjustments exist',()=>{
 const d={...base,amount:'90',adjustments:[{id:'d',amount:'-10'}]};assert.equal(f.buildPayload(d).expenses[0].items[0].amount.amount,'100.00');assert.deepEqual(f.previewRows(d).map(r=>r.amount),['45.00','45.00']);
});

test('delete shows submission loading and rejects duplicate deletion until finished',async()=>{
 let resolve,writes=0;const p=pageHarness({deleteExpense:()=>{writes++;return new Promise(r=>resolve=r)}});p.setData({canDelete:true,expenseId:'e'});global.wx.showModal=o=>o.success({confirm:true});const run=p.deleteExpense();await Promise.resolve();assert.equal(p.data.deleting,true);await p.deleteExpense();assert.equal(writes,1);resolve();await run;assert.equal(p.data.deleting,false);
});

test('last saved exchange rate is only used by explicit choice and keeps its date visible',async()=>{
 const p=pageHarness({getExchangeRate:async()=>{throw {message:'网络不可用'}}});p.tripRecords=[{occurred_at:'2026-09-30',payload:{settlement_currency:'CNY',expenses:[{reference_rate:'0.04259',items:[{amount:{currency:'JPY',amount:'1680'}}]}]}}];p.setData({currency:'JPY',settlementCurrency:'CNY'});await p.refreshRate();assert.equal(p.data.referenceRate,'');assert.match(p.data.savedRateLabel,/2026-09-30/);p.useSavedRate();assert.equal(p.data.referenceRate,'0.04259');assert.match(p.data.rateStatus,/非消费日参考/);
});

test('JPY settlement trip keeps JPY through form selection and payload creation',async()=>{
 const p=pageHarness({listTripMembers:async()=>[{id:'a',is_current:true},{id:'b'}],listExpenses:async()=>[]});await p.selectTrip({id:'t',name:'日元结算',default_currency:'JPY'});p.onField({currentTarget:{dataset:{field:'amount'}},detail:{value:'1234'}});
 assert.equal(p.data.settlementCurrency,'JPY');assert.equal(p.data.currency,'JPY');const payload=f.buildPayload(p.data);assert.equal(payload.settlement_currency,'JPY');assert.equal(payload.expenses[0].settlement_currency,'JPY');assert.equal(f.validate(p.data),'');
});

test('renaming legacy items and title preserves calculation version and every financial field',()=>{
 const payload=legacyTax(),p=pageHarness({});p.sync({...f.hydrate({id:'r',payload}),financialChanged:false});p.onItem({currentTarget:{dataset:{id:'i1',field:'name'}},detail:{value:'新商品名'}});p.onField({currentTarget:{dataset:{field:'title'}},detail:{value:'新商户名'}});
 assert.equal(p.data.financialChanged,false);const result=f.buildPayload(p.data);result.title=payload.title;result.expenses[0].items[0].name=payload.expenses[0].items[0].name;assert.deepEqual(result,payload);
});

test('custom multi-item reload can change only its title and still save unchanged allocations',()=>{
 const payload=f.buildPayload({...base,amount:'100',splitMode:'custom',customShares:{a:'40',b:'60'},items:[{id:'1',amount:'50',name:'甲'},{id:'2',amount:'50',name:'乙'}]});const p=pageHarness({});p.sync({...f.hydrate({payload}),financialChanged:false});p.onField({currentTarget:{dataset:{field:'title'}},detail:{value:'只改标题'}});assert.deepEqual(p.data.customShares,{a:'40.00',b:'60.00'});assert.equal(f.validate(p.data),'');const result=f.buildPayload(p.data);result.title=payload.title;assert.deepEqual(result,payload);
});

test('repeated previews and reopening the page reuse a file but check permission every time',async()=>{
 const cache=require('../services/receipt-preview-cache');cache.clear();let reads=0,downloads=0,shown=[];
 const api={getExpenseReceipt:async()=>({status:'available',url:'https://private.example/image.png?token='+(++reads)}),downloadExpenseReceipt:async()=>'/tmp/original-'+(++downloads)+'.png'};
 const setup=p=>{global.wx.getStorageSync=()=> 'a';global.wx.previewImage=o=>shown.push(o.current);global.wx.getImageInfo=o=>o.success({type:'png'});p.setData({expenseId:'e',receiptJobId:'job',receiptPath:''})};
 const p=pageHarness(api);setup(p);await p.loadReceipt();await p.previewReceipt();await p.previewReceipt();
 const q=pageHarness(api);setup(q);await q.previewReceipt();assert.deepEqual(shown,['/tmp/original-1.png','/tmp/original-1.png','/tmp/original-1.png']);assert.equal(reads,4);assert.equal(downloads,1);assert.equal(q.data.previewLoading,false);cache.clear();
});

test('failed original download never opens a blank preview and allows retry',async()=>{
 let shown=0;const p=pageHarness({getExpenseReceipt:async()=>({status:'available',url:'https://private/image.png'}),downloadExpenseReceipt:async()=>{throw {message:'原图下载超时，请重试'}}});p.setData({expenseId:'e',receiptJobId:'job',receiptPath:''});global.wx.previewImage=()=>shown++;
 await p.previewReceipt();assert.equal(shown,0);assert.equal(p.data.previewLoading,false);assert.match(p.data.receiptError,/下载超时/);
});

test('receipt replacement skips OCR and preserves every financial field until save',async()=>{
 let options;const p=pageHarness({uploadReceipt:async(t,path,o)=>{options=o;return {id:'new-job'}}});
 const payload=f.buildPayload({...base,items:[{id:'old',name:'旧商品',amount:'100',owner:'equal'}]});p.sync({...f.hydrate({id:'e',payload}),financialChanged:false,reading:false});
 const before=f.buildPayload(p.data);await p.uploadReceiptAttachment('/tmp/receipt.jpg');const after=f.buildPayload(p.data);
 assert.deepEqual(options,{recognize:false});assert.equal(p.data.financialChanged,false);assert.equal(p.data.receiptPath,'/tmp/receipt.jpg');
 assert.equal(after.receipt_job_id,'new-job');delete after.receipt_job_id;assert.deepEqual(after,before);assert.equal(p.data.reading,false);
});

test('failed replacement retains previous attachment and save is blocked while uploading',async()=>{
 let writes=0;const p=pageHarness({uploadReceipt:async()=>{throw {message:'保存图片失败'}},createExpense:async()=>writes++});p.setData({receiptJobId:'previous',receiptPath:'old.jpg'});
 await p.uploadReceiptAttachment('new.jpg');assert.equal(p.data.receiptJobId,'previous');assert.equal(p.data.receiptPath,'old.jpg');assert.match(p.data.receiptError,/保存图片失败/);
 p.setData({uploadingReceipt:true});await p.save();assert.equal(writes,0);
});

test('missing historical image is distinct from a temporary storage error',async()=>{
 const p=pageHarness({getExpenseReceipt:async()=>({status:'missing'})});p.setData({expenseId:'e',receiptJobId:'old-job'});await p.loadReceipt();assert.equal(p.data.receiptStatus,'missing');
 const q=pageHarness({getExpenseReceipt:async()=>{throw {message:'图片服务暂不可用'}}});q.setData({expenseId:'e',receiptJobId:'old-job'});await q.loadReceipt();assert.equal(q.data.receiptStatus,'unavailable');assert.match(q.data.receiptError,/暂不可用/);
});

test('manual continuation during upload keeps the eventual original without applying OCR',async()=>{
 let resolve,writes=0;const p=pageHarness({uploadReceipt:()=>new Promise(r=>resolve=r),createExpense:async()=>writes++});
 const pending=p.startOcr('/tmp/new.jpg');p.continueManual();await p.save();assert.equal(writes,0);
 p.onField({currentTarget:{dataset:{field:'title'}},detail:{value:'手动商户'}});resolve({id:'stored-job'});await pending;
 assert.equal(p.data.receiptJobId,'stored-job');assert.equal(p.data.title,'手动商户');assert.equal(p.data.uploadingReceipt,false);assert.equal(p.data.reading,false);
});

test('attachment-only save preserves original cross-currency settlement and payment conversion exactly',async()=>{
 let saved;const p=pageHarness({uploadReceipt:async()=>({id:'new-job'}),updateExpense:async(t,id,rev,date,payload)=>{saved={date,payload}}});
 const original={title:'旧账单',participants:['a','b'],settlement_currency:'CNY',expenses:[{expense_id:'old',payer_id:'a',settlement_currency:'CNY',actual_payment:{currency:'USD',amount:'10'},payment_to_settlement_rate:'7',items:[{item_id:'i',name:'旧商品',amount:{currency:'JPY',amount:'1000.00'},allocation:{a:'0.5',b:'0.5'}}]}]};
 p.sync({...f.hydrate({id:'e',revision:4,occurred_at:'2026-09-30',payload:original}),financialChanged:false});
 await p.uploadReceiptAttachment('new.jpg');await p.save();
 assert.equal(saved.date,'2026-09-30');assert.deepEqual(saved.payload,{...original,receipt_job_id:'new-job'});
});

test('failed new receipt upload can retry the selected image and blocks duplicate retries',()=>{
 const p=pageHarness({});let retry;p.setData({receiptPath:'original.png',receiptJobId:'',reading:false,uploadingReceipt:false});p.startOcr=path=>retry=path;p.retryReceiptUpload();assert.equal(retry,'original.png');retry=null;p.setData({uploadingReceipt:true});p.retryReceiptUpload();assert.equal(retry,null);
});

test('expired temporary file redownloads and replacement invalidates the previous image',async()=>{
 const cache=require('../services/receipt-preview-cache');cache.clear();let downloads=0,version='v1',expired=false,shown=[];
 const p=pageHarness({getExpenseReceipt:async()=>({status:'available',url:'https://private/'+version+'?token=short'}),downloadExpenseReceipt:async()=>'/tmp/'+(++downloads)});
 global.wx.getStorageSync=()=> 'a';global.wx.previewImage=o=>shown.push(o.current);global.wx.getImageInfo=o=>expired&&o.src==='/tmp/1'?o.fail({}):o.success({});p.setData({expenseId:'e',receiptJobId:'job',receiptPath:''});
 await p.previewReceipt();await p.previewReceipt();assert.equal(downloads,1);expired=true;await p.previewReceipt();version='v2';await p.previewReceipt();assert.equal(downloads,3);assert.deepEqual(shown,['/tmp/1','/tmp/1','/tmp/2','/tmp/3']);cache.clear();
});

test('permission denial and account changes cannot display cached private receipts',async()=>{
 const cache=require('../services/receipt-preview-cache');cache.clear();let account='a',allowed=true,downloads=0,shown=0;
 const p=pageHarness({getExpenseReceipt:async()=>{if(!allowed)throw {statusCode:403,message:'无权查看'};return {status:'available',url:'https://private/v1'}},downloadExpenseReceipt:async()=>'/tmp/'+(++downloads)});
 global.wx.getStorageSync=()=>account;global.wx.getImageInfo=o=>o.success({});global.wx.previewImage=()=>shown++;p.setData({expenseId:'e',receiptJobId:'job',receiptPath:''});
 await p.previewReceipt();allowed=false;await p.previewReceipt();assert.equal(shown,1);allowed=true;await p.previewReceipt();assert.equal(downloads,2);account='b';await p.previewReceipt();assert.equal(downloads,3);assert.equal(shown,3);cache.clear();
});

test('switching accounts while the permission request is pending never opens the original',async()=>{
 let account='a',resolve,shown=0,downloads=0;const p=pageHarness({getExpenseReceipt:()=>new Promise(r=>resolve=r),downloadExpenseReceipt:async()=>{downloads++;return '/tmp/image'}});
 global.wx.getStorageSync=()=>account;global.wx.previewImage=()=>shown++;global.wx.getImageInfo=o=>o.success({});p.setData({expenseId:'e',receiptJobId:'job',receiptPath:''});const pending=p.previewReceipt();await Promise.resolve();account='b';resolve({status:'available',url:'https://private/image'});await pending;assert.equal(downloads,0);assert.equal(shown,0);assert.match(p.data.receiptError,/登录|账号/);
});
