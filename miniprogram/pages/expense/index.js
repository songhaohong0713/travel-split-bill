const api=require('../../services/api')
const receiptCache=require('../../services/receipt-preview-cache')
const form=require('../../services/expense-form')
const money=require('../../services/money')
const ui=require('../../services/ledger-view')
const {memberChoices}=require('../../services/member-labels')
Page({
 data:{loading:true,tripId:'',tripName:'选择旅行',tripOptions:[],tripNames:[],tripIndex:0,expenseId:'',reading:false,canDelete:false,title:'',amount:'',currency:'CNY',currencies:['CNY','JPY','USD','KRW','EUR'],currencyIndex:0,settlementCurrency:'CNY',actualPaymentCurrency:'CNY',actualCurrencyIndex:0,actualPaymentAmount:'',referenceRate:'',occurredAt:'',payer:'',payerIndex:0,memberOptions:[],memberLabels:[],splitMode:'even',customShares:{},splitChanged:false,items:[],adjustments:[],showMore:false,responsibilityRows:[],today:'',yesterday:'',receiptPath:'',receiptStatus:'none',receiptError:'',receiptLoading:false,previewLoading:false,previewProgress:0,uploadingReceipt:false,receiptDate:'',receiptRecognized:false,dateFromReceipt:false,ocrStatus:'',error:'',saving:false},
 sync(changes={}){const next={...this.data,...changes},rows=form.previewRows(next);let convertedText='—',shareNote='只有我 · 全额由我承担',effectiveRate='',itemDifferenceText='';try{if(next.items.length){const assigned=form.itemizedUnits(next),difference=form.units(next.amount||'0',next.currency)-assigned;itemDifferenceText=difference>0n?'尚有 '+money.formatMoney(next.currency,form.decimal(difference,next.currency))+' 未分配':difference<0n?'明细超出 '+money.formatMoney(next.currency,form.decimal(-difference,next.currency)):next.items.some(i=>!i.amount)?'请填写每项明细金额':''}}catch(_){itemDifferenceText='请核对商品明细金额'}try{const target=next.actualPaymentAmount?next.actualPaymentCurrency:next.settlementCurrency;convertedText=next.currency===target&&!next.actualPaymentAmount?'':next.actualPaymentAmount || next.currency===target || (!next.rateLoading && Number(next.referenceRate)>0) ? money.formatMoney(target,next.actualPaymentAmount||money.convert(next.amount,next.referenceRate||'1',target),{approx:true}) : '汇率待补充';if(next.actualPaymentAmount&&Number(next.amount)>0)effectiveRate=money.formatRate(money.rateFor(next.actualPaymentAmount,next.amount));const other=rows.find(r=>r.id!==next.payer),payer=next.memberOptions.find(m=>m.id===next.payer);if(other){const who=payer.label==='我'?'你':payer.label;shareNote=`${who}付了全部 ${money.formatMoney(next.currency,next.amount)}，${other.label==='我'?'你':other.label}应还${who} ${other.convertedAmount!=null?money.formatMoney(target,other.convertedAmount):other.originalText+'（汇率待补充）'}`}}catch(_){}if(itemDifferenceText)shareNote=itemDifferenceText;let referenceRateLabel=next.referenceRate || '—';try{if(next.referenceRate)referenceRateLabel=money.formatRate(next.referenceRate)}catch(_){}const dateOutside=form.dateOutsideTrip(next.occurredAt,{start_date:next.tripStartDate,end_date:next.tripEndDate});const originalAmount=next.originalPayload?form.hydrate({payload:next.originalPayload}).amount:'';this.setData({...changes,actualPaymentUnchanged:!!(next.originalPayload&&next.actualPaymentAmount&&next.amount!==originalAmount&&next.actualPaymentAmount===next.originalPayload.expenses?.[0]?.actual_payment?.amount),dateOutside,dateWarning:form.receiptDateIssue(next),referenceRateLabel,itemDifferenceText,convertedText,effectiveRate,shareNote,dateChoice:next.occurredAt===next.today?'today':next.occurredAt===next.yesterday?'yesterday':'calendar',memberCount:next.memberOptions.length,currencyIndex:Math.max(0,next.currencies.indexOf(next.currency)),actualCurrencyIndex:Math.max(0,next.currencies.indexOf(next.actualPaymentCurrency)),payerIndex:Math.max(0,next.memberOptions.findIndex(m=>m.id===next.payer)),responsibilityRows:rows,customRows:next.memberOptions.map(m=>({...m,value:(next.customShares||{})[m.id]||''})),items:(next.items||[]).map(i=>({...i,ownerLabel:!i.owner||i.owner==='equal'?'平分':i.owner===next.currentMemberId?'我的':(next.memberOptions.find(m=>m.id===i.owner)||{label:'同行人'}).label+'的'}))});this.queueAuthoritativePreview()},
 async onLoad(q){this.loadQuery=q;if(wx.setNavigationBarTitle)wx.setNavigationBarTitle({title:q.expenseId?'编辑消费':'记一笔'});const yesterday=new Date();yesterday.setDate(yesterday.getDate()-1);this.setData({today:form.dateString(),yesterday:form.dateString(yesterday),occurredAt:q.date||form.dateString(),expenseId:q.expenseId||'',loading:true,loadFailed:false});try{await api.ensureAuthenticated();const [rawTrips,summary]=await Promise.all([api.listTrips(),api.ledgerSummary().catch(()=>({trips:[]}))]);const trips=rawTrips.map(t=>({...t,latest_expense_date:((summary.trips||[]).find(d=>d.id===t.id)||{}).latest_expense_date}));this.setData({tripOptions:trips,tripNames:trips.map(t=>t.name)});const trip=trips.find(t=>t.id===q.tripId)||form.defaultTrip(trips,form.dateString());if(!trip){this.setData({error:'请先新建一段旅行'});return}if(!q.tripId && !ui.inProgress(trip))this.setData({defaultTripHint:'没有进行中的旅行，已选择最近一段'});await this.selectTrip(trip,{editing:!!q.expenseId});if(q.expenseId){const records=this.tripRecordsTripId===trip.id?this.tripRecords:await api.listExpenses(trip.id),record=records.find(r=>r.id===q.expenseId);if(!record)throw Error('消费记录不存在');this.sync({...form.hydrate(record),financialChanged:false,canDelete:record.can_delete===true||record.is_owner===true||record.is_creator===true||this.data.currentCreator||!!(record.owner_id&&record.owner_id===this.data.currentMemberId)});this.setData({receiptPath:''});this.loadReceipt()}const app=getApp(),path=app.globalData.pendingReceiptPath||q.receiptPath;app.globalData.pendingReceiptPath='';if(path)this.startOcr(path);else if(q.receipt==='1')this.chooseReceipt()}catch(e){this.setData({loadFailed:true,error:e.message||'加载失败，请重试'})}finally{this.sync({loading:false})}},
 retryLoad(){return this.onLoad(this.loadQuery || {})},
 async selectTrip(trip,loadOptions={}){
  this.setData({tripId:trip.id,tripName:trip.name,tripStartDate:trip.start_date||'',tripEndDate:trip.end_date||'',tripIndex:this.data.tripOptions.findIndex(t=>t.id===trip.id),tripOngoing:ui.inProgress(trip),error:''});
  let currencyReadFailed=false;
  const [members,records]=await Promise.all([api.listTripMembers(trip.id),typeof api.listExpenses==='function'?api.listExpenses(trip.id).catch(()=>{currencyReadFailed=true;return []}):Promise.resolve([])]);
  this.tripRecords=records;this.tripRecordsTripId=trip.id;
  const current=members.find(m=>m.is_current)||members[0],options=memberChoices(members).sort((a,b)=>(a.label==='我'?-1:0)-(b.label==='我'?-1:0)),recent=form.latestCurrency(records),currency=this.data.currencies.includes(recent)?recent:trip.default_currency||'CNY';
  this.sync({memberOptions:options,memberLabels:options.map(m=>m.label),payer:current&&String(current.id)||'',currentMemberId:current&&String(current.id),currentCreator:!!trip.is_owner||!!(current&&current.is_owner),currency,settlementCurrency:trip.default_currency||'CNY',actualPaymentCurrency:trip.default_currency||'CNY',customShares:{},currencyDefaultHint:currencyReadFailed?'未取得最近消费币种，请确认币种':recent?'沿用最近一次消费币种':''});
  if(!loadOptions.editing)this.refreshRate();
 },
 async onTrip(e){if(this.data.expenseId)return;const trip=this.data.tripOptions[Number(e.detail.value)];if(trip)try{await this.selectTrip(trip);this.markDirty()}catch(err){this.setData({error:err.message||'成员加载失败'})}},
 onField(e){this.markDirty();const field=e.currentTarget.dataset.field,changes={[field]:e.detail.value};if(field!=='title')changes.financialChanged=true;if(field==='amount'&&this.data.items.length===1&&!this.data.showMore)changes.items=this.data.items.map(i=>({...i,amount:form.decimal(form.units(e.detail.value||'0',this.data.currency)-form.itemizedUnits({...this.data,items:this.data.items.map(x=>({...x,amount:'0'}))}),this.data.currency)}));if(field==='referenceRate'){changes.referenceRateSource='manual';changes.rateStatus='手动汇率'}this.sync(changes)},
 onCurrency(e){this.markDirty();this.sync({financialChanged:true,currency:this.data.currencies[Number(e.detail.value)]});this.refreshRate()},
 onActualCurrency(e){this.markDirty();this.sync({actualPaymentCurrency:this.data.currencies[Number(e.detail.value)]})},
 onPayer(e){this.markDirty();this.sync({financialChanged:true,payer:this.data.memberOptions[Number(e.detail.value)].id})},
 onSplit(e){this.markDirty();const mode=e.currentTarget.dataset.mode;this.sync({splitMode:mode,splitChanged:true,customShares:Object.fromEntries(this.data.responsibilityRows.map(r=>[r.id,r.amount==='—'?'':r.amount])),showMore:mode==='items'||this.data.showMore})},
 onCustomAmount(e){this.markDirty();this.sync({splitChanged:true,customShares:{...this.data.customShares,[e.currentTarget.dataset.id]:e.detail.value}})},
 quickDate(e){const d=new Date();d.setDate(d.getDate()-Number(e.currentTarget.dataset.offset));this.changeDate(form.dateString(d))},
 onDate(e){this.changeDate(e.detail.value)},
 changeDate(value){this.markDirty();this.sync({occurredAt:value,dateFromReceipt:!!this.data.receiptDate&&value===this.data.receiptDate});return this.refreshRate()},
 toggle(){this.setData({showMore:!this.data.showMore})},
 addItem(){this.markDirty();this.sync({splitMode:'items',splitChanged:this.data.splitChanged || this.data.splitMode!=='items',financialChanged:true,showMore:true,items:[...this.data.items,{id:`item-${Date.now()}`,name:'',amount:this.data.items.length?'':this.data.amount,owner:'equal'}]})},
 onItem(e){this.markDirty();const {id,field}=e.currentTarget.dataset;this.sync({financialChanged:field==='amount' || !!this.data.financialChanged,items:this.data.items.map(i=>i.id===id?{...i,[field]:e.detail.value}:i)})},
 itemCustom(e){this.markDirty();const owners=['equal',...this.data.memberOptions.map(m=>m.id)];this.sync({financialChanged:true,items:this.data.items.map(i=>i.id===e.currentTarget.dataset.id?{...i,owner:owners[(owners.indexOf(i.owner||'equal')+1)%owners.length],ownerChanged:true}:i)})},
 removeItem(e){this.markDirty();this.sync({financialChanged:true,items:this.data.items.filter(i=>i.id!==e.currentTarget.dataset.id)})},
 addAdjustment(){this.markDirty();this.sync({adjustments:[...this.data.adjustments,{id:`adjustment-${Date.now()}`,amount:'',direction:'discount',reason:'shared_discount_or_refund'}]})},
 onAdjustment(e){this.markDirty();this.sync({adjustments:this.data.adjustments.map(a=>a.id===e.currentTarget.dataset.id?{...a,amount:(a.direction==='add'?'':'-')+String(e.detail.value).replace(/^-/, '')}:a)})},
 adjustmentDirection(e){this.markDirty();const {id,direction}=e.currentTarget.dataset;this.sync({adjustments:this.data.adjustments.map(a=>a.id===id?{...a,direction,amount:a.amount?(direction==='add'?'':'-')+String(a.amount).replace(/^-/,''):''}:a)})},
 removeAdjustment(e){this.markDirty();this.sync({adjustments:this.data.adjustments.filter(a=>a.id!==e.currentTarget.dataset.id)})},
 async refreshRate(){const sequence=(this.rateSequence||0)+1;this.rateSequence=sequence;const {currency,settlementCurrency,occurredAt}=this.data;this.sync({savedRateLabel:'',rateError:'',referenceRate:'',referenceRateSource:'',rateLoading:currency!==settlementCurrency});if(currency===settlementCurrency){this.sync({referenceRate:'1',rateLoading:false,referenceRateSource:'same-currency',rateStatus:'同币种无需换算'});return}try{const q=await api.getExchangeRate(occurredAt,currency,settlementCurrency);if(sequence!==this.rateSequence||this.stopped)return;this.sync({referenceRate:q.rate,referenceRateSource:q.provider,rateLoading:false,rateStatus:`参考汇率 · ${q.effective_date}`})}catch(e){if(sequence===this.rateSequence){this.savedRate=(this.tripRecords||[]).slice().reverse().flatMap(r=>(r.payload?.expenses||[]).map(b=>({bill:b,date:r.occurred_at,target:b.settlement_currency||r.payload.settlement_currency}))).find(r=>r.bill.items?.[0]?.amount.currency===currency&&r.target===settlementCurrency&&Number(r.bill.reference_rate)>0&&!r.bill.actual_payment);this.sync({referenceRate:'',rateLoading:false,savedRateLabel:this.savedRate?'使用 '+this.savedRate.date+' 已保存汇率 '+money.formatRate(this.savedRate.bill.reference_rate):'',rateError:e.statusCode>=500?'服务暂不可用（HTTP '+e.statusCode+'）':e.message||'网络请求失败',rateStatus:'参考汇率获取失败：'+(e.message||'网络请求失败')})}}},
 useSavedRate(){if(!this.savedRate||!this.data.savedRateLabel)return;this.rateSequence=(this.rateSequence||0)+1;this.sync({referenceRate:this.savedRate.bill.reference_rate,referenceRateSource:'saved-expense:'+this.savedRate.date,rateLoading:false,rateError:'',savedRateLabel:'',rateStatus:'沿用 '+this.savedRate.date+' 已保存汇率（非消费日参考），请核对'})},
 queueAuthoritativePreview(){
  if(typeof api.previewSettlement!=='function' || this.data.loading || !this.data.tripId)return
  if(this.previewTimer)clearTimeout(this.previewTimer)
  this.previewTimer=setTimeout(()=>this.refreshAuthoritativePreview(),180)
 },
 async refreshAuthoritativePreview(){
  if(form.validate(this.data))return
  const payload=form.buildPayload(this.data), key=JSON.stringify(payload)
  this.previewKey=key
  try{
    const result=await api.previewSettlement(this.data.tripId,payload)
    if(this.stopped || this.previewKey!==key || JSON.stringify(form.buildPayload(this.data))!==key)return
    const target=payload.settlement_currency
    const group=(result.groups || []).find(g=>g.currency===target)
    if(!group)return
    const rows=this.data.responsibilityRows.map(row=>{
      const amount=group.responsibility_by_participant[row.id]?.amount
      return amount==null?row:{...row,convertedAmount:amount,convertedText:this.data.currency===target&&!this.data.actualPaymentAmount?'':money.formatMoney(target,amount,{approx:this.data.currency!==target})}
    })
    const other=rows.find(r=>r.id!==this.data.payer),payer=this.data.memberOptions.find(m=>m.id===this.data.payer)
    this.setData({responsibilityRows:rows,shareNote:other?`${payer.label==='我'?'你':payer.label}付了全部 ${money.formatMoney(this.data.currency,this.data.amount)}，${other.label==='我'?'你':other.label}应还${payer.label==='我'?'你':payer.label} ${money.formatMoney(target,other.convertedAmount)}`:'只有我 · 全额由我承担'})
  }catch(_){/* Server remains authoritative at save; incomplete previews stay local. */}
 },
 chooseReceipt(){if(this.data.uploadingReceipt||this.data.saving)return;if(!this.data.tripId){this.setData({error:'请先选择旅行'});return}wx.chooseMedia({count:1,mediaType:['image'],success:r=>{const path=r.tempFiles[0].tempFilePath;if(this.data.expenseId)this.uploadReceiptAttachment(path);else this.startOcr(path)}})},
 retryReceiptUpload(){if(!this.data.uploadingReceipt&&!this.data.reading&&this.data.receiptPath&&!this.data.receiptJobId)return this.startOcr(this.data.receiptPath)},
 async uploadReceiptAttachment(path){
  if(this.data.uploadingReceipt||this.data.saving)return;
  this.receiptReadSequence=(this.receiptReadSequence||0)+1;this.setData({uploadingReceipt:true,receiptLoading:false,receiptError:''});
  try{const job=await api.uploadReceipt(this.data.tripId,path,{recognize:false});if(this.stopped)return;receiptCache.remove(this.data.tripId+'/'+this.data.expenseId);this.markDirty(true);this.setData({receiptJobId:job.id,receiptPath:path,receiptStatus:'available',receiptError:'',receiptAttachmentPending:true,receiptOnlyChanged:!this.formEdited})}
  catch(e){if(!this.stopped)this.setData({receiptError:e.message||'小票图片保存失败，请重试'})}
  finally{if(!this.stopped)this.setData({uploadingReceipt:false})}
 },
 async loadReceipt(){
  if(!this.data.expenseId||!this.data.receiptJobId){this.setData({receiptStatus:'none',receiptLoading:false});return null}
  const sequence=(this.receiptReadSequence||0)+1;this.receiptReadSequence=sequence;this.setData({receiptLoading:true,receiptError:''});
  try{const result=await api.getExpenseReceipt(this.data.tripId,this.data.expenseId);if(this.stopped||sequence!==this.receiptReadSequence)return null;this.setData({receiptStatus:result.status});return result}
  catch(e){if(!this.stopped&&sequence===this.receiptReadSequence)this.setData({receiptStatus:'unavailable',receiptError:e.message||'小票原图暂时无法读取，请重试'});return null}
  finally{if(!this.stopped&&sequence===this.receiptReadSequence)this.setData({receiptLoading:false})}
 },
 stopOcrProgress(){if(this.ocrProgressTimer)clearInterval(this.ocrProgressTimer);this.ocrProgressTimer=null;this.setData({reading:false})},
 continueManual(){this.ocrGeneration=(this.ocrGeneration||0)+1;if(this.ocrTimer)clearTimeout(this.ocrTimer);this.stopOcrProgress();this.setData({ocrStatus:'已切换手动录入'})},
 async startOcr(path){
  this.markDirty();const generation=(this.ocrGeneration||0)+1;this.ocrGeneration=generation;
  if(this.ocrTimer)clearTimeout(this.ocrTimer);this.stopOcrProgress();this.ocrStartedAt=Date.now();
  this.setData({receiptPath:path,receiptJobId:'',uploadingReceipt:true,receiptStatus:'none',receiptAttachmentPending:false,receiptError:'',reading:true,receiptRecognized:false,ocrElapsed:0,ocrStage:'正在上传小票',ocrStatus:''});
  this.ocrProgressTimer=setInterval(()=>{if(!this.stopped&&generation===this.ocrGeneration)this.setData({ocrElapsed:Math.floor((Date.now()-this.ocrStartedAt)/1000)})},1000);
  try{const job=await api.uploadReceipt(this.data.tripId,path);if(this.stopped)return;if(this.data.receiptPath===path)this.setData({receiptJobId:job.id,receiptStatus:'available',uploadingReceipt:false});if(generation!==this.ocrGeneration)return;this.setData({ocrStage:'小票已上传，等待识别'});await this.pollOcr(job.id,generation)}
  catch(e){if(!this.stopped&&this.data.receiptPath===path)this.setData({receiptError:e.message||'小票图片保存失败，请重试'});if(generation===this.ocrGeneration){this.stopOcrProgress();this.setData({ocrStatus:'小票上传失败，可重试或手动填写'})}}
  finally{if(!this.stopped&&this.data.receiptPath===path)this.setData({uploadingReceipt:false})}
 },
 async pollOcr(id,generation=this.ocrGeneration){
  try{const job=await api.getReceiptJob(id);if(this.stopped||generation!==this.ocrGeneration)return;
   if(job.status==='needs_review'||job.status==='completed'){this.stopOcrProgress();await this.applyCandidates(job.candidates||[]);return}
   if(job.status==='queued'||job.status==='processing'){this.setData({ocrStage:job.status==='queued'?'小票已上传，排队等待识别':'正在识别商品和日期'});this.ocrTimer=setTimeout(()=>this.pollOcr(id,generation),1500);return}
   this.stopOcrProgress();this.setData({ocrStatus:'识别失败，可手动填写'})
  }catch(_){if(generation===this.ocrGeneration){this.stopOcrProgress();this.setData({ocrStatus:'识别状态查询失败，可手动填写'})}}
 },
 async confirmReceiptDate(){
  const d=this.data;if(!d.receiptDate || !d.dateFromReceipt || !form.receiptDateIssue(d))return true;
  const key=d.tripId+':'+d.occurredAt;if(this.receiptDateAcknowledgement===key)return true;
  this.setData({dateDecisionPending:true});
  try{const decision=await new Promise(resolve=>{if(!wx.showModal)return resolve(null);wx.showModal({title:d.tripStartDate||d.tripEndDate?'小票日期不在行程内':'请核对小票年份',content:'小票日期 '+d.receiptDate+(d.tripStartDate||d.tripEndDate?'，行程 '+(d.tripStartDate||'未设开始')+' 至 '+(d.tripEndDate||'未设结束'):'，今天 '+d.today)+'。请选择本笔消费日期。',confirmText:'用今天',cancelText:'保留日期',success:r=>resolve(r.confirm?'today':'receipt'),fail:()=>resolve(null)})});
   if(!decision){this.setData({error:'请确认消费日期后再保存'});return false}
   if(decision==='today')await this.changeDate(this.data.today || form.dateString());else this.receiptDateAcknowledgement=key;
   return true;
  }finally{this.setData({dateDecisionPending:false})}
 },
 async applyCandidates(candidates){const metadata=candidates.find(c=>c.merchant||c.occurred_at)||{},currency=(candidates.find(c=>this.data.currencies.includes(c.currency))||{}).currency||this.data.currency;const items=candidates.filter(c=>Number(c.amount)>0).map((c,index)=>({id:`ocr-${index}`,name:c.translated_text||c.source_text||'消费',amount:String(c.amount),owner:'equal'})),total=candidates.find(c=>c.total_amount||c.total);this.sync({splitMode:items.length>1?'items':'even',splitChanged:true,financialChanged:true,receiptRecognized:true,receiptDate:metadata.occurred_at||'',dateFromReceipt:!!metadata.occurred_at,title:metadata.merchant||this.data.title,occurredAt:metadata.occurred_at||this.data.occurredAt,items,amount:String(total&&(total.total_amount||total.total)||form.decimal(items.reduce((s,i)=>s+form.units(i.amount,currency),0n),currency)),currency,ocrStatus:'已识别，请核对金额和日期'});await this.confirmReceiptDate();if(!metadata.occurred_at || this.data.occurredAt===metadata.occurred_at)this.refreshRate()},
 onUnload(){this.stopped=true;this.receiptReadSequence=(this.receiptReadSequence||0)+1;this.ocrGeneration=(this.ocrGeneration||0)+1;if(this.ocrProgressTimer)clearInterval(this.ocrProgressTimer);if(this.ocrTimer)clearTimeout(this.ocrTimer);if(this.previewTimer)clearTimeout(this.previewTimer)},
 markDirty(attachmentOnly=false){if(!attachmentOnly){this.formEdited=true;this.setData({receiptOnlyChanged:false})}if(wx.enableAlertBeforeUnload)wx.enableAlertBeforeUnload({message:'这笔消费还未保存，确定离开？'})},
 clearGuard(){if(wx.disableAlertBeforeUnload)wx.disableAlertBeforeUnload()},
 receiptAccount(){return typeof wx.getStorageSync==='function'?String(wx.getStorageSync('travel:v2:account')||''):''},
 async previewReceipt(){
  if(this.data.receiptLoading||this.data.uploadingReceipt||this.data.previewLoading)return;
  const job=this.data.receiptJobId,account=this.receiptAccount(),scope=this.data.tripId+'/'+this.data.expenseId;
  receiptCache.forAccount(account);this.setData({previewLoading:true,previewProgress:0,receiptError:''});
  const active=()=>!this.stopped&&job===this.data.receiptJobId&&account===this.receiptAccount();
  const decode=path=>new Promise((resolve,reject)=>wx.getImageInfo({src:path,success:resolve,fail:()=>reject({message:'原图未能完整读取，请重试下载'})}));
  try{
   let path=this.data.receiptPath,info;
   if(!path){
    const metadata=await api.getExpenseReceipt(this.data.tripId,this.data.expenseId);
    if(account!==this.receiptAccount())throw {message:'登录账号已变更，请重新打开账单'};
    if(!active())return;
    if(metadata.status!=='available'||!metadata.url){receiptCache.remove(scope);throw {code:'RECEIPT_IMAGE_MISSING',message:'原图不存在，请重新上传小票'}}
    const version=metadata.url.split('?')[0];
    path=receiptCache.get(account,scope,version);
    if(path){try{info=await decode(path)}catch(_){receiptCache.remove(scope);path=''}}
    if(!path){
     path=await api.downloadExpenseReceipt(this.data.tripId,this.data.expenseId,p=>{if(active())this.setData({previewProgress:Math.max(0,Math.min(100,Number(p.progress)||0))})});
     if(account!==this.receiptAccount())throw {message:'登录账号已变更，请重新打开账单'};
     if(!active())return;
     info=await decode(path);
     if(!active())return;
     receiptCache.put(account,scope,version,path);
    }
   }else info=await decode(path);
   if(!active())return;
   wx.previewImage({current:info.path||path,urls:[info.path||path],fail:()=>{receiptCache.remove(scope);if(!this.stopped)this.setData({receiptError:'原图暂时无法打开，请重试'})}});
  }catch(e){
   if([401,403,404].includes(e.statusCode)||e.code==='RECEIPT_IMAGE_MISSING')receiptCache.remove(scope);
   receiptCache.forAccount(this.receiptAccount());
   if(!this.stopped)this.setData({receiptError:e.message||'原图暂时无法下载，请重试',...(e.code==='RECEIPT_IMAGE_MISSING'?{receiptStatus:'missing'}:{})});
  }finally{if(!this.stopped)this.setData({previewLoading:false})}
 },
 returnSource(options={}){
  this.clearGuard();const moved=!options.deleted && !!(this.data.expenseId&&this.data.originalOccurredAt&&this.data.originalOccurredAt!==this.data.occurredAt);
  const fallback=()=>wx.redirectTo({url:(moved || options.deleted)?ui.tripUrl({id:this.data.tripId,name:this.data.tripName,default_currency:this.data.settlementCurrency}):`/pages/day-bill/index?tripId=${encodeURIComponent(this.data.tripId)}&date=${encodeURIComponent(this.data.occurredAt)}&name=${encodeURIComponent(this.data.tripName)}`});
  if(wx.showToast)wx.showToast({title:options.deleted?'已删除消费':moved?'已移到 '+ui.dateTitle(this.data.occurredAt):this.data.expenseId?'已保存修改':'已保存',icon:moved?'none':'success'});
  if(moved || options.deleted){const pages=typeof getCurrentPages==='function'?getCurrentPages():[],index=pages.map(p=>p.route==='pages/trip-detail/index'&&p.data.tripId===this.data.tripId).lastIndexOf(true);if(index>=0&&index<pages.length-1&&wx.navigateBack)wx.navigateBack({delta:pages.length-1-index,fail:fallback});else fallback();return}
  if(wx.navigateBack)wx.navigateBack({delta:1,fail:fallback});else fallback();
 },
 async save(e){if(this.data.saving||this.data.uploadingReceipt||this.data.reading||this.data.dateDecisionPending)return;if(!await this.confirmReceiptDate() || this.data.saving||this.data.uploadingReceipt||this.data.reading)return;const error=form.validate(this.data);if(error){this.setData({error});return}const keep=!!(e&&e.currentTarget&&e.currentTarget.dataset.continue);this.setData({saving:true,error:''});try{const d=this.data,payload=form.buildPayload(d);if(d.expenseId)await api.updateExpense(d.tripId,d.expenseId,d.revision,d.occurredAt,payload);else await api.createExpense(d.tripId,d.occurredAt,payload);this.clearGuard();if(keep){this.sync({expenseId:'',originalOccurredAt:'',originalPayload:null,originalCurrency:'',title:'',amount:'',items:[],adjustments:[],customShares:{},receiptPath:'',receiptJobId:'',receiptStatus:'none',receiptAttachmentPending:false,receiptOnlyChanged:false,receiptError:'',receiptDate:'',receiptRecognized:false,dateFromReceipt:false,actualPaymentAmount:'',splitMode:'even',splitChanged:false,financialChanged:false,ocrStatus:'',showMore:false,occurredAt:this.data.today});this.refreshRate();if(wx.showActionSheet)wx.showActionSheet({itemList:['拍照或选小票','手动记一笔'],success:r=>{if(r.tapIndex===0)this.chooseReceipt()}})}else this.returnSource()}catch(e){this.setData({error:e.message||'保存失败，请重试'})}finally{this.setData({saving:false})}},
 async deleteExpense(){
  if(!this.data.canDelete||this.data.deleting||this.data.saving)return;
  const confirmed=await new Promise(resolve=>wx.showModal({title:'删除这笔消费？',content:'删除后当天账单会重新计算。',success:r=>resolve(r.confirm),fail:()=>resolve(false)}));
  if(!confirmed||this.data.deleting)return;this.setData({deleting:true,error:''});
  try{await api.deleteExpense(this.data.tripId,this.data.expenseId);this.returnSource({deleted:true})}catch(e){this.setData({error:e.message||'删除失败'})}finally{this.setData({deleting:false})}
 }
})
