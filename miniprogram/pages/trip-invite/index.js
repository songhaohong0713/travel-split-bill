const api=require('../../services/api')
function localTime(value){
 const text=String(value || '')
 const date=new Date(/[zZ]|[+-]\d\d:\d\d$/.test(text)?text:text+'Z')
 if(Number.isNaN(date.getTime()))return '有效期暂不可用'
 return `${date.getMonth()+1}月${date.getDate()}日 ${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')} 前有效`
}
Page({
 data:{token:'',invite:null,loading:true,joining:false,error:'',alreadyMember:false},
 async onLoad(q){this.setData({token:q.token || ''});return this.reload()},
 async reload(){
  if(!this.data.token){this.setData({loading:false,error:'邀请链接无效'});return}
  this.setData({loading:true,error:''})
  try{await api.ensureAuthenticated();const invite=await api.getTripInvite(this.data.token);this.setData({invite,expiresText:localTime(invite.expires_at),alreadyMember:!!invite.already_member,loading:false})}
  catch(e){this.setData({loading:false,error:e.message || '暂时无法加载邀请，请重试'})}
 },
 enterTrip(){const i=this.data.invite;if(i)wx.redirectTo({url:`/pages/trip-detail/index?tripId=${encodeURIComponent(i.trip_id || i.id)}&name=${encodeURIComponent(i.name || '')}`})},
 async joinTrip(){
  if(this.data.joining || !this.data.token)return
  this.setData({joining:true,error:''})
  try{await api.ensureAuthenticated();await api.acceptTripInvite(this.data.token);this.setData({alreadyMember:true});if(wx.showToast)wx.showToast({title:'已加入旅行',icon:'success'});this.enterTrip()}
  catch(e){if(e.code==='ALREADY_A_MEMBER'){this.setData({alreadyMember:true});return}this.setData({error:e.message || '加入失败，请重试'})}
  finally{this.setData({joining:false})}
 },
 onShareAppMessage(){return {title:'邀请你一起记旅行账',path:'/pages/trip-invite/index?token='+encodeURIComponent(this.data.token)}},
})
module.exports={localTime}
