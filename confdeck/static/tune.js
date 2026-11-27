import './seat-radar.js';
import {room,  $, $$, esc, api, fail, toast, icon } from './common.js';

const fields = [
  ['confirm_ms', 'Confirm a speaker', 100, 10000, 100, 'ms', 'Wait for a steady position before choosing a seat.'],
  ['min_hold_ms', 'Hold the shot', 500, 30000, 100, 'ms', 'Minimum time between camera recalls.'],
  ['deadband_deg', 'Ignore small movements', 0, 45, 1, '°', 'A new target must be this far from the previous target.'],
  ['zone_margin_deg', 'Zone safety margin', 0, 30, 1, '°', 'The position must sit this far inside a zone.'],
  ['ema_alpha', 'Smoothing response', .01, 1, .01, '', 'Lower values smooth more; higher values respond faster.'],
  ['median_n', 'Median samples', 1, 15, 1, '', 'Suppress short spikes using the most recent samples. 1 turns this off.'],
  ['elevation_min', 'Minimum elevation', 0, 90, 1, '°', 'Ignore positions below this angle.'],
  ['elevation_max', 'Maximum elevation', 0, 90, 1, '°', 'Ignore positions above this angle.'],
  ['level_threshold', 'Speech level threshold', -90, 0, 1, 'dB', 'Used only when the level gate is enabled. Tune against your room noise.'],
  ['max_moves_per_min', 'Maximum recalls per minute', 1, 60, 1, '', 'Counts every assigned camera, including wide recalls.'],
  ['settle_ms', 'Camera settling time', 0, 10000, 100, 'ms', 'Allow the camera to finish moving before another recall.'],
  ['silence_ms', 'Return to wide after', 1000, 120000, 500, 'ms', 'Use the level gate for speech-based silence. Position-only mode treats missing input as inactivity.'],
];
const titles = {noise_gate:'Noise gate', beam:'Sensitivity & installation', leds:'Microphone LEDs', threshold:'Noise gate threshold', holdTime:'Hold time', installationType:'Mounting type', sourceDetectionThreshold:'Room sensitivity', offset:'Device azimuth offset', showFarendActivity:'Show far-end activity', brightness:'LED brightness', micOn:'Microphone on', micMute:'Microphone muted', micCustom:'Custom LED', color:'Color', enabled:'Enabled', azimuth:'Azimuth', elevation:'Elevation', min:'Minimum', max:'Maximum', weight:'Priority weight'};
const help = {threshold:'Signals below this dB threshold are gated on outputs where noise gate is enabled.', holdTime:'Keeps the gate open briefly after the signal drops.', sourceDetectionThreshold:'Quiet room is more sensitive; Loud room is less sensitive.', installationType:'Match the physical ceiling installation.', showFarendActivity:'Visualize far-end activity using the microphone’s supported LED option.', offset:'Rotates the device beam and device zones. Avoid also applying the same app offset.', brightness:'0 turns the LED ring off; 5 is full brightness.', weight:'How strongly this zone is favored by the microphone.'};
const btn = (label, action, cls='ghost', extra='') => `<button type="button" class="${cls}" data-tune="${action}" ${extra}>${label}</button>`;
const panel = (title, body, extra='') => `<section class="panel"><div class="panel-header"><h2>${title}</h2>${extra}</div><div class="panel-body">${body}</div></section>`;
const download = (name, data) => { const url = URL.createObjectURL(new Blob([JSON.stringify(data,null,2)], {type:'application/json'})); const a=document.createElement('a'); a.href=url; a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); };

class TuneFollow extends HTMLElement {
  async connectedCallback() {
    this.tab='filter'; this.draft=null; this.guide=null;
    this.innerHTML='<p class="note">Loading local tracking settings…</p>';
    this.addEventListener('click', e=>this.click(e));
    this.addEventListener('input', e=>this.input(e));
    this.addEventListener('seats-saved',e=>{this.draft=e.detail;});
    this.addEventListener('change', e=>this.change(e));
    this.addEventListener('submit', e=>{e.preventDefault();e.stopPropagation();});
    try { const data={settings:structuredClone(room.state.settings.tracking),state:room.state.tracking,profiles:room.state.tracking_profiles}; if (!this.isConnected) return; this.draft=data.settings; this.profiles=data.profiles; this.live=data.state; this.render(); } catch(e){fail(e);}
  }
  update(state) {
    this.querySelector('seat-radar')?.update(state); this.scenes=state.settings.scenes||[]; this.cameras=state.settings.cameras; this.mic=state.mic; this.live=state.tracking;
    if(this.draft) this.paint();
  }
  render() {
    const s=this.draft;
    this.innerHTML=`<div class="page-heading"><div><div class="eyebrow">VOICE TRACKING DIRECTOR</div><h1>Advanced tracking<span class="title-dot">.</span></h1><p>A steady shot starts with a clear decision.</p></div><div class="actions">${btn('Test with my voice','guide','ghost')}${btn('Stop following','stop','danger stop-follow')}</div></div>
      <section class="follow-bar"><div><span class="dot" id="follow-dot"></span><strong id="follow-mode">Dry-run</strong><span id="follow-description">Review decisions before moving cameras.</span></div><div class="segmented" role="group" aria-label="Tracking mode">${btn('Dry-run','dry')}${btn('Follow live','live')}</div></section>
      <nav class="tune-tabs" aria-label="Tuning sections">${[['filter','Tracking filter'],['zones','Seat and camera assignments'],['device','Device schema']].map(([id,name])=>btn(name,'tab','',`data-tab="${id}" aria-pressed="${this.tab===id}"`)).join('')}</nav>
      <div id="tune-guide"></div><div class="tune-content">${this.tab==='filter'?this.filters():this.tab==='zones'?this.zones():this.devices()}</div>
      <dialog class="tune-dialog"></dialog>`;
    this.paint();
  }
  filters() {
    const s=this.draft;
    return panel('Make the camera feel natural',`<div class="profile-row">${['Calm','Balanced','Responsive','Custom'].map(p=>btn(p,'profile',s.profile===p?'primary':'ghost',`data-profile="${p}"`)).join('')}<span class="hint">Starting points. Tune these values in your room.</span></div><div class="filter-grid">${fields.map(([key,label,min,max,step,unit,desc])=>`<label class="range-field"><span>${label}<output id="out-${key}">${s[key]} ${unit}</output></span><input type="range" data-filter="${key}" min="${min}" max="${max}" step="${step}" value="${s[key]}" aria-label="${label}"><small>${desc}</small></label>`).join('')}</div><div class="form-grid gate-controls"><label class="checkbox"><input type="checkbox" data-filter="activity_gate" ${s.activity_gate?'checked':''}>Require a talking flag</label><label class="checkbox"><input type="checkbox" data-filter="level_gate" ${s.level_gate?'checked':''}>Require speech above the level threshold</label><label>Canon recall speed (optional, 1–100)<input type="number" min="1" max="100" data-filter="preset_speed" value="${s.preset_speed??''}" placeholder="Camera default"></label></div><p class="hint">TCC Medium provides microphone level and far-end beam freeze, not a direct near-end talking flag. Enable the level gate for silence detection. If a required signal is missing, tracking waits. Recall speed is supported here for Canon; VISCA/ONVIF use their camera setting.</p><p class="note">Keep zones realistic: tune their width using speech at the actual seats. The displayed beam cone is illustrative.</p><div class="form-actions"><span id="draft-note" class="hint">Changes take effect when saved.</span>${btn('Export settings','export')}${btn('Import settings','import')}${btn('Save tuning','save','primary')}<input type="file" id="settings-file" hidden accept=".json,application/json"></div>`);
  }
  assignments(mapping, prefix) {
    return (this.cameras||[]).map(c=>{
      const cur = mapping?.[c.id] || '';
      const recs = c.preset_records || {};
      const hasRec = cur && Object.prototype.hasOwnProperty.call(recs, cur);
      return `<label>${esc(c.name)} · saved framing<select data-map="${prefix}" data-camera="${esc(c.id)}"><option value="" ${!cur?'selected':''}>Unassigned</option>${cur && !hasRec ? `<option value="${esc(cur)}" selected>Preset ${esc(cur)} (unnamed)</option>` : ''}${Object.entries(recs).map(([token,name])=>`<option value="${esc(token)}" ${cur===token?'selected':''}>${esc(name)}</option>`).join('')}</select></label>`;
    }).join('')||'<p class="hint">Save camera framing in the joystick panel first.</p>';
  }
  zones() {
    const s=this.draft;
    return `<div class="two-col"><div class="stack">${panel('Seats in the room',`<p>Learn a seat from 20 seconds of soft, normal and loud speech, then assign existing camera presets.</p><div class="actions">${btn('Learn a seat · 20 seconds','learn','primary')}${btn('Add manually','add-zone')}</div><p class="hint">Learning saves angles only. It never creates or overwrites a camera preset.</p><div class="seat-list">${s.zones.map((z,i)=>`<article class="seat-row"><span class="seat-number">${String(i+1).padStart(2,'0')}</span><div><h3>${esc(z.name)}</h3><small>${z.azimuth}° azimuth · ${z.elevation}° elevation · ${z.radius}° radius</small><p class="hint">${Object.entries(z.presets).map(([cid,p])=>`${esc(this.cameras?.find(c=>c.id===cid)?.name||cid)} → ${esc(p)}`).join(' · ')||'No presets assigned'}</p></div><div class="actions">${btn('Edit','edit-zone','ghost',`data-id="${z.id}"`)}${btn('Test recall','test-zone','ghost',`data-id="${z.id}"`)}${btn('Remove','remove-zone','ghost',`data-id="${z.id}"`)}</div></article>`).join('')||'<div class="empty"><h3>No seats learned yet</h3><p>Speak from your first seat, then choose Learn a seat.</p></div>'}</div><div id="zone-warnings"></div>`)}${panel('Wide shot',`<p>Recall these presets after silence. Leave a camera blank to keep its current view.</p><div class="form-grid">${this.assignments(s.wide_presets,'wide')}</div><div class="form-actions">${btn('Save seats & wide shot','save','primary')}</div>`)}</div><div class="stack">${panel('Camera control',`<p>Enable only the cameras you want this app to move.</p>${(this.cameras||[]).map(c=>`<form class="tracking-camera" data-camera="${c.id}"><h3>${esc(c.name)}</h3><label class="checkbox"><input name="control_enabled" type="checkbox" ${c.control_enabled?'checked':''}>Allow camera control</label><label>Protocol<select name="control_protocol">${[['none','Choose protocol'],['canon','Canon XC'],['onvif','ONVIF'],['visca','Sony VISCA over IP (UDP)']].map(([v,n])=>`<option value="${v}" ${v===c.control_protocol?'selected':''}>${n}</option>`).join('')}</select></label><div class="form-grid"><label>HTTP control port<input name="control_port" type="number" min="1" max="65535" value="${c.control_port}"></label><label>VISCA UDP port<input name="visca_port" type="number" min="1" max="65535" value="${c.visca_port}"></label></div>${btn('Save camera control','camera-save','ghost',`data-id="${c.id}"`)}</form>`).join('')||'<p>Add cameras on the Cameras page.</p>'}<p class="hint">Save camera framing only in the joystick panel on the Cameras page. Assign its named scene to a seat here.</p>`)}</div></div>`;
  }
  devices() {
    const d=this.device;
    return panel('TeamConnect Ceiling Medium',`<div class="device-intro"><div><p>Read the connected microphone before making changes.</p><span class="hint">A snapshot of all exposed settings is saved before your first change.</span></div>${btn('Read current settings','device-read','primary')}</div>${d?`<p class="note">${d.snapshot_at?`Your original snapshot: ${new Date(d.snapshot_at*1000).toLocaleString()}`:'No changes saved yet. Your snapshot will be created automatically.'}</p><div class="device-grid">${Object.entries(d.resources).map(([key,r])=>`<fieldset class="device-group" ${key in d.unavailable?'disabled':''}><legend>${esc(titles[key]||key.replace('_',' zone '))}</legend>${d.values[key]?this.schemaFields(d.values[key],r.schema,key):`<p class="hint">${esc(d.unavailable[key]||'Unavailable')}</p>`}</fieldset>`).join('')}</div><div class="form-actions">${btn('Factory defaults','factory','ghost','disabled title="Not offered by the published API"')}${btn('Revert to my snapshot','device-revert','ghost',!d.snapshot_at?'disabled':'')}${btn('Review & apply','device-apply','primary')}</div><p class="hint">${esc(d.note)}</p>`:'<div class="empty"><span class="device-symbol">'+icon('mic')+'</span><h3>Your microphone, tuned for this room</h3><p>Connect using SSCv2 on the Microphone page, then read its current settings here.</p><a href="#microphone">Open microphone connection →</a></div>'}`);
  }
  schemaFields(value,schema,path) {
    return Object.entries(schema.properties||{}).filter(([k])=>k in value).map(([k,r])=>{
      const p=`${path}.${k}`, v=value[k], name=titles[k]||k;
      if(r.type==='object')return `<fieldset><legend>${name}</legend>${this.schemaFields(v,r,p)}</fieldset>`;
      let input;
      if(r.type==='boolean')input=`<input type="checkbox" data-device="${p}" ${v?'checked':''}>`;
      else if(r.enum)input=`<select data-device="${p}" data-type="${r.type}">${r.enum.map(n=>`<option value="${n}" ${n===v?'selected':''}>${esc(({QuietRoom:'Quiet · more sensitive',NormalRoom:'Normal',LoudRoom:'Loud · less sensitive',FlushMounted:'Flush mounted',SurfaceMounted:'Surface mounted',Suspended:'Suspended'})[n]||n)}</option>`).join('')}</select>`;
      else input=`<input type="${r.type==='integer'||r.type==='number'?'number':'text'}" data-device="${p}" value="${esc(v)}" min="${r.minimum??''}" max="${r.maximum??''}" step="${r.multipleOf||1}">`;
      if(path==='noise_gate')input=`<input type="range" data-device="${p}" value="${v}" min="${r.minimum}" max="${r.maximum}" step="${r.multipleOf||1}"><output>${v} ${k==='threshold'?'dB':'ms'}</output>`;
      return `<label class="${r.type==='boolean'?'checkbox':''}">${r.type==='boolean'?input:''}${name}${r.type==='boolean'?'':input}${help[k]?`<small>${help[k]}</small>`:''}</label>`;
    }).join('');
  }
  input(e) {
    const el=e.target;
    if(el.dataset.filter){const k=el.dataset.filter; this.draft[k]=el.type==='checkbox'?el.checked:el.value===''?null:Number(el.value);this.draft.profile='Custom';const out=$(`#out-${k}`,this);if(out)out.textContent=`${el.value} ${fields.find(f=>f[0]===k)?.[5]||''}`; const n=$('#draft-note',this);if(n)n.textContent='Unsaved changes · save to use this tuning.';}
    if(el.dataset.map==='wide'){if(el.value.trim())this.draft.wide_presets[el.dataset.camera]=el.value.trim();else delete this.draft.wide_presets[el.dataset.camera];}
    if(el.dataset.device&&el.type==='range') el.nextElementSibling.textContent=`${el.value} ${el.dataset.device.endsWith('threshold')?'dB':'ms'}`;
  }
  async change(e) {
    try {
      if(e.target.dataset.map==='wide'||e.target.dataset.filter) this.input(e);
      if(e.target.id==='settings-file'){const file=e.target.files[0];if(!file)return;if(file.size>128000)throw Error('Settings file is too large.');const s=JSON.parse(await file.text());const result=await api('/api/tracking/settings',s,'PUT');this.draft=(await api('/api/tracking')).settings;this.render();toast(result.warnings.join(' ')||'Settings imported and saved.');}
      if(e.target.id==='replay-file'){const file=e.target.files[0];if(!file)return;if(file.size>4000000)throw Error('Recording must be under 4 MB.');const d=JSON.parse(await file.text());await api('/api/tracking/replay',{events:d.events,speed:Number($('#replay-speed',this).value)});this.showReplay=true;toast('Replay started in dry-run.');}
    }catch(error){fail(error);}
  }
  async click(e) {
    const b=e.target.closest('[data-tune]');if(!b)return;e.preventDefault();e.stopPropagation();
    const act=b.dataset.tune;b.disabled=true;
    try {
      if(act==='tab'){this.tab=b.dataset.tab;this.render();}
      if(act==='stop'){await api('/api/tracking/stop',{});this.guide=null;toast('Following stopped. A recall already accepted by a camera may still finish.');}
      if(act==='dry'||act==='live'){if(act==='live'&&!confirm('Follow live will recall presets on enabled cameras. Start following?'))return;await api('/api/tracking/mode',{live:act==='live'});}
      if(act==='profile'){this.draft={...this.draft,...(this.profiles[b.dataset.profile]||{}),profile:b.dataset.profile};this.render();}
      if(act==='save'){const r=await api('/api/tracking/settings',this.draft,'PUT');toast(r.warnings.length?`Saved! Advisory note: ${r.warnings.join(' ')}`:'Tuning saved. Dry-run is active.');}
      if(act==='export')download('voice-tracker-settings.json',this.draft);
      if(act==='import')$('#settings-file',this).click();
      if(act==='learn'){b.textContent='Listening… speak from one seat';const learned=await api('/api/tracking/learn',{});this.zoneDialog({...learned,name:`Seat ${this.draft.zones.length+1}`,presets:{}});}
      if(act==='add-zone')this.zoneDialog({id:crypto.randomUUID().replaceAll('-','').slice(0,12),name:`Seat ${this.draft.zones.length+1}`,azimuth:0,elevation:45,radius:25,presets:{}});
      if(act==='edit-zone')this.zoneDialog(this.draft.zones.find(z=>z.id===b.dataset.id));
      if(act==='zone-done')await this.saveZone();
      if(act==='dialog-close')$('dialog',this).close();
      if(act==='remove-zone'){if(!confirm('Remove this seat mapping?'))return;this.draft.zones=this.draft.zones.filter(z=>z.id!==b.dataset.id);await api('/api/tracking/settings',this.draft,'PUT');this.render();}
      if(act==='test-zone'){const z=this.draft.zones.find(z=>z.id===b.dataset.id);if(!Object.keys(z.presets).length)throw Error('Assign a preset first.');if(!confirm(`Move the assigned cameras to ${z.name}?`))return;for(const [camera,preset] of Object.entries(z.presets))await api('/api/tracking/test-recall',{camera,preset,confirm:true});toast('Preset commands accepted. Check the camera framing.');}
      if(act==='camera-save'){const f=new FormData(b.closest('form'));const body={control_protocol:f.get('control_protocol'),control_enabled:f.get('control_enabled')==='on',control_port:Number(f.get('control_port')),visca_port:Number(f.get('visca_port'))};await api(`/api/cameras/${b.dataset.id}`,body,'PATCH');toast('Camera control saved.');}
      if(act==='device-read'){this.device=await api('/api/mic/device-settings');this.render();}
      if(act==='device-apply'){const changes={};for(const el of $$('[data-device]',this)){const path=el.dataset.device.split('.');let root=changes;path.forEach((k,i)=>{if(i===path.length-1)root[k]=el.type==='checkbox'?el.checked:['number','range'].includes(el.type)||['number','integer'].includes(el.dataset.type)?Number(el.value):el.value;else root=root[k]??={};});}if(!confirm('Apply these microphone settings? Your original snapshot will be preserved and values read back to verify.'))return;this.device=await api('/api/mic/device-settings',{changes,confirm:true});this.render();toast('Device settings applied and verified.');}
      if(act==='device-revert'){if(!confirm('Restore all microphone values from your original snapshot?'))return;this.device=await api('/api/mic/device-settings/revert',{confirm:true});this.render();toast('Snapshot restored and verified.');}
      if(act==='record')await api('/api/tracking/record',{action:'start'});
      if(act==='record-stop')await api('/api/tracking/record',{action:'stop'});
      if(act==='record-export')download('voice-tracker-session.json',await api('/api/tracking/recording'));
      if(act==='replay-stop'){await api('/api/tracking/replay/stop',{});this.showReplay=false;}
      if(act==='sample'){const sample=await api(`/api/tracking/sample?scenario=${$('#sample-scenario',this).value}`);await api('/api/tracking/replay',{events:sample.events,speed:Number($('#replay-speed',this).value)});this.showReplay=true;}
      if(act==='guide')await this.startGuide();
      if(act==='guide-next')this.nextGuide();
      if(act==='guide-close'){this.guide=null;this.paint();}
    }catch(error){fail(error);}finally{b.disabled=false;if(act==='learn')b.textContent='Learn a seat · 20 seconds';}
  }
  zoneDialog(z) {
    this.editing=z.id;const d=$('dialog',this);
    d.innerHTML=`<div class="dialog-head"><div><div class="eyebrow">SEAT MAPPING</div><h2>${esc(z.name)}</h2></div>${btn('Close','dialog-close')}</div><form id="seat-form"><div class="form-grid">${[['name','Seat name','text'],['azimuth','Azimuth (°)','number'],['elevation','Elevation (°)','number'],['radius','Radius (°)','number']].map(([k,n,t])=>`<label>${n}<input name="${k}" type="${t}" value="${esc(z[k])}" required ${t==='number'?'step="0.1"':''}></label>`).join('')}</div><label>Scene<select name="scene_id"><option value="">No scene assigned</option>${(this.scenes||[]).map(s=>`<option value="${esc(s.id)}" ${z.scene_id===s.id?'selected':''}>${esc(s.name)}</option>`).join('')}</select></label><a href="#cameras">Create scene in Camera Studio</a><div class="form-actions">${btn('Save seat','zone-done','primary')}</div></form>`;d.showModal();
  }
  async saveZone() {
    const f=new FormData($('#seat-form',this));const presets={};$$('[data-map="seat"]',this).forEach(el=>{if(el.value.trim())presets[el.dataset.camera]=el.value.trim();});
    const z={scene_id:f.get('scene_id')||'',id:this.editing,name:f.get('name'),azimuth:Number(f.get('azimuth')),elevation:Number(f.get('elevation')),radius:Number(f.get('radius')),presets};
    const proposed={...this.draft,zones:[...this.draft.zones.filter(v=>v.id!==z.id),z]};const r=await api('/api/tracking/settings',proposed,'PUT');this.draft=proposed;this.render();toast(r.warnings.length?`Seat saved! Advisory: ${r.warnings.join(' ')}`:'Seat saved.');
  }
  async startGuide() {
    const zones=this.draft.zones.filter(z=>Object.keys(z.presets).length);
    if(zones.length<2)throw Error('Learn and assign at least two seats, then save your tuning first.');
    await api('/api/tracking/mode',{live:!!this.live.live});
    this.tab='filter';this.guide={zones:zones.slice(0,2),step:-1,result:null};this.nextGuide();this.render();
  }
  nextGuide() {if(!this.guide)return;this.guide.step++;this.guide.started=Date.now();this.guide.result=null;this.guide.initial=this.live?.current;this.guide.leftInitial=false;}
  paint() {
    if(!this.live||document.hidden)return;const s=this.live;
    const set=(id,v)=>{const n=$(id,this);if(n)n.textContent=v;};
    set('#follow-mode',s.live?'Following live':'Dry-run');set('#follow-description',s.live?'Enabled cameras can move.':'Review decisions before moving cameras.');
    const dot=$('#follow-dot',this);if(dot)dot.className=`dot ${s.live?'good':'warn'}`;
    $$('[data-tune="dry"],[data-tune="live"]',this).forEach(b=>b.classList.toggle('selected',(b.dataset.tune==='live')===s.live));
    const replay=this.showReplay?s.replay:null, view=replay||s;
    const label=id=>id==='wide'?'Wide shot':this.draft.zones.find(z=>z.id===id)?.name||id||'—';
    set('#t-current',label(view.current));set('#t-candidate',label(view.candidate));set('#t-cooldown',`${view.cooldown.toFixed(1)} s`);set('#t-moves',`${s.actual_moves_per_min} / ${this.draft.max_moves_per_min}`);
    const progress=$('#t-progress',this);if(progress)progress.value=view.progress;
    set('#tune-position',view.smoothed?`${view.smoothed[0].toFixed(1)}° azimuth · ${view.smoothed[1].toFixed(1)}° elevation`:'Waiting for microphone');
    set('#chart-source',replay?'recording (dry-run)':'live microphone');
    set('#record-status',`${s.recording?'Recording':'Captured'} · ${s.record_count} samples`);
    set('#replay-status',s.replay?`Replay ${s.replay.running?'running':'stopped'} · ${s.replay.time.toFixed(1)} s · ${s.replay.decisions.length} simulated decisions`:'Replay always runs in dry-run.');
    const log=$('#decision-log',this);if(log)log.innerHTML=(replay?replay.decisions.map(d=>({time:0,message:`Replay ${d.time.toFixed(1)} s: ${label(d.zone)} → ${Object.entries(d.presets).map(([c,p])=>`${c} preset ${p}`).join(', ')}`})):s.log).slice(-30).reverse().map(r=>`<li><time>${r.time?new Date(r.time*1000).toLocaleTimeString():''}</time><span>${esc(r.message)}</span></li>`).join('')||'<li>Waiting for microphone events.</li>';
    this.paintGuide();
  }
  paintGuide() {
    const root=$('#tune-guide',this);if(!root)return;
    const g=this.guide;if(!g){root.innerHTML='';return;}
    if(g.step>=3){root.innerHTML=panel('Voice test finished',`<p>Review the decision log and camera framing. Command acceptance does not verify the physical shot.</p>${btn('Close test','guide-close')}`);return;}
    const target=g.step<2?g.zones[g.step].id:'wide', seconds=(Date.now()-g.started)/1000;
    if(this.live.current!==g.initial)g.leftInitial=true;
    const fresh=this.mic?.age!=null&&this.mic.age<2;
    const reached=this.live.current===target&&(g.leftInitial||this.live.log.some(r=>r.time*1000>=g.started&&r.message.includes(target)&&(/Dry-run:|Moved to/.test(r.message))));
    if(!g.result&&reached&&fresh)g.result='PASS';
    if(!g.result&&seconds>(g.step===2?this.draft.silence_ms/1000+this.draft.min_hold_ms/1000+10:25))g.result='NEEDS TUNING';
    root.innerHTML=panel(`Voice test · step ${g.step+1} of 3`, `<div class="guide-row"><div><h3>${g.step<2?`Speak steadily at ${esc(g.zones[g.step].name)}`:'Pause and let the room become quiet'}</h3><p>${g.step<2?'Keep speaking until the seat is confirmed.':'Wait for your configured silence timer and wide-shot recall.'} ${this.live.live?'Check the physical camera framing.':'This is a dry-run decision test.'}</p><span class="badge ${g.result==='PASS'?'good':'warn'}">${g.result||`${Math.floor(seconds)} s · listening`}</span></div><div class="actions">${g.result?btn(g.step===2?'Finish':'Next step','guide-next','primary'):''}${btn('Close','guide-close')}</div></div>`);
  }
}
customElements.define('tune-follow',TuneFollow);
