// =============================================================================
// SETUP MAIN COORDINATOR: Navigation, Controller Setup, Dashboard & Startup
// =============================================================================

document.getElementById('reload-live-preview')?.addEventListener('click', () => { const p=document.getElementById('live-display-preview'); if (p) p.src=p.dataset.src; });

// 1. Navigation & Floating Apply Button
function checkShowApplyButton() {
    const activePage = document.querySelector('.page-content.active-page');
    if (floatingApplyBtn && activePage && activePage.id === 'page-layout') {
        floatingApplyBtn.classList.remove('hidden');
        floatingApplyBtn.classList.add('flex');
    } else if (floatingApplyBtn) {
        floatingApplyBtn.classList.add('hidden');
        floatingApplyBtn.classList.remove('flex');
    }
}

navBtns.forEach(btn => {
    btn.addEventListener('click', async () => {
        navBtns.forEach(b => {
            b.classList.remove('active');
            b.classList.add('text-gray-300');
            b.classList.remove('text-white');
        });
        btn.classList.add('active');
        btn.classList.remove('text-gray-300');
        btn.classList.add('text-white');

        const pageId = btn.id.replace('nav-', 'page-');
        pages.forEach(p => p.classList.remove('active-page'));
        const targetPage = document.getElementById(pageId);
        if (targetPage) targetPage.classList.add('active-page');

        // Run the room viewer only while the Display page is open.
        const displayPreview = document.getElementById('live-display-preview');
        if (displayPreview) {
            if (pageId === 'page-display') {
                if (!displayPreview.getAttribute('src')) displayPreview.src = displayPreview.dataset.src;
            } else if (displayPreview.getAttribute('src')) {
                displayPreview.removeAttribute('src');
            }
        }

        if (pageId === 'page-mapping') {
            try {
                const isSenn = ctrlType && ctrlType.value === 'Sennheiser';
                const genTable = document.getElementById('generic-mic-table-container') || document.getElementById('mic-table')?.parentElement;
                const saveBtn = document.getElementById('table-save');
                const sennPanel = document.getElementById('sennheiser-mapping-panel');
                if (isSenn) {
                    if (genTable) genTable.style.display = 'none';
                    if (saveBtn) saveBtn.style.display = 'none';
                    if (sennPanel) sennPanel.style.display = 'block';
                    if (typeof checkSennheiserStatus === 'function') checkSennheiserStatus();
                } else {
                    if (genTable) genTable.style.display = 'block';
                    if (saveBtn) saveBtn.style.display = 'block';
                    if (sennPanel) sennPanel.style.display = 'none';
                    await fetch('/api/refresh_mic_data', { method: 'POST' });
                    if (typeof loadMappingData === 'function') await loadMappingData();
                }
            } catch (e) { }
        }
        if (pageId === 'page-mapping' || pageId === 'page-monitoring') {
            if (typeof startMicPolling === 'function') startMicPolling();
        } else {
            if (typeof stopMicPolling === 'function') stopMicPolling();
        }

        if (pageId === 'page-monitoring' && typeof loadMonitorSetup === 'function') {
            loadMonitorSetup();
        }

        if (pageId === 'page-layout') {
            if (typeof loadAvailableCameras === 'function') await loadAvailableCameras();
            if (typeof loadCurrentLayout === 'function') await loadCurrentLayout();
        }

        if (pageId === 'page-setup' && typeof generateCameraList === 'function') {
            generateCameraList();
        }

        if (pageId === 'page-settings') {
            if (typeof switchSettingsTab === 'function') switchSettingsTab('tab-settings-info');
            if (typeof loadSystemInfo === 'function') loadSystemInfo();
            if (typeof renderLicenseHistory === 'function') renderLicenseHistory();
            if (typeof loadAdminUsers === 'function') loadAdminUsers();
        }

        checkShowApplyButton();
    });
});

// Setup Page Sub-Tabs (Controller vs Cameras vs Camera Presets)
function switchTab(tabId) {
    const tabs = [tabController, tabAssign, tabPresets].filter(Boolean);
    const contents = [contentController, contentAssign, contentPresets].filter(Boolean);
    
    tabs.forEach(t => { t.classList.remove('active'); t.classList.add('inactive'); });
    contents.forEach(c => { c.classList.add('hidden'); c.classList.remove('flex'); });

    const tab = document.getElementById(tabId);
    if (tab) {
        tab.classList.add('active');
        tab.classList.remove('inactive');
    }

    if (tabId === 'tab-controller') {
        if (contentController) {
            contentController.classList.remove('hidden');
            contentController.classList.add('flex');
        }
        if (window.CameraPresetsModule && typeof window.CameraPresetsModule.onDeactivated === 'function') {
            window.CameraPresetsModule.onDeactivated();
        }
    } else if (tabId === 'tab-assign') {
        if (contentAssign) {
            contentAssign.classList.remove('hidden');
            contentAssign.classList.add('flex');
        }
        if (typeof generateCameraList === 'function') generateCameraList();
        if (window.CameraPresetsModule && typeof window.CameraPresetsModule.onDeactivated === 'function') {
            window.CameraPresetsModule.onDeactivated();
        }
    } else if (tabId === 'tab-presets') {
        if (contentPresets) {
            contentPresets.classList.remove('hidden');
            contentPresets.classList.add('flex');
        }
        if (window.CameraPresetsModule && typeof window.CameraPresetsModule.onActivated === 'function') {
            window.CameraPresetsModule.onActivated();
        }
    }
}
window.switchTab = switchTab;

if (tabController) tabController.addEventListener('click', () => switchTab('tab-controller'));
if (tabAssign) tabAssign.addEventListener('click', () => switchTab('tab-assign'));
if (tabPresets) tabPresets.addEventListener('click', () => switchTab('tab-presets'));

// Settings Page Sub-Tabs
const settingsTabsList = [
    { tabId: 'tab-settings-info', contentId: 'content-settings-info' },
    { tabId: 'tab-settings-network', contentId: 'content-settings-network' },
    { tabId: 'tab-settings-users', contentId: 'content-settings-users' },
    { tabId: 'tab-settings-cameras', contentId: 'content-settings-cameras' },
    { tabId: 'tab-settings-maintenance', contentId: 'content-settings-maintenance' },
    { tabId: 'tab-settings-backup', contentId: 'content-settings-maintenance' },
    { tabId: 'tab-settings-general', contentId: 'content-settings-info' },
    { tabId: 'tab-settings-reports', contentId: 'content-settings-maintenance' }
];

function switchSettingsTab(activeTabId) {
    if (activeTabId === 'tab-settings-general') activeTabId = 'tab-settings-info';
    if (activeTabId === 'tab-settings-backup' || activeTabId === 'tab-settings-reports') activeTabId = 'tab-settings-maintenance';

    const targetConfig = settingsTabsList.find(s => s.tabId === activeTabId);
    const targetContentId = targetConfig ? targetConfig.contentId : null;

    settingsTabsList.forEach(({ tabId, contentId }) => {
        const tabEl = document.getElementById(tabId);
        const contentEl = document.getElementById(contentId);
        if (tabEl) {
            if (tabId === activeTabId) {
                tabEl.classList.add('active');
                tabEl.classList.remove('inactive');
            } else {
                tabEl.classList.remove('active');
                tabEl.classList.add('inactive');
            }
        }
        if (contentEl) {
            if (contentId === targetContentId) {
                contentEl.classList.remove('hidden');
                contentEl.classList.add('flex');
            } else {
                contentEl.classList.add('hidden');
                contentEl.classList.remove('flex');
            }
        }
    });

    if (activeTabId === 'tab-settings-info' && typeof loadSystemInfo === 'function') {
        loadSystemInfo();
    }
    if (activeTabId === 'tab-settings-users' && typeof loadAdminUsers === 'function') {
        loadAdminUsers();
    }
    if (activeTabId === 'tab-settings-cameras') {
        if (typeof renderLicenseHistory === 'function') renderLicenseHistory();
        if (typeof loadHardwareLockId === 'function') loadHardwareLockId();
    }
    if (activeTabId === 'tab-settings-network' && typeof loadNetworkDashboard === 'function') {
        loadNetworkDashboard();
    }
    if (activeTabId === 'tab-settings-maintenance') {
        if (typeof loadMaintenanceTelemetry === 'function') loadMaintenanceTelemetry();
        if (typeof initPingToolModule === 'function') initPingToolModule();
    }
}
window.switchSettingsTab = switchSettingsTab;

settingsTabsList.forEach(({ tabId }) => {
    const tabEl = document.getElementById(tabId);
    if (tabEl) {
        tabEl.addEventListener('click', () => switchSettingsTab(tabId));
    }
});

// Layout Sub-Tabs
if (tabLayoutAuto) tabLayoutAuto.addEventListener('click', () => {
    if (typeof switchLayoutTab === 'function') switchLayoutTab('tab-layout-auto');
});
if (tabLayoutManual) tabLayoutManual.addEventListener('click', () => {
    if (typeof switchLayoutTab === 'function') switchLayoutTab('tab-layout-manual');
});
if (layoutStyleSelect) layoutStyleSelect.addEventListener('change', () => {
    currentLayout.layout_type = layoutStyleSelect.value;
    if (typeof renderLayoutSlots === 'function') renderLayoutSlots();
});

if (restartBackendBtn) {
    restartBackendBtn.addEventListener('click', async () => {
        if (!confirm("Are you sure you want to restart the backend service?")) return;
        try {
            await fetch('/api/restart_backend', { method: 'POST' });
            alert("Restart command sent. Reloading interface...");
            setTimeout(() => window.location.reload(), 3000);
        } catch (e) {
            alert("Error sending restart request.");
        }
    });
}

// =============================================================================
// 2. CONTROLLER CONFIGURATION & CONNECTION MANAGEMENT
// =============================================================================

async function loadSerialPorts(selectedPort = null) {
    if (!ctrlComPort) return;
    try {
        const res = await fetch('/api/get_serial_ports');
        const data = await res.json();
        const ports = data.ports || [];
        const currentVal = selectedPort || ctrlComPort.value || 'Auto';

        let html = '<option value="Auto">Auto (Auto-Detect)</option>';
        ports.forEach(p => {
            html += `<option value="${p}">${p}</option>`;
        });
        if (currentVal && currentVal !== 'Auto' && !ports.includes(currentVal)) {
            html += `<option value="${currentVal}">${currentVal}</option>`;
        }
        ctrlComPort.innerHTML = html;
        ctrlComPort.value = currentVal;
    } catch (e) {
        console.warn('Failed to load serial ports:', e);
    }
}
window.loadSerialPorts = loadSerialPorts;

if (refreshPortsBtn) {
    refreshPortsBtn.addEventListener('click', () => loadSerialPorts());
}

function updateControllerFormVisibility() {
    const brand = ctrlType ? ctrlType.value : 'Televic';
    const isClarilus = brand === 'Clarilus';
    const isBosch = brand === 'Bosch';
    const isSennheiser = brand === 'Sennheiser';
    const isTelevic = brand === 'Televic';
    const model = ctrlModel ? ctrlModel.value : '';
    const isDual = isSennheiser && (model.includes('Dual') || model.includes('both') || model.includes('Both'));
    const isTcc2 = isSennheiser && !isDual && (model.includes('2') || model.includes('TCC2') || model.includes('tcc2') || model.includes('SLCM'));
    const isTccm = isSennheiser && !isTcc2;

    const secCont = document.getElementById('secondary-ip-container');
    if (secCont) secCont.classList.toggle('hidden', !isDual);
    if (hostIpContainer) {
        const hostLabel = hostIpContainer.querySelector('label');
        if (hostLabel) hostLabel.textContent = isDual ? 'Primary Microphone IP (TCC2)' : 'Host IP Address';
    }

    if (clarilusModeContainer) clarilusModeContainer.classList.toggle('hidden', !isClarilus);
    if (boschUserContainer) boschUserContainer.classList.toggle('hidden', !isBosch);
    if (boschPassContainer) {
        const needsPass = isBosch || isTccm;
        boschPassContainer.classList.toggle('hidden', !needsPass);
        const passLabel = boschPassContainer.querySelector('label');
        if (passLabel) {
            passLabel.textContent = isBosch ? 'Password (Bosch)' : 'Password (TCCM)';
        }
    }
    if (apiKeyContainer) apiKeyContainer.classList.toggle('hidden', !isTelevic);

    if (isClarilus) {
        const mode = clarilusMode ? clarilusMode.value : 'serial';
        if (mode === 'serial') {
            if (clarilusSerialContainer) clarilusSerialContainer.classList.remove('hidden');
            if (hostIpContainer) hostIpContainer.classList.add('hidden');
            if (clarilusLanPortContainer) clarilusLanPortContainer.classList.add('hidden');
            loadSerialPorts();
        } else {
            if (clarilusSerialContainer) clarilusSerialContainer.classList.add('hidden');
            if (hostIpContainer) hostIpContainer.classList.remove('hidden');
            if (clarilusLanPortContainer) clarilusLanPortContainer.classList.remove('hidden');
        }
    } else {
        if (clarilusSerialContainer) clarilusSerialContainer.classList.add('hidden');
        if (clarilusLanPortContainer) clarilusLanPortContainer.classList.add('hidden');
        if (hostIpContainer) hostIpContainer.classList.remove('hidden');
    }
}
window.updateControllerFormVisibility = updateControllerFormVisibility;

function updateControllerModels() {
    if (!ctrlModel || !ctrlType) return;
    const selectedBrand = ctrlType.value || "Televic";
    const models = CONTROLLER_MODELS[selectedBrand] || [];

    ctrlModel.innerHTML = models.map(m => `<option value="${m}">${m}</option>`).join('');
    ctrlModel.dispatchEvent(new Event('change'));
}
window.updateControllerModels = updateControllerModels;

if (ctrlType) {
    ctrlType.addEventListener('change', () => {
        updateControllerModels();
        updateControllerFormVisibility();
    });
}

if (clarilusMode) {
    clarilusMode.addEventListener('change', () => {
        updateControllerFormVisibility();
    });
}

if (ctrlModel) {
    ctrlModel.addEventListener('change', () => {
        updateControllerFormVisibility();
        const val = ctrlModel.value;
        const needsKey = !["D-CERNO CUR", "CCS1000D", "Clarilus CLA"].includes(val) && !val.includes("TeamConnect") && !val.includes("TCC");

        if (apiKey) {
            if (needsKey) {
                apiKey.disabled = false;
                apiKey.classList.remove('bg-gray-100', 'cursor-not-allowed');
                apiKey.placeholder = "••••••••";
            } else {
                apiKey.value = "";
                apiKey.disabled = true;
                apiKey.classList.add('bg-gray-100', 'cursor-not-allowed');
                apiKey.placeholder = "Not required for " + val;
            }
        }
    });
}

function setConnectedState(isConnected) {
    isDcernoConnected = isConnected;
    if (!statusDot || !statusText || !connectBtn || !disconnectBtn) return;

    if (isConnected) {
        statusDot.classList.replace('bg-gray-400', 'bg-green-500');
        statusDot.classList.add('animate-pulse');
        statusText.textContent = "CONNECTED";
        statusText.classList.replace('text-gray-600', 'text-green-600');
        connectBtn.classList.add('hidden');
        disconnectBtn.classList.remove('hidden');
        const allCtrlInputs = [hostIp, apiKey, ctrlType, ctrlModel, ctrlUsername, ctrlPassword, clarilusMode, ctrlComPort, ctrlPort, refreshPortsBtn];
        allCtrlInputs.forEach(el => { if (el) el.disabled = true; });
        if (homeCtrlStatus) {
            homeCtrlStatus.textContent = "Connected";
            homeCtrlStatus.className = "px-2 py-0.5 rounded-full bg-green-100 text-green-700 text-xs font-bold uppercase";
        }
    } else {
        statusDot.classList.replace('bg-green-500', 'bg-gray-400');
        statusDot.classList.remove('animate-pulse');
        statusText.textContent = "DISCONNECTED";
        statusText.classList.replace('text-green-600', 'text-gray-600');
        connectBtn.classList.remove('hidden');
        disconnectBtn.classList.add('hidden');
        const allCtrlInputs = [hostIp, apiKey, ctrlType, ctrlModel, ctrlUsername, ctrlPassword, clarilusMode, ctrlComPort, ctrlPort, refreshPortsBtn];
        allCtrlInputs.forEach(el => { if (el) el.disabled = false; });
        if (homeCtrlStatus) {
            homeCtrlStatus.textContent = "Disconnected";
            homeCtrlStatus.className = "px-2 py-0.5 rounded-full bg-red-100 text-red-700 text-xs font-bold uppercase";
        }
    }
}
window.setConnectedState = setConnectedState;

async function checkConnection() {
    try {
        const res = await fetch('/api/check_dcerno_status');
        const data = await res.json();
        setConnectedState(data.connected);

        if (data.connected && micTableBody && micTableBody.children.length === 0) {
            await fetch('/api/refresh_mic_data', { method: 'POST' });
            if (typeof loadMappingData === 'function') await loadMappingData();
        }
    } catch (e) { setConnectedState(false); }
}
window.checkConnection = checkConnection;

if (connectBtn) {
    connectBtn.addEventListener('click', async () => {
        const modelVal = ctrlModel ? ctrlModel.value : 'D-CERNO AE';
        const isDual = ctrlType && ctrlType.value === 'Sennheiser' && modelVal.includes('Dual');
        const isTcc2Model = ctrlType && ctrlType.value === 'Sennheiser' && !isDual && (modelVal.includes('2') || modelVal.includes('TCC2') || modelVal.includes('tcc2') || modelVal.includes('SLCM'));
        const secIpField = document.getElementById('sec-host-ip');
        const body = {
            host_ip: hostIp ? hostIp.value.trim() : '',
            secondary_ip: isDual ? (secIpField ? secIpField.value.trim() : '192.168.1.92') : '',
            secondary_password: isDual ? (ctrlPassword ? ctrlPassword.value.trim() : '') : '',
            api_key: apiKey ? apiKey.value.trim() : '',
            controller_type: ctrlType ? ctrlType.value : 'Televic',
            controller_model: modelVal,
            username: ctrlUsername ? ctrlUsername.value.trim() : '',
            password: isTcc2Model ? '' : (ctrlPassword ? ctrlPassword.value.trim() : ''),
            connection_mode: clarilusMode ? clarilusMode.value : 'serial',
            com_port: ctrlComPort ? ctrlComPort.value : 'Auto',
            port: ctrlPort ? (parseInt(ctrlPort.value) || (isTcc2Model ? 45 : (ctrlType && ctrlType.value === "Sennheiser" ? 443 : 8080))) : (isTcc2Model ? 45 : (ctrlType && ctrlType.value === "Sennheiser" ? 443 : 8080))
        };
        if (statusText) statusText.textContent = "Connecting...";
        try {
            const res = await fetch('/api/connect_dcerno', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            if (res.ok) {
                setConnectedState(true);
                if (ctrlType.value !== 'Sennheiser' && tabAssign) switchTab('tab-assign');
            } else {
                const d = await res.json();
                alert("Connection Failed: " + (d.error || "Unknown Error"));
                setConnectedState(false);
            }
        } catch (e) {
            alert("Server Error");
            setConnectedState(false);
        }
    });
}

if (disconnectBtn) {
    disconnectBtn.addEventListener('click', async () => {
        if (confirm("Disconnect?")) {
            await fetch('/api/disconnect_dcerno', { method: 'POST' });
            setConnectedState(false);
            window.dispatchEvent(new Event('microphone-disconnected'));
        }
    });
}

// =============================================================================
// 3. MASTER DATA LOADER
// =============================================================================

async function loadData() {
    try {
        const licRes = await fetch('/api/get_license_status');
        if (licRes.ok) {
            const licData = await licRes.json();
            licenseState.camera3 = licData.camera3 !== undefined ? licData.camera3 : true;
            licenseState.camera6 = licData.camera6 || false;
            licenseState.camera6Licensed = licData.camera6_licensed || false;
            licenseState.camera9 = licData.camera9 || false;
            licenseState.camera9Licensed = licData.camera9_licensed || false;
            licenseState.monitoring = licData.monitoring || false;
            licenseState.monitoringLicensed = licData.monitoring_licensed || false;
        }
    } catch (e) {
        console.log("Could not fetch license, using defaults.");
    }

    if (typeof updateLicenseUI === 'function') updateLicenseUI();
    if (typeof updateCameraLimit === 'function') updateCameraLimit();
    if (typeof updateMonitoringVisibility === 'function') updateMonitoringVisibility();

    try {
        const ts = Date.now();

        try {
            const res = await fetch('/api/check_dcerno_status');
            const data = await res.json();
            if (data.connected && statusDot) {
                statusDot.classList.replace('bg-gray-400', 'bg-green-500');
                if (statusText) statusText.textContent = "CONNECTED";
            }
        } catch (e) { }

        const r1 = await fetch(`/api/get_host_config?t=${ts}`);
        const d1 = await r1.json();

        if (hostIp) hostIp.value = d1.host_ip || '';
        const secField = document.getElementById('sec-host-ip');
        if (secField && d1.secondary_ip) secField.value = d1.secondary_ip;
        if (apiKey) apiKey.value = d1.api_key || '';
        if (ctrlUsername) ctrlUsername.value = d1.username || '';
        if (ctrlPassword) ctrlPassword.value = d1.password || '';
        if (clarilusMode && d1.connection_mode) clarilusMode.value = d1.connection_mode;
        if (ctrlPort && d1.port) ctrlPort.value = d1.port;
        if (ctrlComPort) {
            await loadSerialPorts(d1.com_port || 'Auto');
        }

        if (ctrlType) {
            ctrlType.value = d1.controller_type || 'Televic';
            ctrlType.dispatchEvent(new Event('change'));
        }

        if (ctrlModel) {
            const desired = d1.controller_model || 'D-CERNO AE';
            ctrlModel.value = desired;
            if (!ctrlModel.value && desired) {
                for (let opt of ctrlModel.options) {
                    if (opt.value.toLowerCase().includes(desired.toLowerCase()) || desired.toLowerCase().includes(opt.value.toLowerCase())) {
                        ctrlModel.value = opt.value;
                        break;
                    }
                }
            }
            ctrlModel.dispatchEvent(new Event('change'));
        }

        updateControllerFormVisibility();

        const displayTarget = (d1.controller_type === 'Clarilus' && d1.connection_mode === 'serial')
            ? `Serial (${d1.com_port || 'Auto'})`
            : (d1.host_ip || "Unconnected");
        if (homeCtrlIp) homeCtrlIp.textContent = displayTarget;
        if (homeCtrlModel) homeCtrlModel.textContent = d1.controller_model || "None";
        checkConnection();

        const r2 = await fetch(`/api/get_camera_config?t=${ts}`);
        const d2 = await r2.json();
        
        try {
            const usbRes = await fetch('/api/scan_usb_cameras');
            const usbData = await usbRes.json();
            if (usbData && usbData.success) {
                window.discoveredUsbCameras = usbData.cameras || [];
            } else {
                window.discoveredUsbCameras = [];
            }
        } catch(e) { window.discoveredUsbCameras = []; }

        if (d2.num_cameras !== undefined && numCamerasInput) {
            numCamerasInput.value = d2.num_cameras;
            cameraConfigData = d2.camera_config || {};
            if (typeof generateCameraList === 'function') generateCameraList();

            if (homeCamCount) homeCamCount.textContent = d2.num_cameras;

            if (homeCamBrands) {
                const unique = [...new Set(Object.values(cameraConfigData).map(c => c.brand).filter(b => b))];
                homeCamBrands.textContent = unique.length > 0 ? unique.join(", ") : "None";
            }

            if (homeTrackStatus) {
                if (parseInt(d2.num_cameras) > 0) {
                    homeTrackStatus.textContent = "Ready";
                    homeTrackStatus.className = "px-2 py-0.5 rounded-full bg-green-100 text-green-700 text-xs font-bold uppercase";
                } else {
                    homeTrackStatus.textContent = "Not Configured";
                    homeTrackStatus.className = "px-2 py-0.5 rounded-full bg-orange-100 text-orange-700 text-xs font-bold uppercase";
                }
            }
        }

        const r3 = await fetch('/api/get_username');
        const d3 = await r3.json();
        const usernameSpan = document.getElementById('username-display');
        const localUser = localStorage.getItem('loggedInUser');
        const localRole = localStorage.getItem('userRole');
        if (usernameSpan) {
            if (localUser) {
                if (localUser.toLowerCase() === 'admin') {
                    usernameSpan.textContent = 'Admin';
                } else {
                    usernameSpan.textContent = localUser.charAt(0).toUpperCase() + localUser.slice(1);
                }
            } else if (d3 && d3.username) {
                if (d3.username.toLowerCase() === 'admin') {
                    usernameSpan.textContent = 'Admin';
                } else {
                    usernameSpan.textContent = d3.username.charAt(0).toUpperCase() + d3.username.slice(1);
                }
            } else {
                usernameSpan.textContent = 'Admin';
            }
        }

        const r4 = await fetch('/api/get_device_ip');
        const d4 = await r4.json();
        if (deviceIpDisplay) deviceIpDisplay.textContent = d4.ip;
        if (deviceIpDisplayHome) deviceIpDisplayHome.textContent = d4.ip;

        if (typeof getLayoutMode === 'function') await getLayoutMode();

    } catch (e) { console.error("Load Error:", e); }
}
window.loadData = loadData;

// =============================================================================
// 4. HOME DASHBOARD CLICK NAVIGATION & STARTUP
// =============================================================================

document.addEventListener('DOMContentLoaded', () => {
    updateControllerModels();
    if (ctrlType) ctrlType.dispatchEvent(new Event('change'));

    const homeCards = document.querySelectorAll('#page-home .bg-white.rounded-xl');
    homeCards.forEach((card, idx) => {
        card.style.cursor = 'pointer';
        card.addEventListener('click', () => {
            const setupBtn = document.getElementById('nav-setup');
            if (setupBtn) setupBtn.click();
            setTimeout(() => {
                if (idx === 0 && typeof switchTab === 'function') {
                    switchTab('tab-controller');
                } else if (idx === 1 && typeof switchTab === 'function') {
                    switchTab('tab-assign');
                }
            }, 50);
        });
    });

    loadData();
});
