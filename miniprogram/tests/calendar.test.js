const test = require('node:test')
const assert = require('node:assert/strict')
const fs=require('node:fs')
const path=require('node:path')
const vm=require('node:vm')
const calendarModule={exports:{}}
const calendarSource=fs.readFileSync(path.join(__dirname,'../components/calendar-picker/index.js'),'utf8')
vm.runInNewContext(calendarSource,{Component(){},module:calendarModule,Date,require(){throw new Error('日历入口不应依赖未打包的辅助模块')}})
const {monthDays,shiftMonth,selectRange}=calendarModule.exports

test('calendar includes leap day and aligns weekdays without timezone shifts',()=>{
  const days=monthDays(2024,2)
  assert.equal(days.filter(d=>d.value).length,29)
  assert.equal(days.findIndex(d=>d.value==='2024-02-01'),4)
  assert.equal(days.find(d=>d.value==='2024-02-29').day,29)
  assert.deepEqual(JSON.parse(JSON.stringify(shiftMonth(2026,1,-1))),{year:2025,month:12})
})

test('range selection orders dates and starts a fresh range after completion',()=>{
  const first=selectRange({start:'',end:'',awaitingEnd:false},'2026-10-08')
  assert.equal(first.awaitingEnd,true)
  const second=selectRange(first,'2026-10-05')
  assert.deepEqual(JSON.parse(JSON.stringify(second)),{start:'2026-10-05',end:'2026-10-08',awaitingEnd:false})
  assert.deepEqual(JSON.parse(JSON.stringify(selectRange(second,'2026-11-01'))),{start:'2026-11-01',end:'',awaitingEnd:true})
})

function component(properties) {
  let definition
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../components/calendar-picker/index.js'),'utf8'),{require(){throw new Error('日历入口不应依赖未打包的辅助模块')},module:{exports:{}},Component:value=>{definition=value},Date})
  const events=[]
  return {...definition.methods,properties:{mode:'single',value:'',start:'',end:'',...properties},data:{...definition.data},events,setData(value){Object.assign(this.data,value)},triggerEvent(name,detail){events.push({name,detail})}}
}

test('calendar confirms single dates and cancel leaves parent selection unchanged',()=>{
  const c=component({value:'2026-10-07'})
  c.open()
  assert.equal(c.data.month,10)
  c.choose({currentTarget:{dataset:{value:'2026-10-08'}}})
  c.close()
  assert.equal(c.events.length,0)
  c.open()
  assert.equal(c.data.draftValue,'2026-10-07')
  c.confirm()
  assert.equal(c.events[0].detail.value,'2026-10-07')
})

test('calendar range requires two dates before emitting selection',()=>{
  const c=component({mode:'range'})
  c.open()
  c.choose({currentTarget:{dataset:{value:'2026-10-05'}}})
  c.confirm()
  assert.equal(c.events.length,0)
  c.choose({currentTarget:{dataset:{value:'2026-10-08'}}})
  c.confirm()
  assert.equal(c.events[0].detail.start,'2026-10-05')
  assert.equal(c.events[0].detail.end,'2026-10-08')
  assert.equal(c.data.visible,false)
})
