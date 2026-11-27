import {api, esc, fail, toast, room} from './common.js';


class SmartPreview extends HTMLElement {
  connectedCallback(){
    this.innerHTML='<section class="panel"><div class="panel-body"><h2>Smart preview</h2><p>Loading…</p></div></section>';
    this.update(room.state);
  }
  update(state){
    this.state=state;
    const settings=state.settings.tracking;
    if(!this.ready){
      this.ready=true;
      this.innerHTML=`<section class="panel"><div class="panel-body"><h2>Smart preview</h2><label class="checkbox"><input data-smart-enabled type="checkbox" ${settings.conversation_layout?'checked':''}>Automatically show the conversation</label><div class="form-grid"><label>Presenter seat (optional)<select data-presenter-seat><option value="">None</option>${settings.zones.map(z=>`<option value="${esc(z.id)}" ${z.id===settings.presenter_seat?'selected':''}>${esc(z.name)}</option>`).join('')}</select></label><label>Presenter camera (optional)<select data-presenter-camera><option value="">None</option>${state.settings.cameras.map(c=>`<option value="${esc(c.id)}" ${c.id===settings.presenter_camera?'selected':''}>${esc(c.name)}</option>`).join('')}</select></label></div><button data-smart-save>Save smart preview</button><p class="hint">Uses saved seat scenes. One confirmed speaker is shown at a time. The wide shot covers moves on a shared camera and returns after silence. A presenter camera is reserved for the selected presenter seat. Save, then enable Follow in Tune & Follow.</p><p class="hint">Conversation is inferred from confirmed turns; the microphone supplies one direction at a time.</p><p data-smart-status role="status"></p></div></section>`;
      this.querySelector('[data-smart-save]').onclick=async()=>{
        const button=this.querySelector('[data-smart-save]');button.disabled=true;
        try{await api('/api/tracking/conversation',{conversation_layout:this.querySelector('[data-smart-enabled]').checked,presenter_seat:this.querySelector('[data-presenter-seat]').value,presenter_camera:this.querySelector('[data-presenter-camera]').value});toast('Smart preview saved. Enable Follow when ready.');}catch(e){fail(e);}finally{button.disabled=false;}
      };
    }
    const active=settings.conversation_layout&&state.tracking.live;
    const ids=state.settings.display.cameras;
    const cameras=ids.map(id=>state.settings.cameras.find(c=>c.id===id)).filter(Boolean).slice(0,3);
    this.querySelector('[data-smart-status]').textContent=!settings.conversation_layout?'Smart preview off':!state.tracking.live?'Ready — enable Follow to switch automatically':cameras.length?`${cameras.length===1?'Single view':cameras.length+' camera view'} · follows the room display`:'Framing the next speaker…';
  }
}
customElements.define('smart-preview',SmartPreview);
