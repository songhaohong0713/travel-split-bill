// Decimal strings at the boundary; BigInt is used internally, never stored in page data.
const SCALES = {CNY:2, JPY:0, KRW:0, USD:2, EUR:2}
const ZERO = BigInt(0), ONE = BigInt(1), TEN = BigInt(10)
function scale(currency) {
  if (!(currency in SCALES)) throw Error('不支持的币种')
  return SCALES[currency]
}
function power(n) { return BigInt('1'+'0'.repeat(Number(n))) }
function decimal(value) {
  const text = String(value == null ? '' : value).trim()
  if (!/^-?\d+(\.\d+)?$/.test(text)) throw Error('金额格式不正确')
  const parts = text.replace('-','').split('.')
  return {value:BigInt(parts.join('')) * (text[0]==='-' ? -ONE : ONE), scale:(parts[1] || '').length}
}
function divideRound(value, denominator) {
  const sign = value < ZERO ? -ONE : ONE
  const absolute = value < ZERO ? -value : value
  return sign * ((absolute * BigInt(2) + denominator) / (denominator * BigInt(2)))
}
function units(value, currency) {
  const parsed = decimal(value), places = scale(currency)
  return parsed.scale <= places ? parsed.value * power(places-parsed.scale) : divideRound(parsed.value,power(parsed.scale-places))
}
function fromUnits(value, currency) {
  const places=scale(currency), negative=value<ZERO
  const text=(negative ? -value : value).toString().padStart(places+1,'0')
  return (negative?'-':'') + (places ? text.slice(0,-places)+'.'+text.slice(-places) : text)
}
function normalize(value,currency) { return fromUnits(units(value,currency),currency) }
function sum(values,currency='CNY') { return fromUnits(values.reduce((s,v)=>s+units(v,currency),ZERO),currency) }
function convert(amount,rate,targetCurrency='CNY') {
  const a=decimal(amount),r=decimal(rate)
  if(r.value<=ZERO)throw Error('汇率必须大于零')
  const places=scale(targetCurrency), decimals=a.scale+r.scale
  const value=a.value*r.value
  return fromUnits(decimals>places?divideRound(value,power(decimals-places)):value*power(places-decimals),targetCurrency)
}
function rateFor(actual,original,places=10) {
  const a=decimal(actual),o=decimal(original)
  if(o.value<=ZERO || a.value<=ZERO)throw Error('金额必须大于零')
  const value=divideRound(a.value*power(o.scale+places),o.value*power(a.scale))
  const text=value.toString().padStart(places+1,'0')
  return (text.slice(0,-places)+'.'+text.slice(-places)).replace(/0+$/,'').replace(/\.$/,'')
}
function formatRate(value,places=6){const parsed=decimal(value),v=parsed.scale<=places?parsed.value*power(places-parsed.scale):divideRound(parsed.value,power(parsed.scale-places)),text=v.toString().padStart(places+1,'0');return (text.slice(0,-places)+'.'+text.slice(-places)).replace(/0+$/,'').replace(/\.$/,'')}
function formatMoney(currency,amount,options={}) {
  if(amount==null || amount==='')return '—'
  const text=normalize(amount,currency), negative=text[0]==='-'
  const parts=text.replace('-','').split('.')
  parts[0]=parts[0].replace(/\B(?=(\d{3})+(?!\d))/g,',')
  return (options.approx?'≈ ':'')+(negative?'-':'')+(currency==='CNY'?'¥'+parts.join('.'):parts.join('.')+' '+currency)
}
function split(amount,currency,members,payerId,mode='equal',items=[],customShares={}) {
  const ids=members.map(m=>typeof m==='string'?m:m.id)
  if(!ids.length || new Set(ids).size!==ids.length || !ids.includes(payerId))throw Error('参与成员无效')
  const total=units(amount,currency), result=Object.fromEntries(ids.map(id=>[id,ZERO]))
  function equal(value) {
    const each=value/BigInt(ids.length)
    for(const id of ids)result[id]+=each
    result[payerId]+=value-each*BigInt(ids.length)
  }
  if(ids.length===1)result[payerId]=total
  else if(mode==='equal' || mode==='even')equal(total)
  else if(mode==='custom') {
    for(const id of ids) {
      if(customShares[id]==null || String(customShares[id]).trim()==='')throw Error('请填写每人的分摊金额')
      result[id]=units(customShares[id],currency)
      if(result[id]<ZERO)throw Error('分摊金额不能为负')
    }
    if(Object.values(result).reduce((a,b)=>a+b,ZERO)!==total)throw Error('分摊金额合计不等于总金额')
  } else if(mode==='items') {
    if(items.reduce((n,i)=>n+units(i.amount,currency),ZERO)!==total)throw Error('商品合计不等于总金额')
    for(const item of items) {
      const value=units(item.amount,currency)
      if(item.owner==='equal' || item.owner==='split' || !item.owner)equal(value)
      else if(ids.includes(item.owner))result[item.owner]+=value
      else throw Error('商品归属成员无效')
    }
  } else throw Error('分摊方式无效')
  return ids.map(id=>({id,amount:fromUnits(result[id],currency)}))
}
function convertShares(rows,rate,payerId,targetCurrency='CNY') {
  const sourceTotal=rows.reduce((sum,row)=>{
    const d=decimal(row.amount),n=decimal(sum),places=Math.max(d.scale,n.scale)
    const value=d.value*power(places-d.scale)+n.value*power(places-n.scale)
    const text=value.toString().padStart(places+1,'0')
    return places?text.slice(0,-places)+'.'+text.slice(-places):text
  },'0')
  const total=units(convert(sourceTotal,rate,targetCurrency),targetCurrency)
  let allocated=ZERO
  const result=rows.map(row=>{
    if(row.id===payerId)return {...row,amount:''}
    const amount=convert(row.amount,rate,targetCurrency); allocated+=units(amount,targetCurrency)
    return {...row,amount}
  })
  const payer=result.find(r=>r.id===payerId)
  if(!payer)throw Error('付款人无效')
  payer.amount=fromUnits(total-allocated,targetCurrency)
  return result
}
function expenseView(record,settleCurrency='CNY',labels={}) {
  const payload=record.payload || {}, bills=payload.expenses || []
  const originals={},converted={}
  let missing=false
  for(const bill of bills) {
    const currency=bill.items && bill.items[0] && bill.items[0].amount.currency || payload.currency || settleCurrency
    const values=[]
    for(const i of bill.items || []) { values.push(i.amount.amount); if(i.tax_amount && !i.tax_included)values.push(i.tax_amount.amount) }
    for(const a of bill.adjustments || [])if(!a.received_by)values.push(a.amount.amount)
    const total=sum(values,currency)
    originals[currency]=sum([originals[currency] || '0',total],currency)
    let target=bill.settlement_currency || payload.settlement_currency || settleCurrency
    let value
    if(bill.actual_payment) {
      value=bill.actual_payment.amount
      if(bill.actual_payment.currency!==target) {
        if(bill.payment_to_settlement_rate)value=convert(value,bill.payment_to_settlement_rate,target)
        else target=bill.actual_payment.currency
      }
    } else if(currency===target)value=total
    else if(bill.reference_rate)value=convert(total,bill.reference_rate,target)
    else { missing=true; continue }
    converted[target]=sum([converted[target] || '0',value],target)
  }
  if(record.ledger_summary) {
    const authoritative=record.ledger_summary
    if(authoritative.error)missing=true
    else if(authoritative.groups) {
      Object.keys(converted).forEach(c=>delete converted[c])
      for(const group of authoritative.groups) {
        converted[group.currency]=sum(Object.values(group.paid_by_participant || {}).map(m=>m.amount),group.currency)
      }
      missing=false
    }
  }
  const currency=Object.keys(originals)[0] || settleCurrency
  const settlementCurrency=Object.keys(converted)[0] || settleCurrency
  const originalAmount=originals[currency] || '0'
  const settlementAmount=missing?'':converted[settlementCurrency] || '0'
  return {id:record.id,title:payload.title || '消费',currency,originalAmount,settlementCurrency,settlementAmount,originalText:Object.entries(originals).map(([c,v])=>formatMoney(c,v)).join(' + '),settlementText:!missing && Object.keys(originals).length===1 && Object.keys(converted).length===1 && originals[currency]===converted[currency]?'':missing?'汇率待补充':Object.entries(converted).map(([c,v])=>formatMoney(c,v,{approx:c!==currency})).join(' + '),originalTotals:originals,settlementTotals:converted,incomplete:missing,payer:labels[bills[0] && bills[0].payer_id] || '同行人',itemCount:bills.reduce((n,b)=>n+(b.items || []).length,0),record}
}
module.exports={formatRate,scale,units,fromUnits,normalize,sum,convert,formatMoney,split,convertShares,expenseView,rateFor}
