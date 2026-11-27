import {api,esc,fail,toast,room,icon} from './common.js';
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const wrap=a=>(a%360+360)%360;
const delta=(a,b)=>(a-b+540)%360-180;
export const point=(a,e)=>[260+Math.sin(a*Math.PI/180)*(35+e*2),260-Math.cos(a*Math.PI/180)*(35+e*2)];
export const angles=(x,y)=>[wrap(Math.atan2(x-260,260-y)*180/Math.PI),clamp((Math.hypot(x-260,y-260)-35)/2,0,90)];
class SeatRadar extends HTMLElement {
 async connectedCallback(){
  this.locked=true;this.undo=[];this.redo=[];this.revision=0;this.saved=0;this.queue=Promise.resolve();this.selected=null;this.source=['seats','tuning'].includes(this.getAttribute('mode'))?(room.micId||'primary'):'primary';
  this.innerHTML='<p>Loading visual room setup…</p>';
  try{const tracking={settings:structuredClone(room.state.settings.tracking),state:room.state.tracking},scenes=room.state.settings.scenes;if(!this.isConnected)return;this.draft=tracking.settings;this.scenes=scenes;this.view=tracking.state;this.render();if(room.pendingSeat && this.zones().some(z=>z.id===room.pendingSeat)){this.selected=room.pendingSeat;room.pendingSeat=null;this.fill();this.paint();}}catch(e){fail(e);}
 }
 disconnectedCallback(){cancelAnimationFrame(this.frame);clearTimeout(this.timer);if(this.revision!==this.saved)this.save();}
 update(state){
  if(this.getAttribute('mode')==='preview')this.source=state.tracking.active_mic||'primary';
  const sourceView=state.tracking.sources?.[this.source];
  this.view=sourceView?{...state.tracking,...sourceView,events:state.tracking.events.filter(e=>(e.mic_id||'primary')===this.source)}:state.tracking;
  this.mic=this.source==='primary'?state.mic:(state.microphones||[]).find(m=>m.id===this.source);
  this.micSettings=this.source==='primary'?state.settings.mic:(state.settings.microphones||[]).find(m=>m.id===this.source);
  if(this.getAttribute('mode')==='seats'){
    const p=this.mic?.position;
    this.view={...this.view,smoothed:p?[p.azimuth,p.elevation]:null};
  }
  const scenes=state.settings.scenes||[];if(JSON.stringify(this.scenes)!==JSON.stringify(scenes)){this.scenes=scenes;const select=this.q('[data-scene]');if(select){select.innerHTML='<option value="">No scene</option>'+scenes.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');select.value=this.seat()?.scene_id||'';}}if(this.view?.live){this.locked=true;this.drag=null;}if(this.draft)this.paint();}
 zones(){return this.draft.zones.filter(z=>(z.mic_id||'primary')===this.source);}
 q(s){return this.querySelector(s);}
 editable(){return this.getAttribute('mode')!=='preview'&& !this.locked&&!this.view?.live;}
 checkpoint(){this.undo.push(structuredClone(this.draft));if(this.undo.length>40)this.undo.shift();this.redo.length=0;}
 changed(){this.revision++;clearTimeout(this.timer);this.timer=setTimeout(()=>this.save(),500);this.paint();}
 async save(){
  if(!this.draft||this.revision===this.saved)return;
  const revision=this.revision, snapshot=structuredClone(this.draft);
  const ids=new Set(room.state.settings.cameras.map(c=>c.id));
  for(const mapping of [snapshot.wide_presets,...snapshot.zones.map(z=>z.presets)]){
   for(const cid of Object.keys(mapping))if(!ids.has(cid))delete mapping[cid];
  }
  this.queue=this.queue.catch(()=>{}).then(async()=>{try{const result=await api('/api/tracking/settings',snapshot,'PUT');this.saved=revision;const n=this.q('[data-note]');if(n)n.textContent=result.warnings.join(' ')||'Seats saved';this.dispatchEvent(new CustomEvent('seats-saved',{bubbles:true,detail:snapshot}));}catch(e){fail(e);const n=this.q('[data-note]');if(n)n.textContent='Seats not saved. Retry Save seats.';}});await this.queue;
 }
 render(){
  this.innerHTML=`<section class="panel"><div class="panel-header"><h2>Seat map & sound exclusions</h2><button data-lock>Unlock setup</button></div><div class="panel-body radar-editor"><div><canvas width="520" height="520" tabindex="0" aria-label="Seat radar. Select a seat, unlock setup, then drag its center or square tolerance handle. Arrow keys move the selected seat." style="width:100%;max-width:600px;touch-action:none"></canvas><p>Shared seats are edited under their first microphone; other direction profiles are sampled in Accuracy Lab. Orange: live beam · Blue: seats · Red: sound exclusions · Purple: device priority zones. Drag a dot to place a seat; drag its square handle to change the error range.</p><p data-position role="status"></p><p data-warning role="status"></p><p data-note role="status">Seat editing is locked. Unlock setup to make changes.</p></div><div class="stack"><label>Seat<select data-seat><option value="">Choose a seat</option>${this.zones().map(z=>`<option value="${esc(z.id)}">${esc(z.name)}</option>`).join('')}</select></label><div class="actions"><button data-add>Add seat here</button><button data-capture>Use live beam position</button></div><label>Seat name<input data-name maxlength="80"></label><label>Seat matching range · tighter to wider<input data-radius type="range" min="${Math.max(5,this.draft.zone_margin_deg+1)}" max="90" step="1"><output data-range></output></label><label>Camera scene<select data-scene><option value="">No scene</option>${(this.scenes||[]).map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('')}</select></label><fieldset data-preset-fields><legend>Camera presets</legend><div data-presets></div></fieldset><a href="#cameras">Save camera framing with the joystick</a><div class="actions"><button data-delete>Delete seat</button><button data-undo>Undo</button><button data-redo>Redo</button><button data-save>Save seats</button></div><hr><h3>Microphone sound settings</h3><button data-read>Read microphone settings</button><div data-device><p>Read the microphone to edit sensitivity, noise gate and sound exclusion areas.</p></div></div></div></section>`;
  const canvas=this.q('canvas');
  this.q('[data-lock]').onclick=()=>{if(this.view?.live){toast('Turn Follow off before editing the seat map.',true);return;}this.locked=!this.locked;this.fill();this.paint();
   if(!this.locked){for(const c of room.state.settings.cameras.filter(c=>c.control_enabled&&['canon','onvif'].includes(c.control_protocol))){
    api('/api/cameras/'+encodeURIComponent(c.id)+'/presets').then(data=>{
     if(!this.isConnected)return;
     this.catalogs??={};this.catalogs[c.id]=Object.fromEntries(data.presets.map(p=>[p.token,p.name]));this.fillPresets();
    }).catch(fail);
   }}};
  this.q('[data-seat]').onchange=e=>{this.selected=e.target.value;this.fill();this.paint();};
  this.q('[data-add]').onclick=()=>{if(!this.editable())return;this.checkpoint();const [a,e]=this.view?.smoothed||[0,45];const z={id:crypto.randomUUID().replaceAll('-',''),name:`Seat ${this.draft.zones.length+1}`,mic_id:this.source,azimuth:a,elevation:e,radius:Math.max(25,this.draft.zone_margin_deg+1),presets:{},scene_id:''};this.draft.zones.push(z);this.selected=z.id;this.refreshSeats();this.changed();};
  this.q('[data-capture]').onclick=()=>{const z=this.seat();if(!z||!this.editable())return;if(!this.view?.smoothed||this.mic?.age==null||this.mic.age>3){toast('Wait for a fresh microphone position.',true);return;}this.checkpoint();[z.azimuth,z.elevation]=this.view.smoothed;this.fill();this.changed();};
  for(const selector of ['[data-name]','[data-radius]','[data-scene]'])this.q(selector).onchange=()=>{const z=this.seat();if(!z||!this.editable())return;this.checkpoint();z.name=this.q('[data-name]').value.trim()||z.name;z.radius=Number(this.q('[data-radius]').value);if(selector==='[data-scene]'){z.scene_id=this.q('[data-scene]').value;z.presets={...(this.scenes.find(s=>s.id===z.scene_id)?.presets||{})};}this.refreshSeats();this.changed();};
  this.q('[data-presets]').onchange=e=>{
   const z=this.seat(),cid=e.target.dataset.camera;
   if(!z||!cid||!this.editable())return;
   this.checkpoint();z.scene_id='';if(e.target.value)z.presets[cid]=e.target.value;else delete z.presets[cid];
   this.fill();this.changed();
  };
  this.q('[data-radius]').oninput=e=>this.q('[data-range]').textContent=`±${e.target.value}°`;
  this.q('[data-delete]').onclick=()=>{if(!this.seat()||!this.editable()||!confirm('Delete this seat?'))return;this.checkpoint();this.draft.zones=this.draft.zones.filter(z=>z.id!==this.selected);this.selected=null;this.refreshSeats();this.changed();};
  for(const [selector,from,to] of [['[data-undo]',this.undo,this.redo],['[data-redo]',this.redo,this.undo]])this.q(selector).onclick=()=>{if(!this.editable()||!from.length)return;to.push(structuredClone(this.draft));this.draft=from.pop();this.refreshSeats();this.changed();};
  this.q('[data-save]').onclick=()=>this.save();this.q('[data-read]').onclick=()=>this.readDevice();
  canvas.onpointerdown=e=>this.down(e);canvas.onpointermove=e=>this.move(e);canvas.onpointerup=()=>{if(this.drag){const kind=this.drag.kind;this.drag=null;if(!['exclusion','edge'].includes(kind))this.changed();}};canvas.onpointercancel=()=>{this.drag=null;};
  canvas.onkeydown=e=>{if(this.getAttribute('mode')==='tuning')return;const z=this.seat();if(!z||!this.editable()||!e.key.startsWith('Arrow'))return;e.preventDefault();this.checkpoint();z.azimuth=wrap(z.azimuth+(e.key==='ArrowRight'?1:e.key==='ArrowLeft'?-1:0));z.elevation=clamp(z.elevation+(e.key==='ArrowUp'?1:e.key==='ArrowDown'?-1:0),0,90);this.changed();};
  if(this.getAttribute('mode')==='preview'){this.q('.stack').hidden=true;this.q('[data-lock]').hidden=true;this.q('[data-note]').hidden=true;}
  if(this.getAttribute('mode')==='tuning'){this.q('.stack').querySelectorAll(':scope > label,:scope > .actions,:scope > a').forEach(n=>n.hidden=true);}
  if(this.getAttribute('mode')==='seats'){this.q('[data-read]').parentElement.querySelectorAll('hr,h3,[data-read],[data-device]').forEach(n=>n.hidden=true);}
  this.fill();this.paint();
 }
 seat(){return this.draft.zones.find(z=>z.id===this.selected);}
 refreshSeats(){this.q('[data-seat]').innerHTML='<option value="">Choose a seat</option>'+this.zones().map(z=>`<option value="${esc(z.id)}">${esc(z.name)}</option>`).join('');this.fill();}
 fillPresets(){
  const z=this.seat(),root=this.q('[data-presets]');
  root.innerHTML=room.state.settings.cameras.filter(c=>c.control_enabled).map(c=>{
   const records={...(this.catalogs?.[c.id]||c.preset_records)},token=z?.presets?.[c.id];
   if(token&&!records[token])records[token]='Assigned preset';
   return `<label>${esc(c.name)}<select data-camera="${esc(c.id)}"><option value="">Unassigned</option>${Object.entries(records).map(([id,name])=>`<option value="${esc(id)}" ${id===token?'selected':''}>${esc(id)} · ${esc(name)}</option>`).join('')}</select></label>`;
  }).join('');
 }
 fill(){this.fillPresets();const z=this.seat();this.q('[data-seat]').value=z?.id||'';this.q('[data-name]').value=z?.name||'';this.q('[data-radius]').value=z?.radius||25;this.q('[data-range]').textContent=z?`±${z.radius.toFixed(0)}°`:'';this.q('[data-scene]').value=z?.scene_id||'';}
 coordinates(e){const r=this.q('canvas').getBoundingClientRect();const x=(e.clientX-r.left)*520/r.width-260,y=(e.clientY-r.top)*520/r.height-260,a=-(this.rotation||0)*Math.PI/180;return [260+x*Math.cos(a)-y*Math.sin(a),260+x*Math.sin(a)+y*Math.cos(a)];}
 down(e){if(!this.editable())return;const [x,y]=this.coordinates(e);const z=this.seat();if(z&&this.getAttribute('mode')!=='tuning'){const h=point(z.azimuth+z.radius,z.elevation);if(Math.hypot(x-h[0],y-h[1])<18){this.checkpoint();this.drag={kind:'radius',id:z.id};this.q('canvas').setPointerCapture(e.pointerId);return;}}
  const hit=this.zones().find(z=>{const p=point(z.azimuth,z.elevation);return Math.hypot(x-p[0],y-p[1])<18;});
  if(hit&&this.getAttribute('mode')!=='tuning'){this.selected=hit.id;this.checkpoint();this.drag={kind:'seat',id:hit.id};this.fill();this.paint();this.q('canvas').setPointerCapture(e.pointerId);return;}
  const ex=this.device?.values[this.exclusionKey];
  if(ex?.enabled){for(const axis of ['azimuth','elevation'])for(const edge of ['min','max']){const a=axis==='azimuth'?ex.azimuth[edge]:(ex.azimuth.min+ex.azimuth.max)/2;const el=axis==='elevation'?ex.elevation[edge]:(ex.elevation.min+ex.elevation.max)/2;const p=point(this.appAngle(a),el);if(Math.hypot(x-p[0],y-p[1])<16){this.drag={kind:'edge',axis,edge};this.q('canvas').setPointerCapture(e.pointerId);return;}}}
  if(this.exclusionKey&&this.device?.values[this.exclusionKey]?.enabled){this.drag={kind:'exclusion'};this.q('canvas').setPointerCapture(e.pointerId);this.move(e);}
 }
 move(e){if(!this.drag||!this.editable())return;let [a,el]=angles(...this.coordinates(e));if(this.drag.kind==='edge'){const v=this.device.values[this.exclusionKey],{axis,edge}=this.drag,value=Math.round((axis==='azimuth'?this.deviceAngle(a):el)/5)*5;v[axis][edge]=edge==='min'?clamp(value,0,v[axis].max-5):clamp(value,v[axis].min+5,axis==='azimuth'?360:90);this.deviceDirty.add(this.exclusionKey);this.paint();return;}if(this.drag.kind==='exclusion'){const v=this.device.values[this.exclusionKey];a=this.deviceAngle(a);const width=v.azimuth.max-v.azimuth.min,height=v.elevation.max-v.elevation.min;const amin=clamp(Math.round((a-width/2)/5)*5,0,360-width),emin=clamp(Math.round((el-height/2)/5)*5,0,90-height);v.azimuth={min:amin,max:amin+width};v.elevation={min:emin,max:emin+height};this.deviceDirty.add(this.exclusionKey);this.paint();return;}const z=this.seat();if(!z)return;if(this.drag.kind==='radius')z.radius=clamp(Math.hypot(delta(a,z.azimuth),el-z.elevation),Math.max(5,this.draft.zone_margin_deg+1),90);else{z.azimuth=Math.round(a*10)/10;z.elevation=Math.round(el*10)/10;}this.fill();this.paint();}
 appAngle(a){const s=this.micSettings||{};return wrap((s.invert?-a:a)+(s.azimuth_offset||0));}
 deviceAngle(a){const s=this.micSettings||{};return wrap((a-(s.azimuth_offset||0))*(s.invert?-1:1));}
 paint(){if(!this.isConnected||document.hidden||this.frame)return;this.frame=requestAnimationFrame(()=>{this.frame=null;this.draw();});}
 draw(){if(!this.draft||!this.q('canvas'))return;const ctx=this.q('canvas').getContext('2d');ctx.clearRect(0,0,520,520);ctx.save();ctx.translate(260,260);ctx.rotate((this.rotation||0)*Math.PI/180);ctx.translate(-260,-260);ctx.strokeStyle=document.body.classList.contains('light')?'#cfdae8':'#65758b';ctx.fillStyle=document.body.classList.contains('light')?'#526b85':'#b9c8dc';ctx.font='13px sans-serif';for(const e of [0,30,60,90]){ctx.beginPath();ctx.arc(260,260,35+e*2,0,Math.PI*2);ctx.stroke();}for(const [a,label] of [[0,'Front'],[90,'Right'],[180,'Back'],[270,'Left']]){const p=point(a,105);ctx.fillText(label,p[0]-15,p[1]+4);}
  for(const [key,v] of Object.entries(this.device?.values||{})){if(!(key.startsWith('exclusion_')||key.startsWith('priority'))||!v.enabled)continue;ctx.beginPath();let first=true;for(const e of [v.elevation.min,v.elevation.max]){const arr=Array.from({length:61},(_,i)=>v.azimuth.min+(v.azimuth.max-v.azimuth.min)*i/60);if(e===v.elevation.max)arr.reverse();for(const a of arr){const p=point(this.appAngle(a),e);first?ctx.moveTo(...p):ctx.lineTo(...p);first=false;}}ctx.closePath();ctx.fillStyle=key.startsWith('priority')?'#c5a0ff44':'#e75d5d44';ctx.strokeStyle=key.startsWith('priority')?'#d6bcff':'#ff8383';ctx.fill();ctx.stroke();if(key===this.exclusionKey){for(const axis of ['azimuth','elevation'])for(const edge of ['min','max']){const p=point(this.appAngle(axis==='azimuth'?v.azimuth[edge]:(v.azimuth.min+v.azimuth.max)/2),axis==='elevation'?v.elevation[edge]:(v.elevation.min+v.elevation.max)/2);ctx.fillStyle='#fff4e8';ctx.fillRect(p[0]-5,p[1]-5,10,10);}ctx.fillText(`Exclusion ${v.azimuth.min}–${v.azimuth.max}° / ${v.elevation.min}–${v.elevation.max}°`,12,505);}}
  for(const [min,max] of [[0,this.draft.elevation_min],[this.draft.elevation_max,90]]){if(max<=min)continue;ctx.beginPath();ctx.arc(260,260,35+max*2,0,Math.PI*2);ctx.arc(260,260,35+min*2,0,Math.PI*2,true);ctx.fillStyle='#ffc07825';ctx.fill();}
  for(const p of [...(this.view?.events||[]).slice(-80),...(this.ghosts||[])]){if(!Number.isFinite(p.azimuth)||!Number.isFinite(p.elevation))continue;const xy=point(p.azimuth,p.elevation);ctx.beginPath();ctx.arc(...xy,p.accepted?2:3,0,Math.PI*2);ctx.strokeStyle=p.accepted?'#67e6be99':'#ff969699';ctx.stroke();if(p.accepted){ctx.fillStyle='#67e6be66';ctx.fill();}}
  for(const z of this.zones()){ctx.beginPath();for(let i=0;i<=72;i++){const t=i/72*Math.PI*2,p=point(z.azimuth+Math.cos(t)*z.radius,clamp(z.elevation+Math.sin(t)*z.radius,0,90));i?ctx.lineTo(...p):ctx.moveTo(...p);}ctx.closePath();ctx.fillStyle=z.id===this.view?.current?'#ff8a3d33':'#3aa0ff22';ctx.strokeStyle=this.highlight==='tolerance'?'#eb852e':'#2868bb';ctx.lineWidth=this.highlight==='tolerance'?3:1;ctx.fill();ctx.stroke();ctx.lineWidth=1;const p=point(z.azimuth,z.elevation);ctx.beginPath();ctx.arc(...p,8,0,Math.PI*2);ctx.fillStyle='#2868bb';ctx.fill();ctx.fillStyle=document.body.classList.contains('light')?'#203247':'#e5edf7';ctx.fillText(z.name,p[0]+12,p[1]);if(z.id===this.selected){const h=point(z.azimuth+z.radius,z.elevation);ctx.fillStyle='#fff';ctx.fillRect(h[0]-6,h[1]-6,12,12);}}
  ctx.restore();
  const trail=(this.view?.history||[]).filter(row=>Array.isArray(row.smooth)).slice(-40);ctx.strokeStyle='#ff8a3d66';ctx.beginPath();trail.forEach((row,i)=>{const p=point(...row.smooth);i?ctx.lineTo(...p):ctx.moveTo(...p);});ctx.stroke();
  if(this.view?.smoothed){const p=point(...this.view.smoothed);ctx.fillStyle='#ff8a3d';ctx.beginPath();ctx.arc(...p,7,0,Math.PI*2);ctx.fill();}
  const selected=this.seat();let warning='';
  if(selected){
   if(this.zones().some(z=>z.id!==selected.id&&Math.hypot(delta(z.azimuth,selected.azimuth),z.elevation-selected.elevation)<z.radius+selected.radius))warning='This seat overlaps another seat. Tighten the error range or move the centers apart.';
   const samples=[[selected.azimuth,selected.elevation],...Array.from({length:72},(_,i)=>[selected.azimuth+Math.cos(i*Math.PI/36)*selected.radius,clamp(selected.elevation+Math.sin(i*Math.PI/36)*selected.radius,0,90)])];
   if(Object.entries(this.device?.values||{}).some(([key,v])=>key.startsWith('exclusion_')&&v.enabled&&samples.some(([a,e])=>{a=this.deviceAngle(a);return a>=v.azimuth.min&&a<=v.azimuth.max&&e>=v.elevation.min&&e<=v.elevation.max;})))warning+=' This seat touches a microphone exclusion area; check the red region.';
  }
  this.q('[data-warning]').textContent=warning+' '+(this.view?.reason||'')+' · Rings: elevation limits. Green circles: accepted; red outlines: rejected; ghost outlines: proposed replay.';
  const locked=!this.editable();this.q('[data-preset-fields]').disabled=locked||!this.seat();this.q('[data-preset-fields]').hidden=this.getAttribute('mode')==='tuning';this.q('[data-lock]').textContent=locked?'Unlock setup':'Lock for live use';for(const s of ['[data-add]','[data-capture]','[data-name]','[data-radius]','[data-scene]','[data-delete]','[data-undo]','[data-redo]'])this.q(s).disabled=locked;
  if(this.device){this.querySelectorAll('[data-device] input,[data-device] select,[data-apply],[data-revert]').forEach(el=>el.disabled=locked||el.matches('[data-revert]')&&!this.device.snapshot_at);if(!this.device.values.beam)this.q('[data-sensitivity]').disabled=true;if(!this.device.values.noise_gate){this.q('[data-gate]').disabled=true;this.q('[data-hold]').disabled=true;}}
  this.q('[data-position]').textContent=this.view?.smoothed?`Live beam ${this.view.smoothed[0].toFixed(0)}° / ${this.view.smoothed[1].toFixed(0)}°${this.mic?.age>3?' · stale':''}`:'Waiting for live microphone position';
 }
 deviceQuery(){return this.source==='primary'?'':'?source='+encodeURIComponent(this.source);}
 async readDevice(){const button=this.q('[data-read]');button.disabled=true;try{this.device=await api('/api/mic/device-settings'+this.deviceQuery());this.deviceDirty=new Set();this.devicePanel();this.paint();}catch(e){fail(e);}finally{button.disabled=false;}}
 devicePanel(){const d=this.device;const keys=Object.keys(d.values).filter(k=>k.startsWith('exclusion_'));this.exclusionKey=keys[0];const beam=d.values.beam,gate=d.values.noise_gate;
  this.q('[data-device]').innerHTML=`<label>Sensitivity<select data-sensitivity ${beam?'':'disabled'}>${[['QuietRoom','Quiet room · more sensitive'],['NormalRoom','Normal room'],['LoudRoom','Loud room · less sensitive']].map(([v,n])=>`<option value="${v}" ${beam?.sourceDetectionThreshold===v?'selected':''}>${n}</option>`).join('')}</select></label><details><summary>More device options · preview not exact</summary><label>Device noise threshold (dB)<input data-gate type="range" min="-90" max="-40" step="1" value="${gate?.threshold??-80}" ${gate?'':'disabled'}><output data-gate-value>${gate?.threshold??'Unavailable'} dB</output></label><label>Device gate hold (ms)<input data-hold type="range" min="50" max="1000" step="50" value="${gate?.holdTime??350}" ${gate?'':'disabled'}><output data-hold-value>${gate?.holdTime??'Unavailable'} ms</output></label></details><label>Sound exclusion area<select data-exclusion>${keys.map((k,i)=>`<option value="${k}">Noise area ${i+1}</option>`).join('')}</select></label>${keys.length?'':'<p>Exclusion settings unavailable. Use Sennheiser Control Cockpit.</p>'}<label><input data-exclusion-enabled type="checkbox">Exclude sound in this area</label><p>Select an area and enable it, then drag an empty part of the radar to place it. Adjust its size below.</p><label>Area width<input data-width type="range" min="5" max="360" step="5"></label><label>Area height<input data-height type="range" min="5" max="90" step="5"></label><p>Red areas are microphone device exclusions; blue areas are seat matching tolerances.</p><button data-practical-device>Practical microphone profile</button><button data-apply>Review & apply microphone changes</button><button data-revert ${d.snapshot_at?'':'disabled'}>Revert microphone snapshot</button>`;
  this.q('[data-practical-device]').onclick=()=>{if(!this.editable())return;if(d.values.beam){d.values.beam.sourceDetectionThreshold='NormalRoom';this.deviceDirty.add('beam');}if(d.values.noise_gate){d.values.noise_gate.threshold=-80;d.values.noise_gate.holdTime=350;this.deviceDirty.add('noise_gate');}const dirty=new Set(this.deviceDirty);this.devicePanel();this.deviceDirty=dirty;toast('Practical draft: Normal room, −80 dB, 350 ms. Review and apply; preview not exact.');};
  this.q('[data-sensitivity]').onchange=e=>{if(!this.editable())return;d.values.beam.sourceDetectionThreshold=e.target.value;this.deviceDirty.add('beam');};
  for(const [selector,key,field,output,unit] of [['[data-gate]','noise_gate','threshold','[data-gate-value]','dB'],['[data-hold]','noise_gate','holdTime','[data-hold-value]','ms']])this.q(selector).oninput=e=>{if(!this.editable())return;d.values[key][field]=Number(e.target.value);this.deviceDirty.add(key);this.q(output).textContent=`${e.target.value} ${unit}`;};
  const fill=()=>{const v=d.values[this.exclusionKey];this.q('[data-exclusion-enabled]').checked=!!v?.enabled;this.q('[data-width]').value=v?v.azimuth.max-v.azimuth.min:30;this.q('[data-height]').value=v?v.elevation.max-v.elevation.min:20;};fill();
  this.q('[data-exclusion]').onchange=e=>{this.exclusionKey=e.target.value;fill();};
  this.q('[data-exclusion-enabled]').onchange=e=>{if(!this.editable()||!this.exclusionKey)return;const v=d.values[this.exclusionKey];v.enabled=e.target.checked;this.deviceDirty.add(this.exclusionKey);this.paint();};
  for(const [selector,axis] of [['[data-width]','azimuth'],['[data-height]','elevation']])this.q(selector).oninput=e=>{if(!this.editable()||!this.exclusionKey)return;const v=d.values[this.exclusionKey],size=Number(e.target.value),max=axis==='azimuth'?360:90,center=(v[axis].min+v[axis].max)/2,min=clamp(Math.round((center-size/2)/5)*5,0,max-size);v[axis]={min,max:min+size};this.deviceDirty.add(this.exclusionKey);this.paint();};
  this.q('[data-apply]').onclick=async()=>{if(!this.editable())return;const changes={};for(const key of this.deviceDirty){const v=d.values[key];changes[key]=key==='beam'?{sourceDetectionThreshold:v.sourceDetectionThreshold}:key==='noise_gate'?{threshold:v.threshold,holdTime:v.holdTime}:{enabled:v.enabled,azimuth:v.azimuth,elevation:v.elevation};}if(!Object.keys(changes).length)return;if(!confirm('Apply the displayed microphone sensitivity, gate or red exclusion areas? Values will be read back; your original snapshot is retained.'))return;try{this.device=await api('/api/mic/device-settings'+this.deviceQuery(),{changes,confirm:true});this.deviceDirty.clear();this.devicePanel();this.paint();this.dispatchEvent(new CustomEvent('device-applied',{bubbles:true}));toast('Microphone changes applied and verified.');}catch(e){fail(e);}};
  this.q('[data-revert]').onclick=async()=>{if(!this.editable()||!confirm('Restore the microphone settings from its original snapshot?'))return;try{this.device=await api('/api/mic/device-settings/revert'+this.deviceQuery(),{confirm:true});this.deviceDirty.clear();this.devicePanel();this.paint();}catch(e){fail(e);}};
 }
}
customElements.define('seat-radar',SeatRadar);


// Each mini-map is drawn in its own microphone's angular coordinates.
class MicTeam extends HTMLElement {
 connectedCallback(){this.open=!!room.mixedMapOpen;this.render();}
 render(){
  this.innerHTML=`<section class="panel mic-team"><div class="panel-header"><div><h2>Microphone team</h2><p class="hint">Live levels, room lock and seat maps</p></div><button data-mixed aria-expanded="false">Show mixed map</button></div><div class="panel-body"><div class="mic-team-cards"></div><p data-team-lock class="hint" role="status"></p><div data-maps hidden><p class="hint">Each map is relative to its microphone. Select a seat to edit its existing assignment below. The brighter microphone has the louder fresh level; that alone does not confirm a speaker.</p><div class="mic-team-maps"></div><p class="hint">Use Mic Tuning for the selected microphone to review sound exclusions. TCC 2 exclusions are configured in Sennheiser Control Cockpit.</p></div></div></section>`;
  this.querySelector('[data-mixed]').onclick=()=>{this.open=!this.open;room.mixedMapOpen=this.open;this.querySelector('[data-maps]').hidden=!this.open;this.querySelector('[data-mixed]').setAttribute('aria-expanded',String(this.open));this.querySelector('[data-mixed]').textContent=this.open?'Hide mixed map':'Show mixed map';this.update(room.state);};
  this.addEventListener('click',async e=>{const b=e.target.closest('[data-team-source]');if(!b)return;await document.querySelector('seat-radar')?.save();room.micId=b.dataset.teamSource;room.pendingSeat=b.dataset.teamSeat||null;location.hash='seats';if(room.onMicTeamSelect)room.onMicTeamSelect();});
  this.querySelector('[data-maps]').hidden=!this.open;
  this.querySelector('[data-mixed]').setAttribute('aria-expanded',String(this.open));
  this.querySelector('[data-mixed]').textContent=this.open?'Hide mixed map':'Show mixed map';
  this.update(room.state);
 }
 update(state){
  if(!state?.settings)return;
  const mics=[{...state.settings.mic,id:'primary'},...(state.settings.microphones||[])];
  if(mics.length<2){this.hidden=true;return;}this.hidden=false;
  const readings=mics.map((m,i)=>{const live=(state.microphones||[]).find(x=>x.id===m.id)||(m.id==='primary'?state.mic:null);const level=live?.position?.level;return {m,i,live,level:live?.age!=null&&live.age<1&&Number.isFinite(level)?level:null};});
  const eligible=readings.filter(r=>r.level!==null&&r.level>=state.settings.tracking.level_threshold);const loudest=eligible.length?eligible.reduce((a,b)=>a.level>=b.level?a:b).m.id:null;
  this.querySelector('.mic-team-cards').innerHTML=readings.map(({m,i,live,level})=>`<button class="mic-team-card ${loudest===m.id?'is-loud':''} ${m.id===(room.micId||'primary')?'is-selected':''}" data-team-source="${esc(m.id)}"><span class="team-mic-icon" aria-hidden="true">${icon('mic')}</span><span><strong>Mic ${i+1} · ${esc(m.ip)}</strong><small>${esc(live?.state||'Disconnected')} · ${level===null?'No fresh level':level.toFixed(0)+' dB'}${loudest===m.id?' · loudest':''}</small></span></button>`).join('');
  const lock=state.settings.tracking.zones.find(z=>z.id===state.tracking.current);
  this.querySelector('[data-team-lock]').textContent=`Room lock: ${lock?.name||'Waiting for a confirmed seat'} · ${state.tracking.reason||'Waiting for microphone'}`;
  if(!this.open)return;
  const container=this.querySelector('.mic-team-maps');
  container.innerHTML=readings.map(({m,i})=>`<div class="mic-team-map"><strong>Mic ${i+1} · ${esc(m.ip)}</strong><canvas width="260" height="260" data-map-source="${esc(m.id)}" aria-label="Mic ${i+1} seat map"></canvas><div class="team-seat-list">${state.settings.tracking.zones.filter(z=>(z.mic_id||'primary')===m.id).map(z=>`<button data-team-source="${esc(m.id)}" data-team-seat="${esc(z.id)}" class="team-seat ${z.id===state.tracking.current?'is-locked':''}">${esc(z.name)}${z.id===state.tracking.current?' · locked':''}</button>`).join('')||'<span class="hint">No seats assigned</span>'}</div></div>`).join('');
  for(const {m,live} of readings){const canvas=[...container.querySelectorAll('canvas')].find(c=>c.dataset.mapSource===m.id),ctx=canvas.getContext('2d');ctx.scale(.5,.5);ctx.strokeStyle='#90a7bd';for(const el of [0,30,60,90]){ctx.beginPath();ctx.arc(260,260,35+el*2,0,Math.PI*2);ctx.stroke();}for(const z of state.settings.tracking.zones.filter(z=>(z.mic_id||'primary')===m.id)){const p=point(z.azimuth,z.elevation);ctx.fillStyle=z.id===state.tracking.current?'#ef8b38':'#398bd4';ctx.beginPath();ctx.arc(...p,10,0,Math.PI*2);ctx.fill();ctx.fillStyle=document.body.classList.contains('light')?'#203247':'#e5edf7';ctx.font='18px sans-serif';ctx.fillText(z.name,p[0]+13,p[1]);}const pos=live?.age!=null&&live.age<1?live.position:null;if(pos&&Number.isFinite(pos.azimuth)&&Number.isFinite(pos.elevation)){ctx.fillStyle='#fa9e35';ctx.beginPath();ctx.arc(...point(pos.azimuth,pos.elevation),6,0,Math.PI*2);ctx.fill();}}
 }
}
customElements.define('mic-team',MicTeam);
