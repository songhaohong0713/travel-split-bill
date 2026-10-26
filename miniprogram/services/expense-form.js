function cents(value) { const s=String(value == null ? '' : value).trim(); return /^-?\d+(\.\d{1,2})?$/.test(s) ? Math.round(Number(s)*100) : NaN }
function money(value) { return (value/100).toFixed(2) }
function allocation(data, percent) { const members=data.memberOptions || []; const self=members.find(m=>m.label==='我') || members[0]; const friend=members.find(m=>m.id!==self.id); const p=percent == null ? (data.splitMode==='even'?50:Number(data.selfPercent)) : Number(percent); if (!friend) return {[self.id]:'1'}; return {[self.id]:String(p/100),[friend.id]:String((100-p)/100)} }
function validate(data) {
 if (!data.tripId && data.tripId!==undefined) return '请选择旅行'
 if (!(cents(data.amount)>0)) return '请填写大于 0 的消费金额'
 if (!data.payer || !(data.memberOptions || []).length) return '请等待成员加载'
 if ((data.memberOptions || []).length<2 && !(data.splitMode==='custom' && Number(data.selfPercent)===100)) return '同行人尚未加入，请邀请后保存，或明确选择自己承担 100%'
 if (data.splitMode==='custom' && (!String(data.selfPercent == null ? '' : data.selfPercent).trim() || !Number.isFinite(Number(data.selfPercent)) || Number(data.selfPercent)<0 || Number(data.selfPercent)>100)) return '自定义比例应为 0—100%'
 if ((data.items || []).length) { if (data.items.some(i=>!(cents(i.amount)>0))) return '请填写每项明细金额'; if (data.items.reduce((s,i)=>s+cents(i.amount),0)!==cents(data.amount)) return '明细合计必须等于顶部消费金额'; if (data.items.some(i=>i.custom && (!String(i.selfPercent == null ? '' : i.selfPercent).trim() || !Number.isFinite(Number(i.selfPercent)) || Number(i.selfPercent)<0 || Number(i.selfPercent)>100))) return '明细比例应为 0—100%' }
 if (data.actualPaymentAmount && !(cents(data.actualPaymentAmount)>0)) return '实际支付金额必须大于 0'
 if (data.adjustments && data.adjustments.some(a=>!Number.isFinite(cents(a.amount)))) return '请填写有效的整单调整金额'
 if (!data.actualPaymentAmount && data.currency!==data.settlementCurrency && (data.rateLoading || !(Number(data.referenceRate)>0))) return '请等待有效汇率，或填写实际支付金额'
 return ''
}
function buildPayload(data) {
 const original=data.originalPayload || {}; const old=(original.expenses || [])[0] || {}; const currency=data.currency;const unchangedCurrency=!data.originalCurrency || data.originalCurrency===currency;
 const items=(data.items.length ? data.items : [{id:'total',name:data.title || '消费',amount:data.amount}]).map(i=>({...i.raw,item_id:i.id,name:i.name || '消费',amount:{currency:unchangedCurrency && i.raw?i.raw.amount.currency:currency,amount:i.amount},allocation:i.custom?allocation(data,i.selfPercent):i.raw && !data.splitChanged?i.raw.allocation:allocation(data)}))
 const adjustments=(data.adjustments || []).map(a=>({...a.raw,adjustment_id:a.id,amount:{currency:unchangedCurrency && a.raw?a.raw.amount.currency:currency,amount:a.amount},reason:a.reason || 'shared_discount_or_refund',allocation:a.raw && a.raw.allocation || allocation(data)}))
 const bill={...old,expense_id:old.expense_id || `bill-${Date.now()}`,payer_id:data.payer,items,adjustments,settlement_currency:data.actualPaymentAmount ? data.actualPaymentCurrency || data.settlementCurrency : data.settlementCurrency || currency}
 if (data.originalPayload && !Object.prototype.hasOwnProperty.call(old,'settlement_currency') && bill.settlement_currency===original.settlement_currency) delete bill.settlement_currency
 if (data.actualPaymentAmount) bill.actual_payment={currency:data.actualPaymentCurrency || data.settlementCurrency,amount:data.actualPaymentAmount}; else delete bill.actual_payment
 delete bill.reference_rate; delete bill.reference_rate_source
 if (data.actualPaymentAmount) delete bill.payment_to_settlement_rate
 if (data.referenceRate && !data.actualPaymentAmount) {bill.reference_rate=data.referenceRate;bill.reference_rate_source=data.referenceRateSource || 'manual'}
 const result={...original,title:data.title || '',participants:(data.memberOptions || []).map(m=>m.id),settlement_currency:data.actualPaymentAmount ? data.actualPaymentCurrency || data.settlementCurrency : data.settlementCurrency || currency,expenses:[bill,...(original.expenses || []).slice(1)]}
 if (data.receiptJobId) result.receipt_job_id=data.receiptJobId
 return result
}
function hydrate(record) {
 const p=record.payload || {};const b=(p.expenses || [])[0] || {};const items=(b.items || []).map(i=>({id:i.item_id,name:i.name || '',amount:i.amount.amount,raw:i,custom:false,selfPercent:''}));
 return {originalPayload:p,expenseId:record.id,revision:record.revision,occurredAt:record.occurred_at,title:p.title || '',payer:b.payer_id,currency:items.length?b.items[0].amount.currency:p.settlement_currency,settlementCurrency:b.settlement_currency || p.settlement_currency,originalCurrency:items.length?b.items[0].amount.currency:p.settlement_currency,amount:money(items.reduce((s,i)=>s+cents(i.amount),0)),items,adjustments:(b.adjustments || []).map(a=>({id:a.adjustment_id,amount:a.amount.amount,direction:Number(a.amount.amount)<0?'discount':'add',reason:a.reason,raw:a})),actualPaymentAmount:b.actual_payment && b.actual_payment.amount || '',actualPaymentCurrency:b.actual_payment && b.actual_payment.currency || p.settlement_currency,referenceRate:b.reference_rate || '',referenceRateSource:b.reference_rate_source || '',receiptJobId:p.receipt_job_id || '',splitChanged:false}
}
function previewRows(data) {
 const members=data.memberOptions || [];if (!members.length) return []; const people=members.length===1?[...members,{id:'pending',label:'同行人（未加入）'}]:members;const totals=Object.fromEntries(people.map(m=>[m.id,0]));
 const add=(n,a)=>{let used=0;const entries=Object.entries(a);entries.forEach(([id,v],index)=>{const part=index===entries.length-1?n-used:Math.round(n*Number(v));totals[id]=(totals[id] || 0)+part;used+=part})};
 const previewData={...data,memberOptions:people};const items=data.items.length?data.items:[{amount:data.amount}];items.forEach(i=>{const a=i.custom?allocation(previewData,i.selfPercent):i.raw && !data.splitChanged?i.raw.allocation:allocation(previewData);add(cents(i.amount)||0,a);if(i.raw && i.raw.tax_amount && !i.raw.tax_included)add(cents(i.raw.tax_amount.amount)||0,a)});(data.adjustments || []).forEach(a=>add(cents(a.amount)||0,a.raw && a.raw.allocation || allocation(previewData)));return people.map(m=>({...m,amount:money(totals[m.id] || 0)}))
}
function defaultTrip(trips,today) { return trips.find(t=>t.start_date && t.end_date && t.start_date<=today && t.end_date>=today) || trips.find(t=>{if(t.start_date || t.end_date)return false;const d=t.latest_expense_date || t.last_expense_date || t.latest_expense_at;return d && d.slice(0,10)<=today && (new Date(today)-new Date(d.slice(0,10)))/86400000<=2}) || null }
function dateString(date=new Date()) {return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`}
module.exports={cents,money,allocation,validate,buildPayload,hydrate,previewRows,defaultTrip,dateString}
