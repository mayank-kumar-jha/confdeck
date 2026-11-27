// public/js/modules/camera_presets_module.js
// Enterprise PTZ & Camera Presets Management Module (Direct Hardware Integration)

(function (window) {
    'use strict';

    // Module State
    const state = {
        currentCamera: 'camera_1',
        cameraList: [],
        pan: 0,
        tilt: 0,
        zoom: 1.4,
        moveSpeed: 5,
        isChangingPreset: false, // Mode toggle: false = Recall, true = Save/Change
        isDragging: false,
        dragStartX: 0,
        dragStartY: 0,
        currentDragDir: null,
        dragThrottleTimer: null,
        peerConnection: null,
        reconnectTimer: null,
        webrtcAvailable: false,
        imageSettings: {
            brightness: 50,
            sharpness: 26,
            contrast: 25,
            saturation: 50
        },
        saveSettingsTimeout: null,
        snapshotFallbackTimer: null,
        previewWatchdog: null,
        streamGeneration: 0
    };

    // DOM Element References (cached on initialization)
    let dom = {};

    function cacheDomElements() {
        dom = {
            container: document.getElementById('content-presets'),
            cameraSelectorTabs: document.getElementById('ptz-camera-selector-tabs'),
            cameraSelectorDropdown: document.getElementById('ptz-camera-selector-dropdown'),
            currentCamTitle: document.getElementById('ptz-current-cam-title'),
            currentCamBadge: document.getElementById('ptz-current-cam-badge'),
            previewContainer: document.getElementById('ptz-preview-container'),
            video: document.getElementById('ptz-preview-video'),
            previewImg: document.getElementById('ptz-preview-img'),
            videoPlaceholder: document.getElementById('ptz-video-placeholder'),
            dragOverlay: document.getElementById('ptz-drag-overlay'),
            dragIndicator: document.getElementById('ptz-drag-indicator'),
            dragArrow: document.getElementById('ptz-drag-arrow'),
            overlayBadge: document.getElementById('ptz-overlay-badge'),
            statusResolution: document.getElementById('ptz-status-res'),
            statusBitrate: document.getElementById('ptz-status-bitrate'),
            statusFps: document.getElementById('ptz-status-fps'),
            // Sliders
            sliderBrightness: document.getElementById('ptz-slider-brightness'),
            valBrightness: document.getElementById('ptz-val-brightness'),
            sliderSharpness: document.getElementById('ptz-slider-sharpness'),
            valSharpness: document.getElementById('ptz-val-sharpness'),
            sliderContrast: document.getElementById('ptz-slider-contrast'),
            valContrast: document.getElementById('ptz-val-contrast'),
            sliderSaturation: document.getElementById('ptz-slider-saturation'),
            valSaturation: document.getElementById('ptz-val-saturation'),
            // D-Pad
            dpadBtns: document.querySelectorAll('.ptz-dpad-btn'),
            homeBtn: document.getElementById('ptz-home-btn'),
            // Speed & Zoom
            sliderSpeed: document.getElementById('ptz-slider-speed'),
            valSpeed: document.getElementById('ptz-val-speed'),
            sliderZoom: document.getElementById('ptz-slider-zoom'),
            valZoom: document.getElementById('ptz-val-zoom'),
            btnZoomMinus: document.getElementById('ptz-btn-zoom-minus'),
            btnZoomPlus: document.getElementById('ptz-btn-zoom-plus'),
            // Preset Dropdown & Controls (in Pan/Tilt card)
            controlPresetSelect: document.getElementById('ptz-control-preset-select'),
            btnControlRecall: document.getElementById('ptz-btn-control-recall'),
            btnControlSave: document.getElementById('ptz-btn-control-save'),
            presetRangeHint: document.getElementById('ptz-preset-range-hint'),
            // Quick Presets
            quickPresetTitle: document.getElementById('ptz-quick-preset-title'),
            quickPresetGrid: document.getElementById('ptz-quick-preset-grid'),
            presetChangeToggleBtn: document.getElementById('ptz-change-preset-btn'),
            presetQuickBtns: document.querySelectorAll('.ptz-preset-quick-btn'),
            // Direct Presets
            directPresetTitle: document.getElementById('ptz-direct-preset-title'),
            directPresetBadge: document.getElementById('ptz-direct-preset-badge'),
            directPresetInput: document.getElementById('ptz-direct-preset-input'),
            btnDirectRecall: document.getElementById('ptz-btn-direct-recall'),
            btnDirectSave: document.getElementById('ptz-btn-direct-save'),
            // Toast container
            toastContainer: document.getElementById('ptz-toast-container')
        };
    }

    // -------------------------------------------------------------------------
    // Toast Notification System
    // -------------------------------------------------------------------------
    function showToast(message, type = 'info') {
        if (!dom.toastContainer) return;
        const toast = document.createElement('div');
        toast.className = `ptz-toast flex items-center gap-3 px-4 py-3 rounded-xl shadow-xl border text-sm font-semibold transition-all transform duration-300 translate-y-2 opacity-0 select-none z-50 ${
            type === 'success' ? 'bg-emerald-900/90 text-white border-emerald-500/50 shadow-emerald-900/20' :
            type === 'error' ? 'bg-rose-900/90 text-white border-rose-500/50 shadow-rose-900/20' :
            type === 'warning' ? 'bg-amber-900/90 text-white border-amber-500/50 shadow-amber-900/20' :
            'bg-slate-900/90 text-white border-slate-700 shadow-black/40'
        }`;

        const icon = type === 'success' ? '✓' : type === 'error' ? '✕' : type === 'warning' ? '⚠' : 'ℹ';
        toast.innerHTML = `<span class="w-6 h-6 rounded-full flex items-center justify-center bg-white/20 text-xs font-bold">${icon}</span><span>${message}</span>`;
        
        dom.toastContainer.appendChild(toast);
        requestAnimationFrame(() => {
            toast.classList.remove('translate-y-2', 'opacity-0');
        });

        setTimeout(() => {
            toast.classList.add('opacity-0', '-translate-y-2');
            setTimeout(() => toast.remove(), 300);
        }, 2600);
    }

    // -------------------------------------------------------------------------
    // Camera List & Selector Dropdown
    // -------------------------------------------------------------------------
    async function loadCameras() {
        try {
            const res = await fetch('/api/ptz/cameras');
            if (res.ok) {
                const data = await res.json();
                state.cameraList = data.cameras || [];
                if (data.webrtc_available !== undefined) {
                    state.webrtcAvailable = !!data.webrtc_available;
                }
                renderCameraSelector();
                updateCameraHeaderInfo();
                updatePresetLimitsAndDropdown();
            }
        } catch (e) {
            console.warn('[PTZ] Could not load camera list:', e);
            state.cameraList = Array.from({ length: 9 }, (_, i) => ({
                id: i + 1,
                key: `camera_${i + 1}`,
                cam_name: `Camera ${i + 1}`,
                brand: i === 1 ? 'CANON' : 'LUMENS',
                ip: '',
                configured: false
            }));
            renderCameraSelector();
            updateCameraHeaderInfo();
            updatePresetLimitsAndDropdown();
        }
    }

    function renderCameraSelector() {
        if (dom.cameraSelectorDropdown) {
            dom.cameraSelectorDropdown.innerHTML = '';
            state.cameraList.forEach(cam => {
                const opt = document.createElement('option');
                opt.value = cam.key;
                const ipText = cam.ip ? ` • ${cam.ip}` : '';
                opt.textContent = `${cam.cam_name || `Cam ${cam.id}`} (${cam.brand}${ipText})`;
                if (cam.key === state.currentCamera) {
                    opt.selected = true;
                }
                dom.cameraSelectorDropdown.appendChild(opt);
            });
            dom.cameraSelectorDropdown.value = state.currentCamera;

            if (!dom.cameraSelectorDropdown._hasChangeListener) {
                dom.cameraSelectorDropdown._hasChangeListener = true;
                dom.cameraSelectorDropdown.addEventListener('change', (e) => {
                    selectCamera(e.target.value);
                });
            }
        }

        if (dom.cameraSelectorTabs) {
            dom.cameraSelectorTabs.innerHTML = '';
            state.cameraList.forEach(cam => {
                const btn = document.createElement('button');
                const isActive = cam.key === state.currentCamera;
                btn.className = `ptz-cam-tab px-3.5 py-1.5 rounded-xl text-xs font-bold transition-all duration-200 flex items-center gap-2 border select-none cursor-pointer ${
                    isActive
                        ? 'bg-gradient-to-r from-[#F58220] to-[#ff9b44] text-white border-transparent shadow-md shadow-orange-500/20 scale-[1.02]'
                        : 'bg-white hover:bg-gray-100 text-gray-700 border-gray-200 shadow-sm'
                }`;

                const dotClass = cam.configured ? 'bg-emerald-500' : 'bg-gray-300';
                btn.innerHTML = `
                    <span class="w-2 h-2 rounded-full ${dotClass}"></span>
                    <span>${cam.cam_name || `Cam ${cam.id}`}</span>
                `;

                btn.title = cam.ip ? `${cam.brand} - ${cam.ip}` : `${cam.brand} (Not Configured)`;

                btn.addEventListener('click', () => {
                    selectCamera(cam.key);
                });

                dom.cameraSelectorTabs.appendChild(btn);
            });
        }
    }

    function selectCamera(cameraKey) {
        if (state.currentCamera === cameraKey) return;
        sendStop();
        state.currentCamera = cameraKey;
        state.pan = 0;
        state.tilt = 0;
        state.zoom = 1.4;

        if (dom.cameraSelectorDropdown) {
            dom.cameraSelectorDropdown.value = cameraKey;
        }

        renderCameraSelector();
        updateCameraHeaderInfo();
        updatePresetLimitsAndDropdown();
        loadImageSettings(cameraKey);
        loadStreamInfo(cameraKey);
        startStream(cameraKey);
        updateOverlayBadge();

        const cam = state.cameraList.find(c => c.key === cameraKey);
        const name = cam ? cam.cam_name : cameraKey;
        showToast(`${name} selected`, 'info');
    }

    function updateCameraHeaderInfo() {
        const cam = state.cameraList.find(c => c.key === state.currentCamera) || {
            cam_name: state.currentCamera.replace('_', ' ').toUpperCase(),
            brand: 'LUMENS',
            ip: ''
        };

        if (dom.currentCamTitle) {
            dom.currentCamTitle.textContent = `${cam.cam_name} (${cam.brand})`;
        }
        if (dom.currentCamBadge) {
            dom.currentCamBadge.textContent = cam.ip ? `IP: ${cam.ip}` : 'IP: Not Configured';
        }
    }

    // -------------------------------------------------------------------------
    // Real-Time Hardware Image Filter Calibration (Brightness, Contrast, Saturation, Sharpness)
    // Synchronized live with UI stream and backend hardware controls
    // -------------------------------------------------------------------------
    function applyImageFilters() {
        const b = state.imageSettings.brightness !== undefined ? state.imageSettings.brightness : 50;
        const c = state.imageSettings.contrast !== undefined ? state.imageSettings.contrast : 25;
        const sh = state.imageSettings.sharpness !== undefined ? state.imageSettings.sharpness : 26;
        const sat = state.imageSettings.saturation !== undefined ? state.imageSettings.saturation : 50;

        // Brightness: default 50 -> 1.0 (range 0 to 100 -> 0.0 to 2.0)
        const bCss = (b / 50).toFixed(2);

        // Contrast: default 25 in UI slider -> 1.0 neutral
        const cBase = Math.max(0.2, 1.0 + (c - 25) / 50);
        // Sharpness: default 26 -> micro-contrast and edge clarity boost
        const shFactor = ((sh - 26) / 100) * 0.35;
        const cFinal = Math.max(0.2, cBase + shFactor).toFixed(2);

        // Saturation: default 50 -> 1.0 (range 0 to 100 -> 0.0 grayscale to 2.0 vivid)
        const satCss = (sat / 50).toFixed(2);

        const filterString = `brightness(${bCss}) contrast(${cFinal}) saturate(${satCss})`;

        if (dom.video) dom.video.style.filter = filterString;
        if (dom.previewImg) dom.previewImg.style.filter = filterString;
    }

    function clearUiFilters() {
        applyImageFilters();
    }

    // -------------------------------------------------------------------------
    // Live Camera Stream Management (MediaMTX WebRTC WHEP + MJPEG Fallback)
    // Modeled directly on viewer.html for 0-latency hardware performance
    // -------------------------------------------------------------------------
    function startSnapshotFallback(cameraKey) {
        if (state.snapshotFallbackTimer) {
            clearInterval(state.snapshotFallbackTimer);
            state.snapshotFallbackTimer = null;
        }
        console.log('[PTZ] Activating high-res snapshot refresh loop for:', cameraKey);
        const poll = () => {
            if (state.currentCamera !== cameraKey) return;
            const tempImg = new Image();
            tempImg.onload = () => {
                if (state.currentCamera !== cameraKey) return;
                if (dom.previewImg) {
                    dom.previewImg.src = tempImg.src;
                    dom.previewImg.classList.remove('hidden');
                    dom.previewImg.style.display = 'block';
                }
                if (dom.videoPlaceholder) dom.videoPlaceholder.classList.add('hidden');
                clearUiFilters();
            };
            tempImg.src = `/api/ptz/snapshot?camera=${cameraKey}&_t=${Date.now()}`;
        };
        poll();
        state.snapshotFallbackTimer = setInterval(poll, 120); // ~8 FPS smooth refresh
    }

    async function startStream(cameraKey) {
        stopStream();
        const gen = ++state.streamGeneration;

        const host = window.location.hostname || '127.0.0.1';
        clearUiFilters();

        const startDirectFallback = () => {
            if (gen !== state.streamGeneration || state.currentCamera !== cameraKey || !dom.previewImg) return;
            dom.previewImg.classList.remove('hidden');
            dom.previewImg.style.display = 'block';
            if (dom.video) {
                dom.video.classList.add('hidden');
                dom.video.style.display = 'none';
            }
            if (dom.videoPlaceholder) dom.videoPlaceholder.classList.add('hidden');

            dom.previewImg.onload = () => {
                if (gen !== state.streamGeneration) return;
                if (dom.videoPlaceholder) dom.videoPlaceholder.classList.add('hidden');
                dom.previewImg.classList.remove('hidden');
                dom.previewImg.style.display = 'block';
                clearUiFilters();
            };
            dom.previewImg.onerror = () => {
                if (gen !== state.streamGeneration) return;
                console.warn('[PTZ] Preview stream error, switching to snapshot polling');
                startSnapshotFallback(cameraKey);
            };

            const streamUrl = `/api/ptz/preview_feed?camera=${cameraKey}&_t=${Date.now()}`;
            dom.previewImg.src = streamUrl;

            state.previewWatchdog = setTimeout(() => {
                if (gen === state.streamGeneration && state.currentCamera === cameraKey && (!dom.previewImg.naturalWidth || dom.previewImg.naturalWidth === 0)) {
                    startSnapshotFallback(cameraKey);
                }
            }, 2000);
        };

        // 1. Attempt High-Performance WebRTC WHEP Stream (Identical to viewer.html)
        let webrtcConnected = false;
        let hasVideoTrack = false;
        if (window.RTCPeerConnection && dom.video) {
            try {
                // Critical: iceServers: [] allows instant local LAN connection on Lenovo Z13 Gen i5 without internet
                const pc = new RTCPeerConnection({
                    iceServers: [],
                    iceTransportPolicy: 'all',
                    bundlePolicy: 'max-bundle',
                    rtcpMuxPolicy: 'require'
                });
                state.peerConnection = pc;

                pc.ontrack = (event) => {
                    if (gen !== state.streamGeneration || state.currentCamera !== cameraKey) return;
                    if (event.streams && event.streams[0]) {
                        hasVideoTrack = true;
                        dom.video.srcObject = event.streams[0];
                        dom.video.classList.remove('hidden');
                        dom.video.style.display = 'block';
                        if (dom.videoPlaceholder) dom.videoPlaceholder.classList.add('hidden');
                        if (dom.previewImg) {
                            dom.previewImg.src = '';
                            dom.previewImg.classList.add('hidden');
                            dom.previewImg.style.display = 'none';
                        }
                        dom.video.play().catch(e => console.log('[PTZ] WebRTC play notice:', e.message));
                        applyImageFilters();
                    }
                };

                pc.onconnectionstatechange = () => {
                    if (gen !== state.streamGeneration || state.currentCamera !== cameraKey) return;
                    if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
                        handleStreamDisconnect(cameraKey);
                    }
                };

                pc.addTransceiver('video', { direction: 'recvonly' });
                const offer = await pc.createOffer({ offerToReceiveVideo: true });
                if (gen !== state.streamGeneration) {
                    try { pc.close(); } catch (e) { }
                    return;
                }
                await pc.setLocalDescription(offer);

                const whepUrl = `http://${host}:8889/${cameraKey}/whep`;
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 1800);

                const response = await fetch(whepUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/sdp' },
                    body: pc.localDescription.sdp,
                    signal: controller.signal
                });
                clearTimeout(timeoutId);
                if (gen !== state.streamGeneration) {
                    try { pc.close(); } catch (e) { }
                    return;
                }

                if (response.ok) {
                    const answerSdp = await response.text();
                    if (gen !== state.streamGeneration) {
                        try { pc.close(); } catch (e) { }
                        return;
                    }
                    if (answerSdp && answerSdp.trim()) {
                        await pc.setRemoteDescription(new RTCSessionDescription({ type: 'answer', sdp: answerSdp }));
                        webrtcConnected = true;
                        // Fallback cleanly if ICE media never flows within 2.5s
                        state.previewWatchdog = setTimeout(() => {
                            if (gen === state.streamGeneration && state.currentCamera === cameraKey && (!hasVideoTrack || dom.video.videoWidth === 0)) {
                                startDirectFallback();
                            }
                        }, 2500);
                    }
                }
            } catch (err) {
                console.log('[PTZ] WebRTC initial connect deferred, falling back to direct feed:', err.message);
            }
        }

        // 2. Direct Low-Latency RTSP Preview Feed via Backend
        if (!webrtcConnected && gen === state.streamGeneration) {
            startDirectFallback();
        }
    }

    function stopStream() {
        state.streamGeneration += 1;
        if (state.previewWatchdog) {
            clearTimeout(state.previewWatchdog);
            state.previewWatchdog = null;
        }
        if (state.snapshotFallbackTimer) {
            clearInterval(state.snapshotFallbackTimer);
            state.snapshotFallbackTimer = null;
        }
        if (state.reconnectTimer) {
            clearTimeout(state.reconnectTimer);
            state.reconnectTimer = null;
        }
        if (state.peerConnection) {
            try {
                state.peerConnection.close();
            } catch (e) { }
            state.peerConnection = null;
        }
        if (dom.video) {
            if (dom.video.srcObject) {
                try {
                    dom.video.srcObject.getTracks().forEach(t => t.stop());
                } catch (e) { }
                dom.video.srcObject = null;
            }
            dom.video.classList.add('hidden');
            dom.video.style.display = 'none';
        }
        if (dom.previewImg) {
            dom.previewImg.onload = null;
            dom.previewImg.onerror = null;
            dom.previewImg.src = '';
            dom.previewImg.classList.add('hidden');
            dom.previewImg.style.display = 'none';
        }
        if (dom.videoPlaceholder) {
            dom.videoPlaceholder.classList.remove('hidden');
        }
    }

    function handleStreamDisconnect(cameraKey) {
        state.reconnectTimer = setTimeout(() => {
            if (state.currentCamera === cameraKey) {
                startStream(cameraKey);
            }
        }, 3000);
    }

    async function loadStreamInfo(cameraKey) {
        try {
            const res = await fetch(`/api/ptz/stream_info?camera=${cameraKey}`);
            if (res.ok) {
                const info = await res.json();
                if (info.webrtc_available !== undefined) {
                    state.webrtcAvailable = !!info.webrtc_available;
                }
                if (dom.statusResolution) dom.statusResolution.textContent = info.resolution || '1920x1080';
                if (dom.statusBitrate) dom.statusBitrate.textContent = info.bitrate || '4096 kbps';
                if (dom.statusFps) dom.statusFps.textContent = info.fps || '25 fps';
            }
        } catch (e) { }
    }

    // -------------------------------------------------------------------------
    // PTZ Commands (Move, Stop, Home, Zoom)
    // -------------------------------------------------------------------------
    // Serialize move and stop requests: a delayed move must not arrive after STOP.
    let ptzQueue = Promise.resolve();
    let ptzHeartbeat = null;
    let activeMove = null;
    function enqueuePtz(path, body, current = null) {
        ptzQueue = ptzQueue.catch(() => {}).then(() => {
            if (current && activeMove !== current) return;
            return fetch(path, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body), keepalive: true, signal: AbortSignal.timeout(1500)
            }).then(response => { if (!response.ok) throw new Error('PTZ request failed'); });
        });
        ptzQueue.catch(() => { activeMove = null; clearInterval(ptzHeartbeat); });
        return ptzQueue;
    }
    function sendMove(direction) {
        const camera = state.currentCamera;
        activeMove = { camera, direction, speed: state.moveSpeed };
        clearInterval(ptzHeartbeat);
        enqueuePtz('/api/ptz/move', activeMove, activeMove);
        ptzHeartbeat = setInterval(() => {
            if (activeMove && (document.hidden || dom.container?.classList.contains('hidden'))) sendStop();
            else if (activeMove) enqueuePtz('/api/ptz/move', activeMove, activeMove);
        }, 250);
    }
    function sendStop() {
        const camera = activeMove?.camera || state.currentCamera;
        activeMove = null;
        clearInterval(ptzHeartbeat);
        return enqueuePtz('/api/ptz/stop', { camera });
    }
    window.addEventListener('blur', () => { if (activeMove) sendStop(); });
    document.addEventListener('visibilitychange', () => { if (document.hidden && activeMove) sendStop(); });
    window.addEventListener('pagehide', () => { if (activeMove) sendStop(); });

    async function sendHome() {
        try {
            await fetch('/api/ptz/home', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ camera: state.currentCamera })
            });
            state.pan = 0;
            state.tilt = 0;
            updateOverlayBadge();
            showToast('Recalled HOME Position', 'success');
        } catch (e) {
            showToast('Failed to send Home command', 'error');
        }
    }

    async function sendZoom(action, speed = 3) {
        try {
            await fetch('/api/ptz/zoom', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    camera: state.currentCamera,
                    action: action,
                    speed: speed
                })
            });
        } catch (e) { }
    }

    // -------------------------------------------------------------------------
    // Drag to Pan / Tilt & Scroll to Zoom on Video Preview
    // -------------------------------------------------------------------------
    function initDragControls() {
        const overlay = dom.dragOverlay;
        if (!overlay) return;

        const onStart = (clientX, clientY) => {
            state.isDragging = true;
            state.dragStartX = clientX;
            state.dragStartY = clientY;
            state.currentDragDir = null;
            if (dom.dragIndicator) dom.dragIndicator.classList.remove('hidden');
        };

        const onMove = (clientX, clientY) => {
            if (!state.isDragging) return;

            const dx = clientX - state.dragStartX;
            const dy = clientY - state.dragStartY;
            const distance = Math.hypot(dx, dy);

            if (distance < 15) {
                if (state.currentDragDir) {
                    state.currentDragDir = null;
                    sendStop();
                }
                if (dom.dragArrow) dom.dragArrow.style.transform = 'scale(0)';
                return;
            }

            // Calculate angle: 0 is Right, 90 is Down, -90 is Up, 180 is Left
            const angleDeg = (Math.atan2(dy, dx) * 180) / Math.PI;
            let dir = 'stop';

            // 8-direction detection
            if (angleDeg >= -22.5 && angleDeg < 22.5) {
                dir = 'right';
            } else if (angleDeg >= 22.5 && angleDeg < 67.5) {
                dir = 'down_right';
            } else if (angleDeg >= 67.5 && angleDeg < 112.5) {
                dir = 'down';
            } else if (angleDeg >= 112.5 && angleDeg < 157.5) {
                dir = 'down_left';
            } else if (angleDeg >= -67.5 && angleDeg < -22.5) {
                dir = 'up_right';
            } else if (angleDeg >= -112.5 && angleDeg < -67.5) {
                dir = 'up';
            } else if (angleDeg >= -157.5 && angleDeg < -112.5) {
                dir = 'up_left';
            } else {
                dir = 'left';
            }

            // Visual Arrow Rotation
            if (dom.dragArrow) {
                dom.dragArrow.style.transform = `rotate(${angleDeg}deg) scale(1.2)`;
            }

            if (state.currentDragDir !== dir) {
                state.currentDragDir = dir;
                sendMove(dir);
            }
        };

        const onEnd = () => {
            if (!state.isDragging) return;
            state.isDragging = false;
            state.currentDragDir = null;
            if (dom.dragIndicator) dom.dragIndicator.classList.add('hidden');
            sendStop();
        };

        // Mouse events
        overlay.addEventListener('mousedown', (e) => {
            e.preventDefault();
            onStart(e.clientX, e.clientY);
        });

        window.addEventListener('mousemove', (e) => {
            if (state.isDragging) {
                e.preventDefault();
                onMove(e.clientX, e.clientY);
            }
        });

        window.addEventListener('mouseup', onEnd);

        // Touch events
        overlay.addEventListener('touchstart', (e) => {
            if (e.touches.length > 0) {
                onStart(e.touches[0].clientX, e.touches[0].clientY);
            }
        }, { passive: true });

        overlay.addEventListener('touchmove', (e) => {
            if (state.isDragging && e.touches.length > 0) {
                e.preventDefault();
                onMove(e.touches[0].clientX, e.touches[0].clientY);
            }
        }, { passive: false });

        overlay.addEventListener('touchend', onEnd);
        overlay.addEventListener('touchcancel', onEnd);

        // Wheel Zoom on Preview (Debounced)
        let wheelDebounceTimer = null;
        overlay.addEventListener('wheel', (e) => {
            e.preventDefault();
            const isZoomIn = e.deltaY < 0;
            if (isZoomIn) {
                state.zoom = Math.min(10.0, parseFloat((state.zoom + 0.1).toFixed(1)));
            } else {
                state.zoom = Math.max(1.0, parseFloat((state.zoom - 0.1).toFixed(1)));
            }
            updateZoomUI();

            clearTimeout(wheelDebounceTimer);
            wheelDebounceTimer = setTimeout(() => {
                sendZoom(isZoomIn ? 'in' : 'out', 2);
                setTimeout(() => sendZoom('stop'), 200);
            }, 80);
        }, { passive: false });
    }

    function updateOverlayBadge() {
        if (!dom.overlayBadge) return;
        dom.overlayBadge.style.display = 'none';
        dom.overlayBadge.textContent = '';
    }

    function updateZoomUI() {
        if (dom.valZoom) dom.valZoom.textContent = `${state.zoom.toFixed(1)}x`;
        if (dom.sliderZoom) dom.sliderZoom.value = state.zoom;
        updateOverlayBadge();
    }

    // -------------------------------------------------------------------------
    // D-Pad Directional Buttons & Center Home
    // -------------------------------------------------------------------------
    function initDpad() {
        dom.dpadBtns.forEach(btn => {
            const dir = btn.dataset.dir;
            if (!dir) return;

            const startAction = (e) => {
                e.preventDefault();
                btn.classList.add('bg-orange-100', 'border-primary-orange', 'scale-95');
                sendMove(dir);
            };

            const stopAction = (e) => {
                btn.classList.remove('bg-orange-100', 'border-primary-orange', 'scale-95');
                sendStop();
            };

            btn.addEventListener('mousedown', startAction);
            btn.addEventListener('mouseup', stopAction);
            btn.addEventListener('mouseleave', stopAction);

            btn.addEventListener('touchstart', startAction, { passive: true });
            btn.addEventListener('touchend', stopAction);
            btn.addEventListener('touchcancel', stopAction);
        });

        if (dom.homeBtn) {
            dom.homeBtn.addEventListener('click', (e) => {
                e.preventDefault();
                sendHome();
            });
        }
    }

    // -------------------------------------------------------------------------
    // Sliders: Speed & Zoom
    // -------------------------------------------------------------------------
    function initSpeedAndZoom() {
        if (dom.sliderSpeed) {
            dom.sliderSpeed.addEventListener('input', (e) => {
                state.moveSpeed = parseInt(e.target.value, 10) || 5;
                if (dom.valSpeed) dom.valSpeed.textContent = state.moveSpeed;
            });
        }

        if (dom.sliderZoom) {
            dom.sliderZoom.addEventListener('input', (e) => {
                state.zoom = parseFloat(parseFloat(e.target.value).toFixed(1)) || 1.4;
                if (dom.valZoom) dom.valZoom.textContent = `${state.zoom}x`;
                updateOverlayBadge();
            });
            dom.sliderZoom.addEventListener('change', (e) => {
                sendZoom('in', 2);
                setTimeout(() => sendZoom('stop'), 150);
            });
        }

        if (dom.btnZoomMinus) {
            dom.btnZoomMinus.addEventListener('click', () => {
                state.zoom = Math.max(1.0, parseFloat((state.zoom - 0.2).toFixed(1)));
                updateZoomUI();
                sendZoom('out', 2);
                setTimeout(() => sendZoom('stop'), 200);
            });
        }

        if (dom.btnZoomPlus) {
            dom.btnZoomPlus.addEventListener('click', () => {
                state.zoom = Math.min(10.0, parseFloat((state.zoom + 0.2).toFixed(1)));
                updateZoomUI();
                sendZoom('in', 2);
                setTimeout(() => sendZoom('stop'), 200);
            });
        }
    }

    // -------------------------------------------------------------------------
    // Hardware Image Calibration (Brightness, Sharpness, Contrast, Saturation)
    // Dispatches directly to camera hardware sensor/DSP via ONVIF, VISCA & V4L2
    // -------------------------------------------------------------------------
    function initImageSliders() {
        const scheduleHardwareSave = (immediate = false) => {
            clearTimeout(state.saveSettingsTimeout);
            const doSave = async () => {
                try {
                    await fetch('/api/ptz/image_settings', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            camera: state.currentCamera,
                            brightness: state.imageSettings.brightness,
                            sharpness: state.imageSettings.sharpness,
                            contrast: state.imageSettings.contrast,
                            saturation: state.imageSettings.saturation
                        })
                    });
                } catch (e) {
                    console.warn('[PTZ] Hardware image settings dispatch failed:', e);
                }
            };
            if (immediate) {
                doSave();
            } else {
                state.saveSettingsTimeout = setTimeout(doSave, 80);
            }
        };

        const attachSlider = (slider, valEl, key) => {
            if (!slider) return;
            const update = (immediateSave) => {
                const val = parseInt(slider.value, 10);
                state.imageSettings[key] = val;
                if (valEl) valEl.textContent = val;
                applyImageFilters();
                scheduleHardwareSave(immediateSave);
            };
            slider.addEventListener('input', () => update(false));
            slider.addEventListener('change', () => update(true));
        };

        attachSlider(dom.sliderBrightness, dom.valBrightness, 'brightness');
        attachSlider(dom.sliderSharpness, dom.valSharpness, 'sharpness');
        attachSlider(dom.sliderContrast, dom.valContrast, 'contrast');
        attachSlider(dom.sliderSaturation, dom.valSaturation, 'saturation');
    }

    async function loadImageSettings(cameraKey) {
        try {
            const res = await fetch(`/api/ptz/image_settings?camera=${cameraKey}`);
            if (res.ok) {
                const data = await res.json();
                const s = data.settings || {};
                state.imageSettings.brightness = s.brightness !== undefined ? s.brightness : 50;
                state.imageSettings.sharpness = s.sharpness !== undefined ? s.sharpness : 26;
                state.imageSettings.contrast = s.contrast !== undefined ? s.contrast : 25;
                state.imageSettings.saturation = s.saturation !== undefined ? s.saturation : 50;

                // Sync UI sliders
                if (dom.sliderBrightness) dom.sliderBrightness.value = state.imageSettings.brightness;
                if (dom.valBrightness) dom.valBrightness.textContent = state.imageSettings.brightness;
                if (dom.sliderSharpness) dom.sliderSharpness.value = state.imageSettings.sharpness;
                if (dom.valSharpness) dom.valSharpness.textContent = state.imageSettings.sharpness;
                if (dom.sliderContrast) dom.sliderContrast.value = state.imageSettings.contrast;
                if (dom.valContrast) dom.valContrast.textContent = state.imageSettings.contrast;
                if (dom.sliderSaturation) dom.sliderSaturation.value = state.imageSettings.saturation;
                if (dom.valSaturation) dom.valSaturation.textContent = state.imageSettings.saturation;

                clearUiFilters();
            }
        } catch (e) { }
    }

    // -------------------------------------------------------------------------
    // Presets Management: Integrated Pan/Tilt Dropdown, Quick P## & Direct Input
    // -------------------------------------------------------------------------
    function updatePresetLimitsAndDropdown() {
        const cam = state.cameraList.find(c => c.key === state.currentCamera) || {};
        const isCanon = (cam.brand || '').toUpperCase() === 'CANON';
        const minPreset = isCanon ? 2 : 1;
        const maxPreset = isCanon ? 100 : 254;

        // 1. Integrated Pan/Tilt Control Preset Dropdown (Next to D-Pad)
        if (dom.controlPresetSelect) {
            const currentSelected = parseInt(dom.controlPresetSelect.value, 10);
            dom.controlPresetSelect.innerHTML = '';
            
            const maxDropdown = isCanon ? 100 : 100;
            for (let i = minPreset; i <= maxDropdown; i++) {
                const opt = document.createElement('option');
                opt.value = i;
                opt.textContent = `Preset ${String(i).padStart(2, '0')}`;
                dom.controlPresetSelect.appendChild(opt);
            }

            if (!isNaN(currentSelected) && currentSelected >= minPreset && currentSelected <= maxDropdown) {
                dom.controlPresetSelect.value = currentSelected;
            } else {
                dom.controlPresetSelect.value = minPreset;
            }
        }

        if (dom.presetRangeHint) {
            dom.presetRangeHint.textContent = isCanon ? 'Canon: 2 - 100' : 'VISCA: 1 - 254';
        }

        // 2. Direct Preset Input Limits
        if (dom.directPresetInput) {
            dom.directPresetInput.min = minPreset;
            dom.directPresetInput.max = maxPreset;
            let currentDirectVal = parseInt(dom.directPresetInput.value, 10);
            if (isNaN(currentDirectVal) || currentDirectVal < minPreset) {
                dom.directPresetInput.value = minPreset;
            } else if (currentDirectVal > maxPreset) {
                dom.directPresetInput.value = maxPreset;
            }
        }

        if (dom.directPresetTitle) {
            dom.directPresetTitle.textContent = `Direct Preset Number (${minPreset} ~ ${maxPreset}):`;
        }

        if (dom.directPresetBadge) {
            dom.directPresetBadge.textContent = isCanon ? 'Canon Limit: 2 - 100' : 'Standard VISCA: 1 - 254';
            dom.directPresetBadge.className = isCanon
                ? 'text-[10px] text-red-500 font-mono font-bold tracking-tight'
                : 'text-[10px] text-blue-600 font-mono font-bold tracking-tight';
        }

        // 3. Quick Presets Grid (P02 ~ P09 for Canon, P01 ~ P08 for standard)
        if (dom.quickPresetTitle) {
            dom.quickPresetTitle.textContent = isCanon ? 'Preset Positions (P02 ~ P09)' : 'Preset Positions (P01 ~ P08)';
        }

        if (dom.quickPresetGrid) {
            dom.quickPresetGrid.innerHTML = '';
            const startP = isCanon ? 2 : 1;
            for (let i = 0; i < 8; i++) {
                const pNum = startP + i;
                const btn = document.createElement('button');
                btn.dataset.preset = pNum;
                btn.className = 'ptz-preset-quick-btn py-2.5 rounded-xl border border-gray-200 bg-white hover:bg-orange-50 hover:border-[#F58220] text-gray-700 font-bold text-xs transition-all shadow-sm active:scale-95 cursor-pointer';
                btn.textContent = `P${String(pNum).padStart(2, '0')}`;
                btn.addEventListener('click', () => {
                    if (state.isChangingPreset) {
                        handleSavePreset(pNum);
                        state.isChangingPreset = false;
                        updateChangePresetButtonState();
                    } else {
                        handleRecallPreset(pNum);
                    }
                });
                dom.quickPresetGrid.appendChild(btn);
            }
        }
    }

    function initPresets() {
        if (dom.presetChangeToggleBtn) {
            dom.presetChangeToggleBtn.addEventListener('click', () => {
                state.isChangingPreset = !state.isChangingPreset;
                updateChangePresetButtonState();
            });
        }

        if (dom.btnControlRecall) {
            dom.btnControlRecall.addEventListener('click', () => {
                const val = parseInt(dom.controlPresetSelect.value, 10);
                if (!isNaN(val)) {
                    handleRecallPreset(val);
                }
            });
        }

        if (dom.btnControlSave) {
            dom.btnControlSave.addEventListener('click', () => {
                const val = parseInt(dom.controlPresetSelect.value, 10);
                if (!isNaN(val)) {
                    handleSavePreset(val);
                }
            });
        }

        if (dom.btnDirectRecall) {
            dom.btnDirectRecall.addEventListener('click', () => {
                const cam = state.cameraList.find(c => c.key === state.currentCamera) || {};
                const isCanon = (cam.brand || '').toUpperCase() === 'CANON';
                const minP = isCanon ? 2 : 1;
                const maxP = isCanon ? 100 : 254;

                const val = parseInt(dom.directPresetInput.value, 10);
                if (isNaN(val) || val < minP || val > maxP) {
                    showToast(`Preset number must be between ${minP} and ${maxP}`, 'error');
                    return;
                }
                handleRecallPreset(val);
            });
        }

        if (dom.btnDirectSave) {
            dom.btnDirectSave.addEventListener('click', () => {
                const cam = state.cameraList.find(c => c.key === state.currentCamera) || {};
                const isCanon = (cam.brand || '').toUpperCase() === 'CANON';
                const minP = isCanon ? 2 : 1;
                const maxP = isCanon ? 100 : 254;

                const val = parseInt(dom.directPresetInput.value, 10);
                if (isNaN(val) || val < minP || val > maxP) {
                    showToast(`Preset number must be between ${minP} and ${maxP}`, 'error');
                    return;
                }
                handleSavePreset(val);
            });
        }
    }

    function updateChangePresetButtonState() {
        if (!dom.presetChangeToggleBtn) return;
        if (state.isChangingPreset) {
            dom.presetChangeToggleBtn.textContent = 'Save Mode (Click P#)';
            dom.presetChangeToggleBtn.className = 'px-3 py-1.5 bg-amber-500 hover:bg-amber-600 text-white text-xs font-bold rounded-lg shadow-md transition-all tracking-wide animate-pulse border border-amber-300';
            showToast('Save Mode Active: Click any quick preset button to save position', 'warning');
        } else {
            dom.presetChangeToggleBtn.textContent = 'Change preset';
            dom.presetChangeToggleBtn.className = 'px-3 py-1.5 bg-[#F58220] hover:bg-orange-600 text-white text-xs font-bold rounded-lg shadow-sm transition-all tracking-wide';
        }
    }

    async function handleRecallPreset(presetNum) {
        const cam = state.cameraList.find(c => c.key === state.currentCamera) || {};
        const isCanon = (cam.brand || '').toUpperCase() === 'CANON';
        const minP = isCanon ? 2 : 1;
        const maxP = isCanon ? 100 : 254;

        if (presetNum < minP || presetNum > maxP) {
            showToast(`Preset must be between ${minP} and ${maxP}`, 'error');
            return;
        }

        showToast(`Recalling Preset ${presetNum}...`, 'info');
        try {
            const res = await fetch('/api/ptz/preset/recall', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    camera: state.currentCamera,
                    preset: presetNum
                })
            });
            const data = await res.json();
            if (data.success) {
                showToast(`Preset ${presetNum} Recalled Successfully`, 'success');
            } else {
                showToast(`Recall Preset ${presetNum} Failed: ${data.error || 'Camera unreachable'}`, 'error');
            }
        } catch (e) {
            showToast(`Preset ${presetNum} Recall Error`, 'error');
        }
    }

    async function handleSavePreset(presetNum) {
        const cam = state.cameraList.find(c => c.key === state.currentCamera) || {};
        const isCanon = (cam.brand || '').toUpperCase() === 'CANON';
        const minP = isCanon ? 2 : 1;
        const maxP = isCanon ? 100 : 254;

        if (presetNum < minP || presetNum > maxP) {
            showToast(`Preset must be between ${minP} and ${maxP}`, 'error');
            return;
        }

        showToast(`Saving Preset ${presetNum}...`, 'info');
        try {
            const res = await fetch('/api/ptz/preset/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    camera: state.currentCamera,
                    preset: presetNum
                })
            });
            const data = await res.json();
            if (data.success) {
                showToast(`Preset ${presetNum} Saved Successfully!`, 'success');
            } else {
                showToast(`Save Preset ${presetNum} Failed: ${data.error || 'Camera unreachable'}`, 'error');
            }
        } catch (e) {
            showToast(`Preset ${presetNum} Save Error`, 'error');
        }
    }

    // -------------------------------------------------------------------------
    // Keyboard Shortcuts
    // -------------------------------------------------------------------------
    function initKeyboardShortcuts() {
        window.addEventListener('keydown', (e) => {
            if (!dom.container || dom.container.classList.contains('hidden')) return;
            if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;

            let handled = true;
            switch (e.key) {
                case 'ArrowUp':
                    sendMove('up');
                    break;
                case 'ArrowDown':
                    sendMove('down');
                    break;
                case 'ArrowLeft':
                    sendMove('left');
                    break;
                case 'ArrowRight':
                    sendMove('right');
                    break;
                case '+':
                case '=':
                    state.zoom = Math.min(10.0, parseFloat((state.zoom + 0.2).toFixed(1)));
                    updateZoomUI();
                    sendZoom('in', 2);
                    setTimeout(() => sendZoom('stop'), 200);
                    break;
                case '-':
                case '_':
                    state.zoom = Math.max(1.0, parseFloat((state.zoom - 0.2).toFixed(1)));
                    updateZoomUI();
                    sendZoom('out', 2);
                    setTimeout(() => sendZoom('stop'), 200);
                    break;
                case 'Home':
                case 'h':
                case 'H':
                    sendHome();
                    break;
                default:
                    handled = false;
            }
            if (handled) e.preventDefault();
        });

        window.addEventListener('keyup', (e) => {
            if (!dom.container || dom.container.classList.contains('hidden')) return;
            if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;
            if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
                sendStop();
            }
        });
    }

    // -------------------------------------------------------------------------
    // Initialization & Public Hook
    // -------------------------------------------------------------------------
    let isInitialized = false;

    function init() {
        cacheDomElements();
        if (isInitialized) return;
        isInitialized = true;

        initDragControls();
        initDpad();
        initSpeedAndZoom();
        initImageSliders();
        initPresets();
        initKeyboardShortcuts();
    }

    function onActivated() {
        init();
        loadCameras();
        loadImageSettings(state.currentCamera);
        loadStreamInfo(state.currentCamera);
        startStream(state.currentCamera);
        updateOverlayBadge();
    }

    function onDeactivated() {
        stopStream();
    }

    // Export module to window
    window.CameraPresetsModule = {
        init: init,
        onActivated: onActivated,
        onDeactivated: onDeactivated,
        selectCamera: selectCamera,
        getState: () => state
    };

})(window);
