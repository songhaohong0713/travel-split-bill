// Display only: authoritative settlement amounts are always calculated by the API.
function expenseSummary(record, currency, labels = {}) {
  const payload=record.payload || {}
  const bill=(payload.expenses || [])[0] || {}
  const items=bill.items || []
  const cents=value=>Math.round(Number(value || 0)*100)
  const total=items.reduce((sum,item)=>sum+cents(item.amount && item.amount.amount)+(!item.tax_included && item.tax_amount ? cents(item.tax_amount.amount) : 0),0)+(bill.adjustments || []).reduce((sum,a)=>sum+cents(a.amount && a.amount.amount),0)
  const itemCurrency=items[0] && items[0].amount && items[0].amount.currency || currency
  const format=value=>(value/100).toFixed(['JPY','KRW'].includes(itemCurrency) ? 0 : 2)
  const actual=bill.actual_payment && /^\d+(\.\d{1,2})?$/.test(String(bill.actual_payment.amount || '')) && Number(bill.actual_payment.amount)>0 ? bill.actual_payment : null
  return {id:record.id,revision:record.revision,occurredAt:record.occurred_at,title:payload.title || '未命名消费',payer:labels[bill.payer_id] || '同行人',itemCount:items.length,amountLabel:actual ? '实际支付' : '消费合计',total:actual ? actual.amount : format(total),currency:actual ? actual.currency : itemCurrency,originalTotal:actual ? format(total) : '',originalCurrency:actual ? itemCurrency : '',isCreator:Boolean(record.is_creator)}
}
module.exports={expenseSummary}
