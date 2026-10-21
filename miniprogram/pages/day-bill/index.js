const { getLedger, closeDay, previewPayment, markPayment, revokePayment, listExpenses, listTripMembers, deleteExpense } = require('../../services/api')
const { memberLabelMap } = require('../../services/member-labels')
const { expenseSummary } = require('../../services/expense-summary')

function confirm(content, title = '确认结清') {
  return new Promise(resolve => wx.showModal({ title, content, success: result => resolve(result.confirm), fail: () => resolve(false) }))
}

Page({
  data: { tripId: '', date: '', day: null, name: '', labels: {}, expenses: [], payments: [], busy: false, loading: true, error: '', all: false, statusLabel: '' },
  onLoad(q) {
    this.setData({ tripId: q.tripId, date: q.date || '', all: !q.date, name: q.name ? decodeURIComponent(q.name) : '' })
    return this.reload()
  },
  onShow() { if (this.loaded) return this.reload() },
  reload() {
    this.setData({loading:true,error:'',day:null,pending:[],groupRows:[],previewGroups:[],scopeDates:[],scopeMessage:'',expenses:[],payments:[]})
    return Promise.all([getLedger(this.data.tripId), listExpenses(this.data.tripId), listTripMembers(this.data.tripId)]).then(([ledger, records, members]) => {
      const labels = memberLabelMap(members)
      const day = ledger.days.find(d => d.date === this.data.date) || null
      const groupRows = (day && day.groups || []).map(group => ({ currency: group.currency, members: members.map(m => ({ id: m.id, name: labels[m.id], paid: (group.paid_by_participant[m.id] || {}).amount || '0', responsibility: (group.responsibility_by_participant[m.id] || {}).amount || '0' })) }))
      const pending = (day && day.pending || []).map(p => ({ ...p, text: `${labels[p.from_participant_id] || '同行人'} 需付给 ${labels[p.to_participant_id] || '同行人'} ${p.amount} ${p.currency}` }))
      const expenses = records.filter(r => this.data.all || r.occurred_at === this.data.date).map(r => {
        return {...expenseSummary(r,'CNY',labels),date:r.occurred_at,canDelete:r.is_creator || members.some(m=>m.is_current && m.is_owner)}
      })
      const payments = ledger.payments.filter(p => this.data.all || p.allocations.some(a => a.date === this.data.date)).map(p => ({ ...p, by: labels[p.created_by] || '同行人', time: new Date(p.created_at).toLocaleString(), direction: p.offset_only ? '跨日抵扣，无需转账' : `${labels[p.from_participant_id] || '同行人'} → ${labels[p.to_participant_id] || '同行人'}`, revokedBy: labels[p.revoked_by] || '同行人', revokedTime: p.revoked_at ? new Date(p.revoked_at).toLocaleString() : '' })).reverse()
      const closedDates = ledger.days.filter(d=>d.status !== 'recording').map(d=>d.date)
      this.setData({ day, labels, groupRows, pending, expenses:this.data.all ? expenses.filter(e=>closedDates.includes(e.date)) : expenses, payments, loading: this.data.all && closedDates.length > 0, statusLabel: day ? ({ recording: '记录中', pending: '待转账', settled: '已结清', invalid: '需补全消费' }[day.status]) : '' })
      this.loaded = true
      if (this.data.all) {
        if (!closedDates.length) { this.setData({previewGroups:[],scopeDates:[],scopeMessage:'请先进入当天账单，手动结束当天记账'}); return }
        return previewPayment(this.data.tripId).then(p=>this.setData({loading:false,previewGroups:p.groups.map(g=>({...g,direction:g.offset_only ? '跨日抵扣，无需转账' : `${labels[g.from_participant_id] || '同行人'} → ${labels[g.to_participant_id] || '同行人'}`})),scopeDates:p.dates,scopeMessage:p.groups.length ? '' : '所选日期已结清'}))
      }
    }).catch(e => this.setData({ error: e.message || '读不到账单，请返回重试', loading: false }))
  },
  endDay() {
    if (this.data.busy) return Promise.resolve()
    this.setData({ busy: true, error: '' })
    return confirm('结束后将计入旅行待结清金额，仍可补记消费。', '结束当天记账').then(ok => ok ? closeDay(this.data.tripId, this.data.date).then(() => this.reload()) : null)
      .catch(e => this.setData({ error: e.message || '操作失败' })).finally(() => this.setData({ busy: false }))
  },
  async confirmPayment() {
    if (this.data.busy) return
    this.setData({ busy: true, error: '' })
    try {
      const dates = this.data.all ? null : [this.data.date]
      let preview = await previewPayment(this.data.tripId, dates)
      if (!preview.groups.length) { wx.showToast({title:'当前已结清',icon:'none'}); return }
      // Confirm each currency separately; a previous confirmation invalidates the token.
      const currencies = preview.groups.map(g => g.currency)
      for (const currency of currencies) {
        preview = await previewPayment(this.data.tripId, dates)
        const group = preview.groups.find(g => g.currency === currency)
        if (!group) continue
        const labels = this.data.labels
        const content = group.offset_only ? '所选日期互相抵扣后无需转账，确认结清这些日期？' : `${labels[group.from_participant_id] || '同行人'} 付给 ${labels[group.to_participant_id] || '同行人'} ${group.amount} ${currency}。请确认已实际转账。`
        if (!await confirm(content)) break
        await markPayment(this.data.tripId, {dates:preview.dates, token:preview.token, currency})
      }
      await this.reload()
    } catch (e) {
      await this.reload()
      this.setData({error:e.message || '结清失败，请刷新后重试'})
    } finally { this.setData({busy:false}) }
  },
  revoke(e) {
    if (this.data.busy) return Promise.resolve()
    this.setData({busy:true,error:''})
    return confirm('撤销后将恢复对应日期的待结清金额，历史记录仍会保留。', '撤销转账标记')
      .then(ok => ok ? revokePayment(this.data.tripId,e.currentTarget.dataset.id).then(() => this.reload()) : null)
      .catch(error => this.setData({error:error.message || '撤销失败'})).finally(() => this.setData({busy:false}))
  },
  copyAmount() { const p = this.data.pending[0]; if (p) wx.setClipboardData({data:p.amount}) },
  deleteRecord(e) {
    if (this.data.busy) return Promise.resolve()
    this.setData({busy:true,error:''})
    return confirm('删除消费后会重算差额，历史转账保留。', '删除消费').then(ok => ok ? deleteExpense(this.data.tripId,e.currentTarget.dataset.id).then(()=>this.reload()) : null)
      .catch(error=>this.setData({error:error.message || '删除失败'})).finally(()=>this.setData({busy:false}))
  },
  openExpense(e) { wx.navigateTo({url:`/pages/expense/index?tripId=${encodeURIComponent(this.data.tripId)}&expenseId=${encodeURIComponent(e.currentTarget.dataset.id)}`}) },
})
