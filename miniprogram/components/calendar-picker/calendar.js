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
module.exports={isoDate,monthDays,shiftMonth,selectRange}
