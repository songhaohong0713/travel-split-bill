const api=require('../../services/api')
const ui=require('../../services/ledger-view')
Page({
  data:{name:'',currency:'CNY',currencies:['CNY','JPY','USD','KRW'],startDate:'',endDate:'',currencyOpen:false,creating:false,error:''},
  onName(e){this.setData({name:e.detail.value})},
  onDates(e){this.setData({startDate:e.detail.start,endDate:e.detail.end})},
  openCurrency(){this.setData({currencyOpen:true})},
  closeCurrency(){this.setData({currencyOpen:false})},
  chooseCurrency(e){this.setData({currency:e.currentTarget.dataset.currency,currencyOpen:false})},
  stop(){},
  async createTrip(){
    if(this.data.creating)return
    const name=this.data.name.trim()
    if(!name)return this.setData({error:'请输入旅行名称'})
    this.setData({creating:true,error:''})
    try{
      await api.ensureAuthenticated()
      const trips=await api.listTrips()
      if(trips.some(t=>t.name.trim()===name) && !await ui.confirm('已有同名旅行，仍要创建吗？','同名旅行'))return
      const trip=await api.createTrip(name,this.data.currency)
      let dateFailed=false
      if(this.data.startDate || this.data.endDate){
        try{await api.updateTripDates(trip.id,{start_date:this.data.startDate || null,end_date:this.data.endDate || null})}
        catch(e){dateFailed=true}
      }
      wx.redirectTo({url:ui.tripUrl({...trip,name,is_owner:true},'&inviteGuide=1'+(dateFailed?'&dateFailed=1':''))})
    }catch(e){this.setData({error:e.message || '创建失败，请重试'})}
    finally{this.setData({creating:false})}
  },
})
