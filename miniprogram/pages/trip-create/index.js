const api=require('../../services/api')
const ui=require('../../services/ledger-view')
Page({
  data:{name:'',currency:'CNY',currencies:['CNY','JPY','USD','KRW'],startDate:'',endDate:'',currencyOpen:false,creating:false,error:''},
  onName(e){this.setData({name:e.detail.value,validName:!!e.detail.value.trim()})},
  onDates(e){this.setData({startDate:e.detail.start,endDate:e.detail.end})},
  openCurrency(){this.setData({currencyOpen:true})},
  closeCurrency(){this.setData({currencyOpen:false})},
  chooseCurrency(e){this.setData({currency:e.currentTarget.dataset.currency,currencyOpen:false})},
  stop(){},
  createTrip(){return ui.createTrip.call(this)},
})
