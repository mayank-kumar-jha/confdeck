import {room} from "./common.js";
import { retainNavigationPlayers, finishNavigationPlayers, cameraIsPlaying } from './player.js';
import './studio.js';
import './smart-preview.js';
import {
  $,
  $$,
  esc,
  icon,
  api,
  toast,
  fail,
  badge,
  statusClass,
  bindTooltips,
  liveSocket,
} from "./common.js";
import { syncPlayers, syncOutput } from "./player.js";
import "./tune.js";
import "./accuracy-lab.js";

let state,
  page = "home",
  socketClose,
  lastScan = "";
let presetCamera, presetData, pendingPreset;
const nav = [["home","Overview","home"],["cameras","Cameras","camera"],["display","Display","display"],["seats","Seats and Mic","mic"],["tuning","Mic Tuning","scan"],["advanced","Advanced","settings"]];
const button = (text, act, cls = "", ico = "", extra = "") =>
  `<button class="${cls}" data-act="${act}" ${extra}>${ico ? icon(ico) : ""}${text}</button>`;
const heading = (title, description, actions = "") =>
  `<div class="page-heading"><div><h1>${title}</h1><p>${description}</p></div><div class="actions">${actions}</div></div>`;
const panel = (title, content, extra = "", cls = "") =>
  `<section class="panel ${cls}"><div class="panel-header"><h2>${title}</h2>${extra}</div><div class="panel-body">${content}</div></section>`;
const textField = (label, name, value = "", type = "text", placeholder = "") =>
  `<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" placeholder="${esc(placeholder)}" ${type === "password" ? 'autocomplete="new-password"' : 'autocomplete="off"'}></label>`;
const checked = (value) => (value ? "checked" : "");
const selected = (a, b) => (a === b ? "selected" : "");
const switchControl = (name, on) =>
  `<label class="switch"><input type="checkbox" name="${name}" ${checked(on)} aria-label="${name}"></label>`;
const radarStats = (large = false) =>
  `<div class="radar-stats"><div><small>AZIMUTH</small><strong class="orange" data-live="azimuth">—</strong></div><div><small>ELEVATION</small><strong data-live="elevation">—</strong></div>${large ? '<div><small>MESSAGE RATE</small><strong data-live="rate">—</strong></div>' : ""}</div>`;
const issueHTML = (issue) =>
  issue
    ? `<div class="note warning"><h3>${esc(issue.title)}</h3><p>${esc(issue.cause)}</p><p>${esc(issue.fix)}</p>${issue.detail ? `<small>${esc(issue.detail)}</small>` : ""}</div>`
    : "";
function dialog(title, content) {
  const d = $("#dialog");
  d.innerHTML = `<div class="dialog-head"><h2>${title}</h2>${button("", "close-dialog", "ghost", "close", 'aria-label="Close dialog"')}</div>${content}`;
  d.showModal();
  bindTooltips(d);
}
async function boot() {
  try {
    state = room.state = await api("/api/state");
    if (!state.tracking_profiles) {
      $("#app").innerHTML='<main class="login-card"><h1>Restart ConfDeck to load this update</h1><p>The interface has been updated, but the backend is still the previous version. Use the button below to stop the running backend, reopen Start ConfDeck.cmd, then reload this page.</p><button id="restart-old-backend" class="danger">Stop running backend</button></main>';
      $("#restart-old-backend").onclick=async()=>{if(!confirm("Stop ConfDeck? Camera previews and tracking will stop until you reopen Start ConfDeck.cmd."))return;try{await api('/api/system/stop',{});$("#restart-old-backend").disabled=true;$("#restart-old-backend").textContent='Stopped. Reopen Start ConfDeck.cmd.';}catch(e){fail(e);}};
      return;
    }
    await import('./mic-tuning.js');
    shell();
    showMicNotice();
  } catch (error) {
    $("#app").innerHTML = '<main class="login-card"><h1>Waiting for ConfDeck</h1><p>Start the local launcher, then reload this page.</p></main>';
    setTimeout(boot, 4000);
  }
}
function shell() {
  if (localStorage.getItem("confdeck-theme") !== "dark")
    document.body.classList.add("light");
  $("#app").innerHTML =
    `<div class="app-layout"><aside class="sidebar"><a class="logo" href="#home" aria-label="Voice Tracker Pro overview"><img src="/static/ordain-logo-light.png" alt="ORDAIN · AV Connect Forever"></a><div class="product-name">Voice Tracker <b>PRO</b></div><div class="eyebrow nav-label">WORKSPACE</div><nav class="nav" aria-label="Main navigation">${nav.map(([id, name, ico]) => `<a href="#${id}" data-nav="${id}">${icon(ico)}${name}</a>`).join("")}</nav><div class="sidebar-bottom"><div class="offline">${icon("lock")} Local room control</div><small>Runs locally · No cloud required</small><small>Voice Tracker Pro · ORDAIN</small></div></aside><div class="workspace"><header class="topbar"><div class="room-chip"><span class="room-dot"></span><span data-live="room">Meeting room</span></div><div class="statusbar">${["cameras", "mic", "display"].map((k) => `<span class="status-indicator" title="${k} connection"><span class="dot" data-dot="${k}"></span>${k === "mic" ? "Mic" : k[0].toUpperCase() + k.slice(1)}<span class="status-word" data-status="${k}"></span></span>`).join("")}</div><div class="actions">${state.demo ? '<span class="badge warn">MIC SIM</span>' : ""}<button class="danger" id="global-follow-stop" data-act="tracking-stop" hidden>Stop following</button>${button("", "theme", "icon-button", "sun", 'aria-label="Toggle light theme"')}</div></header><div id="connection-banner" class="banner" role="status" hidden>Connection to the room controller was interrupted. Reconnecting automatically…</div><main id="content" class="main"></main></div></div>`;
  route();
  socketClose?.();
  socketClose = liveSocket(
    "/api/ws",
    (snapshot) => {
      state = room.state = snapshot;
      update();
    },
    (connected) => {
      $("#connection-banner").hidden = connected;
    },
  );
  if (!state.settings.wizard_done && !location.hash) {
    location.hash = "seats";
  }
  bindTooltips();
}
room.onMicTeamSelect=()=>route();
function route() {
  if (!state) return;
  page = location.hash.slice(1) || "home";
  if(page!=="seats"&&room.micId==="new")room.micId="primary";
  if (!nav.some(n => n[0] === page)) page = "home";
  $$("[data-nav]").forEach((a) => {
    a.classList.toggle("active", a.dataset.nav === page);
    if (a.dataset.nav === page) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const render = {
    home: homePage, cameras: camerasPage, display: displayPage,
    seats: () => heading("Seats and Mic", "Connect your microphone. Map each seat to a saved camera scene.") + (room.micId==='new'?'':'<mic-team></mic-team>') + panel("Microphone connection",micForm()) + (room.micId==='new'?'':'<seat-radar mode="seats"></seat-radar>') + '<div id="mic-issue"></div>',
    tuning: () => heading("Mic Tuning", "Tune seat locking, preview recent activity, then apply your settings.") + '<mic-tuning></mic-tuning>',
    advanced: () => heading("Advanced", "Inspect tracking decisions, evaluate accuracy and manage system settings.") + '<details><summary>Tracking filter and camera control</summary><tune-follow></tune-follow></details><details><summary>Camera handoff timing</summary><pre id="handoff-timing"></pre></details><details><summary>Accuracy, recorder, replay and exports</summary><accuracy-lab></accuracy-lab></details>' + settingsPanel() + healthPanel(),
  }[page];
  retainNavigationPlayers($("#content"));
  $("#content").innerHTML = render();
  lastScan = "";
  bindTooltips($("#content"));
  update();
  finishNavigationPlayers();
  if (page === "home" || page === "display") loadMonitors();
}
function homePage() {
 return heading("Overview", "Monitor cameras, follow the speaker and manage your room display.",button("Connect TV","connect-tv","primary","display")+button("Follow on / off","toggle-follow")+button("Stop","tracking-stop","danger")) +
 '<div id="overview-status" class="actions" role="status"></div><div class="home-grid"><section class="panel"><div class="panel-header"><h2>Camera previews</h2></div><div id="home-cameras" class="camera-grid"></div></section><seat-radar mode="preview"></seat-radar></div><section class="panel"><div class="panel-header"><h2>Display output</h2></div><div id="program-preview" class="display-grid"></div></section>' + panel("Display layout", displayControls()) + '<details><summary>Connect and manage displays</summary>'+displayConnectionPanel()+'</details>';
}
function displayPage() {
  return heading("Display", "See the live room output and configure smart following.", button("Connect TV", "connect-tv", "primary", "display")) +
    '<section class="panel"><div class="panel-header"><h2>Live smart preview</h2><span id="display-follow-status" class="hint"></span></div><div id="program-preview" class="display-grid"></div></section><smart-preview></smart-preview>' +
    panel("Display layout", displayControls()) + '<details><summary>Connect and manage displays</summary>'+displayConnectionPanel()+'</details>';
}
function camerasPage() {
  return (
    heading(
      "Cameras",
      "Connect cameras, frame your shots and save presets for each seat.",
      button("Scan network", "scan-dialog", "ghost", "scan") +
        button("Add by IP", "add-camera", "primary", "plus"),
    ) +
    `<div class="note">${icon("info")} Camera previews use your existing video settings. H.264 is required for reliable browser playback.</div><camera-studio></camera-studio><div id="camera-list" class="camera-list"></div>`
  );
}
async function openPresets(camera) {
  presetCamera = camera;
  presetData = null;
  dialog(`Presets · ${esc(camera.name)}`, `<form id="preset-settings"><div class="form-grid"><label>Control protocol<select name="control_protocol">${[["none", "Choose a protocol"], ["canon", "Canon XC (CR-N100)"], ["onvif", "ONVIF PTZ"], ["visca", "VISCA over IP (AVer / Sony)"]].map(([v, label]) => `<option value="${v}" ${selected(v, camera.control_protocol || "none")}>${label}</option>`).join("")}</select></label>${textField("ONVIF / Canon HTTP port", "control_port", camera.control_port || 80, "number")}${textField("VISCA UDP port", "visca_port", camera.visca_port || 52381, "number")}${textField("ONVIF profile token (optional)", "control_profile", camera.control_profile || "")}</div><p class="hint">ONVIF and Canon use the credentials saved in Edit camera. VISCA uses its UDP port without a username or password. Connect checks control without moving the camera. ONVIF must be enabled on the device.</p><div class="form-actions"><button class="primary" type="submit">Connect / refresh presets</button></div></form><div id="preset-results"></div>`);
  if (camera.control_protocol && camera.control_protocol !== "none") await loadPresets();
}
async function loadPresets() {
  const root = $("#preset-results");
  if (!root) return;
  root.innerHTML = '<p class="hint">Reading camera presets…</p>';
  try {
    presetData = await api(`/api/cameras/${presetCamera.id}/presets`);
    if (presetData.protocol === "visca") {
      root.innerHTML = `<div class="row"><h3>VISCA control connected</h3>${button("Stop movement", "preset-stop", "danger")}</div><p class="hint">${esc(presetData.probe || "Read-only inquiry replied")}. Enter a preset number already saved on the camera. App records track saved slots. If presets behave oddly, disable VISCA Customized Function in camera settings.</p><label>Preset number (0–127)<input id="visca-preset" required type="number" min="0" max="127" step="1" value="1"></label><div class="form-actions">${button("Recall preset", "preset-recall", "primary")}</div><h3>1 Frame it / 2 Save it / 3 Test it</h3><p>Frame with your camera controller, save here, then recall to verify.</p>${presetData.presets.map(p => `<p>Slot ${esc(p.token)}: ${esc(p.name)} (Saved)</p>`).join("")}<form id="preset-save"><label>Reserved slot<input name="token" type="number" min="${presetCamera.preset_start ?? 21}" max="${presetCamera.preset_end ?? 29}" value="${presetCamera.preset_start ?? 21}" required></label><label>Name<input name="name" maxlength="80" required></label><button type="submit" class="primary">Save current framing here</button></form>`;
      return;
    }
    const canon = presetData.protocol === "canon";
    root.innerHTML = `<div class="row"><h3>Saved on camera</h3>${button("Stop movement", "preset-stop", "danger", "", `data-id="${presetCamera.id}"`)}</div>${canon ? '<label>Recall speed (1–100; blank uses camera setting)<input id="preset-speed" type="number" min="1" max="100" placeholder="Camera setting"></label>' : ""}<div class="preset-list">${presetData.presets.map(p => `<div class="row"><div><strong>${esc(p.name)}</strong><p class="hint">Preset ${esc(p.token)}</p></div><div class="actions">${button("Recall", "preset-recall", "primary", "", `data-token="${esc(p.token)}"`)}${button("Use as scene", "preset-link", "ghost", "", `data-token="${esc(p.token)}"`)}${button("Update", "preset-edit", "ghost", "", `data-token="${esc(p.token)}"`)}${button("Delete", "preset-delete", "ghost", "", `data-token="${esc(p.token)}"`)}</div></div>`).join("") || '<p class="hint">No presets saved on this camera yet.</p>'}</div><h3>Save current position</h3><form id="preset-save"><div class="form-grid"><label>${canon ? "Preset number (1–100)" : "Preset token (blank creates a new preset)"}<input name="token" ${canon ? 'type="number" min="1" max="100" required' : 'maxlength="128"'}></label><label>Preset name<input name="name" maxlength="80" required></label></div><p class="hint">Position the camera with its controller first. Saving an existing preset replaces its position and name. Canon saves pan, tilt, zoom and focus; other existing preset groups are retained.</p><div class="form-actions"><button class="primary" type="submit">Save current position</button></div></form>`;
  } catch (error) {
    root.innerHTML = `<div class="note warning"><h3>Preset control unavailable</h3><p>${esc(error.detail?.detail || error.message)}</p><p>${presetCamera.control_protocol === "visca" ? "Check the VISCA UDP port, camera power and firewall. VISCA does not use camera login credentials." : "Check the ONVIF / Canon HTTP port and saved camera credentials. For cameras with VISCA enabled, choose VISCA over IP and UDP port 52381."}</p></div>`;
  }
}
function cameraForm(camera) {
  return `<form id="camera-form" data-id="${esc(camera?.id || "")}"><div class="form-grid">${textField("Camera name", "name", camera?.name || "Meeting room camera")}${textField("Camera IP address", "ip", camera?.ip || "", "text", "192.168.1.20")}${textField("Video port", "port", camera?.port || 554, "number")}${textField("RTSP path", "path", camera?.path || "/rtpstream/config1")}${textField("Username", "username", "", "text", camera ? "Leave blank to keep saved" : "Camera username")}${textField("Password", "password", "", "password", camera ? "Leave blank to keep saved" : "Camera password")}</div><p class="hint">Canon CR-N100 usually uses /rtpstream/config1, config2 or config3. Credentials are saved only in the protected local secrets file.</p><div class="form-actions">${button("Cancel", "close-dialog", "ghost")}<button class="primary" type="submit">${camera ? "Save camera" : "Connect camera"}</button></div></form>`;
}
function scanForm() {
  return `<form id="scan-form"><div class="form-grid"><label>Camera network<select name="subnet">${state.network.map((n) => `<option value="${esc(n.subnet)}">${esc(n.subnet)} · ${esc(n.interface)}</option>`).join("") || '<option value="192.168.1.0/24">192.168.1.0/24</option>'}</select></label>${textField("Or enter a private /24", "custom_subnet", "", "text", "192.168.10.0/24")}${textField("Camera username", "username", "", "text", "Used for this scan")}${textField("Camera password", "password", "", "password", "Used for this scan")}</div><p class="hint">We check local addresses only. Cameras are never moved or reconfigured.</p><div class="actions"><button class="primary" type="submit">${icon("scan")} Find cameras</button>${button("Stop scan", "cancel-scan", "ghost")}</div></form><div class="progress-track"><span id="scan-progress"></span></div><p id="scan-status" class="hint"></p><div id="scan-results"></div><div class="form-actions">${button("Add selected cameras", "add-selected", "primary", "plus")}</div>`;
}
function displayControls() {
  const d = state.settings.display;
  return `<div class="layout-options">${[
    ["single", "Single", 1],
    ["two", "Two-up", 2],
    ["grid", "2 × 2", 4],
    ["pip", "Picture-in-picture", 2],
  ]
    .map(
      ([id, label, count]) =>
        `<button class="layout-option ${d.layout === id ? "selected" : ""}" data-act="layout" data-layout="${id}"><span class="layout-icon ${id}">${"<span></span>".repeat(count)}</span>${label}</button>`,
    )
    .join(
      "",
    )}</div><h3 style="margin-top:22px">Cameras on the display</h3><p class="hint">Choose up to four. Single view shows the first selected camera.</p><div class="camera-select">${state.settings.cameras.map((c) => `<label class="checkbox"><input type="checkbox" class="display-camera" value="${c.id}" ${checked(d.cameras.includes(c.id))}>${esc(c.name)}</label>`).join("") || "<p>Add a camera to choose your view.</p>"}</div><div class="form-actions">${button(d.blank ? "Show cameras" : "Blank / black", "blank-display", d.blank ? "primary" : "ghost", "display")}${button("Apply cameras", "apply-display", "primary")}</div><p class="hint">Multiple streams can be demanding for a TV browser. If playback stutters, select one camera or use a laptop connected by HDMI.</p>`;
}
function displayConnectionPanel() {
  return (
    heading(
      "Room display",
      "A live window into the meeting. No streaming platform needed.",
      button("Connect to TV", "connect-tv", "primary", "display"),
    ) +
    `<div class="two-col"><div class="stack">${panel("Connected displays", '<div id="displays-list"></div>')}</div><div class="stack">${panel("Local monitor / HDMI", '<p>Connect a second monitor, select it below, and open a dedicated full-screen window.</p><div id="monitors"><p>Finding connected monitors…</p></div>', button("Refresh", "refresh-monitors", "ghost", "refresh"))}${panel("Network display", `<p>Open the display page on your TV or another device on the same local network.</p><div class="url-box">${esc(state.display_url)}</div>${state.lan_enabled ? '<p class="hint">Your separate TV listener is available on this local network. Pairing is required.</p>' : '<div class="note warning">Network display is currently limited to this laptop. Enable a local network address below.</div>' + `<label>Local network address<select id="display-host">${state.network.map((n) => `<option value="${esc(n.ip)}">${esc(n.ip)} · ${esc(n.interface)}</option>`).join("")}</select></label><div class="form-actions">${button("Enable network display", "enable-lan", "primary")}</div>`}`)}</div></div>`
  );
}
function selectedMic() {
  const id=room.micId||'primary';
  if(id==='new')return {...state.settings.mic,ip:'',tls_fingerprint:'',enabled:false};
  return id==='primary'?state.settings.mic:(state.settings.microphones||[]).find(m=>m.id===id)||state.settings.mic;
}
function micEndpoint(){return !room.micId||room.micId==='primary'?'/api/mic':room.micId==='new'?'/api/microphones':'/api/microphones/'+encodeURIComponent(room.micId);}
function micMethod(){return room.micId&& !['primary','new'].includes(room.micId)?'PUT':'POST';}
function micControls(){
  const all=[{id:'primary',...state.settings.mic},...(state.settings.microphones||[])];
  const id=room.micId||'primary';
  return `<div class="actions">${all.length>1||id==='new'?`<label>Microphone<select id="mic-select">${all.map((m,i)=>`<option value="${esc(m.id)}" ${selected(m.id,id)}>Mic ${i+1} · ${esc(m.ip)}${m.enabled?'':' · disconnected'}</option>`).join('')}${id==='new'?'<option value="new" selected>New microphone</option>':''}</select></label>`:''}${id!=='new'&&all.length<8?button('Add microphone','add-mic','ghost'):''}${id==='new'?button('Cancel','cancel-add-mic','ghost'):id!=='primary'?button('Remove microphone','remove-mic','ghost'):''}</div><p class="hint">${all.length>1?'Each microphone has its own seat map. Select a mic, place its seats, and assign camera scenes. For overlapping coverage, assign the same scene to both seat maps.':'One microphone is enough when it covers every seat. Add another for separate coverage.'}</p>`;
}
function micForm() {
  const m = selectedMic();
  const resourcePaths = (m.resource_paths || [
    "/api/audio/inputs/microphone/beam/direction",
  ]).join("\n");
  const fieldMap = m.field_map || { azimuth: "azimuth", elevation: "elevation" };
  return `${micControls()}<form id="mic-form"><div class="form-grid"><label>Microphone model<select name="protocol"><option value="sscv2" ${selected(m.protocol||"sscv2","sscv2")}>TCC Medium · SSCv2</option><option value="tcc2" ${selected(m.protocol,"tcc2")}>TCC 2 · SSCv1</option></select></label>${textField("Microphone IP", "ip", m.ip)}<label>Password<input name="password" type="password" autocomplete="new-password" placeholder="Leave blank to keep saved"></label></div><p class="hint">TCC Medium uses the third-party password. TCC 2 uses legacy SSC over TCP, normally port 45, without a password.</p><div class="note" style="margin:8px 0">To enable third-party access: open Sennheiser Control Cockpit › your device › <strong>Access</strong> tab › enable Third-party API access. The password is set there.</div><details class="details"><summary>Advanced</summary><div class="form-grid">${textField("Port", "port", m.port, "number")}</div><p class="hint">Confirm the angle convention on site. These settings change only ConfDeck’s visualization.</p><div class="form-grid">${textField("Azimuth offset (degrees)", "azimuth_offset", m.azimuth_offset, "number")}<label>Elevation measured from<select name="elevation_reference"><option value="vertical" ${selected(m.elevation_reference, "vertical")}>Straight down (vertical)</option><option value="horizontal" ${selected(m.elevation_reference, "horizontal")}>The horizon (horizontal)</option></select></label><label class="checkbox"><input name="invert" type="checkbox" ${checked(m.invert)}>Reverse azimuth direction</label>${textField("Beam cone width (degrees)", "beam_width", m.beam_width, "number")}<label class="checkbox wide"><input name="estimate_position" type="checkbox" ${checked(m.estimate_position)}>Estimate position on the room floor</label>${textField("Ceiling height (metres)", "ceiling_height", m.ceiling_height, "number")}${textField("Typical head height (metres)", "head_height", m.head_height, "number")}<label class="wide">Certificate SHA-256 fingerprint<input name="tls_fingerprint" value="${esc(m.tls_fingerprint)}" placeholder="Verified device certificate fingerprint"></label><label class="wide">Resource paths (one per line)<textarea name="resource_paths" rows="3" style="font-family:monospace;font-size:12px">${esc(resourcePaths)}</textarea></label><label class="wide">Azimuth field name<input name="field_az" value="${esc(fieldMap.azimuth || "azimuth")}" placeholder="azimuth"></label><label class="wide">Elevation field name<input name="field_el" value="${esc(fieldMap.elevation || "elevation")}" placeholder="elevation"></label></div></details><p class="hint">Position data uses the control network. It does not require a capture device.</p><div id="mic-staged-results"></div><div class="form-actions">${button("Disconnect", "disconnect-mic", "ghost")}<button class="primary" type="submit">Connect & test microphone</button></div></form>`;
}
function healthPanel() {
  return (
    heading(
      "Room health",
      "Live checks, useful details, and a clear next step.",
      button("Copy diagnostics", "diagnostics", "primary", "health"),
    ) +
    `<div class="metric-grid"><div class="metric"><span>APP MEMORY</span><strong data-live="ram">—</strong><small>Target: under 300 MB while idle</small></div><div class="metric"><span>APP CPU</span><strong data-live="cpu">—</strong><small>Media relay runs separately</small></div><div class="metric"><span>FREE DISK SPACE</span><strong data-live="disk">—</strong><small>Settings and offline models drive</small></div></div><section class="panel" style="margin-top:22px"><div class="panel-header"><h2>System checks</h2><span class="badge good">${icon("lock")} LOCAL ONLY</span></div><table class="health-table"><thead><tr><th>Component</th><th>Status</th><th>Details</th><th>Next step</th></tr></thead><tbody id="health-rows"></tbody></table></section><div id="health-issues"></div><div class="note">Video tiles report measured frame rate and estimated browser buffering. These are not glass-to-glass latency measurements. Verify the under-500 ms target with a filmed clock on your wired LAN; HLS fallback usually adds delay.</div>${state.demo ? panel("Demo failure controls", `<p>Use these local simulators to check how the room recovers.</p><div class="actions">${button("Stop relay process", "fault-relay", "danger")}${button("Drop mic packets", "fault-mic-loss", "ghost")}${button("Restore mic packets", "fault-mic-restore", "ghost")}${button("Send malformed messages", "fault-garbage", "ghost")}</div>`) : ""}`
  );
}
function settingsPanel() {
  return (
    heading("Settings", "Simple defaults for a dependable room.") +
    `<div class="settings-list">${panel("Room preferences", `<form id="settings-form"><div class="form-grid">${textField("Room name", "room_name", state.settings.room_name)}<label>Maximum concurrent previews<select name="preview_limit">${[1, 2, 3, 4].map((n) => `<option ${selected(n, state.settings.preview_limit)}>${n}</option>`).join("")}</select></label></div><div class="form-actions"><button type="submit" class="primary">Save preferences</button></div></form>`)}${panel("Setup and appearance", `<div class="row"><div><h3>First-run setup</h3><p>Revisit cameras, display and microphone.</p></div>${button("Run setup", "setup", "ghost", "settings")}</div><div class="row"><div><h3>Theme</h3><p>Switch between dark and light appearance.</p></div>${button("Change theme", "theme", "ghost", "sun")}</div><div class="row"><div><h3>Local access</h3><p>PIN sign-in is currently disabled. Local administration runs on your room controller.</p></div></div><div class="row"><div><h3>Reboot room system</h3><p>Restart the configured cameras, microphone connections, media relays and seat tracking.</p></div>${button("Reboot System", "reboot-system", "danger", "refresh")}</div><div class="row"><div><h3>Stop the room system</h3><p>Close video and microphone services.</p></div>${button("Stop ConfDeck", "stop-system", "ghost")}</div>`)}${panel("Local processing", '<p>All interface assets and fonts are stored locally. The runtime connects only to private network addresses. Video stays on your network, and camera passwords never appear in display responses.</p><p class="hint">Follow mode recalls the presets assigned to each seat. Accuracy Lab can save microphone events for evaluation; speech diagnostics can save level and probability data without recording audio.</p>')}</div>`
  );
}

async function triggerReboot(hardware) {
  toast(hardware ? "Rebooting Mini PC hardware… Reconnecting in ~30s." : "Rebooting ConfDeck… Re-initializing cameras, mic & seats.");
  showRebootDialog(hardware);
  try {
    await api("/api/system/reboot", { hardware });
  } catch (e) {
    // Expected during process restart
  }
}

function showRebootDialog(hardware) {
  const d = $("#dialog");
  d.innerHTML = `
    <div class="dialog-head">
      <h2>${hardware ? "Mini PC Hardware Reboot" : "Room System Reboot"}</h2>
    </div>
    <div style="padding:16px 0;text-align:center">
      <div style="margin-bottom:12px;font-size:15px;line-height:1.5">
        ${hardware 
          ? "The Mini PC is restarting. Please wait while the system boots and connects to devices…" 
          : "Re-initializing all 6 cameras, microphone, media relays, and tracking seats…"}
      </div>
      <div id="reboot-status" class="hint" style="font-weight:600;font-size:14px">
        Connecting to services…
      </div>
    </div>
  `;
  d.showModal();

  let elapsed = 0;
  const pollInterval = setInterval(async () => {
    elapsed += 1.5;
    const statusEl = $("#reboot-status");
    if (statusEl) {
      statusEl.textContent = `Waiting for system response… (${Math.round(elapsed)}s elapsed)`;
    }
    try {
      const res = await fetch("/api/state", { headers: { "x-confdeck": "1" } });
      if (res.ok) {
        const data = await res.json();
        const onlineCams = (data.cameras || []).filter(c => c.state === "Online" || c.online).length;
        const micConnected = data.mic && data.mic.state === "Connected";
        if (onlineCams > 0 || micConnected || elapsed >= 8) {
          clearInterval(pollInterval);
          d.close();
          toast("System reboot complete! All cameras, mic, and seats are live.");
          state = room.state = data;
          update();
        }
      }
    } catch (e) {
      // Waiting for backend to start
    }
  }, 1500);
}
function update() {
  const waterfall=document.querySelector('#handoff-timing');
  if(waterfall){
    const rows=Object.entries(state?.tracking?.handoffs||{});
    waterfall.textContent=rows.map(([cid,h])=>{
      const label=state.settings.cameras.find(c=>c.id===cid)?.name||cid;
      const trace=Object.entries(h.trace||{}).map(([stage,t])=>stage+' '+Math.round(1000*(t-(h.trace.decision??t)))+' ms');
      return [label+' · '+h.state,...trace,h.median_ms!=null?'Median '+h.median_ms+' ms · p90 '+h.p90_ms+' ms':''].join('\n');
    }).join('\n\n')||'No handoffs recorded.';
  }
  $("smart-preview")?.update(state);
  $("tune-follow")?.update(state);
  $("camera-studio")?.update(state);
  $("accuracy-lab")?.update(state);
  $("mic-tuning")?.update(state);
  if(!$("mic-tuning")) $("seat-radar")?.update(state);
  $("mic-team")?.update(state);
  if ($("#global-follow-stop")) $("#global-follow-stop").hidden = !state?.tracking?.live;
  if (!state || !$("#content")) return;
  const online = state.cameras.filter((c) => cameraIsPlaying(c.id) || c.state === "Online").length,
    live = state.displays.filter((d) => d.state === "Live").length,
    m = (state.microphones||[]).find(m=>m.id===(page==='seats'?(room.micId||'primary'):(state.tracking.active_mic||'primary'))) || state.mic,
    health = state.health;
  const values = {
    room: state.settings.room_name,
    "cameras-count": `${online} / ${state.cameras.length} online`,
    "cameras-summary": state.cameras.length
      ? "Ready for a local preview"
      : "Scan the network to get started",
    "mic-state": m.state === "Connected" ? "Connected" : m.state,
    "mic-summary":
      m.position && m.age < 5
        ? "Live speaker position"
        : "Waiting for a fresh beam reading",
    "display-state": live ? "Live" : "Not connected",
    "display-summary": live
      ? `${live} display${live > 1 ? "s" : ""} receiving your view`
      : "Connect a TV or second monitor",
    azimuth:
      m.position && m.age < 5 ? `${m.position.azimuth.toFixed(1)}°` : "—",
    elevation:
      m.position && m.age < 5 ? `${m.position.elevation.toFixed(1)}°` : "—",
    rate: `${(m.rate || 0).toFixed(1)} /s`,
    "mic-age":
      m.age == null ? "No readings yet" : `${m.age.toFixed(1)} seconds ago`,
    "mic-activity":
      m.position?.activity == null
        ? "Not provided by this device"
        : m.position.activity
          ? "Talking"
          : "Quiet",
    ram: `${health.rss_mb ?? "—"} MB`,
    cpu: `${health.cpu_percent ?? "—"}%`,
    disk: health.free_disk_mb
      ? `${(health.free_disk_mb / 1024).toFixed(1)} GB`
      : "—",
  };
  $$("[data-live]").forEach(
    (n) => (n.textContent = values[n.dataset.live] ?? "—"),
  );
  const statuses = {
    cameras: online ? "Online" : "Waiting",
    mic: m.state,
    display: live ? "Live" : "Not connected",
  };
  Object.entries(statuses).forEach(([key, value]) => {
    const dot = $(`[data-dot="${key}"]`);
    if (dot) dot.className = `dot ${statusClass(value)}`;
    const word = $(`[data-status="${key}"]`);
    if (word)
      word.textContent =
        key === "cameras" ? `${online}/${state.cameras.length}` : "";
  });
  $$('[data-badge="mic"]').forEach((n) => (n.innerHTML = badge(m.state)));
  $$("mic-radar").forEach(
    (r) => (r.data = { ...m, beam_width: state.settings.mic.beam_width }),
  );
  const ready = $("#readiness");
  if (ready)
    ready.innerHTML = [
      ["Cameras", online > 0, "cameras"],
      ["Display", live > 0, "display"],
      ["Microphone", m.state === "Connected", "microphone"],
    ]
      .map(
        ([name, ok, target]) =>
          `<a class="check-item ${ok ? "good" : "warn"}" href="#${target}"><span class="check-circle">${ok ? "✓" : "!"}</span>${name}${ok ? " ready" : " · Set up"}</a>`,
      )
      .join("");
  if (page === "home" || page === "display") {
    const d=state.settings.display;
    const ids=d.cameras.length?d.cameras:state.tracking.live&&state.settings.tracking.conversation_layout?[]:state.cameras.slice(0,4).map(c=>c.id);
    const program=$("#program-preview");
    syncOutput(program,state.cameras.filter(c=>ids.includes(c.id)),d.layout,ids,d.blank);
    if ($("#overview-status")) $("#overview-status").textContent=`Cameras video: ${online}/${state.cameras.length} · Cameras control: ${state.cameras.filter(c=>c.control?.state=== "OK").length}/${state.cameras.length} · Mic: ${m.state} · Display: ${live?"Live":"Not connected"} · Following: ${state.tracking.live?"ON":"OFF"}`;
    if ($("#home-cameras")) syncPlayers($("#home-cameras"), state.cameras.slice(0,state.settings.preview_limit));
    if ($("#display-follow-status")) $("#display-follow-status").textContent=state.tracking.live ? (state.settings.tracking.conversation_layout ? 'Smart following live' : 'Following live · enable smart following below for automatic display switching') : 'Follow is off';
  }
  if (page === "cameras") updateCameras();
  if (page === "home" || page === "display") {
    updateDisplays();
    $$("[data-layout]").forEach((b) =>
      b.classList.toggle(
        "selected",
        b.dataset.layout === state.settings.display.layout,
      ),
    );
  }
  if (page === "seats") {
    const issue = $("#mic-issue");
    if (issue) issue.innerHTML = issueHTML(m.issue);
    const xy = m.position?.estimated_xy;
    if($("#floor-estimate")) $("#floor-estimate").innerHTML = xy
      ? `<div class="note">Estimated floor position: ${xy[0]} m across, ${xy[1]} m forward from the microphone.</div>`
      : "";
  }

  if (page === "advanced") updateHealth();
  updateScan();
}
function updateCameras() {
  const root = $("#camera-list");
  const signature = JSON.stringify(state.cameras.map((c) => [c.id, c.name]));
  if (root.dataset.signature !== signature) {
    root.dataset.signature = signature;
    const known = new Set(state.cameras.map((c) => c.id));
    $$(".camera-card", root).forEach((c) => {
      if (!known.has(c.dataset.id)) c.remove();
    });
    if (state.cameras.length) $(".empty", root)?.remove();
    for (const [index, c] of state.cameras.entries()) {
      let card = $(`[data-id="${c.id}"]`, root);
      if (!card) {
        card = document.createElement("section");
        card.className = "panel camera-card";
        card.dataset.id = c.id;
        card.innerHTML = `<div class="panel-header"><h2 class="cam-name"></h2><span class="cam-badge"></span></div>${index < state.settings.preview_limit ? `<camera-player camera="${c.id}" name="${esc(c.name)}"></camera-player>` : '<div class="empty"><p>Preview limit reached. Increase it in Settings or view on the TV.</p></div>'}<div class="panel-body"><div class="camera-meta"></div><div class="cam-issue"></div><p class="relay-share"></p><div class="cam-diagnosis" role="status"></div><div class="actions">${button("Edit", "edit-camera", "ghost", "settings", `data-id="${c.id}"`)}${button("Diagnose video", "diagnose-camera", "ghost", "", `data-id="${c.id}"`)}${button("Check control", "control-camera", "ghost", "", `data-id="${c.id}"`)}${button("Retry", "retry-camera", "ghost", "refresh", `data-id="${c.id}"`)}${button("Remove", "remove-camera", "ghost", "", `data-id="${c.id}"`)}<label class="checkbox" style="margin-left:auto;font-size:11px"><input type="checkbox" class="use-hls" data-id="${c.id}">Use HLS</label></div></div>`;
        root.append(card);
      }
      $(".cam-name", card).textContent = c.name;
    }
    if (!state.cameras.length)
      root.innerHTML =
        '<div class="panel empty"><span>◉</span><h3>No cameras connected</h3><p>Add your camera by IP or scan the local network. Only real camera connections appear here.</p></div>';
    bindTooltips(root);
  }
  for (const c of state.cameras) {
    const card = $(`[data-id="${c.id}"]`, root);
    const playing = cameraIsPlaying(c.id);
    $(".cam-badge", card).innerHTML = `Video: ${badge(playing ? "Online" : c.video?.state || c.state)} Control: ${badge(c.control?.state || "Not checked")}`;
    $(".camera-meta", card).textContent = [
      c.ip,
      c.width ? `${c.width} × ${c.height} · ${c.fps} fps` : c.model,
    ].join(" · ");
    $(".relay-share",card).textContent=`Stream: ${c.active_path||c.path} · Share relay: ${c.relay_url||""}. ${state.lan_enabled?"Other systems can read this relay on the selected local /24 network.":"Enable network display in Overview for LAN sharing."}`;
    $(".cam-diagnosis",card).textContent=c.diagnosis?`${c.diagnosis.state}: ${(c.diagnosis.stages||[]).map(s=>`${s.stage}: ${s.ok?"Passed":s.cause+" — "+s.fix}`).join(" · ")}`:"";
    const issue = $(".cam-issue", card);
    const currentIssue = playing && c.issue?.code === "keyframe" ? null : c.issue;
    const signature = JSON.stringify(currentIssue);
    if (issue.dataset.signature !== signature) {
      issue.dataset.signature = signature;
      issue.innerHTML = issueHTML(currentIssue);
    }
  }
}
function updateDisplays() {
  const root = $("#displays-list");
  if (!root) return;
  root.innerHTML =
    state.displays
      .map(
        (d) =>
          `<div class="row"><div><h3>${esc(d.name)}</h3><p>${d.last_seen ? `Last seen ${Math.max(0, Math.round(Date.now() / 1000 - d.last_seen))} seconds ago` : "Waiting for the display to connect"}${d.latency_ms != null ? ` · ~${d.latency_ms} ms buffer` : ""}</p>${d.note ? `<p class="warn">${esc(d.note)}</p>` : ""}</div><div class="actions">${badge(d.state)}${button("Unpair", "unpair", "ghost", "", `data-id="${d.id}"`)}</div></div>`,
      )
      .join("") ||
    '<div class="empty"><h3>No displays connected yet</h3><p>Use Connect to TV to show a pairing code.</p></div>';
}
function updateScan() {
  if (!$("#scan-status")) return;
  $("#scan-status").textContent =
    `${state.scan.state}${state.scan.state === "Scanning" ? ` · ${state.scan.progress} / 254 addresses` : ""}`;
  $("#scan-progress").style.width =
    `${Math.min(100, (state.scan.progress / 254) * 100)}%`;
  const signature = JSON.stringify(state.scan.results);
  if (signature !== lastScan) {
    lastScan = signature;
    $("#scan-results").innerHTML =
      state.scan.results
        .map(
          (c) =>
            `<div class="scan-result"><label><input type="checkbox" class="scan-choice" value="${esc(c.id)}"><span><strong>${esc(c.ip)}</strong><p>${esc(c.path)}</p></span></label>${badge(c.status.state)}</div>`,
        )
        .join("") ||
      (state.scan.state === "Finished"
        ? "<p>No cameras found. Check the network cable or use Add by IP.</p>"
        : "");
  }
}
function updateHealth() {
  const rows = [
    [
      "Video relay",
      state.media.state,
      `${state.media.restarts} automatic restart(s)`,
      button("Restart", "restart-media", "ghost"),
    ],
    ...state.cameras.map((c) => [
      c.name,
      c.state,
      c.metrics
        ? `${c.metrics.transport} · ${c.metrics.fps ?? "—"} fps · ~${c.metrics.latency_ms ?? "—"} ms buffer`
        : c.issue?.fix || "Waiting for playback measurements",
      button("Retry", "retry-camera", "ghost", "", `data-id="${c.id}"`),
    ]),
    [
      "Ceiling microphone",
      state.mic.state,
      state.mic.age == null
        ? "No data yet"
        : `${state.mic.age.toFixed(1)} s since data · ${state.mic.rate.toFixed(1)} messages/s`,
      '<a href="#seats">Open settings →</a>',
    ],
    [
      "Room display",
      state.displays.some((d) => d.state === "Live") ? "Live" : "Not connected",
      `${state.displays.length} paired display(s)`,
      '<a href="#home">Connect →</a>',
    ],
  ];
  $("#health-rows").innerHTML = rows
    .map(
      ([name, status, detail, action]) =>
        `<tr><td>${esc(name)}</td><td>${badge(status)}</td><td>${esc(detail)}</td><td>${action}</td></tr>`,
    )
    .join("");
  $("#health-issues").innerHTML =
    [state.media.issue, ...(state.health.issues || [])]
      .map(issueHTML)
      .join("") +
    (state.health.warning
      ? `<div class="note warning">${esc(state.health.warning)}</div>`
      : "") +
    (state.health.clock_skew
      ? '<div class="note warning">The laptop clock changed. Check the system time. Live data freshness uses a monotonic clock.</div>'
      : "");
}
let micNoticeShown = false;
async function refresh(render = false) {
  state = room.state = await api("/api/state");
  if (render) route();
  else update();
  showMicNotice();
}
function showMicNotice() {
  if (state.mic_notice && !micNoticeShown) {
    micNoticeShown = true;
    dialog("Microphone password needed", `<p>${esc(state.mic_notice)}</p><div class="form-actions">${button("Open microphone settings", "mic-notice-dismiss", "primary")}</div>`);
  }
}
async function loadMonitors() {
  try {
    const monitors = await api("/api/monitors");
    const root = $("#monitors");
    if (root)
      root.innerHTML =
        monitors
          .map(
            (m) =>
              `<div class="monitor-row"><div><strong>${esc(m.name)}</strong><p class="hint">${m.width} × ${m.height}${m.primary ? " · Primary" : ""}</p></div>${button("Open full screen", "open-monitor", "ghost", "display", `data-index="${m.id}"`)}</div>`,
          )
          .join("") ||
        "<p>No monitors were detected. Open the display URL in your browser and move it to the desired screen.</p>";
  } catch (error) {
    fail(error);
  }
}
async function connectTV() {
  const data = await api("/api/display/connect", {});
  dialog(
    "Connect your room display",
    `<div class="qr-pair"><img src="/api/display/qr" alt="QR code for the local display page"><div><div class="eyebrow">PAIRING CODE · VALID FOR 10 MINUTES</div><div class="pair-code">${esc(data.code)}</div><p class="hint">Enter this code on the display page.</p></div></div><div class="url-box">${esc(data.url)}</div><p>Open this address on a TV or device on your local network, or scan the QR code.</p>${!state.lan_enabled ? '<div class="note warning">This address currently works on this laptop only. Enable a LAN address on the Display page for a network TV.</div>' : ""}<div class="form-actions"><a href="${esc(data.url)}" target="_blank" rel="noopener">Open display page ↗</a>${button("Done", "close-dialog", "primary")}</div>`,
  );
}
function microphoneBody(form) {
  const f = new FormData(form),
    m = { ...selectedMic(), enabled: true };
  delete m.id;
  for (const k of ["ip", "protocol", "elevation_reference", "tls_fingerprint"])
    m[k] = String(f.get(k) || "");
  for (const k of [
    "port",
    "azimuth_offset",
    "beam_width",
    "ceiling_height",
    "head_height",
  ])
    m[k] = Number(f.get(k));
  for (const k of ["invert", "estimate_position"]) m[k] = f.has(k);
  const raw = String(f.get("resource_paths") || "").trim();
  m.resource_paths = raw ? raw.split(/\s+/).filter(Boolean) : m.resource_paths;
  const az = String(f.get("field_az") || "azimuth").trim() || "azimuth";
  const el = String(f.get("field_el") || "elevation").trim() || "elevation";
  m.field_map = { ...m.field_map, azimuth: az, elevation: el };
  const body = { settings: m };
  if (f.get("password")) body.password = String(f.get("password"));
  return body;
}
document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  event.preventDefault();
  const submit = event.submitter;
  submit?.setAttribute("disabled", "");
  try {
    const f = new FormData(form);
    switch (form.id) {
      case "preset-settings": {
        const config = { control_protocol: f.get("control_protocol"), control_port: Number(f.get("control_port")), control_profile: f.get("control_profile"), visca_port: Number(f.get("visca_port")), control_enabled: true };
        await api(`/api/cameras/${presetCamera.id}`, config, "PATCH");
        Object.assign(presetCamera, config);
        await loadPresets();
        break;
      }
      case "preset-save": {
        pendingPreset = { token: String(f.get("token")), name: String(f.get("name")) };
        if (presetData.presets.some(p => p.token === pendingPreset.token)) {
          dialog("Replace this preset?", `<p>Preset ${esc(pendingPreset.token)} will be replaced with the camera's current position and the name ${esc(pendingPreset.name)}.</p><div class="form-actions">${button("Back", "presets-back", "ghost")}${button("Replace preset", "preset-confirm-save", "danger")}</div>`);
        } else {
          await api(`/api/cameras/${presetCamera.id}/presets/save`, pendingPreset);
          toast("Preset saved on camera.");
          await loadPresets();
        }
        break;
      }
      case "camera-form": {
        const data = Object.fromEntries(f);
        data.port = Number(data.port);
        if (form.dataset.id) {
          if (!data.password) delete data.password;
          if (!data.username) delete data.username;
          await api(`/api/cameras/${form.dataset.id}`, data, "PATCH");
        } else await api("/api/cameras", data);
        $("#dialog").close();
        toast("Camera saved. Checking the video stream…");
        await refresh(true);
        break;
      }
      case "scan-form":
        await api("/api/scan", {
          subnet: f.get("custom_subnet") || f.get("subnet"),
          username: f.get("username"),
          password: f.get("password"),
        });
        form.querySelector("[name=password]").value = "";
        toast(
          "Scanning the selected local network. Stream checks may take a few minutes.",
        );
        break;
      case "mic-form": {
        const isSscv2 = !state.demo && f.get("protocol") === "sscv2";
        const resultsEl = $("#mic-staged-results");
        if (resultsEl && isSscv2) {
          resultsEl.innerHTML = `<p class="hint">Connecting and running staged tests…</p>`;
        }
        const savedMic = await api(micEndpoint(), microphoneBody(form), micMethod());
        room.micId=savedMic.id;
        form.querySelector("[name=password]").value = "";
        if (isSscv2) {
          try {
            const testRes = await api("/api/mic/test?source="+encodeURIComponent(room.micId||"primary"), {});
            if (resultsEl && testRes && testRes.stages) {
              resultsEl.innerHTML = `<div class="staged-test" style="margin: 12px 0; border: 1px solid var(--border); border-radius: 6px; padding: 10px; background: var(--surface);">
                <div style="font-weight: 600; margin-bottom: 8px;">Connection test stages:</div>
                ${testRes.stages
                  .map(
                    (s) => `
                  <div style="margin-bottom: 6px; font-size: 13px;">
                    <span style="display:inline-block; width:18px; font-weight:bold; color: ${s.ok ? "#22c55e" : "#ef4444"}">${s.ok ? "✓" : "✗"}</span>
                    <strong>Stage ${s.stage}: ${esc(s.name)}</strong> — ${esc(s.detail || (s.ok ? "Passed" : "Failed"))}
                    ${!s.ok && s.fix ? `<div class="note warning" style="margin: 4px 0 4px 22px; font-size: 12px;"><strong>Fix:</strong> ${esc(s.fix)}</div>` : ""}
                  </div>
                `,
                  )
                  .join("")}
              </div>`;
            }
          } catch (err) {
            if (resultsEl) {
              resultsEl.innerHTML = `<div class="note warning" style="margin: 12px 0;"><strong>Test error:</strong> ${esc(err.message || String(err))}</div>`;
            }
          }
        }
        toast("Connecting to the microphone. Watch for the first reading.");
        await refresh();
        if(!$("seat-radar"))route();
        break;
      }
      case "calibration-form":
        await api("/api/mic/calibration", { label: f.get("label") });
        toast("Reference reading saved. Move to the next marked seat.");
        form.elements.label.value = `Seat ${state.settings.calibration.length + 1}`;
        await refresh();
        break;
      case "settings-form":
        await api("/api/settings", {
          room_name: f.get("room_name"),
          preview_limit: Number(f.get("preview_limit")),
        });
        toast("Room preferences saved.");
        await refresh();
        break;
    }
  } catch (error) {
    fail(error);
  } finally {
    submit?.removeAttribute("disabled");
  }
});
document.addEventListener("click", async (event) => {
  const control = event.target.closest("[data-act]");
  if (!control) return;
  const action = control.dataset.act,
    id = control.dataset.id;
  try {
    switch (action) {
      case "presets":
        await openPresets(state.cameras.find(c => c.id === id));
        break;
      case "presets-back":
        await openPresets(presetCamera);
        break;
      case "preset-stop":
        await api(`/api/cameras/${presetCamera.id}/presets/stop`, {});
        toast("Stop accepted by camera.");
        break;
      case "preset-recall": {
        const speed = $("#preset-speed")?.value;
        const number = $("#visca-preset");
        if (number && !number.reportValidity()) break;
        const token = number ? number.value : control.dataset.token;
        control.disabled = true;
        try {
          await api(`/api/cameras/${presetCamera.id}/presets/recall`, { token, ...(speed ? {speed: Number(speed)} : {}) });
          toast("Preset recall accepted by camera.");
        } finally { control.disabled = false; }
        break;
      }
      case "preset-link": {
        const token = control.dataset.token;
        const preset = presetData.presets.find(p => p.token === token);
        const name = prompt("Scene name", preset?.name)?.trim();
        if (!name) break;
        const exists = (await api("/api/scenes")).some(s => s.name.toLowerCase() === name.toLowerCase());
        if (exists && !confirm("Update the selected camera in this scene?")) break;
        await api("/api/scenes/capture", {camera:presetCamera.id, name, existing_token:token, confirm_update:exists});
        await $("camera-studio")?.scenes();
        toast("Scene ready to assign in Seats and Mic.");
        break;
      }
      case "preset-edit": {
        const preset = presetData.presets.find(p => p.token === control.dataset.token);
        $("#preset-save [name=token]").value = preset.token;
        $("#preset-save [name=name]").value = preset.name;
        $("#preset-save").scrollIntoView({block: "nearest"});
        break;
      }
      case "preset-delete":
        pendingPreset = { token: control.dataset.token };
        dialog("Delete camera preset?", `<p>Preset ${esc(pendingPreset.token)} will be deleted from this camera.</p><div class="form-actions">${button("Back", "presets-back", "ghost")}${button("Delete preset", "preset-confirm-delete", "danger")}</div>`);
        break;
      case "preset-confirm-save":
      case "preset-confirm-delete":
        control.disabled = true;
        try {
          await api(`/api/cameras/${presetCamera.id}/presets/${action === "preset-confirm-save" ? "save" : "delete"}`, pendingPreset);
          toast(action === "preset-confirm-save" ? "Preset saved on camera." : "Preset deleted from camera.");
          await openPresets(presetCamera);
        } finally { control.disabled = false; }
        break;
      case "close-dialog":
        $("#dialog").close();
        break;
      case "setup":
        location.hash = "seats";
        break;
      case "goto-display":
        location.hash = "display";
        break;
      case "toggle-follow":
        if(!state.tracking.live && !confirm("Enable following? Assigned cameras may move.")) break;
        await api("/api/tracking/mode",{live:!state.tracking.live});
        break;
      case "tracking-stop":
        await api("/api/tracking/stop", {});
        toast("Following stopped. An accepted camera move may still finish.");
        break;
      case "theme":
        document.body.classList.toggle("light");
        localStorage.setItem(
          "confdeck-theme",
          document.body.classList.contains("light") ? "light" : "dark",
        );
        break;
      case "reboot-system":
        dialog(
          "Reboot Room System?",
          `<p>This will fully re-initialize all 6 cameras, microphone connection, media relays, and reset seat tracking from scratch.</p>
           <label class="checkbox" style="margin-top:14px;display:flex;align-items:center;gap:8px">
             <input type="checkbox" id="reboot-hardware">
             <strong>Full Mini PC hardware reboot</strong> (restart the physical machine)
           </label>
           <p class="hint">Leave unchecked to reboot ConfDeck services, camera streams, and tracking without restarting the Mini PC.</p>
           <div class="form-actions" style="margin-top:16px">
             ${button("Cancel", "close-dialog", "ghost")}
             ${button("Reboot System", "confirm-reboot", "danger", "refresh")}
           </div>`
        );
        break;
      case "confirm-reboot": {
        const hardware = !!$("#reboot-hardware")?.checked;
        $("#dialog").close();
        triggerReboot(hardware);
        break;
      }
      case "stop-system":
        dialog("Stop ConfDeck?", `<p>This will stop the video relay, display streams and microphone tracking. Start the launcher again when you need the room.</p><div class="form-actions">${button("Keep running", "close-dialog", "ghost")}${button("Stop ConfDeck", "confirm-stop", "danger")}</div>`);
        break;
      case "confirm-stop":
        await api("/api/system/stop", {});
        socketClose?.();
        $$("camera-player").forEach(player => player.stop());
        $("#dialog").close();
        $("#app").innerHTML = '<main class="login-card"><h1>ConfDeck is stopped</h1><p>Open the ConfDeck launcher to start the room again.</p></main>';
        break;
      case "add-camera":
        dialog("Add a camera", cameraForm());
        break;
      case "edit-camera":
        dialog(
          "Edit camera",
          cameraForm(state.cameras.find((c) => c.id === id)),
        );
        break;
      case "remove-camera":
        dialog(
          "Remove this camera?",
          `<p>The camera will be removed from ConfDeck and the display layout. Its own settings stay as they are.</p><div class="form-actions">${button("Cancel", "close-dialog", "ghost")}${button("Remove camera", "confirm-remove", "danger", "", `data-id="${id}"`)}</div>`,
        );
        break;
      case "confirm-remove":
        await api(`/api/cameras/${id}`, undefined, "DELETE");
        $("#dialog").close();
        await refresh(true);
        break;
      case "diagnose-camera":
        await api(`/api/cameras/${id}/diagnose`,{}); toast("Diagnosing video through the relay…"); break;
      case "control-camera":
        await api(`/api/cameras/${id}/control-test`,{}); break;
      case "retry-camera":
        await api(`/api/cameras/${id}/retry`, {});
        toast("Checking the camera stream…");
        break;
      case "scan-dialog":
        dialog("Find cameras on your network", scanForm());
        lastScan = "";
        updateScan();
        break;
      case "cancel-scan":
        await api("/api/scan/cancel", {});
        break;
      case "add-selected": {
        const choices = $$(".scan-choice:checked").map((n) => n.value);
        if (!choices.length)
          throw new Error("Tick one or more discovered cameras first.");
        for (const id of choices) {
          const c = state.scan.results.find((c) => c.id === id);
          await api("/api/cameras", {
            name: c.name,
            ip: c.ip,
            port: c.port,
            path: c.path,
            use_scan_credentials: true,
          });
        }
        toast(`${choices.length} camera(s) added.`);
        await refresh();
        if (page !== "setup") {
          $("#dialog").close();
          route();
        }
        break;
      }
      case "connect-tv":
        await connectTV();
        break;
      case "layout":
        await api("/api/display", {
          ...state.settings.display,
          layout: control.dataset.layout,
        });
        await refresh();
        break;
      case "blank-display":
        await api("/api/display", {
          ...state.settings.display,
          blank: !state.settings.display.blank,
        });
        await refresh(true);
        break;
      case "apply-display": {
        const ids = $$(".display-camera:checked").map((n) => n.value);
        if (ids.length > 4)
          throw new Error("Choose up to four display cameras.");
        await api("/api/display", { ...state.settings.display, cameras: ids });
        toast("Display cameras updated.");
        break;
      }
      case "unpair":
        await api(`/api/displays/${id}`, undefined, "DELETE");
        await refresh();
        break;
      case "enable-lan": {
        const host = $("#display-host").value;
        if (!host) throw new Error("Connect a local network cable first.");
        await api("/api/display/network", { host });
        toast("Network display enabled. Pair the TV using the new URL.");
        setTimeout(() => refresh(true).catch(fail), 1200);
        break;
      }
      case "refresh-monitors":
      case "open-monitor":
        await api(`/api/monitors/${control.dataset.index}/open`, {});
        toast("Opening the display window. Allow a few seconds for playback.");
        break;
      case "mic-notice-dismiss":
        await api("/api/mic/notice/dismiss", {});
        $("#dialog").close();
        location.hash = "seats";
        await refresh(true);
        break;
      case "add-mic":
        await $("seat-radar")?.save();
        room.micId='new'; route(); break;
      case "cancel-add-mic":
        room.micId='primary'; route(); break;
      case "remove-mic":
        if(!confirm('Remove this microphone? Its seats must be removed first.'))break;
        await $("seat-radar")?.save();
        await api(micEndpoint(),undefined,'DELETE');
        room.micId='primary'; await refresh(true); break;
      case "disconnect-mic": {
        if(room.micId==='new')break;
        const settings={...selectedMic(),enabled:false}; delete settings.id;
        await api(micEndpoint(),{settings},micMethod());
        await refresh(); break;
      }
      case "calibrate":
        dialog(
          "Calibration helper",
          `<ol><li>Stand directly underneath the microphone and speak.</li><li>Save a reading named “Under microphone”.</li><li>Move to each marked seat, speak, and save its reading.</li><li>Compare the radar with the room. Adjust offset, direction or elevation reference in installation settings.</li></ol><form id="calibration-form">${textField("Reference point", "label", "Under microphone")}<div class="form-actions"><button type="submit" class="primary">Save current reading</button></div></form><p class="hint">${state.settings.calibration.length} reference points saved. This records observations; it does not change the mic.</p>`,
        );
        break;
      case "diagnostics": {
        const text = JSON.stringify(await api("/api/diagnostics"), null, 2);
        try {
          await navigator.clipboard.writeText(text);
          toast("Redacted diagnostics copied.");
        } catch {
          dialog(
            "Redacted diagnostics",
            `<textarea readonly style="width:100%;height:300px" aria-label="Diagnostics">${esc(text)}</textarea><p>Select and copy the text above.</p>`,
          );
        }
        break;
      }
      case "restart-media":
        await api("/api/media/restart", {});
        toast("Restarting the local video relay. Streams will reconnect.");
        break;
      default:
        if (action.startsWith("fault-")) {
          const faults = {
            "fault-relay": { kind: "relay" },
            "fault-mic-loss": { kind: "mic-loss", enabled: true },
            "fault-mic-restore": { kind: "mic-loss", enabled: false },
            "fault-garbage": { kind: "mic-garbage", enabled: true },
          };
          await api("/api/demo/fault", faults[action]);
          toast("Demo failure control applied.");
        }
    }
  } catch (error) {
    fail(error);
  }
});
document.addEventListener("change", async (event) => {
  const target = event.target;
  try {
    if(target.name==='protocol' && target.closest('#mic-form')){
      const port=target.form.elements.port;
      if ([45,443].includes(Number(port.value))) port.value=target.value==='tcc2'?45:443;
    }
    if(target.id==='mic-select'){
      await $("seat-radar")?.save();
      room.micId=target.value; route(); return;
    }
    if (target.classList.contains("use-hls"))
      $(`camera-player[camera="${target.dataset.id}"]`)?.useHLS(target.checked);

  } catch (error) {
    fail(error);
  }
});
document.addEventListener("metrics", (event) =>
  api("/api/metrics", event.detail).catch(() => {}),
);
window.addEventListener("hashchange", route);
boot();
