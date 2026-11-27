import {room, api, esc, fail} from './common.js';
import {syncPlayers} from './player.js';
const conditions=['normal','soft','loud','short','alternation','overlap','silence','noise','echo','moving','transient','long_pause'];
let movementBefore=null;
const quiet=new Set(['silence','noise','echo','transient','long_pause']);
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const download=(name,data)=>{const a=document.createElement('a'),u=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);};
class AccuracyLab extends HTMLElement {
 async connectedCallback(){
  this.generation=0;this.busy=false;this.imported=null;
  try{this.state=room.state;if(!this.isConnected)return;this.settings=this.state.settings.tracking;this.render();await this.refresh();}catch(e){fail(e);}
 }
 update(s){this.state=s;if(this.settings)this.refresh().catch(fail);}
 disconnectedCallback(){this.stopFrames();clearInterval(this.samplePoll);this.generation++;clearInterval(this.poll);clearInterval(this.frames);speechSynthesis.cancel();if(this.guiding)api('/api/accuracy/session',{action:'stop'}).catch(()=>{});}
 q(s){return this.querySelector(s);}
 payload(extra={}){return {...(this.imported?{session:this.imported}:{}),...extra};}
 output(value){this.q('[data-result]').textContent=JSON.stringify(value,null,2);}
 render(){
  const seats=this.settings.zones,cameras=this.state.settings.cameras;
  const opts=seats.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
  this.innerHTML=`<div class="page-heading"><div><h1>Accuracy Lab</h1><p>Measure the same labelled speech with legacy and spherical tracking. Evaluation never moves cameras.</p></div></div>
  <section class="panel"><div class="panel-body"><h2>Create a seat from a 20-second sample</h2><p>Sit in the intended seat. Have one person speak naturally for 20 seconds; keep other voices and the far-end speaker quiet. Each microphone’s reported direction forms a preview for you to review. For a shared seat, all microphones must be connected and provide usable readings.</p><div class="form-grid"><label>Seat name<input data-sample-name maxlength="80" placeholder="e.g. Chairperson"></label><label>Microphones<select data-sample-mic>${(this.state.settings.microphones||[]).length?'<option value="__all__">All connected microphones — one shared seat</option>':''}<option value="primary">Primary microphone</option>${(this.state.settings.microphones||[]).map(m=>`<option value="${esc(m.id)}">${esc(m.id)}</option>`).join('')}</select></label></div><div class="actions"><button data-action="sample-start">Start 20-second sample</button><button data-action="sample-cancel">Discard sample</button><button data-action="sample-save" disabled>Save proposed seat</button></div><p data-sample-status role="status" aria-live="polite">Connect a microphone to begin.</p><p data-independent-audio class="hint"></p><progress data-sample-progress max="20" value="0" aria-label="Sample progress"></progress><div data-sample-preview hidden><div class="sample-radars-grid" data-sample-radars></div><p data-sample-warning class="hint" style="color:#ffb775;margin-top:10px;font-weight:500;"></p></div><p class="hint">This is a direction diagram (first microphone only), not a measured floor plan. A stable direction can still be noise or a reflection. Level is optional and does not identify a person. After saving, assign camera presets in Seats, then test again with different voices. A shared seat requires fresh agreement from all its sampled microphones before a new camera move. Independent speech detection is not available for every microphone in this flow; verify each sample with a separate room test. Spherical HMM uses fitted direction clusters; Legacy uses their centers and matching ranges.</p></div></section>
  <details class="panel"><summary>Advanced evaluation and diagnostics</summary>
  <section class="panel"><div class="panel-body"><h2>Echo guidance & live insight</h2><p>Route the far-end loudspeaker signal from your DSP to the microphone's Dante AEC reference input. Review exclusion zones at TV/loudspeaker positions. Mute the TV during ordinary tracking tests.</p><p><a href="https://help.sennheiser.com/hc/en-us/articles/29958524525586-TCC-2-TCC-M-Acoustic-Echo-Cancellation" target="_blank" rel="noopener">Sennheiser AEC guidance</a>. A 315Â° / 90Â° parked signature is configurable and must be confirmed on this device.</p><div class="form-grid"><label>Decision engine<select data-engine><option value="legacy">Legacy</option><option value="new">Spherical HMM</option></select></label><button data-action="engine">Use selected engine in dry-run</button></div><div class="form-grid"><label>First-lock posterior<input data-p-on type="number" min=".51" max=".99" step=".01"></label><label>Switch posterior<input data-switch-p type="number" min=".51" max=".99" step=".01"></label><label>Minimum evidence (ms)<input data-floor type="number" min="100" max="2000" step="50"></label><button data-action="latency-settings">Apply in dry-run</button><button data-action="latency-sweep">Guarded latency sweep</button><select data-latency-profile><option>Fast</option><option>Balanced</option><option>Safe</option></select><button data-action="latency-profile">Apply saved profile</button></div><label><input data-conversation type="checkbox">Conversation display: show two recently confirmed speakers on separate cameras; return to one after 6 seconds without another confirmed turn. This microphone reports one direction, not simultaneous speakers.</label><button data-action="conversation">Apply conversation display</button><h3>Last 20 seat locks (ms)</h3><p data-timing></p><p>Camera reply means acknowledgement; settle is measured separately by position readback when available. Dry-run has no camera timings.</p><p data-live role="status"></p><div data-bars></div><a href="#tuning">View spatial effects in Mic Tuning</a><p data-rejected></p></div></section>
  <section class="panel"><div class="panel-body"><h2>Record labelled trials</h2><p><strong>Label seats yourself:</strong> Choose Calibration and start a session. Select the seat where the person is sitting and a condition. Press Start trial, speak normally for 20 seconds, then press Stop trial. Repeat for every seat. For background noise or silence, select None / ambient and the matching condition. Press Stop &amp; keep session, then Export session. Repeat in a second calibration session, saving each export with a different name. Collect a separate Test session for accuracy. Label only the seat actually occupied during each trial.</p><p>Recording uses dry-run for ConfDeck tracking; camera control is not tested here. The fusion model remains uncalibrated until these sessions are reviewed and trained.</p><div class="form-grid"><label>Session purpose<select data-tag><option value="test">Test â€” scored only</option><option value="calibration">Calibration â€” never scored</option></select></label><label>Session tags<input data-tags placeholder="room, microphone, date"></label><label>Seat<select data-seat>${opts}<option value="">None / ambient</option></select></label><label>Condition<select data-condition>${conditions.map(x=>`<option>${x}</option>`).join('')}</select></label><label>Random seed<input data-seed type="number" value="7"></label></div><div class="actions"><button data-action="start">Start session</button><button data-action="trial-start">Start trial</button><button data-action="trial-stop">Stop trial</button><button data-action="guided">Run guided protocol</button><button data-action="calibration-guide">20+ seconds per seat</button><button data-action="stop">Stop & keep session</button><button data-action="export">Export session</button></div><p data-countdown role="status" aria-live="assertive">Self-paced: pick the actual speaker, then Start/Stop each trial. Guided trials include a spoken countdown.</p><p data-session></p><label>Import labelled JSON session<input data-import type="file" accept=".json"></label><p data-import-name></p><button data-action="clear-import">Use recorded session</button></div></section>
  <section class="panel"><div class="panel-body"><h2>Compare & calibrate</h2><div class="actions"><button data-action="compare">Compare legacy / new</button><button data-action="synthetic">Run synthetic regression demo</button><button data-action="sweep">Suggest tuning profile</button><button data-action="apply-sweep">Confirm suggested profile</button><button data-action="calibrate">Fit calibration & review</button><button data-action="apply-calibration">Apply fitted seats</button><button data-action="nadir">Verify under-mic elevation</button></div><label>Replay vision<select data-replay-vision><option value="off">Off</option><option value="advisory">Advisory (log only)</option><option value="active">Active evidence</option></select></label><p>Calibration: collect soft, normal and loud speech for at least 20 seconds per seat. For the nadir check, record and label a separate trial while standing directly under the microphone.</p><div data-reports></div><div data-comparison></div></div></section>
  <section class="panel"><div class="panel-body"><h2>Optional vision</h2><p>Off does no frame processing. Advisory records scores; Active applies calibrated soft evidence. Frames stay in memory and are never recorded. Vision runs while this page is open.</p><div class="form-grid"><label>Global mode<select data-vision><option value="off">Off</option><option value="advisory">Advisory</option><option value="active">Active</option></select></label><label>Observer camera<select data-camera>${cameras.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}</select></label><label>Camera mode<select data-camera-mode><option value="off">Off</option><option value="advisory">Advisory</option><option value="active">Active</option></select></label><label>Preset context (blank for fixed observer)<input data-preset></label><label>Head region for seat<select data-roi-seat>${opts}</select></label><label>Region type<select data-region><option value="head">Head ROI (seat zone)</option><option value="teaching">Teaching / Blackboard Zone</option><option value="mask">Screen mask</option><option value="erase">Click to Erase a Box</option></select></label></div><div class="actions"><button data-action="vision">Apply vision modes</button><button data-action="track-128" style="border-color:#10b981;color:#10b981;font-weight:600;">🎥 Real-time Fast AI Tracking (Cam 128)</button><button data-action="track-128-native" style="border-color:#6366f1;color:#6366f1;font-weight:600;">🤖 Native Hardware Tracking (Cam 128)</button><button data-action="clear-roi" style="border-color:#f87171;color:#f87171;font-weight:600;">Clear selected seat box</button><button data-action="clear-all-rois" style="border-color:#ef4444;color:#ef4444;font-weight:600;">Clear all boxes</button><button data-action="vision-calibrate">Fit speaking / silent evidence</button><button data-action="before">Capture before recall</button><button data-action="moved">Check after recall</button></div><div data-preview></div><canvas data-roi width="640" height="360" style="width:100%;max-width:760px;aspect-ratio:16/9;display:block;margin:12px 0;touch-action:none;border:2px solid #3b82f6;border-radius:8px;background:#0f172a;box-shadow:0 6px 16px rgba(0,0,0,0.2);cursor:crosshair" aria-label="Drag head ROI or screen mask"></canvas><p class="hint" style="margin-top:8px;line-height:1.5;"><strong>How to box a seat:</strong> Select your seat above, then drag a box enclosing your <strong>head, neck, and shoulders</strong>. The AI tracks your face and mouth <em>inside</em> this zone. 🟢 <strong>PRESENT</strong> = seated occupant. 🔴 <strong>SPEAKER</strong> = active speaking. ⚪ <strong>EMPTY</strong> = empty chair (vetoes camera jumps).</p><p data-vision-status></p></div></section>
  <details class="panel"><summary>Geometry, exclusion proposals & microphone A/B</summary><div class="panel-body"><label>Inference settings (includes convention and parked signature)<textarea data-inference rows="12"></textarea></label><button data-action="inference">Save inference settings</button><label>Known bearings: observed and expected arrays<textarea data-offset rows="3">{"observed":[10,190],"expected":[20,200]}</textarea></label><button data-action="offset">Estimate azimuth offset</button><button data-action="apply-offset">Apply suggested offset</button><button data-action="exclusions">Propose exclusions from noise / echo labels</button><label>Reviewed exclusion changes<textarea data-zones rows="5">{}</textarea></label><button data-action="apply-exclusions">Confirm device exclusions</button><label>Two or three device setting sets (JSON)<textarea data-ab rows="5">[{"beam":{"sourceDetectionThreshold":"NormalRoom"}},{"beam":{"sourceDetectionThreshold":"LoudRoom"}}]</textarea></label><div class="actions"><button data-action="ab">Confirm & start A/B</button><button data-action="ab-next">Finish this setting set</button><button data-action="ab-cancel">Cancel & revert A/B</button></div><p>Run the same guided protocol for each setting set. Finish each set to compare scores. The immediate pre-test settings are restored on completion, failure or cancellation.</p><label>Advanced vision settings (ROIs, masks, local mouth model, likelihoods)<textarea data-vision-json rows="10"></textarea></label><button data-action="vision-json">Apply reviewed vision settings</button></div></details><section class="panel"><div class="panel-body"><h2>Results</h2><pre data-result style="white-space:pre-wrap;max-height:500px;overflow:auto"></pre></div></section></details>`;
  this.q('[data-conversation]').checked=!!this.settings.conversation_layout;this.q('[data-engine]').value=this.settings.engine;this.q('[data-p-on]').value=this.settings.inference.p_on;this.q('[data-switch-p]').value=this.settings.inference.switch_p_on;this.q('[data-floor]').value=this.settings.inference.evidence_floor_ms;
  this.q('[data-inference]').value=JSON.stringify(this.settings.inference,null,2);
  this.q('[data-vision]').value=this.settings.vision.mode;
  this.q('[data-camera-mode]').value=this.settings.vision.camera_modes[this.q('[data-camera]').value]||this.settings.vision.mode;
  this.q('[data-vision-json]').value=JSON.stringify(this.settings.vision,null,2);
  this.querySelectorAll('[data-action]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await this.action(b.dataset.action);}catch(e){fail(e);}finally{if(b.dataset.action==="sample-start")this.refreshSample().catch(()=>{});else if(b.dataset.action!=="sample-save")b.disabled=false;}});
  api('/api/accuracy/vision/autotrack').then(st=>{
    const btnVision = this.q('[data-action="track-128"]');
    const btnNative = this.q('[data-action="track-128-native"]');
    if(btnVision){
      const act = st.enabled && st.mode === 'vision';
      btnVision.style.background = act ? '#059669' : '';
      btnVision.style.color = act ? '#ffffff' : '#10b981';
      btnVision.textContent = act ? '🎥 Fast AI Tracking: ACTIVE' : '🎥 Real-time Fast AI Tracking (Cam 128)';
    }
    if(btnNative){
      const act = st.enabled && st.mode === 'native';
      btnNative.style.background = act ? '#4f46e5' : '';
      btnNative.style.color = act ? '#ffffff' : '#818cf8';
      btnNative.textContent = act ? '🤖 Native Tracking: ACTIVE' : '🤖 Native Hardware Tracking (Cam 128)';
    }
  }).catch(()=>{});
  this.q('[data-import]').onchange=async e=>{const f=e.target.files[0];if(!f)return;try{if(f.size>4000000)throw Error('Session must be under 4 MB');this.imported=JSON.parse(await f.text());this.q('[data-import-name]').textContent=`Using imported session: ${f.name}`;}catch(error){fail(error);}};
  this.q('[data-camera]').onchange=()=>{this.q('[data-camera-mode]').value=this.settings.vision.camera_modes[this.q('[data-camera]').value]||this.settings.vision.mode;this.startFrames();};
  const canvas=this.q('[data-roi]');
  const point=e=>{const r=canvas.getBoundingClientRect();return [Math.max(0,Math.min(319,Math.floor((e.clientX-r.left)/r.width*320))),Math.max(0,Math.min(179,Math.floor((e.clientY-r.top)/r.height*180)))];};
  canvas.onpointerdown=e=>{this.drag=point(e);this.dragCurrent=this.drag;canvas.setPointerCapture(e.pointerId);};
  canvas.onpointermove=e=>{if(!this.drag)return;this.dragCurrent=point(e);this.drawOverlay(this.latestResult);};
  canvas.onpointerup=async e=>{
    if(!this.drag)return;
    const p=point(e),a=this.drag;this.drag=null;this.dragCurrent=null;
    const w=Math.abs(p[0]-a[0]),h=Math.abs(p[1]-a[1]);
    const camera=this.q('[data-camera]').value,preset=this.q('[data-preset]').value,seat=this.q('[data-roi-seat]').value;
    if(this.q('[data-region]').value==='erase'){
      let removed=false;const rois=[...this.settings.vision.rois];
      for(let i=rois.length-1;i>=0;i--){
        const r=rois[i];if(r.camera!==camera)continue;
        const [rx,ry,rw,rh]=r.rect;
        if(p[0]>=rx&&p[0]<=rx+rw&&p[1]>=ry&&p[1]<=ry+rh){rois.splice(i,1);removed=true;break;}
        for(let j=(r.masks||[]).length-1;j>=0;j--){
          const [mx,my,mw,mh]=r.masks[j];
          if(p[0]>=mx&&p[0]<=mx+mw&&p[1]>=my&&p[1]<=my+mh){r.masks.splice(j,1);removed=true;break;}
        }
        if(removed)break;
      }
      if(removed){
        const v={...this.settings.vision,rois};await this.save({vision:v});
        this.q('[data-vision-json]').value=JSON.stringify(this.settings.vision,null,2);
        this.drawOverlay(this.latestResult);
        this.q('[data-vision-status]').textContent='Box erased.';
      }
      return;
    }
    if(w<6&&h<6)return;
    const rect=[Math.min(p[0],a[0]),Math.min(p[1],a[1]),Math.max(1,w),Math.max(1,h)];
    const regionType=this.q('[data-region]').value;
    let roi=this.settings.vision.rois.find(r=>r.camera===camera&&(r.preset===preset||!r.preset||preset==='')&&r.seat===seat);
    if(regionType==='mask'){
      if(!roi)return fail(Error('Draw a head ROI first'));
      roi.masks.push(rect);
    }else{
      const kind=regionType==='teaching'?'teaching':'seat';
      if(roi){roi.rect=rect;roi.masks=[];roi.kind=kind;}
      else this.settings.vision.rois.push({camera,preset,seat,rect,masks:[],kind});
    }
    const v={...this.settings.vision};await this.save({vision:v});
    this.q('[data-vision-json]').value=JSON.stringify(this.settings.vision,null,2);
    this.drawOverlay(this.latestResult);
  };
  this.startFrames();
  this.refreshSample().then(()=>this.startSamplePoll()).catch(()=>{});
 }
 async save(profile){this.settings=await api('/api/accuracy/profile',{profile,confirm:true});}
 async action(action){
  const endpoint='/api/accuracy/';
  if(action==='sample-start'){await api(endpoint+'seat-sample',{action:'start',name:this.q('[data-sample-name]').value,mic_id:this.q('[data-sample-mic]').value});this.startSamplePoll();await this.refreshSample();}
  if(action==='sample-cancel'){await api(endpoint+'seat-sample',{action:'cancel'});clearInterval(this.samplePoll);await this.refreshSample();}
  if(action==='sample-save'){const result=await api(endpoint+'seat-sample',{action:'save'});this.settings.zones.push(result.saved);for(const selector of ['[data-seat]','[data-roi-seat]']){const o=document.createElement('option');o.value=result.saved.id;o.textContent=result.saved.name;this.q(selector).append(o);}await this.refreshSample();this.q('[data-sample-status]').textContent=`${result.saved.name} saved. Assign its camera presets in Seats, then test it with a fresh sample.`;}
  if(action==='conversation')await this.save({conversation_layout:this.q('[data-conversation]').checked});
  if(action==='latency-settings')await this.save({engine:'new',inference:{...this.settings.inference,p_on:Number(this.q('[data-p-on]').value),switch_p_on:Number(this.q('[data-switch-p]').value),evidence_floor_ms:Number(this.q('[data-floor]').value)}});
  if(action==='latency-sweep'){this.latency=await api(endpoint+'latency-sweep',this.payload());this.output(this.latency);}
  if(action==='latency-profile'){const profile=this.latency?.profiles?.[this.q('[data-latency-profile]').value]?.profile;if(!profile)throw Error('Run the guarded sweep first');await this.save(profile);}
  if(action==='engine')await this.save({engine:this.q('[data-engine]').value});
  if(action==='start'){this.imported=null;await api(endpoint+'session',{action:'start',tag:this.q('[data-tag]').value,tags:this.q('[data-tags]').value.split(',')});}
  if(action==='stop'){this.generation++;speechSynthesis.cancel();await api(endpoint+'session',{action:'stop'});}
  if(action==='trial-start')await api(endpoint+'trial',{action:'start',seat:this.q('[data-seat]').value||null,condition:this.q('[data-condition]').value});
  if(action==='trial-stop')await api(endpoint+'trial',{action:'stop'});
  if(action==='export')download('accuracy-session.json',await api(endpoint+'session'));
  if(action==='clear-import'){this.imported=null;this.q('[data-import-name]').textContent='Using the recorded session';}
  if(action==='guided'||action==='calibration-guide')return this.guide(action==='calibration-guide');
  if(action==='compare'||action==='synthetic'){const r=await api(endpoint+'compare',this.payload({synthetic:action==='synthetic',vision:this.q('[data-replay-vision]').value}));this.output({reports:r.reports,warnings:r.warnings||[]});this.comparison(r);}
  if(action==='sweep'){this.suggestion=await api(endpoint+'sweep',this.payload());this.output(this.suggestion);}
  if(action==='apply-sweep'){if(!this.suggestion?.suggested_profile)throw Error('Run a feasible sweep first');if(confirm('Apply the displayed suggested profile?'))await this.save(this.suggestion.suggested_profile);}
  if(action==='calibrate'||action==='apply-calibration'){if(action==='apply-calibration'&&!confirm('Apply the fitted calibration to the configured seats?'))return;this.output(await api(endpoint+'calibrate',this.payload({apply:action==='apply-calibration'})));}
  if(action==='nadir')this.output(await api(endpoint+'nadir',this.payload()));
  if(action==='inference'){await this.save({inference:JSON.parse(this.q('[data-inference]').value)});}
  if(action==='offset'){this.offset=await api(endpoint+'offset',JSON.parse(this.q('[data-offset]').value));this.output(this.offset);}
  if(action==='apply-offset'){if(this.offset&&confirm('Apply the displayed orientation offset?'))await this.save({inference:{...this.settings.inference,convention:{...this.settings.inference.convention,orientation_offset:this.offset.suggested_offset}}});}
  if(action==='exclusions'){const r=await api(endpoint+'exclusions',this.payload());this.output(r);this.q('[data-zones]').value=JSON.stringify(Object.fromEntries(r.proposals.map((p,i)=>[`exclusion_${i}`,p])),null,2);}
  if(action==='apply-exclusions'&&confirm('Apply the reviewed exclusions to the microphone? A snapshot and read-back are required.'))this.output(await api(endpoint+'exclusions',{apply:true,confirm:true,changes:JSON.parse(this.q('[data-zones]').value)}));
  if(action==='ab'&&confirm('Apply these microphone setting sets for A/B testing and restore afterward?'))this.output(await api(endpoint+'ab',{profiles:JSON.parse(this.q('[data-ab]').value),confirm:true}));
  if(action==='ab-next'||action==='ab-cancel')this.output(await api(endpoint+'ab',{action:action==='ab-next'?'next':'cancel'}));
  if(action==='clear-roi'){
    const camera=this.q('[data-camera]').value,preset=this.q('[data-preset]').value,seat=this.q('[data-roi-seat]').value;
    const v={...this.settings.vision,rois:this.settings.vision.rois.filter(r=>!(r.camera===camera&&(r.preset===preset||!r.preset||preset==='')&&r.seat===seat))};
    await this.save({vision:v});this.q('[data-vision-json]').value=JSON.stringify(this.settings.vision,null,2);
    this.drawOverlay(this.latestResult);this.q('[data-vision-status]').textContent='Box cleared for selected seat. Draw a new box around your head.';
  }
  if(action==='clear-all-rois'){
    const camera=this.q('[data-camera]').value;
    const v={...this.settings.vision,rois:this.settings.vision.rois.filter(r=>r.camera!==camera)};
    await this.save({vision:v});this.q('[data-vision-json]').value=JSON.stringify(this.settings.vision,null,2);
    this.drawOverlay(this.latestResult);this.q('[data-vision-status]').textContent='All boxes cleared. Draw a new box around your head/seating position.';
  }
  if(action==='vision'||action==='vision-json'){const v=action==='vision-json'?JSON.parse(this.q('[data-vision-json]').value):{...this.settings.vision,mode:this.q('[data-vision]').value,camera_modes:{...this.settings.vision.camera_modes,[this.q('[data-camera]').value]:this.q('[data-camera-mode]').value}};await this.save({vision:v});this.startFrames();}
  if(action==='vision-calibrate'){const result=await api(endpoint+'vision/calibrate',this.payload());this.output(result);if(confirm('Apply these displayed calibrated vision likelihoods?'))await this.save({vision:{...this.settings.vision,llr_models:result.llr_models}});}
  if(action==='before'){movementBefore=this.capture();this.q('[data-vision-status]').textContent='Before frame held in memory. Recall a preset in Camera Studio, then check after.';}
  if(action==='moved'){if(!movementBefore)throw Error('Capture a before frame first');this.output(await api(endpoint+'vision/moved',{before:movementBefore,after:this.capture()}));movementBefore=null;}
  if(action==='track-128'||action==='track-128-native'){
    try{
      const targetMode = action==='track-128' ? 'vision' : 'native';
      const curr = await api('/api/accuracy/vision/autotrack');
      const isCurrentlyActiveInThisMode = curr.enabled && curr.mode === targetMode;
      const newState = !isCurrentlyActiveInThisMode;
      const res = await api('/api/accuracy/vision/autotrack', {enabled: newState, camera: '0637f621814b', mode: targetMode});
      const btnVision = this.q('[data-action="track-128"]');
      const btnNative = this.q('[data-action="track-128-native"]');
      if(btnVision){
        const act = res.enabled && res.mode === 'vision';
        btnVision.style.background = act ? '#059669' : '';
        btnVision.style.color = act ? '#ffffff' : '#10b981';
        btnVision.textContent = act ? '🎥 Fast AI Tracking: ACTIVE' : '🎥 Real-time Fast AI Tracking (Cam 128)';
      }
      if(btnNative){
        const act = res.enabled && res.mode === 'native';
        btnNative.style.background = act ? '#4f46e5' : '';
        btnNative.style.color = act ? '#ffffff' : '#818cf8';
        btnNative.textContent = act ? '🤖 Native Tracking: ACTIVE' : '🤖 Native Hardware Tracking (Cam 128)';
      }
      this.q('[data-vision-status]').textContent = res.enabled 
        ? (res.mode === 'vision' ? 'Fast AI face tracking active on Camera 128 (VISCA dynamic servo).' : 'Camera 128 native hardware tracking active.')
        : 'Camera tracking deactivated.';
    }catch(err){fail(err);}
    return;
  }
  await this.refresh();
 }
 startSamplePoll(){clearInterval(this.samplePoll);this.samplePoll=setInterval(()=>this.refreshSample().catch(e=>{clearInterval(this.samplePoll);fail(e);}),500);}
 async refreshSample(){
  const sample=await api('/api/accuracy/seat-sample');if(!this.isConnected)return;
  const state=sample.status,ready=state==='ready',running=state==='recording';
  this.q('[data-action="sample-start"]').disabled=running;this.q('[data-action="sample-save"]').disabled=!ready;
  this.q('[data-sample-progress]').value=sample.elapsed||0;this.q('[data-sample-preview]').hidden=!ready;
  this.q('[data-sample-status]').textContent=running?`Listening for direction readings: ${Math.ceil(20-sample.elapsed)} seconds remaining. Keep speaking from the seat.`:state==='retry'?sample.message:ready?`Preview ready for ${sample.name}. Compare radar direction from both microphones before saving.`:'Connect a microphone to begin.';
  if(ready){
    const z=sample.proposal;
    const sources=[{id:z.mic_id,profile:z,primary:true}];
    if(z.microphone_profiles){
      for(const [mid,prof] of Object.entries(z.microphone_profiles)){
        sources.push({id:mid,profile:prof,primary:false});
      }
    }
    const colors=['#35a9ff','#58e09a','#f58220','#d680ff'];
    const container=this.q('[data-sample-radars]');
    container.innerHTML=sources.map((src,idx)=>{
      const p=src.profile;
      const q=(sample.per_microphone&&sample.per_microphone[src.id])||(src.primary?sample.quality:{});
      const a=p.azimuth*Math.PI/180;
      const r=25+(p.elevation/90)*95;
      const cx=(150+Math.sin(a)*r).toFixed(1);
      const cy=(150-Math.cos(a)*r).toFixed(1);
      const color=colors[idx%colors.length];
      const micLabel=src.primary?`Primary Mic (${src.id})`:`Secondary Mic (${src.id})`;
      return `<div class="sample-radar-card">
        <div class="sample-radar-title"><span>${esc(micLabel)}</span><span class="sample-radar-badge" style="color:${color};font-weight:600;">Az ${p.azimuth.toFixed(1)}° / El ${p.elevation.toFixed(1)}°</span></div>
        <svg viewBox="0 0 300 300" role="img" aria-label="${esc(src.id)} direction preview">
          <circle cx="150" cy="150" r="120" fill="none" stroke="currentColor" opacity=".25"/>
          <circle cx="150" cy="150" r="65" fill="none" stroke="currentColor" opacity=".18"/>
          <circle cx="150" cy="150" r="25" fill="none" stroke="currentColor" opacity=".12"/>
          <path d="M150 20V280 M20 150H280" stroke="currentColor" opacity=".2"/>
          <text x="150" y="15" text-anchor="middle" fill="currentColor" opacity=".6" font-size="11">0°</text>
          <text x="285" y="154" text-anchor="end" fill="currentColor" opacity=".6" font-size="11">90°</text>
          <text x="150" y="295" text-anchor="middle" fill="currentColor" opacity=".6" font-size="11">180°</text>
          <text x="15" y="154" text-anchor="start" fill="currentColor" opacity=".6" font-size="11">270°</text>
          <line x1="150" y1="150" x2="${cx}" y2="${cy}" stroke="${color}" stroke-width="2" stroke-dasharray="4 3" opacity=".7"/>
          <circle cx="${cx}" cy="${cy}" r="14" fill="${color}" opacity=".25"/>
          <circle cx="${cx}" cy="${cy}" r="8" fill="${color}"/>
        </svg>
        <div class="sample-radar-stats">
          <div><strong>Readings:</strong> ${q.readings||0} in ${q.seconds_with_readings||0}/20s</div>
          <div><strong>Direction consistency:</strong> ${Math.round((q.direction_support||0)*100)}% within 25° (spread ${q.spread_90_degrees||0}°)</div>
          <div><strong>Level:</strong> ${q.median_level==null?'unavailable':q.median_level+' dB'}</div>
        </div>
      </div>`;
    }).join('');
    this.q('[data-sample-warning]').textContent=(sample.warnings||[]).join(' ');
  }
  if(!running)clearInterval(this.samplePoll);
 }
 async guide(calibration){
  if(this.guiding)throw Error('A guided protocol is already running');
  const token=++this.generation;this.guiding=true;
  try{
   if(this.status?.recording&&this.status.tag!==(calibration?'calibration':'test'))throw Error('Stop and export the current session, then start one with the correct purpose');
   this.imported=null;
   if(!this.status?.recording)await api('/api/accuracy/session',{action:'start',tag:calibration?'calibration':'test',tags:['guided']});
   const trials=calibration?this.settings.zones.flatMap(s=>['normal','soft','loud'].map(condition=>({seat:s.id,condition,start:0,end:8}))):await api('/api/accuracy/protocol?seed='+Number(this.q('[data-seed]').value));
   for(const trial of trials){
    if(token!==this.generation||!this.isConnected)break;
    const seat=this.settings.zones.find(z=>z.id===trial.seat)?.name||'No speaker';
    const instruction=`${seat}: ${trial.condition.replace('_',' ')}${trial.condition==='overlap'?' â€” two people speak together':''}`;
    this.q('[data-countdown]').textContent=instruction;
    speechSynthesis.speak(new SpeechSynthesisUtterance(instruction));
    for(let n=trial.countdown??3;n>0;n--){if(token!==this.generation)break;this.q('[data-countdown]').textContent=`${instruction} â€” starts in ${n}`;await pause(1000);}
    if(token!==this.generation)break;
    await api('/api/accuracy/trial',{action:'start',seat:quiet.has(trial.condition)?null:trial.seat,condition:trial.condition});
    const deadline=performance.now()+(trial.end-trial.start)*1000;
    while(performance.now()<deadline&&token===this.generation){this.q('[data-countdown]').textContent=`${instruction} â€” ${Math.ceil((deadline-performance.now())/1000)} seconds`;await pause(200);}
    if(token!==this.generation)break;
    await api('/api/accuracy/trial',{action:'stop'});
   }
   if(token===this.generation){this.q('[data-countdown]').textContent='Protocol complete. Stop and export, or finish this A/B setting set.';if(!this.status?.ab?.running)await api('/api/accuracy/session',{action:'stop'});}
  }finally{this.guiding=false;}
 }
 async refresh(){if(!this.isConnected)return;const s={...room.state.accuracy,tracking:room.state.tracking};if(!this.isConnected)return;this.status=s;const t=s.tracking;this.q('[data-timing]').textContent=Object.entries(t.timing?.summary||{}).map(([key,v])=>`${key}: ${v?`median ${v.median.toFixed(0)}, worst ${v.worst.toFixed(0)}`:'unavailable'}`).join(' · ');
  this.q('[data-live]').textContent=`${t.current||'None'} Â· ${t.reason} Â· noise floor ${t.noise_floor??'legacy: unavailable'} dB Â· confidence ${Math.round((t.confidence||0)*100)}%`;
  const audio=room.state.independent_audio||{};this.q('[data-independent-audio]').textContent=audio.enabled?(audio.error||`Independent audio: ${Object.entries(audio.sources||{}).map(([id,status])=>`${id}: ${status}`).join(' · ')||'waiting for inputs'}`):'Shared-seat Follow requires Spherical HMM and configured, verified independent audio for every microphone.';
  this.q('[data-session]').textContent=`${s.recording?'Recording':'Stopped'} Â· ${s.elapsed.toFixed(1)}s Â· dropped ${s.dropped}${s.error?' Â· '+s.error:''}${s.ab.running?' Â· A/B set '+s.ab.index:''}`;
  this.q('[data-bars]').innerHTML=Object.entries(t.posteriors||{}).map(([seat,p])=>`<label>${esc(seat)} <meter min="0" max="1" value="${p}"></meter> ${(p*100).toFixed(1)}%</label>`).join(' ');
  this.q('[data-rejected]').textContent=Object.entries(t.rejected||{}).map(([reason,n])=>`${reason}: ${n}`).join(' Â· ');
  if(s.vision_warning)this.q('[data-vision-status]').textContent=s.vision_warning;
 }
 comparison(r){
  const keys=['correct_rate','wrong_rate','no_decision_rate','opposite_rate','switch_latency_median','spurious_switches_per_min','echo_false_trigger_rate'];
  const fmt=v=>v==null?'N/A':v.toFixed(4);
  this.q('[data-comparison]').innerHTML='<table><tr><th>Metric</th><th>Legacy</th><th>New</th><th>Delta</th></tr>'+keys.map(k=>`<tr><td>${k}</td><td>${fmt(r.reports.legacy[k])}</td><td>${fmt(r.reports.new[k])}</td><td>${fmt(r.new_minus_legacy[k])}</td></tr>`).join('')+'</table>';
  this.q('[data-reports]').innerHTML=['legacy','new'].map(e=>`${e}: `+['html','json','csv'].map(f=>`<a href="/api/accuracy/report/${e}/${f}" target="_blank">${f.toUpperCase()}</a>`).join(' Â· ')).join('<br>');
 }
 stopFrames(){
  this.visionRunning=false;
  if(this.frames){clearInterval(this.frames);this.frames=null;}
  if(this.renderRaf){cancelAnimationFrame(this.renderRaf);this.renderRaf=null;}
 }
 startRendering(){
  if(this.renderRaf)cancelAnimationFrame(this.renderRaf);
  const canvas=this.q('[data-roi]');
  const ctx=canvas?.getContext('2d');
  const loop=()=>{
    if(!this.isConnected||!this.visionRunning)return;
    const video=this.q('video');
    if(video&&video.readyState>=2&&ctx){
      ctx.drawImage(video,0,0,640,360);
      this.drawOverlay(this.latestResult);
    }
    this.renderRaf=requestAnimationFrame(loop);
  };
  this.renderRaf=requestAnimationFrame(loop);
 }
 startFrames(){
  this.stopFrames();
  const camera=this.q('[data-camera]').value,v=this.settings.vision;
  if(!camera||v.mode==='off'||v.camera_modes[camera]==='off'){this.q('[data-preview]').replaceChildren();return;}
  this.visionRunning=true;
  syncPlayers(this.q('[data-preview]'),this.state.settings.cameras.filter(c=>c.id===camera));
  this.startRendering();
  this.frames=setInterval(async()=>{
    if(this.busy||document.hidden||!this.isConnected||!this.visionRunning)return;
    const video=this.q('video');
    if(!video||video.readyState<2)return;
    this.busy=true;
    try{
      const pixels=this.capturePixels(video);
      const result=await api('/api/accuracy/vision/frame',{camera,preset:this.q('[data-preset]').value,pixels,quiet:!!this.status?.trial&&this.status.trial.seat===null});
      this.latestResult=result;
      if(result.mode==='off'&&this.settings.vision.mode==='off'){this.stopFrames();}
    }catch(e){
      this.q('[data-vision-status]').textContent='Vision: '+e.message;
    }finally{
      this.busy=false;
    }
  },60);
 }
 drawOverlay(res){
  const canvas=this.q('[data-roi]');if(!canvas)return;
  const ctx=canvas.getContext('2d');
  const camera=this.q('[data-camera]').value,preset=this.q('[data-preset]').value;
  const rois=this.settings.vision.rois.filter(r=>r.camera===camera&&(r.preset===preset||!r.preset||preset===''));
  const badges=[];

  if(this.drag && this.dragCurrent){
    const x1=Math.min(this.drag[0],this.dragCurrent[0])*2;
    const y1=Math.min(this.drag[1],this.dragCurrent[1])*2;
    const w=Math.abs(this.drag[0]-this.dragCurrent[0])*2;
    const h=Math.abs(this.drag[1]-this.dragCurrent[1])*2;
    ctx.strokeStyle='#facc15';ctx.lineWidth=3;ctx.setLineDash([6,4]);
    ctx.strokeRect(x1,y1,w,h);
    ctx.fillStyle='rgba(250,204,21,0.18)';ctx.fillRect(x1,y1,w,h);
    ctx.setLineDash([]);
    ctx.fillStyle='#facc15';ctx.font='bold 11px monospace';
    ctx.fillText('DRAWING ZONE ('+Math.round(w/2)+'x'+Math.round(h/2)+')',x1+6,Math.max(14,y1-6));
  }

  if(res?.unassigned_faces){
    ctx.lineWidth=2;ctx.strokeStyle='#06b6d4';ctx.setLineDash([4,3]);
    for(const f of res.unassigned_faces){
      const fx=f[0]*2,fy=f[1]*2,fw=f[2]*2,fh=f[3]*2;
      ctx.strokeRect(fx,fy,fw,fh);
      ctx.fillStyle='#06b6d4';
      const tagY=Math.max(0,fy-18);
      ctx.fillRect(fx,tagY,110,18);
      ctx.fillStyle='#ffffff';ctx.font='bold 9px monospace';
      ctx.fillText('FACE DETECTED',fx+4,tagY+12);
    }
    ctx.setLineDash([]);
  }

  for(const r of rois){
    const seat=r.seat;
    const seatName=this.settings.zones?.find(z=>z.id===seat)?.name||seat.replace('seat_','Seat ');
    const isSpeaking=res?.speaking?.[seat]||false;
    const isOccupied=res?.occupancy?.[seat]||false;
    const faceBox=res?.faces?.[seat];
    const energy=res?.motion?.[seat]??0.0;
    const drawRect=(r.kind==='teaching'&&res?.tracking?.locked_roi)?res.tracking.locked_roi:r.rect;
    const rx=drawRect[0]*2,ry=drawRect[1]*2,rw=drawRect[2]*2,rh=drawRect[3]*2;
    if(isSpeaking){
      ctx.strokeStyle='#ef4444';ctx.lineWidth=4;
      ctx.fillStyle='rgba(239,68,68,0.22)';
      ctx.fillRect(rx,ry,rw,rh);ctx.strokeRect(rx,ry,rw,rh);
      ctx.fillStyle='#ef4444';
      const tagY=Math.max(0,ry-22);
      const tagText=seatName+' · SPEAKER ('+energy.toFixed(1)+')';
      const tagW=Math.max(120,ctx.measureText(tagText).width+16);
      ctx.fillRect(rx,tagY,tagW,22);
      ctx.fillStyle='#ffffff';ctx.font='bold 11px monospace';
      ctx.fillText(tagText,rx+6,tagY+15);
      badges.push('🔴 '+seatName+': SPEAKER ('+energy.toFixed(1)+')');
    }else if(isOccupied){
      ctx.strokeStyle='#10b981';ctx.lineWidth=2.5;
      ctx.fillStyle='rgba(16,185,129,0.08)';
      ctx.fillRect(rx,ry,rw,rh);ctx.strokeRect(rx,ry,rw,rh);
      ctx.fillStyle='#10b981';
      const tagY=Math.max(0,ry-22);
      const tagText=seatName+' · PRESENT ('+energy.toFixed(1)+')';
      const tagW=Math.max(110,ctx.measureText(tagText).width+16);
      ctx.fillRect(rx,tagY,tagW,22);
      ctx.fillStyle='#ffffff';ctx.font='bold 11px monospace';
      ctx.fillText(tagText,rx+6,tagY+15);
      badges.push('🟢 '+seatName+': Present (Quiet · '+energy.toFixed(1)+')');
    }else{
      ctx.strokeStyle='#94a3b8';ctx.lineWidth=1.5;
      ctx.setLineDash([5,5]);ctx.strokeRect(rx,ry,rw,rh);ctx.setLineDash([]);
      ctx.fillStyle='rgba(100,116,139,0.85)';
      const tagY=Math.max(0,ry-22);
      const tagText=seatName+' · EMPTY';
      const tagW=Math.max(90,ctx.measureText(tagText).width+16);
      ctx.fillRect(rx,tagY,tagW,22);
      ctx.fillStyle='#ffffff';ctx.font='bold 11px monospace';
      ctx.fillText(tagText,rx+6,tagY+15);
      badges.push('⚪ '+seatName+': EMPTY (VETO)');
    }
    if(faceBox){
      ctx.lineWidth=isSpeaking?3:2;
      ctx.strokeStyle=isSpeaking?'#ef4444':'#10b981';
      ctx.strokeRect(faceBox[0]*2,faceBox[1]*2,faceBox[2]*2,faceBox[3]*2);
    }
    ctx.strokeStyle='#f43f5e';ctx.lineWidth=1.5;
    for(const m of r.masks||[])ctx.strokeRect(m[0]*2,m[1]*2,m[2]*2,m[3]*2);
  }
  if(badges.length)this.q('[data-vision-status]').textContent=badges.join(' · ');
  else if(res?.warning)this.q('[data-vision-status]').textContent=res.warning;
 }
 capturePixels(video){
  if(!this.offscreenCanvas){
    this.offscreenCanvas=document.createElement('canvas');
    this.offscreenCanvas.width=320;
    this.offscreenCanvas.height=180;
  }
  const octx=this.offscreenCanvas.getContext('2d',{willReadFrequently:true});
  octx.drawImage(video,0,0,320,180);
  const d=octx.getImageData(0,0,320,180).data;
  const gray=new Uint8Array(320*180);
  for(let i=0,j=0;i<d.length;i+=4,j++)gray[j]=(d[i]*77+d[i+1]*150+d[i+2]*29)>>8;
  let binary='';
  for(let i=0;i<gray.length;i+=8192)binary+=String.fromCharCode.apply(null,gray.subarray(i,i+8192));
  return btoa(binary);
 }
}
customElements.define('accuracy-lab',AccuracyLab);
