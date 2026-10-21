const {isoDate,monthDays,shiftMonth,selectRange}=require('./calendar')
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
