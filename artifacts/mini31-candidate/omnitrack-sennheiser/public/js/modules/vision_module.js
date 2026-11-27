// =============================================================================
// AI VISION & REAL-TIME FACE TRACKING MODULE FOR PORT 4000 (OMNITRACK)
// Face detection runs locally; camera autotracking remains a separate feature.
// =============================================================================
(function () {
    'use strict';

    const W_VISION = 320;
    const H_VISION = 180;
    const CLIENT_FRAME_STALE_SEC = 0.08;

    const state = {
        active: false,
        globalMode: 'off',
        cameras: [],
        seats: [],
        selectedCameraId: '',
        tool: 'inspect', // 'inspect' | 'roi' | 'mask'
        rois: [],
        screenMasks: [],
        visionData: null,
        dragging: null,
        dragCurrent: null,
        transport: 'none', // 'webrtc' | 'mjpeg' | 'none'
        pc: null,
        pollTimer: null,
        frameInFlight: false,
        rafId: null,
        lastMjpegRefresh: 0,
        scratchCanvas: null,
        scratchCtx: null,
        statusMessage: '',
        roiSyncEpoch: 0,
        savingRois: 0,
        hasStagedMask: false,
        lastRoiStructKey: '',
    };

    function el(id) {
        return document.getElementById(id);
    }

    async function apiJson(url, options = {}) {
        const opts = {
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
            ...options,
        };
        const res = await fetch(url, opts);
        let data = {};
        try {
            data = await res.json();
        } catch (e) {
            data = { error: `HTTP ${res.status}` };
        }
        if (!res.ok) {
            throw new Error(data.error || data.detail || `Request failed (${res.status})`);
        }
        return data;
    }

    function selectedCamera() {
        return state.cameras.find((c) => c.id === state.selectedCameraId) || state.cameras[0] || null;
    }

    function setStatusHint(msg, isError = false) {
        state.statusMessage = msg || '';
        const node = el('vision-status-hint');
        if (!node) return;
        node.textContent = msg || 'Select "+ Seat ROI" or "+ Screen Mask" and drag across the camera preview.';
        node.className = isError
            ? 'text-[11px] font-semibold text-red-600'
            : 'text-[11px] text-gray-500';
    }

    function updateModeButtons() {
        document.querySelectorAll('[data-vision-global-mode]').forEach((btn) => {
            const m = btn.getAttribute('data-vision-global-mode');
            const isSel = m === state.globalMode;
            if (isSel) {
                const bg = m === 'active' ? 'bg-[#F58220] text-white shadow-xs' : (m === 'advisory' ? 'bg-blue-600 text-white shadow-xs' : 'bg-gray-800 text-white shadow-xs');
                btn.className = `vision-mode-btn px-3 py-1.5 rounded-lg text-xs font-bold transition ${bg}`;
            } else {
                btn.className = 'vision-mode-btn px-3 py-1.5 rounded-lg text-xs font-bold text-gray-600 hover:bg-gray-200/70 transition cursor-pointer';
            }
        });
        const badge = el('vision-mode-badge');
        if (badge) {
            badge.textContent = state.globalMode === 'active' ? 'Active Fusion' : (state.globalMode === 'advisory' ? 'Advisory' : 'Off');
            badge.className = state.globalMode === 'active'
                ? 'px-2.5 py-0.5 rounded-full text-[11px] font-bold bg-orange-100 text-[#F58220] uppercase'
                : (state.globalMode === 'advisory'
                    ? 'px-2.5 py-0.5 rounded-full text-[11px] font-bold bg-blue-100 text-blue-700 uppercase'
                    : 'px-2.5 py-0.5 rounded-full text-[11px] font-bold bg-gray-100 text-gray-600 uppercase');
        }
    }

    function updateToolButtons() {
        document.querySelectorAll('[data-vision-tool]').forEach((btn) => {
            const t = btn.getAttribute('data-vision-tool');
            const activeBg = t === 'teaching' ? 'bg-purple-600 text-white shadow-xs' : 'bg-[#F58220] text-white shadow-xs';
            const idleCls = t === 'teaching'
                ? 'vision-tool-btn px-2.5 py-1 rounded-lg text-xs font-bold bg-white border border-purple-200 text-purple-700 hover:bg-purple-50 cursor-pointer'
                : 'vision-tool-btn px-2.5 py-1 rounded-lg text-xs font-bold bg-white border border-gray-200 text-gray-700 hover:bg-gray-50 cursor-pointer';
            btn.className = t === state.tool
                ? `vision-tool-btn px-2.5 py-1 rounded-lg text-xs font-bold ${activeBg} cursor-pointer`
                : idleCls;
        });
    }

    function populateSelectors() {
        const camSel = el('vision-camera-select');
        if (camSel) {
            const prev = state.selectedCameraId;
            camSel.innerHTML = '';
            state.cameras.forEach((cam) => {
                const opt = document.createElement('option');
                opt.value = cam.id;
                const ipText = cam.ip ? ` (${cam.ip})` : '';
                opt.textContent = `${cam.name || cam.key}${ipText}`;
                camSel.appendChild(opt);
            });
            if (prev && state.cameras.some((c) => c.id === prev)) {
                camSel.value = prev;
            } else {
                const preferred = state.cameras.find((c) => c.ip === '192.168.1.128') || state.cameras[0];
                if (preferred) {
                    state.selectedCameraId = preferred.id;
                    camSel.value = preferred.id;
                }
            }
        }

        const seatSel = el('vision-seat-select');
        if (seatSel) {
            const prevSeat = seatSel.value;
            seatSel.innerHTML = '';
            const seats = state.seats.length
                ? state.seats
                : Array.from({ length: 12 }, (_, idx) => ({ id: `Seat ${idx + 1}`, label: `Seat ${idx + 1}`, preset: '' }));
            seats.forEach((s) => {
                const opt = document.createElement('option');
                opt.value = s.id;
                opt.textContent = s.label || s.id;
                seatSel.appendChild(opt);
            });
            if (prevSeat && Array.from(seatSel.options).some((o) => o.value === prevSeat)) {
                seatSel.value = prevSeat;
            }
        }

        const presetSel = el('vision-preset-select');
        if (presetSel) {
            const prevPreset = presetSel.value;
            presetSel.innerHTML = '<option value="">All Presets (Overview)</option>';
            const presets = new Set();
            state.seats.forEach((s) => {
                if (s.preset) presets.add(String(s.preset));
            });
            Array.from(presets).sort().forEach((p) => {
                const opt = document.createElement('option');
                opt.value = p;
                opt.textContent = `Preset ${p}`;
                presetSel.appendChild(opt);
            });
            if (prevPreset) presetSel.value = prevPreset;
        }
    }

    function syncCameraUi() {
        const cam = selectedCamera();
        const camModeSel = el('vision-cam-mode-select');
        const label = el('vision-stream-label');
        if (label) {
            label.textContent = cam ? `${cam.name || cam.key}${cam.ip ? ' • ' + cam.ip : ''}` : 'No camera detected';
        }
        if (cam && camModeSel) {
            camModeSel.value = cam.mode || state.globalMode || 'advisory';
        }
        loadCameraRoisFromMetadata(cam);
    }

    function loadCameraRoisFromMetadata(cam, rawRois, forceMaskReset = false) {
        const list = Array.isArray(rawRois) ? rawRois : (cam && Array.isArray(cam.rois) ? cam.rois : []);
        state.rois = list.map((r) => ({ ...r }));
        const masks = [];
        list.forEach((r) => {
            if (Array.isArray(r.masks)) {
                r.masks.forEach((m) => {
                    if (Array.isArray(m) && m.length === 4 && !masks.some((e) => e.join(',') === m.join(','))) {
                        masks.push([...m]);
                    }
                });
            }
        });
        if (forceMaskReset || masks.length > 0 || !state.hasStagedMask) {
            state.screenMasks = masks;
            if (masks.length > 0) state.hasStagedMask = false;
        }
        renderRoiList();
    }

    function disconnectStream() {
        if (state.pc) {
            try { state.pc.close(); } catch (e) {}
            state.pc = null;
        }
        const vid = el('vision-video');
        if (vid && vid.srcObject) {
            try {
                vid.srcObject.getTracks().forEach((t) => t.stop());
            } catch (e) {}
            vid.srcObject = null;
        }
        const mjpeg = el('vision-mjpeg');
        if (mjpeg) {
            mjpeg.removeAttribute('src');
            mjpeg.classList.add('hidden');
        }
        state.transport = 'none';
        updateTransportBadge();
    }

    function updateTransportBadge() {
        const badge = el('vision-transport-badge');
        if (!badge) return;
        const map = {
            webrtc: { text: 'WebRTC Live', cls: 'bg-emerald-100 text-emerald-700' },
            mjpeg: { text: 'MJPEG Relay', cls: 'bg-blue-100 text-blue-700' },
            none: { text: 'Connecting', cls: 'bg-slate-200 text-slate-700' },
        };
        const cfg = map[state.transport] || map.none;
        badge.textContent = cfg.text;
        badge.className = `px-2 py-0.5 rounded text-[10px] font-bold uppercase ${cfg.cls}`;
    }

    function fallbackToMjpeg(cam) {
        if (!state.active || !cam || !cam.mjpeg_url) return;
        if (state.pc) {
            try { state.pc.close(); } catch (e) {}
            state.pc = null;
        }
        const vid = el('vision-video');
        const mjpeg = el('vision-mjpeg');
        if (vid) {
            vid.classList.add('hidden');
            vid.style.display = 'none';
        }
        if (mjpeg) {
            mjpeg.classList.remove('hidden');
            mjpeg.style.display = 'block';
            mjpeg.src = `${cam.mjpeg_url}${cam.mjpeg_url.includes('?') ? '&' : '?'}t=${Date.now()}`;
        }
        state.transport = 'mjpeg';
        updateTransportBadge();
    }

    async function connectStream(cam) {
        disconnectStream();
        if (!state.active || !cam) return;
        const vid = el('vision-video');
        const mjpeg = el('vision-mjpeg');
        const empty = el('vision-empty-stream');
        if (empty) empty.classList.add('hidden');

        if (!cam.whep_url || typeof RTCPeerConnection === 'undefined') {
            fallbackToMjpeg(cam);
            return;
        }
        if (mjpeg) {
            mjpeg.classList.add('hidden');
            mjpeg.style.display = 'none';
        }
        if (vid) {
            vid.classList.remove('hidden');
            vid.style.display = 'block';
        }

        let switched = false;
        const fallbackTimer = setTimeout(() => {
            if (!switched && state.transport !== 'webrtc') {
                switched = true;
                fallbackToMjpeg(cam);
            }
        }, 2800);

        try {
            const pc = new RTCPeerConnection();
            state.pc = pc;
            pc.addTransceiver('video', { direction: 'recvonly' });
            pc.ontrack = (ev) => {
                if (state.pc !== pc) return;
                clearTimeout(fallbackTimer);
                if (vid && ev.streams && ev.streams[0]) {
                    vid.srcObject = ev.streams[0];
                    vid.play().catch(() => {});
                    state.transport = 'webrtc';
                    updateTransportBadge();
                }
            };
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);
            const res = await fetch(cam.whep_url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/sdp' },
                body: offer.sdp,
            });
            if (!res.ok) throw new Error(`WHEP ${res.status}`);
            const answer = await res.text();
            if (state.pc !== pc) return;
            await pc.setRemoteDescription({ type: 'answer', sdp: answer });
        } catch (err) {
            clearTimeout(fallbackTimer);
            if (!switched) fallbackToMjpeg(cam);
        }
    }

    async function loadCameras(reconnect = true) {
        try {
            const data = await apiJson('/api/sennheiser/vision/cameras');
            state.globalMode = data.mode || 'off';
            state.cameras = Array.isArray(data.cameras) ? data.cameras : [];
            state.seats = Array.isArray(data.seats) ? data.seats : [];
            populateSelectors();
            updateModeButtons();
            syncCameraUi();
            if (reconnect && state.active) {
                await connectStream(selectedCamera());
            }
            await refreshState();
        } catch (err) {
            setStatusHint(`Vision bridge offline: ${err.message}`, true);
        }
    }

    async function pushClientFrameIfNeeded(cam, visionState) {
        if (!state.active || !cam || state.frameInFlight) return;
        const nowSec = Date.now() / 1000;
        const fresh = visionState && visionState.wall_time && (nowSec - visionState.wall_time) < CLIENT_FRAME_STALE_SEC;
        if (fresh) return;

        const vid = el('vision-video');
        const mjpeg = el('vision-mjpeg');
        let srcEl = null;
        if (state.transport === 'webrtc' && vid && vid.videoWidth > 0 && vid.videoHeight > 0) {
            srcEl = vid;
        } else if (state.transport === 'mjpeg' && mjpeg && mjpeg.complete && mjpeg.naturalWidth > 0) {
            srcEl = mjpeg;
        }
        if (!srcEl) return;

        if (!state.scratchCanvas) {
            state.scratchCanvas = document.createElement('canvas');
            state.scratchCanvas.width = W_VISION;
            state.scratchCanvas.height = H_VISION;
            state.scratchCtx = state.scratchCanvas.getContext('2d', { willReadFrequently: true });
        }
        try {
            state.scratchCtx.drawImage(srcEl, 0, 0, W_VISION, H_VISION);
            const rgba = state.scratchCtx.getImageData(0, 0, W_VISION, H_VISION).data;
            const gray = new Uint8Array(W_VISION * H_VISION);
            for (let i = 0, p = 0; i < rgba.length; i += 4, p++) {
                gray[p] = (rgba[i] * 0.299 + rgba[i + 1] * 0.587 + rgba[i + 2] * 0.114) | 0;
            }
            let bin = '';
            const chunk = 8192;
            for (let i = 0; i < gray.length; i += chunk) {
                bin += String.fromCharCode.apply(null, gray.subarray(i, i + chunk));
            }
            const b64 = btoa(bin);
            state.frameInFlight = true;
            await apiJson('/api/sennheiser/accuracy/vision/frame', {
                method: 'POST',
                body: JSON.stringify({
                    camera: cam.id,
                    preset: el('vision-preset-select')?.value || '',
                    pixels: b64,
                }),
            });
        } catch (e) {
            // Best-effort browser fallback when RTSP stream isn't feeding directly
        } finally {
            state.frameInFlight = false;
        }
    }

    async function refreshState() {
        if (!state.active) return;
        const cam = selectedCamera();
        if (!cam) return;
        const reqCamId = cam.id;
        const reqEpoch = state.roiSyncEpoch;
        try {
            const st = await apiJson(`/api/sennheiser/accuracy/vision/state?camera=${encodeURIComponent(reqCamId)}`);
            if (!state.active || state.selectedCameraId !== reqCamId) return;
            state.visionData = st;
            if (Array.isArray(st.rois) && state.savingRois === 0 && reqEpoch === state.roiSyncEpoch && !state.dragging) {
                cam.rois = st.rois.map((r) => ({ ...r }));
                loadCameraRoisFromMetadata(cam, st.rois);
            }
            if (st.mode && st.mode !== state.globalMode && state.savingRois === 0 && reqEpoch === state.roiSyncEpoch) {
                state.globalMode = st.mode;
                updateModeButtons();
            }
            const camModeSel = el('vision-cam-mode-select');
            if (camModeSel && st.camera_mode && document.activeElement !== camModeSel) {
                camModeSel.value = st.camera_mode;
            }
            updateTelemetryBadges(st);
            pushClientFrameIfNeeded(cam, st);
        } catch (e) {
            // Silent retry on next poll
        }
    }

    function updateTelemetryBadges(st) {
        const warn = el('vision-warning-banner');
        if (warn) {
            if (st.warning) {
                warn.textContent = st.warning;
                warn.classList.remove('hidden');
            } else {
                warn.classList.add('hidden');
            }
        }
        const faces = Array.isArray(st.all_faces) ? st.all_faces.length : 0;
        const fBadge = el('vision-faces-badge');
        if (fBadge) fBadge.textContent = `${faces} ${faces === 1 ? 'face' : 'faces'}`;

        const speakingEntries = Object.entries(st.speaking || {}).filter(([, v]) => Boolean(v));
        const sBadge = el('vision-speech-badge');
        if (sBadge) {
            if (speakingEntries.length > 0) {
                sBadge.textContent = `Speaking (${speakingEntries.map(([s]) => s).join(', ')})`;
                sBadge.className = 'px-2 py-0.5 rounded bg-red-100 text-red-700 border border-red-200 text-[10px] font-extrabold uppercase animate-pulse';
            } else if (faces > 0) {
                sBadge.textContent = 'Face Quiet';
                sBadge.className = 'px-2 py-0.5 rounded bg-emerald-100 text-emerald-700 border border-emerald-200 text-[10px] font-bold uppercase';
            } else {
                sBadge.textContent = 'No Speech';
                sBadge.className = 'px-2 py-0.5 rounded bg-slate-100 text-slate-600 border border-slate-200 text-[10px] font-bold uppercase';
            }
        }

        const wBadge = el('vision-worker-badge');
        if (wBadge) {
            const age = st.wall_time ? (Date.now() / 1000 - st.wall_time) : 999;
            if (age < 2.0) {
                const backend = st.detector_backend === 'openvino-gpu'
                    ? 'OpenVINO GPU'
                    : (st.detector_backend === 'opencv-cpu' ? 'OpenCV CPU' : '');
                const source = st.source === 'rtsp' ? 'Backend RTSP Live' : 'Client Stream Live';
                wBadge.textContent = backend ? `${source} · ${backend}` : source;
                wBadge.className = 'px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-100 text-emerald-700';
            } else {
                wBadge.textContent = 'Idle';
                wBadge.className = 'px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-slate-100 text-slate-600';
            }
        }

        const auto = st.autotrack || {};
        const autoMode = auto.mode || 'off';
        const autoStatus = el('vision-autotrack-status');
        if (autoStatus) {
            autoStatus.textContent = autoMode === 'vision' ? 'Fast AI Active' : (autoMode === 'native' ? 'Native Active' : 'Off');
            autoStatus.className = autoMode === 'vision'
                ? 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-700 uppercase'
                : (autoMode === 'native'
                    ? 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-purple-100 text-purple-700 uppercase'
                    : 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-gray-100 text-gray-600 uppercase');
        }
        const vBtn = el('vision-track-vision-btn');
        const nBtn = el('vision-track-native-btn');
        if (vBtn) {
            vBtn.className = autoMode === 'vision'
                ? 'w-full px-3 py-2 rounded-xl border-2 border-emerald-500 bg-emerald-50 text-xs font-extrabold text-emerald-900 flex items-center justify-between cursor-pointer transition'
                : 'w-full px-3 py-2 rounded-xl border border-gray-200 bg-gray-50 hover:bg-emerald-50 text-xs font-extrabold text-gray-800 flex items-center justify-between cursor-pointer transition';
        }
        if (nBtn) {
            nBtn.className = autoMode === 'native'
                ? 'w-full px-3 py-2 rounded-xl border-2 border-purple-500 bg-purple-50 text-xs font-extrabold text-purple-900 flex items-center justify-between cursor-pointer transition'
                : 'w-full px-3 py-2 rounded-xl border border-gray-200 bg-gray-50 hover:bg-purple-50 text-xs font-extrabold text-gray-800 flex items-center justify-between cursor-pointer transition';
        }
        renderRoiList();
    }

    function _roiBadgeState(roi, st) {
        const seat = roi.seat;
        const isTeach = roi.kind === 'teaching' || roi.kind === 'blackboard';
        const llr = (st.scores || {})[seat];
        const mot = (st.motion || {})[seat];
        const occ = Boolean((st.occupancy || {})[seat]);
        const pose = (st.poses || {})[seat] || '';
        const bound = Array.isArray(roi.face_signature) && roi.face_signature.length > 0;
        const rej = Boolean((st.binding_rejected || {})[seat]);
        const spk = Boolean((st.speaking || {})[seat]) || (typeof llr === 'number' && llr > 1.0);
        let pillText = 'Empty Veto';
        let pillCls = 'px-1.5 py-0.5 rounded text-[10px] font-bold bg-gray-100 text-gray-500';
        if (rej) {
            pillText = 'Face Mismatch';
            pillCls = 'px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-100 text-amber-800';
        } else if (spk) {
            pillText = 'SPEAKING';
            pillCls = 'px-1.5 py-0.5 rounded text-[10px] font-bold bg-red-100 text-red-700';
        } else if (isTeach && occ) {
            pillText = pose === 'back_or_body' ? 'BACK / BODY' : 'TEACHER PRESENT';
            pillCls = 'px-1.5 py-0.5 rounded text-[10px] font-bold bg-purple-100 text-purple-800';
        } else if (occ) {
            pillText = 'Present';
            pillCls = 'px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-100 text-emerald-700';
        }
        const scoreStr = typeof llr === 'number' ? `LLR ${llr.toFixed(1)}` : '--';
        const motStr = typeof mot === 'number' ? `M ${mot.toFixed(2)}` : '';
        return { isTeach, bound, pillText, pillCls, telemetry: `[${roi.rect.join(', ')}] ${scoreStr} ${motStr}`.trim() };
    }

    function renderRoiList() {
        const listEl = el('vision-roi-list');
        const countEl = el('vision-roi-count');
        if (!listEl) return;
        const st = state.visionData || {};
        if (countEl) countEl.textContent = `${state.rois.length} ROIs • ${state.screenMasks.length} Masks`;
        if (!state.rois.length && !state.screenMasks.length) {
            if (state.lastRoiStructKey !== 'EMPTY') {
                listEl.innerHTML = '<div class="text-xs text-gray-400 py-3 text-center">No Seat ROIs drawn on this camera yet.</div>';
                state.lastRoiStructKey = 'EMPTY';
            }
            return;
        }
        const structKey = JSON.stringify({
            r: state.rois.map((r) => [
                r.seat,
                r.preset || '',
                r.kind || 'seat',
                r.rect,
                Boolean(Array.isArray(r.face_signature) && r.face_signature.length),
            ]),
            m: state.screenMasks,
        });

        if (structKey !== state.lastRoiStructKey) {
            let html = '';
            state.rois.forEach((roi, idx) => {
                const info = _roiBadgeState(roi, st);
                const kindTag = info.isTeach ? '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-purple-50 text-purple-700 border border-purple-200">Board Zone</span>' : '';
                const bindTag = info.bound ? '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-cyan-50 text-cyan-700 border border-cyan-200">Face Bound</span>' : '';
                html += `<div class="p-2.5 rounded-xl border ${info.isTeach ? 'border-purple-200 bg-purple-50/40' : 'border-gray-200 bg-gray-50'} flex items-center justify-between gap-2">
                    <div>
                        <div class="flex flex-wrap items-center gap-1.5">
                            <span class="text-xs font-extrabold text-gray-800">${roi.seat}</span>
                            ${kindTag}
                            ${bindTag}
                            <span data-roi-pill="${idx}" class="${info.pillCls}">${info.pillText}</span>
                        </div>
                        <div data-roi-telemetry="${idx}" class="text-[10px] text-gray-500 font-mono mt-0.5">${info.telemetry}</div>
                    </div>
                    <div class="flex items-center gap-1">
                        <button type="button" data-bind-roi-idx="${idx}" data-bind-action="${info.bound ? 'clear' : 'bind'}" class="px-2 py-1 rounded-lg bg-white border border-cyan-200 hover:bg-cyan-50 text-[11px] font-bold text-cyan-700 cursor-pointer">${info.bound ? 'Unbind' : 'Bind Face'}</button>
                        <button type="button" data-remove-roi-idx="${idx}" class="px-2 py-1 rounded-lg bg-white border border-gray-200 hover:bg-red-50 hover:text-red-600 text-[11px] font-bold text-gray-600 cursor-pointer">Delete</button>
                    </div>
                </div>`;
            });
            state.screenMasks.forEach((m, idx) => {
                html += `<div class="p-2 rounded-xl border border-orange-200 bg-orange-50/70 flex items-center justify-between gap-2">
                    <div>
                        <span class="text-xs font-bold text-orange-800">Screen Mask #${idx + 1}</span>
                        <div class="text-[10px] text-orange-600 font-mono">[${m.join(', ')}]</div>
                    </div>
                    <button type="button" data-remove-mask-idx="${idx}" class="px-2 py-1 rounded-lg bg-white border border-orange-200 hover:bg-red-50 hover:text-red-600 text-[11px] font-bold text-orange-700 cursor-pointer">Delete</button>
                </div>`;
            });
            listEl.innerHTML = html;
            state.lastRoiStructKey = structKey;
            return;
        }

        state.rois.forEach((roi, idx) => {
            const info = _roiBadgeState(roi, st);
            const pillEl = listEl.querySelector(`[data-roi-pill="${idx}"]`);
            if (pillEl) {
                if (pillEl.textContent !== info.pillText) pillEl.textContent = info.pillText;
                if (pillEl.className !== info.pillCls) pillEl.className = info.pillCls;
            }
            const telEl = listEl.querySelector(`[data-roi-telemetry="${idx}"]`);
            if (telEl && telEl.textContent !== info.telemetry) {
                telEl.textContent = info.telemetry;
            }
        });
    }

    async function saveCameraRois() {
        const cam = selectedCamera();
        if (!cam) return;
        const preset = el('vision-preset-select')?.value || '';
        const payloadRois = state.rois.map((r) => ({
            camera: cam.id,
            preset: r.preset != null ? r.preset : preset,
            seat: r.seat,
            rect: r.rect,
            masks: state.screenMasks.slice(0, 8),
            kind: r.kind || 'seat',
            ...(Array.isArray(r.face_signature) ? { face_signature: r.face_signature } : {}),
            ...(r.signature_threshold != null ? { signature_threshold: r.signature_threshold } : {}),
        }));
        state.roiSyncEpoch += 1;
        state.savingRois += 1;
        try {
            const res = await apiJson('/api/sennheiser/accuracy/vision/rois', {
                method: 'POST',
                body: JSON.stringify({
                    action: 'set_camera',
                    camera: cam.id,
                    preset: '',
                    rois: payloadRois,
                }),
            });
            if (Array.isArray(res.rois)) {
                state.rois = res.rois;
                cam.rois = res.rois.map((r) => ({ ...r }));
                if (!res.rois.length) state.screenMasks = [];
            }
            state.hasStagedMask = false;
            state.lastRoiStructKey = '';
            setStatusHint(payloadRois.length ? 'ROIs & screen masks saved to ConfDeck.' : 'Updated ROIs on ConfDeck.');
            renderRoiList();
        } catch (err) {
            setStatusHint(`Save ROI failed: ${err.message}`, true);
            await loadCameras(false);
        } finally {
            state.savingRois = Math.max(0, state.savingRois - 1);
            await refreshState();
        }
    }

    function drawLoop() {
        if (!state.active) return;
        const canvas = el('vision-canvas');
        if (canvas) {
            const ctx = canvas.getContext('2d');
            const cw = canvas.width;
            const ch = canvas.height;
            const sx = cw / W_VISION;
            const sy = ch / H_VISION;
            ctx.clearRect(0, 0, cw, ch);
            const st = state.visionData || {};

            // Screen Masks
            state.screenMasks.forEach((m, idx) => {
                const [x, y, w, h] = m;
                ctx.save();
                ctx.fillStyle = 'rgba(249, 115, 22, 0.20)';
                ctx.strokeStyle = '#f97316';
                ctx.lineWidth = 2;
                ctx.setLineDash([5, 4]);
                ctx.fillRect(x * sx, y * sy, w * sx, h * sy);
                ctx.strokeRect(x * sx, y * sy, w * sx, h * sy);
                ctx.fillStyle = '#fed7aa';
                ctx.font = 'bold 11px sans-serif';
                ctx.fillText(`MASK #${idx + 1}`, x * sx + 4, y * sy + 14);
                ctx.restore();
            });

            // Seat & Teaching Head ROIs
            state.rois.forEach((roi) => {
                const [x, y, w, h] = roi.rect;
                const seat = roi.seat;
                const isTeach = roi.kind === 'teaching' || roi.kind === 'blackboard';
                const llr = (st.scores || {})[seat];
                const occ = Boolean((st.occupancy || {})[seat]);
                const pose = (st.poses || {})[seat] || '';
                const rej = Boolean((st.binding_rejected || {})[seat]);
                const spk = Boolean((st.speaking || {})[seat]) || (typeof llr === 'number' && llr > 1.0);
                let stroke = isTeach ? '#a855f7' : '#94a3b8';
                let fill = isTeach ? 'rgba(168, 85, 247, 0.12)' : 'rgba(148, 163, 184, 0.12)';
                let tag = isTeach ? `${seat} [BOARD] (CLEAR)` : `${seat} (EMPTY)`;
                if (rej) {
                    stroke = '#f59e0b';
                    fill = 'rgba(245, 158, 11, 0.18)';
                    tag = `${seat} MISMATCH`;
                } else if (spk) {
                    stroke = '#ef4444';
                    fill = 'rgba(239, 68, 68, 0.18)';
                    tag = isTeach ? `${seat} [TEACHER SPEAKING]` : `${seat} SPEAKING`;
                } else if (occ) {
                    stroke = isTeach ? '#9333ea' : '#10b981';
                    fill = isTeach ? 'rgba(147, 51, 234, 0.18)' : 'rgba(16, 185, 129, 0.14)';
                    tag = isTeach ? `${seat} [${pose === 'back_or_body' ? 'BACK/BODY' : 'TEACHER'}]` : `${seat} PRESENT`;
                }
                ctx.save();
                ctx.strokeStyle = stroke;
                ctx.fillStyle = fill;
                ctx.lineWidth = spk ? 3 : 2;
                if (!occ && !spk) ctx.setLineDash([6, 4]);
                ctx.fillRect(x * sx, y * sy, w * sx, h * sy);
                ctx.strokeRect(x * sx, y * sy, w * sx, h * sy);
                ctx.fillStyle = stroke;
                ctx.fillRect(x * sx, Math.max(0, y * sy - 18), Math.max(92, tag.length * 7), 18);
                ctx.fillStyle = '#ffffff';
                ctx.font = 'bold 11px sans-serif';
                ctx.fillText(tag, x * sx + 4, Math.max(13, y * sy - 5));
                ctx.restore();
            });

            // World-anchored Locked Blackboard ROI (tracks physical board during PTZ pans)
            const lockedRoi = st.tracking?.locked_roi;
            if (Array.isArray(lockedRoi) && lockedRoi.length >= 4) {
                const [lx, ly, lw, lh] = lockedRoi;
                ctx.save();
                ctx.strokeStyle = '#06b6d4';
                ctx.lineWidth = 2;
                ctx.setLineDash([4, 4]);
                ctx.strokeRect(lx * sx, ly * sy, lw * sx, lh * sy);
                ctx.fillStyle = '#06b6d4';
                ctx.font = 'bold 10px sans-serif';
                ctx.fillText('LOCKED BOARD', lx * sx + 4, Math.min(canvas.height - 4, (ly + lh) * sy - 4));
                ctx.restore();
            }

            // Detected Faces & Rear Heads (tagged with live speaking / quiet state when matched to a Seat ROI)
            const allFaces = Array.isArray(st.all_faces) ? st.all_faces : [];
            const autoMode = st.autotrack?.mode || 'off';
            const seatByFaceKey = {};
            Object.entries(st.faces || {}).forEach(([seat, box]) => {
                if (Array.isArray(box) && box.length >= 4) {
                    seatByFaceKey[box.slice(0, 4).join(',')] = seat;
                }
            });
            allFaces.forEach((f, idx) => {
                if (!Array.isArray(f) || f.length < 4) return;
                const [fx, fy, fw, fh] = f;
                const key = f.slice(0, 4).join(',');
                const matchedSeat = seatByFaceKey[key] || '';
                const isSpeaking = Boolean(matchedSeat && (st.speaking || {})[matchedSeat]);
                const motVal = matchedSeat && typeof (st.motion || {})[matchedSeat] === 'number'
                    ? ` M:${(st.motion || {})[matchedSeat].toFixed(2)}`
                    : '';
                const isTarget = autoMode === 'vision' && idx === 0;
                const color = isSpeaking ? '#ef4444' : (isTarget ? '#22c55e' : (matchedSeat ? '#10b981' : '#06b6d4'));
                const label = isSpeaking
                    ? `SPEAKING${motVal}`
                    : (isTarget ? `TRACK TARGET${motVal}` : (matchedSeat ? `FACE QUIET${motVal}` : 'FACE'));
                ctx.save();
                ctx.strokeStyle = color;
                ctx.lineWidth = isSpeaking || isTarget ? 3 : 2;
                ctx.strokeRect(fx * sx, fy * sy, fw * sx, fh * sy);
                ctx.fillStyle = color;
                ctx.fillRect(fx * sx, Math.max(0, fy * sy - 16), Math.max(46, label.length * 6.5), 16);
                ctx.fillStyle = '#ffffff';
                ctx.font = 'bold 10px sans-serif';
                ctx.fillText(label, fx * sx + 3, Math.max(11, fy * sy - 4));
                ctx.restore();
            });

            // Active Drag Box
            if (state.dragging && state.dragCurrent) {
                const x1 = Math.min(state.dragging.x, state.dragCurrent.x);
                const y1 = Math.min(state.dragging.y, state.dragCurrent.y);
                const w = Math.abs(state.dragCurrent.x - state.dragging.x);
                const h = Math.abs(state.dragCurrent.y - state.dragging.y);
                ctx.save();
                ctx.strokeStyle = state.tool === 'mask' ? '#f97316' : '#f58220';
                ctx.lineWidth = 2;
                ctx.setLineDash([4, 3]);
                ctx.strokeRect(x1 * sx, y1 * sy, w * sx, h * sy);
                ctx.restore();
            }
        }
        state.rafId = requestAnimationFrame(drawLoop);
    }

    function canvasPoint(ev) {
        const canvas = el('vision-canvas');
        const rect = canvas.getBoundingClientRect();
        const x = Math.max(0, Math.min(W_VISION - 1, Math.round(((ev.clientX - rect.left) / rect.width) * W_VISION)));
        const y = Math.max(0, Math.min(H_VISION - 1, Math.round(((ev.clientY - rect.top) / rect.height) * H_VISION)));
        return { x, y };
    }

    function bindUiEvents() {
        const canvas = el('vision-canvas');
        if (canvas) {
            canvas.addEventListener('pointerdown', (ev) => {
                if (state.tool === 'inspect') return;
                state.dragging = canvasPoint(ev);
                state.dragCurrent = state.dragging;
            });
            window.addEventListener('pointermove', (ev) => {
                if (!state.dragging) return;
                state.dragCurrent = canvasPoint(ev);
            });
            window.addEventListener('pointerup', () => {
                if (!state.dragging || !state.dragCurrent) return;
                const x = Math.max(0, Math.min(state.dragging.x, state.dragCurrent.x));
                const y = Math.max(0, Math.min(state.dragging.y, state.dragCurrent.y));
                const w = Math.min(W_VISION - x, Math.abs(state.dragCurrent.x - state.dragging.x));
                const h = Math.min(H_VISION - y, Math.abs(state.dragCurrent.y - state.dragging.y));
                const tool = state.tool;
                state.dragging = null;
                state.dragCurrent = null;
                if (w < 8 || h < 8) return;

                if (tool === 'roi' || tool === 'teaching') {
                    const isTeach = tool === 'teaching';
                    const seat = isTeach ? 'Teaching Zone' : (el('vision-seat-select')?.value || `Seat ${state.rois.length + 1}`);
                    const preset = el('vision-preset-select')?.value || '';
                    const kind = isTeach ? 'teaching' : 'seat';
                    const idx = state.rois.findIndex((r) => r.seat === seat && (r.preset || '') === preset);
                    const entry = { camera: state.selectedCameraId, preset, seat, rect: [x, y, w, h], masks: state.screenMasks, kind };
                    state.roiSyncEpoch += 1;
                    if (idx >= 0) state.rois[idx] = entry;
                    else state.rois.push(entry);
                    saveCameraRois();
                } else if (tool === 'mask') {
                    state.roiSyncEpoch += 1;
                    if (state.screenMasks.length >= 8) state.screenMasks.shift();
                    state.screenMasks.push([x, y, w, h]);
                    if (state.rois.length) saveCameraRois();
                    else {
                        state.hasStagedMask = true;
                        state.lastRoiStructKey = '';
                        setStatusHint('Screen mask staged (draw 1 Seat ROI to persist masks).');
                        renderRoiList();
                    }
                }
            });
        }

        document.querySelectorAll('[data-vision-global-mode]').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const mode = btn.getAttribute('data-vision-global-mode');
                state.roiSyncEpoch += 1;
                state.savingRois += 1;
                try {
                    await apiJson('/api/sennheiser/accuracy/vision/mode', {
                        method: 'POST',
                        body: JSON.stringify({ mode }),
                    });
                    state.globalMode = mode;
                    updateModeButtons();
                    setStatusHint(`Vision mode set to ${mode}.`);
                } catch (err) {
                    setStatusHint(`Mode change failed: ${err.message}`, true);
                } finally {
                    state.savingRois = Math.max(0, state.savingRois - 1);
                    await refreshState();
                }
            });
        });

        document.querySelectorAll('[data-vision-tool]').forEach((btn) => {
            btn.addEventListener('click', () => {
                state.tool = btn.getAttribute('data-vision-tool');
                updateToolButtons();
            });
        });

        el('vision-roi-list')?.addEventListener('click', async (ev) => {
            const bindBtn = ev.target.closest('[data-bind-roi-idx]');
            if (bindBtn) {
                const idx = Number(bindBtn.getAttribute('data-bind-roi-idx'));
                const act = bindBtn.getAttribute('data-bind-action') || 'bind';
                const roi = state.rois[idx];
                const cam = selectedCamera();
                if (!roi || !cam) return;
                state.roiSyncEpoch += 1;
                state.savingRois += 1;
                try {
                    await apiJson('/api/sennheiser/accuracy/vision/bind-face', {
                        method: 'POST',
                        body: JSON.stringify({ camera: cam.id, seat: roi.seat, preset: roi.preset || '', action: act }),
                    });
                    state.lastRoiStructKey = '';
                    await loadCameras(false);
                    setStatusHint(act === 'clear' ? `Face binding cleared for ${roi.seat}.` : `Bound live face to ${roi.seat}.`);
                } catch (err) {
                    setStatusHint(`Bind failed: ${err.message}`, true);
                } finally {
                    state.savingRois = Math.max(0, state.savingRois - 1);
                }
                return;
            }
            const delRoiBtn = ev.target.closest('[data-remove-roi-idx]');
            if (delRoiBtn) {
                const idx = Number(delRoiBtn.getAttribute('data-remove-roi-idx'));
                if (Number.isInteger(idx) && idx >= 0 && idx < state.rois.length) {
                    state.rois.splice(idx, 1);
                    if (!state.rois.length) {
                        state.screenMasks = [];
                        state.hasStagedMask = false;
                    }
                    state.lastRoiStructKey = '';
                    renderRoiList();
                    saveCameraRois();
                }
                return;
            }
            const delMaskBtn = ev.target.closest('[data-remove-mask-idx]');
            if (delMaskBtn) {
                const idx = Number(delMaskBtn.getAttribute('data-remove-mask-idx'));
                if (Number.isInteger(idx) && idx >= 0 && idx < state.screenMasks.length) {
                    state.screenMasks.splice(idx, 1);
                    state.lastRoiStructKey = '';
                    if (state.rois.length) {
                        saveCameraRois();
                    } else {
                        if (!state.screenMasks.length) state.hasStagedMask = false;
                        renderRoiList();
                    }
                }
            }
        });

        el('vision-camera-select')?.addEventListener('change', async (ev) => {
            state.selectedCameraId = ev.target.value;
            state.screenMasks = [];
            state.hasStagedMask = false;
            state.lastRoiStructKey = '';
            state.roiSyncEpoch += 1;
            syncCameraUi();
            await connectStream(selectedCamera());
            await refreshState();
        });

        el('vision-cam-mode-select')?.addEventListener('change', async (ev) => {
            const cam = selectedCamera();
            if (!cam) return;
            state.roiSyncEpoch += 1;
            state.savingRois += 1;
            try {
                await apiJson('/api/sennheiser/accuracy/vision/mode', {
                    method: 'POST',
                    body: JSON.stringify({ camera: cam.id, camera_mode: ev.target.value }),
                });
                cam.mode = ev.target.value;
                if (ev.target.value !== 'off' && state.globalMode === 'off') {
                    state.globalMode = ev.target.value;
                    updateModeButtons();
                }
                setStatusHint(`Camera mode set to ${ev.target.value}.`);
            } catch (err) {
                setStatusHint(`Camera mode update failed: ${err.message}`, true);
            } finally {
                state.savingRois = Math.max(0, state.savingRois - 1);
                await refreshState();
            }
        });

        el('vision-sync-btn')?.addEventListener('click', () => loadCameras(true));

        el('vision-clear-masks-btn')?.addEventListener('click', () => {
            state.screenMasks = [];
            state.hasStagedMask = false;
            state.lastRoiStructKey = '';
            if (state.rois.length) saveCameraRois();
            else renderRoiList();
        });

        el('vision-clear-rois-btn')?.addEventListener('click', async () => {
            const cam = selectedCamera();
            if (!cam) return;
            state.roiSyncEpoch += 1;
            state.savingRois += 1;
            state.rois = [];
            state.screenMasks = [];
            state.hasStagedMask = false;
            cam.rois = [];
            state.lastRoiStructKey = '';
            renderRoiList();
            try {
                await apiJson('/api/sennheiser/accuracy/vision/rois', {
                    method: 'POST',
                    body: JSON.stringify({ action: 'clear_camera', camera: cam.id }),
                });
                setStatusHint('Cleared ROIs for selected camera.');
            } catch (err) {
                setStatusHint(`Clear failed: ${err.message}`, true);
                await loadCameras(false);
            } finally {
                state.savingRois = Math.max(0, state.savingRois - 1);
                await refreshState();
            }
        });

        async function triggerTrack(mode) {
            const cam = selectedCamera();
            if (!cam) return;
            state.roiSyncEpoch += 1;
            state.savingRois += 1;
            try {
                await apiJson('/api/sennheiser/accuracy/vision/track', {
                    method: 'POST',
                    body: JSON.stringify({ camera: cam.id, mode, enabled: mode !== 'off' }),
                });
                state.lastRoiStructKey = '';
                await loadCameras(false);
                setStatusHint(mode === 'off' ? 'Face tracking stopped.' : `Face tracking active (${mode}).`);
            } catch (err) {
                setStatusHint(`Tracking error: ${err.message}`, true);
            } finally {
                state.savingRois = Math.max(0, state.savingRois - 1);
                await refreshState();
            }
        }

        el('vision-track-vision-btn')?.addEventListener('click', () => triggerTrack('vision'));
        el('vision-track-native-btn')?.addEventListener('click', () => triggerTrack('native'));
        el('vision-track-stop-btn')?.addEventListener('click', () => triggerTrack('off'));
    }

    function activate() {
        if (state.active) return;
        state.active = true;
        loadCameras(true);
        clearInterval(state.pollTimer);
        state.pollTimer = setInterval(refreshState, 80);
        if (!state.rafId) state.rafId = requestAnimationFrame(drawLoop);
    }

    function deactivate() {
        if (!state.active) return;
        state.active = false;
        clearInterval(state.pollTimer);
        state.pollTimer = null;
        if (state.rafId) {
            cancelAnimationFrame(state.rafId);
            state.rafId = null;
        }
        disconnectStream();
    }

    function isPageVisible() {
        const page = el('page-vision');
        if (!page) return false;
        if (page.classList.contains('active-page')) return true;
        return !page.classList.contains('hidden') && window.getComputedStyle(page).display !== 'none';
    }

    function syncVisibility() {
        if (isPageVisible()) activate();
        else deactivate();
    }

    function init() {
        bindUiEvents();
        updateToolButtons();
        const page = el('page-vision');
        if (!page) return;
        const obs = new MutationObserver(syncVisibility);
        obs.observe(page, { attributes: true, attributeFilter: ['class', 'style'] });
        document.querySelectorAll('.nav-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                if (btn.id === 'nav-vision') {
                    setTimeout(() => activate(), 10);
                } else {
                    setTimeout(syncVisibility, 10);
                }
            });
        });
        window.addEventListener('keydown', (ev) => {
            if (ev.key === 'Escape' && state.dragging) {
                state.dragging = null;
                state.dragCurrent = null;
            }
        });
        syncVisibility();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
