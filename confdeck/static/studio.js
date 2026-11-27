import {api, esc, fail, toast, room, sendManual} from './common.js';
class CameraStudio extends HTMLElement {
 connectedCallback(){
  this.vector=[0,0,0]; this.keys=new Set(); this.abort=new AbortController();
  this.innerHTML='<section class="panel"><div class="panel-body"><h2>Camera Preset Studio</h2><p>Loading controls…</p></div></section>';
  const opts={signal:this.abort.signal};
  const releasePointer=()=>{if(this.pointer)this.stop();};
  window.addEventListener('pointerup',releasePointer,opts);
  window.addEventListener('pointercancel',releasePointer,opts);
  window.addEventListener('blur',()=>this.stop(),opts);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)this.stop();},opts);
  window.addEventListener('keydown',e=>this.key(e,true),opts);
  window.addEventListener('keyup',e=>this.key(e,false),opts);
  this.timer=setInterval(()=>this.tick(),100);
 }
 update(s){this.cameras=s.settings.cameras;if(!this.ready){this.ready=true;this.render();}if(s.manual_error){this.stop();this.controlError=s.manual_error;toast(s.manual_error,true);}if(room.socket?.readyState!==1)this.stop();this.updateControls(s);}
 disconnectedCallback(){this.stop();clearInterval(this.timer);this.abort.abort();}
 send(){if(this.camera)sendManual({camera:this.camera,pan:this.vector[0],tilt:this.vector[1],zoom:this.vector[2],speed:Number(this.querySelector('[data-speed]')?.value||.25)});}
 stop(){this.vector=[0,0,0];this.keys.clear();this.pointer=false;this.send();this.gamepad=false;const g=this.querySelector('[data-gamepad]');if(g)g.checked=false;}
 key(e,down){if(!this.contains(document.activeElement)||/INPUT|SELECT|TEXTAREA/.test(e.target.tagName))return;const k=e.key.toLowerCase();if(!['arrowleft','arrowright','arrowup','arrowdown','w','a','s','d','+','-','escape'].includes(k))return;e.preventDefault();if(k==='escape'){this.stop();return;}down?this.keys.add(k):this.keys.delete(k);const has=(...ks)=>ks.some(k=>this.keys.has(k));this.vector=[+has('d','arrowright')-has('a','arrowleft'),+has('w','arrowup')-has('s','arrowdown'),+has('+')-has('-')];if(!this.vector.some(Boolean))this.send();}
 tick(){if(document.hidden)return;if(this.gamepad){const p=Array.from(navigator.getGamepads?.()||[]).find(Boolean);if(!p){this.stop();return;}const dz=v=>Math.abs(v||0)<.15?0:v;this.vector=[dz(p.axes[0]),-dz(p.axes[1]),(p.buttons[7]?.value||0)-(p.buttons[6]?.value||0)];}const now=JSON.stringify(this.vector);if(this.vector.some(Boolean)||now!==this.last){this.send();this.last=now;}}
 updateControls(state={}){
  const select=this.querySelector('select[data-camera]');
  const ids=(this.cameras||[]).map(c=>c.id);
  if(select && JSON.stringify(Array.from(select.options).map(o=>o.value).filter(Boolean))!==JSON.stringify(ids)){
   const selected=ids.includes(this.camera)?this.camera:'';
   select.innerHTML='<option value="">Choose camera</option>'+this.cameras.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
   select.value=selected;
   if(this.camera && !selected){this.stop();this.camera='';this.controlError=null;const preview=this.querySelector('[data-preview]');if(preview)preview.replaceChildren();}
  }
  this.querySelector('[data-handoff-panel]')?.loadCamera(this.camera);
  const camera=this.cameras?.find(c=>c.id===this.camera);
  const hand=this.querySelector('[data-hstate]'), stateRow=state.tracking?.handoffs?.[this.camera];
  if(hand)hand.textContent=camera?'Mode '+(camera.handoff_mode||'off')+(stateRow?' · '+stateRow.state+' · '+Math.floor(Date.now()/1000-stateRow.since)+' s':'')+(stateRow?.p90_ms!=null?' · median '+stateRow.median_ms+' ms · p90 '+stateRow.p90_ms+' ms':''):'Choose a camera';
  const nativePanel=this.querySelector('[data-native-controls]');
  if(nativePanel)nativePanel.hidden=camera?.control_protocol==='canon';
  const ready=!!camera?.control_enabled&&!camera.native_tracking&&['visca','canon'].includes(camera.control_protocol)&&room.socket?.readyState===1;
  this.querySelectorAll('[data-pad],[data-zoom],[data-gamepad]').forEach(el=>el.disabled=!ready);
  const badge=this.querySelector('[data-manual]');
  if(badge)badge.textContent=this.controlError||(!camera?'Choose a camera to control':camera.native_tracking?'Camera-native tracking is ON. Stop camera tracking to restore manual control.':room.socket?.readyState!==1?'Connecting to camera controls':!ready?'Enable Canon XC or VISCA in camera settings':state.manual_cameras?.includes(this.camera)?'Manual control — follow paused':camera.control_protocol==='canon'?'Ready · Canon XC':'Ready · VISCA UDP '+camera.visca_port);
  this.querySelectorAll('[data-camera] option').forEach(o=>{const c=this.cameras?.find(c=>c.id===o.value);if(c)o.textContent=`${c.name} · ${c.control_protocol.toUpperCase()} · ${c.control_enabled?'Enabled':'Disabled'}`;});
 }
 render(){
  this.camera=this.camera||this.cameras.find(c=>c.control_enabled&&['visca','canon'].includes(c.control_protocol))?.id||this.cameras[0]?.id;
  this.innerHTML=`<section class="panel"><div class="panel-body"><h2>Camera Preset Studio</h2><p>Frame your camera and save named scenes.</p><label>Camera<select data-camera><option value="">Choose camera</option>${this.cameras.map(c=>`<option value="${esc(c.id)}" ${c.id===this.camera?'selected':''}>${esc(c.name)} · ${c.control_enabled?'Control enabled':'Control disabled'}</option>`).join('')}</select></label><p data-manual role="status">Control released</p><div data-preview></div><div class="actions"><button data-pad style="touch-action:none;width:200px;height:150px" aria-label="Drag joystick to pan and tilt">Drag to pan / tilt</button><button style="touch-action:none" data-zoom="1">Hold zoom +</button><button style="touch-action:none" data-zoom="-1">Hold zoom −</button><button data-stop class="danger">STOP</button></div><label>Joystick speed<input data-speed type="range" min="0.05" max="1" step="0.05" value="0.25"></label><label><input data-gamepad type="checkbox">Enable USB gamepad (left stick; triggers zoom)</label><p class="hint">Focus the joystick for arrows / WASD and + / −. Escape stops. Release, blur or disconnect stops movement.</p><div data-native-controls><h3>Camera-native tracking</h3><p>Presenter follows a person; Zone uses regions configured on the camera. Enabling stops mic follow and locks this camera’s joystick/preset control.</p><div class="actions"><button data-native="read">Check support / status</button><button data-native="presenter">Start presenter tracking</button><button data-native="off">Stop camera tracking</button></div><p data-native-status role="status"></p></div><h3>Save this framing</h3><p>Arrange the selected camera with the joystick, give the shot a name, then save. Saving the same name updates that scene. Select another camera and save the same name to add its framing.</p><label>Scene name<input data-name maxlength="80" placeholder="For example, Neha"></label><button data-create class="primary">Save current framing as scene</button><p data-save-status role="status"></p><h3>Saved scenes</h3><div data-scenes></div></div></section>`;
  const q=s=>this.querySelector(s);
  q("[data-native-controls]").after(handoffPanel(this));

  const preview=()=>{q('[data-preview]').innerHTML=this.camera?`<camera-player camera="${esc(this.camera)}" name="Studio preview"></camera-player>`:'';};preview();
  q('[data-camera]').onchange=e=>{this.stop();this.camera=e.target.value;this.controlError=null;preview();this.updateControls();};
  q('[data-stop]').onclick=()=>this.stop();q('[data-gamepad]').onchange=e=>this.gamepad=e.target.checked;
  const pad=q('[data-pad]');const move=e=>{if(!this.pointer)return;const r=pad.getBoundingClientRect();this.vector=[Math.max(-1,Math.min(1,(e.clientX-r.left-r.width/2)/(r.width/2))),Math.max(-1,Math.min(1,-(e.clientY-r.top-r.height/2)/(r.height/2))),0];};
  pad.onpointerdown=e=>{this.controlError=null;pad.focus();pad.setPointerCapture(e.pointerId);this.pointer=true;move(e);this.send();};pad.onpointermove=move;pad.onpointerup=pad.onpointercancel=pad.onlostpointercapture=()=>this.stop();
  this.querySelectorAll('[data-zoom]').forEach(b=>{b.onpointerdown=e=>{this.controlError=null;b.setPointerCapture(e.pointerId);this.pointer=true;this.vector=[0,0,Number(b.dataset.zoom)];this.send();};b.onpointerup=b.onpointercancel=b.onlostpointercapture=()=>this.stop();});
  q('[data-create]').onclick=async()=>{
   const button=q('[data-create]');button.disabled=true;
   try{
    const name=q('[data-name]').value.trim();if(!name)throw Error('Enter a name for this scene.');
    const existing=(await api('/api/scenes')).some(s=>s.name.trim().toLowerCase()===name.toLowerCase());
    if(existing&&!confirm(`Update ${name} with the selected camera's current framing? Other cameras in the scene are retained.`))return;
    this.stop();
    await api('/api/scenes/capture',{camera:this.camera,name,confirm_update:existing});
    q('[data-save-status]').textContent=`${name} saved. You can now recall it from Saved scenes.`;
    await this.scenes();toast('Framing saved on camera and linked to scene.');
   }catch(e){fail(e);}finally{button.disabled=false;}
  };
  this.querySelectorAll('[data-native]').forEach(b=>b.onclick=async()=>{try{if(!this.camera)throw Error('Select a camera first.');this.stop();b.disabled=true;const path='/api/cameras/'+encodeURIComponent(this.camera)+'/native-tracking';const result=b.dataset.native==='read'?await api(path):await api(path,{enabled:b.dataset.native!=='off',mode:'presenter'});this.controlError=null;this.update(await api('/api/state'));this.querySelector('[data-native-status]').textContent='Camera tracking '+(result.enabled?'ON':'OFF')+' · '+result.mode;toast(result.enabled?'Camera tracks independently. Mic follow is stopped.':'Camera tracking off; manual control available.');}catch(e){fail(e);}finally{b.disabled=false;}});
  this.updateControls();this.scenes();
 }
 async scenes(){try{const scenes=await api('/api/scenes');if(!this.isConnected)return;const root=this.querySelector('[data-scenes]');root.innerHTML=scenes.map(s=>`<p>${esc(s.name)} <button data-recall="${esc(s.id)}" ${!Object.entries(s.presets).some(([cid,token])=>{const c=this.cameras.find(c=>c.id===cid);return c?.control_enabled&&(c.control_protocol!=='visca'||c.preset_records?.[token]);})?'disabled title="This scene references removed cameras or unsaved framing. Select a connected camera and Update framing."':''}>Recall scene</button> <button data-edit="${esc(s.id)}">Update framing</button> <button class="danger" data-remove="${esc(s.id)}">Remove scene</button></p>`).join('')||'<p>No scenes saved yet.</p>';root.querySelectorAll('[data-remove]').forEach(b=>b.onclick=async()=>{try{const current=await api('/api/state');const scene=current.settings.scenes.find(s=>s.id===b.dataset.remove);if(!scene)return;const linked=current.settings.tracking.zones.filter(z=>z.scene_id===scene.id);if(!confirm('Remove scene “'+scene.name+'” from ConfDeck? '+(linked.length?'This also unassigns it from: '+linked.map(z=>z.name).join(', ')+'. ':'')+'Camera memory is kept.'))return;b.disabled=true;try{await api('/api/scenes/'+encodeURIComponent(scene.id)+'?confirm_unlink=true',undefined,'DELETE');}catch(error){if(error.status===404||error.status===405)throw Error('Restart ConfDeck to activate scene removal, then try again.');throw error;}await this.scenes();toast('Scene removed. Camera memory retained.');}catch(e){fail(e);}finally{b.disabled=false;}});root.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>{const scene=scenes.find(s=>s.id===b.dataset.edit);this.querySelector('[data-name]').value=scene.name;this.querySelector('[data-pad]').focus();toast('Arrange the camera, then save to update this scene.');});root.querySelectorAll('[data-recall]').forEach(b=>b.onclick=async()=>{this.stop();try{const result=await api(`/api/scenes/${b.dataset.recall}/recall`,{});toast(Object.entries(result).map(([id,r])=>`${this.cameras.find(c=>c.id===id)?.name||id}: ${r.ok?'Command accepted':r.error}`).join('; '));}catch(e){fail(e);}});}catch(e){fail(e);}}
}
customElements.define('camera-studio',CameraStudio);
function handoffPanel(studio) {
  const panel=document.createElement('details');
  panel.dataset.handoffPanel='';
  panel.innerHTML=`<summary>Optional seat-locked tracking handoff</summary>
    <p data-hstate role="status"></p>
    <label>Command template<select name="kind"><option>none</option><option>visca_custom</option><option>visca_preset</option><option>http</option></select></label>
    <label>ON bytes, preset number or HTTP body<input name="on"></label>
    <label>OFF bytes, preset number or HTTP body<input name="off"></label>
    <label>HTTP method<select name="method"><option>POST</option><option>GET</option><option>PUT</option></select></label>
    <label>Camera HTTP path (use {value} for GET)<input name="path" value="/"></label>
    <label>Private HTTP headers (JSON)<input name="headers" type="password" placeholder="Leave blank to retain saved headers" autocomplete="off"></label>
    <label>Measured fallback settle time (ms)<input name="settle" type="number" value="1000" min="100" max="15000"></label>
    <label><input name="prep" type="checkbox">Prefer this camera off air when preparing a seat</label><label>Protected preset numbers<input name="slots" value="80,81"></label>
    <label>Keep tracking after silence (seconds, 0 keeps last person)<input name="idle" type="number" value="10" min="0" max="120"></label>
    <label>Re-anchor interval (seconds, 0 disables)<input name="reanchor" type="number" value="0" min="0" max="3600"></label>
    <label>Behavior test: OFF-to-recall gap (ms)<input name="gap" type="number" min="0" max="3000" value="0"></label><label>Behavior test: saved reserved destination preset<input name="destination" type="number" min="0" max="127"></label><div class="actions"></div>`;
  const field=name=>panel.querySelector('[name="'+name+'"]');
  let selectedCamera, loadVersion=0, headersSaved=false;
  panel.loadCamera=async cid=>{
    if(selectedCamera===cid)return;
    selectedCamera=cid;
    const version=++loadVersion;
    panel.querySelectorAll('input,select,button').forEach(el=>el.disabled=true);
    field('on').value='';field('off').value='';field('headers').value='';
    if(!cid)return;
    try{
      const saved=await api('/api/cameras/'+encodeURIComponent(cid)+'/handoff');
      if(version!==loadVersion)return;
      const config=saved.config;
      for(const name of ['kind','on','off','method','path'])field(name).value=config[name];
      field('settle').value=config.settle_ms;
      field('slots').value=saved.protected.join(',');
      field('prep').checked=saved.prep;
      field('idle').value=saved.silence_s;
      field('reanchor').value=saved.reanchor_s;
      field('destination').value='';field('gap').value=0;
      headersSaved=saved.headers_saved;
      panel.querySelectorAll('input,select,button').forEach(el=>el.disabled=false);
    }catch(e){if(version===loadVersion){selectedCamera=undefined;fail(e);}}
  };
  for(const [action,label] of Object.entries({bytes:'Try custom pattern',presets:'Try preset pattern',save:'Save controls',on:'Test ON',off:'Test OFF',travel:'Measure OFF → recall',recall_on:'Test recall while tracking',observe_travel:'Finish timing test',dry:'Dry-run',live:'Live handoff',pause:'Pause tracking handoff'})){
    const button=document.createElement('button');
    button.textContent=label;panel.querySelector('.actions').append(button);
    button.onclick=async()=>{
      const cid=studio.camera;
      if(!cid){toast('Select a camera first.',true);return;}
      const send=body=>api('/api/cameras/'+encodeURIComponent(cid)+'/handoff',body);
      try{
        if(action==='bytes'||action==='presets'){
          if(!confirm('Unverified candidate for this camera. Testing it can cause movement. Fill fields only?'))return;
          field('kind').value=action==='bytes'?'visca_custom':'visca_preset';
          field('on').value=action==='bytes'?'0A 01 32 00 00 02 00':'80';
          field('off').value=action==='bytes'?'0A 01 32 00 00 03 00':'81';return;
        }
        button.disabled=true;
        if(action==='save'){
          await send({action,keep_headers:headersSaved&&!field('headers').value.trim(),silence_s:Number(field('idle').value),reanchor_s:Number(field('reanchor').value),prep:field('prep').checked,config:{kind:field('kind').value,on:field('on').value,off:field('off').value,method:field('method').value,path:field('path').value,headers:JSON.parse(field('headers').value||'{}'),settle_ms:Number(field('settle').value)},protected:field('slots').value.split(',').filter(x=>x.trim()).map(Number)});
          selectedCamera=undefined;
          await panel.loadCamera(cid);
        }else if(action==='on'||action==='off'){
          if(!confirm('Send tracking '+action.toUpperCase()+' to the selected camera now?'))return;
          await send({action,confirm:true});
          if(confirm('Did the camera '+(action==='on'?'start':'stop')+' following you?'))await send({action:'observe',observed:action});
          else if(action==='on')await send({action:'off',confirm:true});
        }else if(action==='travel'||action==='recall_on'){
          if(!confirm('Aim at a different reserved scene first. Run the selected movement test now?'))return;
          await send({action,confirm:true,token:field('destination').value,gap_ms:Number(field('gap').value)});
          toast('Click Finish timing test as soon as the preset settles.');return;
        }else if(action==='observe_travel'){
          const moved=confirm('Did the camera reach the saved framing?');
          await send({action,moved,stopped:moved&&confirm('Did the recall itself switch tracking off?')});
        }else if(action==='pause'){await send({action});}
        else{await send({action:'mode',mode:action});}
        toast('Handoff updated.');
      }catch(e){fail(e);}finally{button.disabled=false;}
    };
  }
  return panel;
}
