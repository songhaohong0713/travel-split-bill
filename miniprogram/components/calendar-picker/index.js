function isoDate(year,month,day) { return `${year}-${String(month).padStart(2,'0')}-${String(day).padStart(2,'0')}` }
function monthDays(year,month) {
  const offset=new Date(year,month-1,1).getDay()
  const count=new Date(year,month,0).getDate()
  return Array.from({length:Math.ceil((offset+count)/7)*7},(_,index)=>{
    const day=index-offset+1
    return day>0 && day<=count ? {key:index,day,value:isoDate(year,month,day)} : {key:index,day:'',value:''}
  })
}
function shiftMonth(year,month,delta) {
  const date=new Date(year,month-1+delta,1)
  return {year:date.getFullYear(),month:date.getMonth()+1}
}
function selectRange(state,value) {
  if (!state.awaitingEnd) return {start:value,end:'',awaitingEnd:true}
  return {start:state.start<value ? state.start : value,end:state.start<value ? value : state.start,awaitingEnd:false}
}


Component({
  properties:{mode:{type:String,value:'single'},value:String,start:String,end:String},
  data:{visible:false,year:0,month:0,days:[],weekdays:['日','一','二','三','四','五','六'],draftValue:'',draftStart:'',draftEnd:'',awaitingEnd:false},
  methods:{
    open() {
      const now=new Date()
      const selected=this.properties.mode==='range' ? this.properties.start : this.properties.value
      const parts=/^(\d{4})-(\d{2})-\d{2}$/.exec(selected || '')
      this.setData({visible:true,year:parts?Number(parts[1]):now.getFullYear(),month:parts?Number(parts[2]):now.getMonth()+1,draftValue:this.properties.value || '',draftStart:this.properties.start || '',draftEnd:this.properties.end || '',awaitingEnd:false,today:isoDate(now.getFullYear(),now.getMonth()+1,now.getDate())})
      this.refresh()
    },
    refresh() {
      const {year,month,draftValue,draftStart,draftEnd,today}=this.data
      this.setData({days:monthDays(year,month).map(day=>({...day,selected:!!day.value && (this.properties.mode==='range' ? day.value===draftStart || day.value===draftEnd : day.value===draftValue),inside:!!day.value && !!draftEnd && day.value>draftStart && day.value<draftEnd,today:day.value===today}))})
    },
    move(e) {this.setData(shiftMonth(this.data.year,this.data.month,Number(e.currentTarget.dataset.delta)));this.refresh()},
    choose(e) {
      const value=e.currentTarget.dataset.value
      if (!value) return
      if (this.properties.mode==='range') {
        const next=selectRange({start:this.data.draftStart,end:this.data.draftEnd,awaitingEnd:this.data.awaitingEnd},value)
        this.setData({draftStart:next.start,draftEnd:next.end,awaitingEnd:next.awaitingEnd})
      } else this.setData({draftValue:value})
      this.refresh()
    },
    confirm() {
      if (this.properties.mode==='range') {
        if (!this.data.draftStart || !this.data.draftEnd) return
        this.triggerEvent('select',{start:this.data.draftStart,end:this.data.draftEnd})
      } else {
        if (!this.data.draftValue) return
        this.triggerEvent('select',{value:this.data.draftValue})
      }
      this.close()
    },
    close() {this.setData({visible:false})},
    stop() {},
  },
})

module.exports={isoDate,monthDays,shiftMonth,selectRange}
