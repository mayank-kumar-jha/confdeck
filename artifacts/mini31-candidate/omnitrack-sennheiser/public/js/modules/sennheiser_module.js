/**
 * Sennheiser Visual Seats & Sound Exclusions Module for OmniTrack
 * Fulfills exact older UI component in Microphone Mapping page (#page-mapping)
 */
(function() {
    'use strict';

    const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
    const wrap = a => (a % 360 + 360) % 360;
    const delta = (a, b) => (a - b + 540) % 360 - 180;
    const point = (a, e) => [
        260 + Math.sin(a * Math.PI / 180) * (35 + e * 2),
        260 - Math.cos(a * Math.PI / 180) * (35 + e * 2)
    ];
    const angles = (x, y) => [
        wrap(Math.atan2(x - 260, 260 - y) * 180 / Math.PI),
        clamp((Math.hypot(x - 260, y - 260) - 35) / 2, 0, 90)
    ];
    const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

    // Insert CSS styling for the Visual Seats & Sound Exclusions component
    const styleEl = document.createElement('style');
    styleEl.textContent = `
        #sennheiser-mapping-panel {
            background: #ffffff;
            color: #0f172a;
            border-radius: 16px;
            border: 1px solid #e2e8f0;
            box-shadow: 0 2px 12px rgba(15, 23, 42, 0.05);
            margin-bottom: 24px;
            overflow: hidden;
            font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }
        #sennheiser-mapping-panel .dual-radar-view-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 18px 24px;
            background: #ffffff;
            border-bottom: 1px solid #e2e8f0;
            flex-wrap: wrap;
            gap: 14px;
        }
        #sennheiser-mapping-panel .dual-radar-view-header .title-group {
            display: flex;
            align-items: center;
            gap: 14px;
        }
        #sennheiser-mapping-panel .dual-radar-view-header h2 {
            font-size: 1.125rem;
            font-weight: 700;
            color: #0f172a;
            margin: 0;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        #sennheiser-mapping-panel .dual-radar-view-header p {
            font-size: 0.8rem;
            color: #64748b;
            margin: 2px 0 0;
        }
        #sennheiser-mapping-panel .view-toggle-group {
            display: flex;
            align-items: center;
            background: #f1f5f9;
            padding: 4px;
            border-radius: 10px;
            border: 1px solid #e2e8f0;
            gap: 4px;
        }
        #sennheiser-mapping-panel .view-toggle-btn {
            padding: 7px 14px;
            font-size: 12px;
            font-weight: 600;
            border-radius: 7px;
            border: none;
            cursor: pointer;
            transition: all 0.15s ease;
            color: #475569;
            background: transparent;
        }
        #sennheiser-mapping-panel .view-toggle-btn:hover {
            color: #0f172a;
            background: rgba(255, 255, 255, 0.7);
        }
        #sennheiser-mapping-panel .view-toggle-btn.active {
            background: #f58220;
            color: #ffffff;
            box-shadow: 0 1px 4px rgba(245, 130, 32, 0.3);
        }
        #sennheiser-mapping-panel .dual-radar-grid {
            display: grid;
            grid-template-columns: 1fr;
            gap: 24px;
            padding: 20px 24px 24px;
            background: #f8fafc;
        }
        @media (min-width: 1400px) {
            #sennheiser-mapping-panel .dual-radar-grid.layout-both {
                grid-template-columns: 1fr 1fr;
            }
        }
        #sennheiser-mapping-panel .radar-card-container {
            background: #ffffff;
            border-radius: 14px;
            border: 1px solid #e2e8f0;
            overflow: hidden;
            box-shadow: 0 1px 4px rgba(15, 23, 42, 0.04);
            display: flex;
            flex-direction: column;
        }
        #sennheiser-mapping-panel .radar-card-container.primary-card {
            border-top: 3px solid #f58220;
        }
        #sennheiser-mapping-panel .radar-card-container.secondary-card {
            border-top: 3px solid #059669;
        }
        #sennheiser-mapping-panel .radar-badge {
            background: #fff7ed;
            border: 1px solid #fdba74;
            color: #c2410c;
            padding: 3px 9px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        #sennheiser-mapping-panel .radar-badge.badge-primary {
            background: #fff7ed;
            border-color: #fdba74;
            color: #c2410c;
        }
        #sennheiser-mapping-panel .radar-badge.badge-secondary {
            background: #ecfdf5;
            border-color: #6ee7b7;
            color: #047857;
        }
        #sennheiser-mapping-panel .radar-panel-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 16px 22px;
            border-bottom: 1px solid #e2e8f0;
            background: #ffffff;
            gap: 12px;
            flex-wrap: wrap;
        }
        #sennheiser-mapping-panel .radar-panel-header h2 {
            font-size: 1.05rem;
            font-weight: 700;
            color: #0f172a;
            margin: 0;
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }
        #sennheiser-mapping-panel .radar-editor {
            display: grid;
            grid-template-columns: minmax(300px, 490px) 1fr;
            gap: 24px;
            padding: 20px 22px;
            background: #ffffff;
        }
        @media (max-width: 1080px) {
            #sennheiser-mapping-panel .radar-editor {
                grid-template-columns: 1fr;
            }
        }
        #sennheiser-mapping-panel .radar-canvas-box {
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        #sennheiser-mapping-panel .radar-canvas-box.expanded {
            position: fixed;
            inset: 16px;
            z-index: 9999;
            background: #ffffff;
            border-radius: 16px;
            border: 1px solid #cbd5e1;
            box-shadow: 0 25px 60px rgba(15, 23, 42, 0.35);
            padding: 18px 24px;
            overflow-y: auto;
            justify-content: flex-start;
        }
        #sennheiser-mapping-panel .radar-canvas-box.expanded canvas {
            max-width: min(72vh, 760px);
            width: min(72vh, 760px);
        }
        #sennheiser-mapping-panel .radar-zoom-bar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 8px;
            width: 100%;
            max-width: 480px;
            margin-bottom: 8px;
        }
        #sennheiser-mapping-panel .radar-canvas-box.expanded .radar-zoom-bar {
            max-width: min(72vh, 760px);
        }
        #sennheiser-mapping-panel .radar-zoom-btn {
            padding: 4px 9px;
            font-size: 11px;
            font-weight: 600;
            border-radius: 7px;
            border: 1px solid #cbd5e1;
            background: #ffffff;
            color: #334155;
            cursor: pointer;
        }
        #sennheiser-mapping-panel .radar-zoom-btn:hover {
            background: #f1f5f9;
        }
        #sennheiser-mapping-panel .radar-zoom-btn.active {
            background: #2563eb;
            border-color: #2563eb;
            color: #ffffff;
        }
        #sennheiser-mapping-panel canvas {
            width: 100%;
            max-width: 480px;
            aspect-ratio: 1/1;
            background: #f8fafc;
            border-radius: 16px;
            border: 1px solid #e2e8f0;
            touch-action: none;
            box-shadow: inset 0 1px 4px rgba(15, 23, 42, 0.04);
            cursor: crosshair;
        }
        #sennheiser-mapping-panel canvas:focus {
            outline: 2px solid #f58220;
            outline-offset: 2px;
        }
        #sennheiser-mapping-panel .radar-legend-row {
            display: flex;
            flex-wrap: wrap;
            justify-content: center;
            gap: 8px;
            margin-top: 12px;
        }
        #sennheiser-mapping-panel .radar-legend-chip {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 3px 9px;
            border-radius: 999px;
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            font-size: 11px;
            font-weight: 600;
            color: #475569;
        }
        #sennheiser-mapping-panel .radar-caption {
            font-size: 12px;
            color: #64748b;
            margin-top: 8px;
            text-align: center;
            line-height: 1.45;
        }
        #sennheiser-mapping-panel .radar-status-line {
            font-size: 12.5px;
            font-weight: 600;
            color: #334155;
            margin-top: 6px;
            text-align: center;
        }
        #sennheiser-mapping-panel .radar-warning-line {
            font-size: 12px;
            font-weight: 600;
            color: #dc2626;
            margin-top: 4px;
            text-align: center;
            min-height: 16px;
        }
        #sennheiser-mapping-panel .radar-stack {
            display: flex;
            flex-direction: column;
            gap: 14px;
        }
        #sennheiser-mapping-panel .radar-section-card {
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 12px;
            padding: 14px;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        #sennheiser-mapping-panel label {
            display: flex;
            flex-direction: column;
            gap: 5px;
            font-size: 11px;
            font-weight: 700;
            color: #475569;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        #sennheiser-mapping-panel input[type="text"],
        #sennheiser-mapping-panel select {
            background: #ffffff;
            color: #0f172a;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            padding: 8px 12px;
            font-size: 13.5px;
            font-weight: 500;
            outline: none;
            transition: border-color 0.15s, box-shadow 0.15s;
        }
        #sennheiser-mapping-panel input[type="text"]:focus,
        #sennheiser-mapping-panel select:focus {
            border-color: #f58220;
            box-shadow: 0 0 0 3px rgba(245, 130, 32, 0.15);
        }
        #sennheiser-mapping-panel .radar-actions {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }
        #sennheiser-mapping-panel button {
            background: #ffffff;
            color: #1e293b;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            padding: 8px 14px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
        }
        #sennheiser-mapping-panel button:hover:not(:disabled) {
            background: #f8fafc;
            border-color: #94a3b8;
        }
        #sennheiser-mapping-panel button:disabled {
            opacity: 0.45;
            cursor: not-allowed;
        }
        #sennheiser-mapping-panel button.primary,
        #sennheiser-mapping-panel button[data-save],
        #sennheiser-mapping-panel button[data-apply] {
            background: #f58220;
            color: #ffffff;
            border-color: #f58220;
            font-weight: 700;
        }
        #sennheiser-mapping-panel button.primary:hover:not(:disabled),
        #sennheiser-mapping-panel button[data-save]:hover:not(:disabled),
        #sennheiser-mapping-panel button[data-apply]:hover:not(:disabled) {
            background: #ea580c;
            border-color: #ea580c;
        }
        #sennheiser-mapping-panel button[data-lock] {
            background: #1e293b;
            color: #ffffff;
            border-color: #1e293b;
        }
        #sennheiser-mapping-panel button[data-lock]:hover {
            background: #334155;
            border-color: #334155;
        }
        #sennheiser-mapping-panel button[data-lock].unlocked {
            background: #f58220;
            color: #ffffff;
            border-color: #f58220;
        }
        #sennheiser-mapping-panel button[data-delete] {
            background: #fff1f2;
            color: #be123c;
            border-color: #fecdd3;
        }
        #sennheiser-mapping-panel button[data-delete]:hover:not(:disabled) {
            background: #ffe4e6;
            border-color: #fda4af;
        }
        #sennheiser-mapping-panel fieldset {
            border: 1px solid #e2e8f0;
            background: #f8fafc;
            border-radius: 10px;
            padding: 12px 14px;
            margin: 2px 0;
        }
        #sennheiser-mapping-panel legend {
            font-size: 11px;
            font-weight: 700;
            color: #475569;
            text-transform: uppercase;
            padding: 0 6px;
        }
        #sennheiser-mapping-panel .preset-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
            gap: 10px;
        }
        #sennheiser-mapping-panel hr {
            border: none;
            border-top: 1px solid #e2e8f0;
            margin: 10px 0;
        }
        #sennheiser-mapping-panel h3 {
            font-size: 14px;
            font-weight: 700;
            color: #0f172a;
            margin: 0;
        }
        #sennheiser-mapping-panel .radar-device-box {
            border-top: 1px solid #e2e8f0;
            padding-top: 12px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        #sennheiser-mapping-panel details {
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            padding: 10px 14px;
            margin: 4px 0;
        }
        #sennheiser-mapping-panel summary {
            cursor: pointer;
            font-weight: 600;
            font-size: 13px;
            color: #c2410c;
            outline: none;
        }
        #sennheiser-mapping-panel input[type="range"] {
            accent-color: #f58220;
            height: 6px;
            border-radius: 999px;
            background: #e2e8f0;
            cursor: pointer;
        }
        #sennheiser-mapping-panel output {
            font-size: 12px;
            font-weight: 700;
            color: #ea580c;
            margin-left: 6px;
        }
        #sennheiser-toast {
            position: fixed;
            bottom: 24px;
            right: 24px;
            background: #0f172a;
            color: #ffffff;
            border: 1px solid #334155;
            padding: 12px 20px;
            border-radius: 10px;
            box-shadow: 0 8px 24px rgba(15, 23, 42, 0.25);
            font-size: 13.5px;
            font-weight: 600;
            z-index: 99999;
            opacity: 0;
            transform: translateY(10px);
            transition: all 0.25s ease;
            pointer-events: none;
        }
        #sennheiser-toast.show {
            opacity: 1;
            transform: translateY(0);
        }
        #sennheiser-toast.error {
            background: #7f1d1d;
            border-color: #f87171;
            color: #fef2f2;
        }

        /* ==========================================================================
           FLOATING AUTO-TRACKING BUTTON (BOTTOM-LEFT)
           ========================================================================== */
        #sennheiser-tracking-floating-pill {
            position: fixed;
            bottom: 22px;
            left: 88px;
            z-index: 9999;
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 9px 16px;
            border-radius: 9999px;
            cursor: pointer;
            user-select: none;
            transition: all 0.28s cubic-bezier(0.4, 0, 0.2, 1);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Poppins, sans-serif;
            box-sizing: border-box;
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
        }

        #main-sidebar.sidebar-expanded ~ * #sennheiser-tracking-floating-pill,
        body.sidebar-is-expanded #sennheiser-tracking-floating-pill {
            left: 240px;
        }

        /* ON State (Active Emerald Gradient) */
        #sennheiser-tracking-floating-pill.senn-pill-on {
            background: linear-gradient(135deg, rgba(6, 78, 59, 0.95) 0%, rgba(4, 120, 87, 0.95) 50%, rgba(5, 150, 105, 0.95) 100%) !important;
            border: 1.5px solid #34d399 !important;
            color: #ecfdf5 !important;
            box-shadow: 0 10px 25px -4px rgba(16, 185, 129, 0.5), 0 0 15px rgba(52, 211, 153, 0.35) !important;
        }
        #sennheiser-tracking-floating-pill.senn-pill-on:hover {
            transform: translateY(-2px) scale(1.02);
            box-shadow: 0 12px 28px -3px rgba(16, 185, 129, 0.65), 0 0 20px rgba(52, 211, 153, 0.45) !important;
        }

        /* OFF State (Dark Slate Gradient) */
        #sennheiser-tracking-floating-pill.senn-pill-off {
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(30, 41, 59, 0.95) 100%) !important;
            border: 1.5px solid rgba(148, 163, 184, 0.35) !important;
            color: #f1f5f9 !important;
            box-shadow: 0 8px 20px -4px rgba(0, 0, 0, 0.45) !important;
        }
        #sennheiser-tracking-floating-pill.senn-pill-off:hover {
            transform: translateY(-2px) scale(1.02);
            border-color: rgba(255, 138, 61, 0.6) !important;
            box-shadow: 0 10px 24px -4px rgba(0, 0, 0, 0.55) !important;
        }

        /* UNCONFIGURED State */
        #sennheiser-tracking-floating-pill.senn-pill-unconfigured {
            background: linear-gradient(135deg, rgba(30, 41, 59, 0.92) 0%, rgba(41, 37, 36, 0.92) 100%) !important;
            border: 1.5px dashed #f59e0b !important;
            color: #fef3c7 !important;
        }
        #sennheiser-tracking-floating-pill.senn-pill-unconfigured:hover {
            transform: translateY(-2px) scale(1.02);
            border-color: #fbbf24 !important;
        }

        /* Icon Wrapper & Pulsing Ring */
        .senn-pill-icon-wrap {
            position: relative;
            width: 32px;
            height: 32px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 50%;
            flex-shrink: 0;
            transition: all 0.25s ease;
        }
        .senn-pill-on .senn-pill-icon-wrap {
            background: rgba(16, 185, 129, 0.25);
        }
        .senn-pill-off .senn-pill-icon-wrap {
            background: rgba(148, 163, 184, 0.15);
        }
        .senn-pill-unconfigured .senn-pill-icon-wrap {
            background: rgba(245, 158, 11, 0.2);
        }

        .senn-pill-pulse-ring {
            position: absolute;
            inset: -4px;
            border-radius: 50%;
            border: 2px solid #34d399;
            opacity: 0;
            pointer-events: none;
        }
        .senn-pill-on .senn-pill-pulse-ring {
            animation: senn-pulse-ring 2s cubic-bezier(0.24, 0, 0.38, 1) infinite;
        }
        @keyframes senn-pulse-ring {
            0% { transform: scale(0.9); opacity: 0.9; }
            70% { transform: scale(1.4); opacity: 0; }
            100% { transform: scale(1.4); opacity: 0; }
        }

        .senn-pill-icon {
            width: 20px;
            height: 20px;
            transition: stroke 0.25s ease;
        }
        .senn-pill-on .senn-pill-icon {
            stroke: #6ee7b7;
        }
        .senn-pill-off .senn-pill-icon {
            stroke: #94a3b8;
        }
        .senn-pill-unconfigured .senn-pill-icon {
            stroke: #fbbf24;
        }

        /* Pill Text Content */
        .senn-pill-content {
            display: flex;
            flex-direction: column;
            justify-content: center;
            line-height: 1.25;
        }
        .senn-pill-title {
            font-size: 9.5px;
            font-weight: 800;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            opacity: 0.85;
        }
        .senn-pill-status {
            font-size: 12.5px;
            font-weight: 700;
            letter-spacing: 0.02em;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .senn-pill-on .senn-pill-status {
            color: #a7f3d0;
        }
        .senn-pill-off .senn-pill-status {
            color: #cbd5e1;
        }
        .senn-pill-unconfigured .senn-pill-status {
            color: #fde68a;
        }

        /* Pill Toggle Switch */
        .senn-pill-switch {
            width: 38px;
            height: 20px;
            border-radius: 9999px;
            position: relative;
            transition: all 0.25s ease;
            flex-shrink: 0;
            margin-left: 2px;
        }
        .senn-pill-on .senn-pill-switch {
            background: #047857;
            box-shadow: inset 0 2px 4px rgba(0,0,0,0.25);
        }
        .senn-pill-off .senn-pill-switch {
            background: #334155;
            box-shadow: inset 0 2px 4px rgba(0,0,0,0.35);
        }
        .senn-pill-unconfigured .senn-pill-switch {
            background: #78350f;
        }

        .senn-pill-knob {
            position: absolute;
            top: 2px;
            left: 2px;
            width: 16px;
            height: 16px;
            border-radius: 50%;
            transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1), background-color 0.25s ease;
            box-shadow: 0 2px 4px rgba(0,0,0,0.3);
        }
        .senn-pill-on .senn-pill-knob {
            background: #34d399;
            transform: translateX(18px);
        }
        .senn-pill-off .senn-pill-knob {
            background: #94a3b8;
            transform: translateX(0);
        }
        .senn-pill-unconfigured .senn-pill-knob {
            background: #f59e0b;
            transform: translateX(0);
        }

        /* Floating Pill Notification Toast */
        #senn-floating-toast {
            position: fixed;
            bottom: 80px;
            left: 88px;
            z-index: 10000;
            background: rgba(15, 23, 42, 0.95);
            color: #ffffff;
            border: 1px solid rgba(255, 255, 255, 0.16);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            padding: 9px 16px;
            border-radius: 12px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.45);
            font-size: 12px;
            font-weight: 600;
            pointer-events: none;
            opacity: 0;
            transform: translateY(8px);
            transition: all 0.25s ease;
            display: flex;
            align-items: center;
            gap: 8px;
            max-width: 380px;
        }
        #senn-floating-toast.show {
            opacity: 1;
            transform: translateY(0);
        }
    `;
    document.head.appendChild(styleEl);

    // Toast helper
    let toastNode = document.getElementById('sennheiser-toast');
    if (!toastNode) {
        toastNode = document.createElement('div');
        toastNode.id = 'sennheiser-toast';
        document.body.appendChild(toastNode);
    }
    let toastTimer = null;
    function toast(msg, error = false) {
        toastNode.textContent = msg;
        toastNode.className = error ? 'show error' : 'show';
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { toastNode.className = ''; }, 4500);
    }

    // API Helper communicating through OmniTrack proxy
    async function api(endpoint, body, method) {
        const url = '/api/sennheiser/' + endpoint.replace(/^\/api\//, '');
        const options = {
            method: method || (body === undefined ? 'GET' : 'POST'),
            headers: { 'Content-Type': 'application/json', 'X-Sennheiser': '1' }
        };
        if (body !== undefined) {
            options.body = JSON.stringify(body);
        }
        const res = await fetch(url, options);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            const err = new Error(data.error?.title || data.error?.detail || data.error || `HTTP ${res.status}`);
            err.detail = data.error;
            err.status = res.status;
            throw err;
        }
        return data;
    }

    // Visual Seats & Sound Exclusions Component Logic
    class SennheiserVisualRadar {
        constructor(container, source = 'primary', micInfo = null) {
            this.container = container;
            this.source = source || 'primary';
            this.micInfo = micInfo;
            this.locked = true;
            this.undo = [];
            this.redo = [];
            this.revision = 0;
            this.saved = 0;
            this.selected = null;
            this.draft = null;
            this.view = null;
            this.scenes = [];
            this.cameras = [];
            this.device = null;
            this.deviceDirty = new Set();
            this.exclusionKey = null;
            this.drag = null;
            this.frame = null;
            this.pollTimer = null;
            this.pollInFlight = false;
            this.highlightSeat = null;
            this.highlightUntil = 0;
            this.lastConfirmed = null;
            this.microphones = [];
            this.activeMic = null;
            this.zoom = 1;
            this.panX = 0;
            this.panY = 0;
            this.expanded = false;
            this.hoverAngles = null;

            this.init();
        }

        async init() {
            const isPrimary = (this.source === 'primary');
            const accentColor = isPrimary ? '#F58220' : '#059669';
            const micTitle = isPrimary ? 'Sennheiser TCC2' : 'Sennheiser TCCM';
            const micShort = isPrimary ? 'TCC2' : 'TCCM';
            const badgeClass = isPrimary ? 'badge-primary' : 'badge-secondary';
            const legendChips = `
                <div class="radar-legend-row">
                    <span class="radar-legend-chip"><span style="width:8px;height:8px;border-radius:999px;background:${accentColor};display:inline-block;"></span>${micShort} Beam</span>
                    <span class="radar-legend-chip"><span style="width:8px;height:8px;border-radius:999px;background:#64748b;display:inline-block;"></span>Seat Zone</span>
                    <span class="radar-legend-chip"><span style="width:8px;height:8px;border-radius:999px;background:#F58220;display:inline-block;"></span>Active Lock</span>
                    <span class="radar-legend-chip"><span style="width:8px;height:8px;border-radius:2px;background:#ef4444;display:inline-block;"></span>Exclusion</span>
                </div>
            `;

            this.container.innerHTML = `
                <div class="radar-panel-header">
                    <h2>
                        <svg class="w-5 h-5" style="color:${accentColor};" fill="none" stroke="currentColor" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" stroke-width="2"></circle><circle cx="12" cy="12" r="3" stroke-width="2"></circle><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3v3M12 18v3M3 12h3M18 12h3"></path></svg>
                        <span>Room Seat Map</span>
                        <span class="radar-badge ${badgeClass}">${micTitle}</span>
                    </h2>
                    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                        <button type="button" data-expand-map style="padding:6px 12px;font-size:12px;font-weight:600;border-radius:8px;border:1px solid #cbd5e1;background:#f8fafc;color:#1e293b;cursor:pointer;">⛶ Enlarge Map</button>
                        <button data-lock>Unlock setup</button>
                    </div>
                </div>
                <div class="radar-editor">
                    <div class="radar-canvas-box">
                        <div class="radar-zoom-bar">
                            <div style="display:flex;align-items:center;gap:4px;flex-wrap:wrap;">
                                <span style="font-size:11px;font-weight:700;color:#64748b;margin-right:2px;">Zoom:</span>
                                <button type="button" class="radar-zoom-btn active" data-zoom="1">1×</button>
                                <button type="button" class="radar-zoom-btn" data-zoom="1.5">1.5×</button>
                                <button type="button" class="radar-zoom-btn" data-zoom="2">2×</button>
                                <button type="button" class="radar-zoom-btn" data-zoom="3">3×</button>
                                <button type="button" class="radar-zoom-btn" data-zoom-reset>Reset</button>
                            </div>
                            <div style="display:flex;align-items:center;gap:6px;">
                                <span data-cursor-coords style="font-size:11px;font-weight:600;color:#334155;background:#f1f5f9;border:1px solid #e2e8f0;padding:3px 8px;border-radius:999px;font-variant-numeric:tabular-nums;">Cursor: —</span>
                                <button type="button" class="radar-zoom-btn" data-close-expand style="display:none;background:#0f172a;color:#ffffff;border-color:#0f172a;">✕ Close</button>
                            </div>
                        </div>
                        <canvas width="1040" height="1040" tabindex="0" aria-label="${micTitle} Radar"></canvas>
                        ${legendChips}
                        <p data-map-caption class="radar-caption">Center = directly below ceiling mic · Outer edge = room perimeter. Scroll or use Zoom to enlarge any corner; drag background to pan.</p>
                        <div style="width:100%;max-width:480px;margin-top:8px;padding:10px 14px;border-radius:10px;background:#f8fafc;border:1px solid #e2e8f0;">
                            <p data-position class="radar-status-line" style="margin:0;color:${accentColor};font-variant-numeric:tabular-nums;">Connecting to ${micTitle}…</p>
                            <p data-speech-status class="radar-status-line" role="status" style="margin:3px 0 0;font-size:11.5px;color:#64748b;font-weight:500;">Checking speech validation…</p>
                            <p data-note class="radar-caption" style="margin:4px 0 0;font-size:11.5px;">Live mode is locked. Unlock setup to edit seats or presets.</p>
                        </div>
                        <p data-warning class="radar-warning-line"></p>
                    </div>
                    <div class="radar-stack">
                        <div class="radar-section-card">
                            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
                                <span style="font-size:11px;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:#64748b;">Seat Position &amp; Range</span>
                            </div>
                            <label>Select Seat
                                <select data-seat><option value="">Choose a seat</option></select>
                            </label>
                            <div class="radar-actions">
                                <button data-add>+ Add seat</button>
                                <button data-capture>Snap to live beam</button>
                            </div>
                            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;align-items:end;">
                                <label>Seat Name
                                    <input data-name type="text" maxlength="80" placeholder="e.g. Seat 1">
                                </label>
                                <label data-radius-wrap>Pickup Radius
                                    <div style="display:flex;align-items:center;gap:8px;min-height:38px;">
                                        <input data-radius type="range" min="6" max="90" step="1" style="flex:1">
                                        <output data-range style="min-width:42px;text-align:right;">±25°</output>
                                    </div>
                                </label>
                            </div>
                            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;align-items:end;">
                                <label>Azimuth (°)
                                    <input data-az-input type="number" min="0" max="359.9" step="0.5" placeholder="0.0" style="font-variant-numeric:tabular-nums;">
                                </label>
                                <label>Elevation (°)
                                    <input data-el-input type="number" min="0" max="90" step="0.5" placeholder="45.0" style="font-variant-numeric:tabular-nums;">
                                </label>
                            </div>
                        </div>

                        <div class="radar-section-card">
                            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
                                <span style="font-size:11px;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:#64748b;">Microphone Orientation</span>
                                <span data-mic-orient-readout style="font-size:11px;font-weight:600;color:#475569;">Offset: 0°</span>
                            </div>
                            <div style="display:grid;grid-template-columns:1fr 84px;gap:8px;align-items:end;">
                                <label style="margin:0;">Azimuth Rotation (°)
                                    <input data-mic-offset type="range" min="-180" max="180" step="1">
                                </label>
                                <input data-mic-offset-num type="number" min="-360" max="360" step="0.5" value="0" style="font-variant-numeric:tabular-nums;">
                            </div>
                            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;">
                                <label style="display:flex;align-items:center;gap:6px;margin:0;cursor:pointer;font-size:12px;font-weight:600;color:#334155;">
                                    <input data-mic-invert type="checkbox" style="width:auto;margin:0;">
                                    Invert / Mirror Azimuth
                                </label>
                                <div class="radar-actions" style="gap:6px;">
                                    <button type="button" data-mic-zero>Set 0° = Live Beam</button>
                                    <button type="button" data-mic-orient-save>Apply</button>
                                </div>
                            </div>
                        </div>

                        <div class="radar-section-card">
                            <span style="font-size:11px;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:#64748b;">Camera &amp; Preset Link</span>
                            <label>Camera Scene
                                <select data-scene><option value="">No scene</option></select>
                            </label>
                            <fieldset data-preset-fields style="background:#ffffff!important;margin:0;">
                                <legend>Camera Presets</legend>
                                <div data-presets class="preset-grid"></div>
                            </fieldset>
                        </div>

                        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;padding-top:2px;">
                            <div class="radar-actions">
                                <button data-undo>Undo</button>
                                <button data-redo>Redo</button>
                                <button data-delete>Delete seat</button>
                            </div>
                            <button data-save class="primary">Save seats</button>
                        </div>

                        <details data-device-box class="radar-device-box" style="margin:0;">
                            <summary>Hardware Noise Exclusions</summary>
                            <div style="margin-top:10px;display:flex;flex-direction:column;gap:8px;">
                                <button data-read style="align-self:flex-start;">Read hardware exclusions</button>
                                <div data-device-panel></div>
                            </div>
                        </details>
                    </div>
                </div>
            `;

            this.bindEvents();
            await this.loadInitialState();
            this.startPolling();
        }

        updateBadge() {
            try {
                const badge = this.q('.radar-badge');
                if (!badge) return;
                const isPrimary = (this.source === 'primary');
                let prod = isPrimary ? 'TCC2' : 'TCCM';
                let ident = this.mic?.identity;
                if (typeof ident === 'string') {
                    try { ident = JSON.parse(ident); } catch (_) {}
                }
                if (ident && ident.product) {
                    prod = (ident.product === 'SLCM2') ? 'TCC2' : ident.product;
                }
                badge.textContent = `Sennheiser ${prod} (${isPrimary ? 'Primary' : 'Secondary'})`;
            } catch (e) {}
        }

        q(selector) {
            return this.container.querySelector(selector);
        }

        editable() {
            return !this.locked;
        }

        checkpoint() {
            if (!this.draft) return;
            this.undo.push(structuredClone(this.draft));
            if (this.undo.length > 40) this.undo.shift();
            this.redo.length = 0;
        }

        zones() {
            return (this.draft?.zones || []).filter(z => (z.mic_id || 'primary') === this.source);
        }

        seat() {
            return (this.draft?.zones || []).find(z => z.id === this.selected);
        }

        updateSpeechStatus(state) {
            const node = this.q('[data-speech-status]');
            if (!node) return;
            const speech = state.speech;
            node.textContent = !speech?.enabled ? 'Speech validation: off'
                : !speech.healthy ? 'Speech validation: unavailable · new seat locks paused'
                : state.tracking?.level_fallback?.includes(this.source) ? `${this.source === 'primary' ? 'TCC2' : 'TCCM'}: sound above room noise · level fallback active`
                : `Shared speech validation: ${speech.status} · both microphones`;
        }

        async loadInitialState() {
            try {
                const state = await api('state');
                this.draft = structuredClone(state.settings.tracking);
                this.scenes = state.settings.scenes || [];
                this.cameras = state.settings.cameras || [];
                this.view = state.tracking || {};
                this.microphones = state.microphones || [];
                this.activeMic = state.tracking?.active_mic || null;

                if (this.source === 'primary') {
                    this.mic = this.microphones.find(m => m.id === 'primary') || state.mic || {};
                } else {
                    this.mic = this.microphones.find(m => m.id === this.source) || this.microphones.find(m => m.id !== 'primary') || {};
                }
                this.micSettings = this.source === 'primary' ? (state.settings.mic || {}) : ((state.settings.microphones || []).find(m => m.id === this.source) || {});

                if (this.mic?.position && this.mic.position.azimuth != null) {
                    this.view.smoothed = [this.mic.position.azimuth, this.mic.position.elevation];
                }

                if (this.source === 'primary') {
                    const initHomeBeam = document.getElementById('home-ctrl-beam');
                    if (initHomeBeam && this.view?.smoothed) {
                        const lvl = (this.mic.position?.level != null) ? ` (${this.mic.position.level} dB)` : '';
                        initHomeBeam.textContent = `🎯 ${this.view.smoothed[0].toFixed(0)}° Az / ${this.view.smoothed[1].toFixed(0)}° El${lvl}`;
                    }
                }

                this.updateSpeechStatus(state);
                this.updateBadge();
                this.fillSeatsDropdown();
                this.fillScenesDropdown();
                this.fill();
                this.paint();
            } catch (err) {
                console.error(`Failed to load initial state for radar [${this.source}]:`, err);
                toast('Could not connect to Sennheiser engine: ' + err.message, true);
            }
        }

        startPolling() {
            if (this.pollTimer) clearInterval(this.pollTimer);
            this.pollTimer = setInterval(async () => {
                if (this.pollInFlight || document.hidden) return;
                this.pollInFlight = true;
                try {
                    const state = await api('state');
                    this.microphones = state.microphones || [];
                    this.activeMic = state.tracking?.active_mic || null;
                    this.view = {...state.tracking, ...(state.tracking?.sources?.[this.source] || {}), current: state.tracking?.current};
                    this.micSettings = this.source === 'primary' ? (state.settings.mic || {}) : ((state.settings.microphones || []).find(m => m.id === this.source) || {});
                    if (this.source === 'primary') window._dualSennheiserManager?.updateTeam(state);

                    if (this.source === 'primary') {
                        this.mic = this.microphones.find(m => m.id === 'primary') || state.mic || {};
                    } else {
                        this.mic = this.microphones.find(m => m.id === this.source) || this.microphones.find(m => m.id !== 'primary') || {};
                    }

                    const pos = this.mic?.position;
                    if (pos && pos.azimuth != null) {
                        this.view.smoothed = [pos.azimuth, pos.elevation];
                    }

                    if (this.source === 'primary') {
                        const homeBeam = document.getElementById('home-ctrl-beam');
                        if (homeBeam && this.view?.smoothed) {
                            const lvl = (pos?.level != null) ? ` (${pos.level} dB)` : '';
                            homeBeam.textContent = `🎯 ${this.view.smoothed[0].toFixed(0)}° Az / ${this.view.smoothed[1].toFixed(0)}° El${lvl}`;
                        }
                    }

                    this.updateSpeechStatus(state);
                    this.paint();
                } catch (e) {
                } finally {
                    this.pollInFlight = false;
                }
            }, 250);
        }

        stop() {
            if (this.pollTimer) {
                clearInterval(this.pollTimer);
                this.pollTimer = null;
            }
        }

        bindEvents() {
            // Lock / Unlock toggle
            this.q('[data-lock]').onclick = () => {
                this.locked = !this.locked;
                const btn = this.q('[data-lock]');
                btn.textContent = this.locked ? 'Unlock setup' : 'Lock live mode';
                btn.className = this.locked ? '' : 'unlocked';
                this.q('[data-note]').textContent = this.locked
                    ? 'Live mode is locked. Unlock setup to edit.'
                    : 'Setup unlocked. Drag seats or adjust error range freely.';
                this.updateControlStates();
                this.paint();
            };

            // Seat selection
            this.q('[data-seat]').onchange = e => {
                this.selected = e.target.value;
                this.fill();
                this.paint();
            };

            // Add seat
            this.q('[data-add]').onclick = () => {
                if (!this.editable()) return;
                this.checkpoint();
                const [a, el] = this.view?.smoothed || [0, 45];
                const countOnMic = this.zones().length;
                const micPrefix = (this.source === 'primary') ? 'TCC2' : 'TCCM';
                const newSeat = {
                    id: crypto.randomUUID ? crypto.randomUUID().replaceAll('-', '') : ('seat_' + Date.now()),
                    name: `${micPrefix} Seat ${countOnMic + 1}`,
                    mic_id: this.source,
                    azimuth: Math.round(a * 10) / 10,
                    elevation: Math.round(el * 10) / 10,
                    radius: Math.max(25, (this.draft.zone_margin_deg || 5) + 1),
                    presets: {},
                    scene_id: ''
                };
                this.draft.zones.push(newSeat);
                this.selected = newSeat.id;
                this.fillSeatsDropdown();
                this.fillScenesDropdown();
                this.fill();
                this.changed();
            };

            // Use live beam position
            this.q('[data-capture]').onclick = () => {
                const z = this.seat();
                if (!z || !this.editable()) return;
                if (!this.view?.smoothed) {
                    toast('Waiting for a fresh microphone position reading.', true);
                    return;
                }
                this.checkpoint();
                z.azimuth = Math.round(this.view.smoothed[0] * 10) / 10;
                z.elevation = Math.round(this.view.smoothed[1] * 10) / 10;
                this.fill();
                this.changed();
                toast(`Seat updated to live beam (${z.azimuth.toFixed(1)}°, ${z.elevation.toFixed(1)}°)`);
            };

            // Direct numeric Azimuth / Elevation coordinate inputs
            this.q('[data-az-input]').oninput = e => {
                const z = this.seat();
                const val = parseFloat(e.target.value);
                if (!z || !this.editable() || !Number.isFinite(val)) return;
                z.azimuth = Math.round(wrap(val) * 10) / 10;
                this.changed();
            };
            this.q('[data-el-input]').oninput = e => {
                const z = this.seat();
                const val = parseFloat(e.target.value);
                if (!z || !this.editable() || !Number.isFinite(val)) return;
                z.elevation = Math.round(clamp(val, 0, 90) * 10) / 10;
                this.changed();
            };

            // Enlarge / Close fullscreen circular map
            const setExpanded = (on) => {
                this.expanded = Boolean(on);
                const box = this.q('.radar-canvas-box');
                const closeBtn = this.q('[data-close-expand]');
                const expandBtn = this.q('[data-expand-map]');
                if (box) box.classList.toggle('expanded', this.expanded);
                if (closeBtn) closeBtn.style.display = this.expanded ? 'inline-block' : 'none';
                if (expandBtn) expandBtn.textContent = this.expanded ? '✕ Exit Enlarge' : '⛶ Enlarge Map';
                this.paint();
            };
            this.q('[data-expand-map]').onclick = () => setExpanded(!this.expanded);
            this.q('[data-close-expand]').onclick = () => setExpanded(false);
            window.addEventListener('keydown', e => {
                if (e.key === 'Escape' && this.expanded) setExpanded(false);
            });

            // Zoom presets & Reset View
            this.container.querySelectorAll('[data-zoom]').forEach(btn => {
                btn.onclick = () => {
                    const targetZoom = parseFloat(btn.dataset.zoom) || 1;
                    // Focus zoom on selected seat or live beam if coming from 1x
                    if (this.zoom === 1 && targetZoom > 1) {
                        const focus = this.seat() ? [this.seat().azimuth, this.seat().elevation] : this.view?.smoothed;
                        if (focus) {
                            const [fx, fy] = point(focus[0], focus[1]);
                            this.panX = (260 - fx) * targetZoom;
                            this.panY = (260 - fy) * targetZoom;
                        }
                    } else if (targetZoom === 1) {
                        this.panX = 0;
                        this.panY = 0;
                    }
                    this.zoom = targetZoom;
                    this.updateZoomButtons();
                    this.paint();
                };
            });
            this.q('[data-zoom-reset]').onclick = () => {
                this.zoom = 1;
                this.panX = 0;
                this.panY = 0;
                this.updateZoomButtons();
                this.paint();
            };

            // Seat name and radius changes
            this.q('[data-name]').oninput = () => {
                const z = this.seat();
                if (!z || !this.editable()) return;
                z.name = this.q('[data-name]').value.trim() || z.name;
                const opt = this.q(`[data-seat] option[value="${z.id}"]`);
                if (opt) opt.textContent = z.name;
                this.changed();
            };

            this.q('[data-radius]').oninput = e => {
                const z = this.seat();
                if (!z || !this.editable()) return;
                z.radius = parseInt(e.target.value, 10);
                this.q('[data-range]').textContent = `±${z.radius}°`;
                this.changed();
            };

            // Scene change
            this.q('[data-scene]').onchange = e => {
                const z = this.seat();
                if (!z || !this.editable()) return;
                this.checkpoint();
                z.scene_id = e.target.value;
                this.fillPresets();
                this.changed();
            };

            // Delete seat
            this.q('[data-delete]').onclick = () => {
                if (!this.editable() || !this.selected) return;
                this.checkpoint();
                this.draft.zones = (this.draft.zones || []).filter(z => z.id !== this.selected);
                const remaining = this.zones();
                this.selected = remaining.length ? remaining[0].id : null;
                this.fillSeatsDropdown();
                this.fill();
                this.changed();
            };

            // Undo & Redo
            this.q('[data-undo]').onclick = () => {
                if (!this.editable() || !this.undo.length) return;
                this.redo.push(structuredClone(this.draft));
                this.draft = this.undo.pop();
                const available = this.zones().map(z => z.id);
                if (!available.includes(this.selected)) {
                    this.selected = available[0] || null;
                }
                this.fillSeatsDropdown();
                this.fill();
                this.changed();
            };

            this.q('[data-redo]').onclick = () => {
                if (!this.editable() || !this.redo.length) return;
                this.undo.push(structuredClone(this.draft));
                this.draft = this.redo.pop();
                const available = this.zones().map(z => z.id);
                if (!available.includes(this.selected)) {
                    this.selected = available[0] || null;
                }
                this.fillSeatsDropdown();
                this.fill();
                this.changed();
            };

            // Save seats
            this.q('[data-save]').onclick = () => this.save();

            // Read device hardware exclusions
            this.q('[data-read]').onclick = () => this.readDevice();

            // Mic Orientation controls (Azimuth Offset + Invert + Zero to live beam)
            const syncOffsetUI = (val) => {
                const v = clamp(Math.round((parseFloat(val) || 0) * 10) / 10, -360, 360);
                if (!this.micSettings) this.micSettings = {};
                this.micSettings.azimuth_offset = v;
                this.q('[data-mic-offset]').value = clamp(v, -180, 180);
                this.q('[data-mic-offset-num]').value = v;
                const ro = this.q('[data-mic-orient-readout]');
                if (ro) ro.textContent = `Offset: ${v}°${this.micSettings.invert ? ' · Mirrored' : ''}`;
            };
            this.q('[data-mic-offset]').oninput = e => syncOffsetUI(e.target.value);
            this.q('[data-mic-offset-num]').oninput = e => syncOffsetUI(e.target.value);
            this.q('[data-mic-invert]').onchange = e => {
                if (!this.micSettings) this.micSettings = {};
                this.micSettings.invert = Boolean(e.target.checked);
                syncOffsetUI(this.micSettings.azimuth_offset || 0);
            };
            this.q('[data-mic-zero]').onclick = async () => {
                const raw = this.mic?.position?.device_angles?.[0];
                const cur = this.view?.smoothed?.[0];
                if (raw == null && cur == null) {
                    toast('Waiting for live microphone beam.', true);
                    return;
                }
                const inv = Boolean(this.q('[data-mic-invert]').checked);
                let newOffset;
                if (raw != null) {
                    newOffset = -((inv ? -1 : 1) * raw);
                } else {
                    newOffset = (this.micSettings?.azimuth_offset || 0) - cur;
                }
                newOffset = ((newOffset % 360) + 540) % 360 - 180;
                syncOffsetUI(Math.round(newOffset * 10) / 10);
                await this.saveOrientation();
            };
            this.q('[data-mic-orient-save]').onclick = () => this.saveOrientation();

            // Canvas Pointer, Hover Coordinates & Wheel Zoom Events
            const canvas = this.q('canvas');
            canvas.onpointerdown = e => this.onPointerDown(e);
            canvas.onpointerleave = () => {
                this.hoverAngles = null;
                const el = this.q('[data-cursor-coords]');
                if (el) el.textContent = 'Cursor: —';
            };
            canvas.addEventListener('wheel', e => {
                e.preventDefault();
                const [vx, vy] = this.coordinates(e);
                const [lx, ly] = this.unviewPt(vx, vy);
                const factor = e.deltaY < 0 ? 1.2 : (1 / 1.2);
                const nextZoom = clamp(Math.round(this.zoom * factor * 100) / 100, 1, 4);
                this.zoom = nextZoom;
                if (nextZoom <= 1.01) {
                    this.zoom = 1;
                    this.panX = 0;
                    this.panY = 0;
                } else {
                    this.panX = vx - 260 - (lx - 260) * this.zoom;
                    this.panY = vy - 260 - (ly - 260) * this.zoom;
                }
                this.updateZoomButtons();
                this.paint();
            }, { passive: false });
            window.addEventListener('pointermove', e => this.onPointerMove(e));
            window.addEventListener('pointerup', () => { this.drag = null; });

            this.updateControlStates();
        }

        updateZoomButtons() {
            this.container.querySelectorAll('[data-zoom]').forEach(b => {
                const zVal = parseFloat(b.dataset.zoom);
                b.classList.toggle('active', Math.abs(this.zoom - zVal) < 0.08);
            });
        }

        viewPt(lx, ly) {
            return [260 + (lx - 260) * this.zoom + this.panX, 260 + (ly - 260) * this.zoom + this.panY];
        }

        unviewPt(vx, vy) {
            return [260 + (vx - 260 - this.panX) / this.zoom, 260 + (vy - 260 - this.panY) / this.zoom];
        }

        async saveOrientation() {
            const btn = this.q('[data-mic-orient-save]');
            const zeroBtn = this.q('[data-mic-zero]');
            if (btn) btn.disabled = true;
            if (zeroBtn) zeroBtn.disabled = true;
            try {
                const fresh = await api('state');
                const currentMic = (this.source === 'primary')
                    ? (fresh.settings?.mic || {})
                    : ((fresh.settings?.microphones || []).find(m => m.id === this.source) || {});
                const updated = {
                    ...currentMic,
                    azimuth_offset: clamp(parseFloat(this.q('[data-mic-offset-num]').value) || 0, -360, 360),
                    invert: Boolean(this.q('[data-mic-invert]').checked),
                };
                const endpoint = (this.source === 'primary')
                    ? 'mic'
                    : `microphones/${encodeURIComponent(this.source)}`;
                const method = (this.source === 'primary') ? 'POST' : 'PUT';
                await api(endpoint, { settings: updated }, method);
                this.micSettings = updated;
                toast(`✓ Microphone orientation updated (offset ${updated.azimuth_offset}°${updated.invert ? ', mirrored' : ''})`);
                this.paint();
            } catch (err) {
                toast('Failed to update orientation: ' + err.message, true);
            } finally {
                this.updateControlStates();
            }
        }

        updateControlStates() {
            const ed = this.editable();
            const z = this.seat();
            const hasSeat = Boolean(z);
            this.q('[data-add]').disabled = !ed;
            this.q('[data-capture]').disabled = !ed || !hasSeat;
            this.q('[data-name]').disabled = !ed || !hasSeat;
            this.q('[data-radius]').disabled = !ed || !hasSeat;
            this.q('[data-az-input]').disabled = !ed || !hasSeat;
            this.q('[data-el-input]').disabled = !ed || !hasSeat;
            this.q('[data-scene]').disabled = !ed || !hasSeat;
            this.q('[data-delete]').disabled = !ed || !hasSeat;
            this.q('[data-undo]').disabled = !ed || this.undo.length === 0;
            this.q('[data-redo]').disabled = !ed || this.redo.length === 0;
            this.q('[data-save]').disabled = !ed;
            const orientOffset = this.q('[data-mic-offset]');
            const orientNum = this.q('[data-mic-offset-num]');
            const orientInv = this.q('[data-mic-invert]');
            const orientZero = this.q('[data-mic-zero]');
            const orientSave = this.q('[data-mic-orient-save]');
            if (orientOffset) orientOffset.disabled = !ed;
            if (orientNum) orientNum.disabled = !ed;
            if (orientInv) orientInv.disabled = !ed;
            if (orientZero) orientZero.disabled = !ed;
            if (orientSave) orientSave.disabled = !ed;
            this.container.querySelectorAll('[data-preset-btn], .preset-select').forEach(b => {
                b.disabled = !ed || !hasSeat;
            });
        }

        fillSeatsDropdown() {
            const sel = this.q('[data-seat]');
            const myZones = this.zones();
            sel.innerHTML = myZones.length
                ? myZones.map(z => `<option value="${esc(z.id)}">${esc(z.name)}</option>`).join('')
                : '<option value="">No seats on this mic</option>';
            if (myZones.length && (!this.selected || !myZones.some(z => z.id === this.selected))) {
                this.selected = myZones[0].id;
            }
            sel.value = this.selected || '';
        }

        fillScenesDropdown() {
            const sel = this.q('[data-scene]');
            sel.innerHTML = '<option value="">Manual / Direct Presets</option>' +
                this.scenes.map(s => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
        }

        fillPresets() {
            const container = this.q('[data-presets]');
            container.innerHTML = '';
            const z = this.seat();
            if (!z) return;

            const camLabels = {
                1: 'Camera 1 (Lumen)',
                2: 'Camera 2 (Solvic)',
                3: 'Camera 3',
                4: 'Camera 4'
            };

            const camAliases = {
                1: ['camera_1', 'omni_192_168_1_106', 'afc60497b9db'],
                2: ['camera_2', 'omni_192_168_1_128', 'a736bacef244'],
                3: ['camera_3'],
                4: ['camera_4']
            };

            for (let i = 1; i <= 4; i++) {
                const camKey = `camera_${i}`;
                const aliases = camAliases[i] || [camKey];
                let assignedPreset = null;
                if (z.presets) {
                    for (const alias of aliases) {
                        if (z.presets[alias] != null && z.presets[alias] !== '') {
                            assignedPreset = z.presets[alias];
                            break;
                        }
                    }
                }

                const label = camLabels[i] || `Camera ${i}:`;
                const row = document.createElement('div');
                row.className = 'flex items-center gap-2 mb-2';
                row.innerHTML = `
                    <span class="text-xs font-semibold text-slate-700 w-32">${label}:</span>
                    <select class="preset-select text-xs bg-white text-slate-800 rounded p-1.5 border border-slate-300" data-cam="${camKey}">
                        <option value="">None</option>
                        ${[1, 2, 3, 4, 5, 6, 7, 8].map(p => `<option value="${p}" ${assignedPreset == p ? 'selected' : ''}>Preset ${p}</option>`).join('')}
                    </select>
                `;

                const select = row.querySelector('select');
                select.onchange = e => {
                    if (!this.editable() || !z) return;
                    this.checkpoint();
                    if (!z.presets) z.presets = {};
                    const val = e.target.value ? parseInt(e.target.value, 10) : null;
                    const omniId = (i === 1) ? 'omni_192_168_1_106' : (i === 2) ? 'omni_192_168_1_128' : null;
                    if (val) {
                        z.presets[camKey] = val;
                        if (omniId) z.presets[omniId] = String(val);
                    } else {
                        delete z.presets[camKey];
                        if (omniId) delete z.presets[omniId];
                    }
                    this.changed();
                };

                container.appendChild(row);
            }
        }

        fill() {
            const z = this.seat();
            const off = Number(this.micSettings?.azimuth_offset || 0);
            const inv = Boolean(this.micSettings?.invert);
            if (this.q('[data-mic-offset]')) this.q('[data-mic-offset]').value = clamp(off, -180, 180);
            if (this.q('[data-mic-offset-num]')) this.q('[data-mic-offset-num]').value = off;
            if (this.q('[data-mic-invert]')) this.q('[data-mic-invert]').checked = inv;
            const ro = this.q('[data-mic-orient-readout]');
            if (ro) ro.textContent = `Offset: ${off}°${inv ? ' · Mirrored' : ''}`;

            if (z) {
                this.q('[data-seat]').value = z.id;
                this.q('[data-name]').value = z.name || '';
                this.q('[data-radius]').value = z.radius || 25;
                this.q('[data-range]').textContent = `±${z.radius || 25}°`;
                this.q('[data-az-input]').value = Number(z.azimuth ?? 0).toFixed(1);
                this.q('[data-el-input]').value = Number(z.elevation ?? 45).toFixed(1);
                this.q('[data-scene]').value = z.scene_id || '';
                this.fillPresets();
            } else {
                this.q('[data-name]').value = '';
                this.q('[data-radius]').value = 25;
                this.q('[data-range]').textContent = '±25°';
                this.q('[data-az-input]').value = '';
                this.q('[data-el-input]').value = '';
                this.q('[data-scene]').value = '';
                this.q('[data-presets]').innerHTML = '<p class="text-xs text-gray-400">Select or add a seat to configure camera presets.</p>';
            }
            this.updateControlStates();
        }

        changed() {
            this.revision++;
            this.paint();
        }

        async save() {
            if (!this.draft) return;
            const saveBtn = this.q('[data-save]');
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving…';

            try {
                // Fetch fresh server state to ensure other mic's seats are preserved
                const fresh = await api('state');
                const serverZones = fresh.settings?.tracking?.zones || [];
                const otherMicsZones = serverZones.filter(z => (z.mic_id || 'primary') !== this.source);
                const myZones = (this.draft.zones || []).filter(z => (z.mic_id || 'primary') === this.source);

                const snapshot = structuredClone(fresh.settings.tracking);
                snapshot.zones = structuredClone([...otherMicsZones, ...myZones]);
                for (const z of (snapshot.zones || [])) {
                    if (z.presets) {
                        for (const k of Object.keys(z.presets)) {
                            if (z.presets[k] != null) z.presets[k] = String(z.presets[k]);
                        }
                    }
                }
                const result = await api('tracking/settings', snapshot, 'PUT');
                this.saved = this.revision;
                const micName = (this.source === 'primary') ? 'TCC2' : 'TCCM';
                toast(`✓ ${micName} seats and exclusions saved successfully!`);
                this.q('[data-note]').textContent = (result.warnings && result.warnings.join(' ')) || `${micName} seats saved and active.`;

                this.draft.zones = snapshot.zones;

                // Sync sibling radar in dual manager
                if (window._dualSennheiserManager && typeof window._dualSennheiserManager.syncZones === 'function') {
                    window._dualSennheiserManager.syncZones(snapshot.zones);
                }

                if (typeof window.loadMappingData === 'function') {
                    window.loadMappingData();
                }
            } catch (err) {
                console.error(`Failed to save seats for [${this.source}]:`, err);
                toast('Seats not saved: ' + err.message, true);
                this.q('[data-note]').textContent = 'Error saving seats. Check network and retry.';
            } finally {
                saveBtn.disabled = !this.editable();
                saveBtn.textContent = 'Save seats';
            }
        }

        async readDevice() {
            if (this.source === 'primary') {
                toast('Configure TCC2 sound exclusions in Sennheiser Control Cockpit.');
                return;
            }
            const btn = this.q('[data-read]');
            btn.disabled = true;
            btn.textContent = 'Reading device…';
            try {
                this.device = await api('mic/device-settings' + (this.source === 'primary' ? '' : '?source=' + encodeURIComponent(this.source)));
                this.renderDevicePanel();
                toast('Read exclusions from microphone successfully.');
                this.paint();
            } catch (err) {
                toast('Failed to read microphone exclusions: ' + err.message, true);
            } finally {
                btn.disabled = false;
                btn.textContent = 'Read hardware exclusions';
            }
        }

        renderDevicePanel() {
            const panel = this.q('[data-device-panel]');
            if (!panel || !this.device) return;
            panel.innerHTML = `
                <div class="device-exclusions-summary mt-2 text-xs text-slate-600">
                    <p>Hardware exclusions read: <strong>${Object.keys(this.device.values || {}).filter(k => k.startsWith('exclusion_')).length}</strong> zones active.</p>
                </div>
            `;
        }

        coordinates(e) {
            const rect = this.q('canvas').getBoundingClientRect();
            return [
                (e.clientX - rect.left) * (520 / rect.width),
                (e.clientY - rect.top) * (520 / rect.height)
            ];
        }

        appAngle(a) {
            return wrap(a + (this.draft?.azimuth_offset || 0));
        }

        deviceAngle(a) {
            return wrap(a - (this.draft?.azimuth_offset || 0));
        }

        onPointerDown(e) {
            const [vx, vy] = this.coordinates(e);
            const [x, y] = this.unviewPt(vx, vy);
            const myZones = this.zones();

            if (this.editable()) {
                // Check if clicking resize handle on currently selected seat
                const current = this.seat();
                if (current) {
                    const p = point(current.azimuth, current.elevation);
                    const r = current.radius * 2;
                    if (Math.hypot(x - (p[0] + r), y - p[1]) < 12 / this.zoom) {
                        this.checkpoint();
                        this.drag = { type: 'radius', target: current, startR: current.radius, startDist: Math.hypot(x - p[0], y - p[1]) };
                        return;
                    }
                }

                // Check if clicking inside any seat belonging to THIS microphone
                for (const z of myZones) {
                    const p = point(z.azimuth, z.elevation);
                    if (Math.hypot(x - p[0], y - p[1]) <= z.radius * 2) {
                        this.checkpoint();
                        this.selected = z.id;
                        this.fill();
                        this.drag = { type: 'seat', target: z };
                        this.paint();
                        return;
                    }
                }
            }

            // Drag empty canvas to pan when zoomed in
            if (this.zoom > 1) {
                this.drag = { type: 'pan', startVX: vx, startVY: vy, origPanX: this.panX, origPanY: this.panY };
            }
        }

        onPointerMove(e) {
            const canvas = this.q('canvas');
            if (canvas && e.target === canvas) {
                const [vx, vy] = this.coordinates(e);
                const [lx, ly] = this.unviewPt(vx, vy);
                const [ha, he] = angles(lx, ly);
                this.hoverAngles = [ha, he];
                const curEl = this.q('[data-cursor-coords]');
                if (curEl) curEl.textContent = `Cursor: ${ha.toFixed(1)}° Az / ${he.toFixed(1)}° El`;
            }
            if (!this.drag) return;
            const [vx, vy] = this.coordinates(e);
            if (this.drag.type === 'pan') {
                this.panX = this.drag.origPanX + (vx - this.drag.startVX);
                this.panY = this.drag.origPanY + (vy - this.drag.startVY);
                this.paint();
                return;
            }
            const [x, y] = this.unviewPt(vx, vy);

            if (this.drag.type === 'seat') {
                const [a, el] = angles(x, y);
                this.drag.target.azimuth = Math.round(a * 10) / 10;
                this.drag.target.elevation = Math.round(el * 10) / 10;
                if (this.q('[data-az-input]')) this.q('[data-az-input]').value = this.drag.target.azimuth.toFixed(1);
                if (this.q('[data-el-input]')) this.q('[data-el-input]').value = this.drag.target.elevation.toFixed(1);
                this.changed();
            } else if (this.drag.type === 'radius') {
                const current = this.drag.target;
                const p = point(current.azimuth, current.elevation);
                const dist = Math.hypot(x - p[0], y - p[1]);
                const newRadius = clamp(Math.round(dist / 2), 6, 90);
                current.radius = newRadius;
                this.q('[data-radius]').value = newRadius;
                this.q('[data-range]').textContent = `±${newRadius}°`;
                this.changed();
            }
        }

        paint() {
            if (this.frame) return;
            this.frame = requestAnimationFrame(() => {
                this.frame = null;
                this.draw();
            });
        }

        updateStatusAndWarnings(myZones, beamPos, isActiveSpeaker, accentColor, micLabel) {
            const posEl = this.q('[data-position]');
            if (posEl) {
                if (beamPos) {
                    const lvl = (this.mic?.position?.level != null) ? ` (${this.mic.position.level} dB)` : '';
                    const activeTxt = isActiveSpeaker
                        ? ` · <span style="color:${accentColor};font-weight:700;">Active Speaker</span>`
                        : '';
                    posEl.innerHTML = `<strong>${micLabel} Live Beam:</strong> ${beamPos[0].toFixed(1)}° Az / ${beamPos[1].toFixed(1)}° El${lvl}${activeTxt}`;
                } else {
                    posEl.textContent = `Connecting to ${micLabel} microphone…`;
                }
            }

            const warnEl = this.q('[data-warning]');
            if (warnEl) {
                let warning = '';
                const selected = this.seat();
                if (selected) {
                    if (myZones.some(z => z.id !== selected.id && Math.hypot(delta(z.azimuth, selected.azimuth), z.elevation - selected.elevation) < z.radius + selected.radius)) {
                        warning = '⚠️ Overlap: This seat overlaps another seat on this microphone. Tighten error range or move centers apart.';
                    }
                }
                warnEl.textContent = warning;
            }
        }

        draw() {
            const canvas = this.q('canvas');
            if (!canvas || !this.draft) return;
            const ctx = canvas.getContext('2d');
            ctx.setTransform(2, 0, 0, 2, 0, 0);
            ctx.clearRect(0, 0, 520, 520);
            ctx.save();
            ctx.translate(260 + this.panX, 260 + this.panY);
            ctx.scale(this.zoom, this.zoom);
            ctx.translate(-260, -260);

            const isPrimary = (this.source === 'primary');
            const accentColor = isPrimary ? '#F58220' : '#059669';
            const micLabel = isPrimary ? 'TCC2' : 'TCCM';

            // Soft white-slate radial room backdrop inside outer perimeter (90 deg = radius 215)
            const bgGrad = ctx.createRadialGradient(260, 260, 20, 260, 260, 218);
            bgGrad.addColorStop(0, '#ffffff');
            bgGrad.addColorStop(1, '#f1f5f9');
            ctx.fillStyle = bgGrad;
            ctx.beginPath();
            ctx.arc(260, 260, 215, 0, Math.PI * 2);
            ctx.fill();

            // Fine compass crosshairs (45 deg normal, 15 deg when zoomed/enlarged)
            const fineGrid = this.zoom > 1.2 || this.expanded;
            ctx.save();
            ctx.strokeStyle = '#e2e8f0';
            ctx.lineWidth = 1 / this.zoom;
            ctx.setLineDash([4 / this.zoom, 4 / this.zoom]);
            for (let deg = 0; deg < 180; deg += (fineGrid ? 15 : 45)) {
                const rad = deg * Math.PI / 180;
                const dx = Math.sin(rad) * 215;
                const dy = -Math.cos(rad) * 215;
                ctx.globalAlpha = (deg % 45 === 0) ? 1 : 0.45;
                ctx.beginPath();
                ctx.moveTo(260 - dx, 260 - dy);
                ctx.lineTo(260 + dx, 260 + dy);
                ctx.stroke();
            }
            ctx.restore();

            // Concentric elevation rings (0 deg center -> 90 deg room edge)
            const rings = fineGrid ? [0, 15, 30, 45, 60, 75, 90] : [0, 30, 60, 90];
            for (const e of rings) {
                const radPx = 35 + e * 2;
                ctx.beginPath();
                ctx.strokeStyle = e === 90 ? '#cbd5e1' : (e % 30 === 0 ? '#e2e8f0' : '#eef2f6');
                ctx.lineWidth = (e === 90 ? 1.5 : 1) / this.zoom;
                ctx.arc(260, 260, radPx, 0, Math.PI * 2);
                ctx.stroke();
                if (e > 0) {
                    ctx.fillStyle = '#94a3b8';
                    ctx.font = `600 ${Math.max(5, 10 / Math.sqrt(this.zoom))}px Inter, sans-serif`;
                    ctx.fillText(`${e}°`, 264, 260 - radPx + (11 / Math.sqrt(this.zoom)));
                }
            }

            // Crisp outer cardinal compass pills
            ctx.font = '700 11px Inter, sans-serif';
            for (const [a, label] of [[0, 'Front · 0°'], [90, 'Right · 90°'], [180, 'Back · 180°'], [270, 'Left · 270°']]) {
                const p = point(a, 104);
                const tw = ctx.measureText(label).width;
                const bx = clamp(p[0] - tw / 2, 10, 510 - tw);
                const by = clamp(p[1] + 4, 18, 508);
                ctx.fillStyle = '#475569';
                ctx.fillText(label, bx, by);
            }

            // Hardware sound exclusions & priority zones
            if (this.device?.values) {
                for (const [key, v] of Object.entries(this.device.values)) {
                    if (!(key.startsWith('exclusion_') || key.startsWith('priority')) || !v.enabled) continue;
                    ctx.beginPath();
                    let first = true;
                    for (const e of [v.elevation.min, v.elevation.max]) {
                        const arr = Array.from({ length: 61 }, (_, i) => v.azimuth.min + (v.azimuth.max - v.azimuth.min) * i / 60);
                        if (e === v.elevation.max) arr.reverse();
                        for (const a of arr) {
                            const p = point(this.appAngle(a), e);
                            first ? ctx.moveTo(...p) : ctx.lineTo(...p);
                            first = false;
                        }
                    }
                    ctx.closePath();
                    ctx.fillStyle = key.startsWith('priority') ? 'rgba(139, 92, 246, 0.16)' : 'rgba(239, 68, 68, 0.16)';
                    ctx.strokeStyle = key.startsWith('priority') ? '#7c3aed' : '#ef4444';
                    ctx.lineWidth = 1.5;
                    ctx.fill();
                    ctx.stroke();
                }
            }

            // Center Microphone Hub indicator
            ctx.beginPath();
            ctx.fillStyle = '#ffffff';
            ctx.strokeStyle = '#cbd5e1';
            ctx.lineWidth = 1.5;
            ctx.arc(260, 260, 18, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
            ctx.fillStyle = '#334155';
            ctx.font = '700 10px Inter, sans-serif';
            const hubW = ctx.measureText('MIC').width;
            ctx.fillText('MIC', 260 - hubW / 2, 263.5);

            // Draw Seats belonging to THIS microphone (slate neutral, orange when active/selected)
            const myZones = this.zones();
            for (const z of myZones) {
                const isSel = (z.id === this.selected);
                const isLocked = (z.id === this.view?.current);
                const p = point(z.azimuth, z.elevation);
                const r = z.radius * 2;

                ctx.beginPath();
                ctx.arc(p[0], p[1], r, 0, Math.PI * 2);
                ctx.fillStyle = isLocked
                    ? 'rgba(245, 130, 32, 0.22)'
                    : isSel
                        ? (isPrimary ? 'rgba(245, 130, 32, 0.13)' : 'rgba(5, 150, 105, 0.13)')
                        : 'rgba(100, 116, 139, 0.10)';
                ctx.fill();
                ctx.lineWidth = (isSel || isLocked) ? 2.5 : 1.5;
                ctx.strokeStyle = isLocked ? '#ea580c' : isSel ? accentColor : '#94a3b8';
                ctx.stroke();

                // Center seat pin
                ctx.beginPath();
                ctx.arc(p[0], p[1], 5, 0, Math.PI * 2);
                ctx.fillStyle = isLocked ? '#ea580c' : isSel ? accentColor : '#475569';
                ctx.fill();
                ctx.strokeStyle = '#ffffff';
                ctx.lineWidth = 1.5;
                ctx.stroke();

                // Crisp high-contrast label pill with precise coordinates on selected seat
                const seatName = z.name || 'Seat';
                const coordTag = (isSel || this.zoom > 1.2 || this.expanded)
                    ? ` · ${Number(z.azimuth).toFixed(1)}°/${Number(z.elevation).toFixed(1)}°`
                    : '';
                const labelText = (isLocked ? `${seatName} · LIVE` : seatName) + coordTag;
                ctx.font = (isSel || isLocked) ? '700 11.5px Inter, sans-serif' : '600 11px Inter, sans-serif';
                const tw = ctx.measureText(labelText).width;
                const lx = clamp(p[0] - tw / 2, 12, 508 - tw);
                const ly = clamp(p[1] - 12, 22, 496);
                ctx.fillStyle = isLocked ? '#fff7ed' : 'rgba(255, 255, 255, 0.94)';
                ctx.strokeStyle = isLocked ? '#fdba74' : isSel ? accentColor : '#cbd5e1';
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.roundRect(lx - 6, ly - 12, tw + 12, 17, 6);
                ctx.fill();
                ctx.stroke();
                ctx.fillStyle = isLocked ? '#c2410c' : '#0f172a';
                ctx.fillText(labelText, lx, ly);

                if (isSel) {
                    ctx.fillStyle = accentColor;
                    ctx.strokeStyle = '#ffffff';
                    ctx.lineWidth = 1.5;
                    ctx.fillRect(p[0] + r - 5, p[1] - 5, 10, 10);
                    ctx.strokeRect(p[0] + r - 5, p[1] - 5, 10, 10);
                }
            }

            // Draw Live Audio Beam + directional ray from Mic Center
            const beamPos = this.view?.smoothed;
            const isActiveSpeaker = this.activeMic === this.source;

            if (beamPos) {
                const p = point(beamPos[0], beamPos[1]);
                ctx.save();
                ctx.strokeStyle = accentColor;
                ctx.globalAlpha = 0.45;
                ctx.lineWidth = 2;
                ctx.setLineDash([5, 4]);
                ctx.beginPath();
                ctx.moveTo(260, 260);
                ctx.lineTo(p[0], p[1]);
                ctx.stroke();
                ctx.restore();

                if (isActiveSpeaker) {
                    ctx.strokeStyle = accentColor;
                    ctx.globalAlpha = 0.35;
                    ctx.lineWidth = 3;
                    ctx.beginPath();
                    ctx.arc(p[0], p[1], 17, 0, Math.PI * 2);
                    ctx.stroke();
                    ctx.globalAlpha = 1;
                }

                ctx.fillStyle = accentColor;
                ctx.beginPath();
                ctx.arc(p[0], p[1], 7.5, 0, Math.PI * 2);
                ctx.fill();
                ctx.strokeStyle = '#ffffff';
                ctx.lineWidth = 2;
                ctx.stroke();

                const beamTag = `${micLabel} Beam`;
                ctx.font = '700 11px Inter, sans-serif';
                const btw = ctx.measureText(beamTag).width;
                const btx = clamp(p[0] + 12, 10, 502 - btw);
                const bty = clamp(p[1] + 4, 20, 500);
                ctx.fillStyle = accentColor;
                ctx.beginPath();
                ctx.roundRect(btx - 5, bty - 11, btw + 10, 16, 5);
                ctx.fill();
                ctx.fillStyle = '#ffffff';
                ctx.fillText(beamTag, btx, bty);
            }

            ctx.restore();
            this.updateStatusAndWarnings(myZones, beamPos, isActiveSpeaker, accentColor, micLabel);
        }
    }

    class DualSennheiserManager {
        constructor(container) {
            this.container = container;
            this.primaryRadar = null;
            this.secondaryRadar = null;
            this.mode = 'both';
            this.init();
        }

        async init() {
            try {
                const state = await api('state');
                const mics = state.microphones || [];
                const secMic = mics.find(m => m.id !== 'primary');
                const ctrlModel = document.getElementById('ctrl-model')?.value || '';
                const isDual = (secMic != null) || ctrlModel.includes('Dual');

                if (isDual) {
                    const secId = secMic ? secMic.id : 'secondary';
                    this.renderDualContainer(secId);
                    const slotPrimary = this.container.querySelector('#radar-slot-primary');
                    const slotSecondary = this.container.querySelector('#radar-slot-secondary');

                    this.primaryRadar = new SennheiserVisualRadar(slotPrimary, 'primary', mics.find(m => m.id === 'primary'));
                    this.secondaryRadar = new SennheiserVisualRadar(slotSecondary, secId, secMic);
                    this.bindViewToggles();
                } else {
                    this.container.innerHTML = `<div id="radar-slot-primary" class="radar-card-container primary-card"></div>`;
                    const slotPrimary = this.container.querySelector('#radar-slot-primary');
                    this.primaryRadar = new SennheiserVisualRadar(slotPrimary, 'primary', mics.find(m => m.id === 'primary') || state.mic);
                }
            } catch (err) {
                console.error('Failed to initialize DualSennheiserManager:', err);
                this.container.innerHTML = `<div id="radar-slot-primary" class="radar-card-container primary-card"></div>`;
                const slotPrimary = this.container.querySelector('#radar-slot-primary');
                this.primaryRadar = new SennheiserVisualRadar(slotPrimary, 'primary', null);
            }
        }

        renderDualContainer(secId) {
            this.container.innerHTML = `
                <div class="dual-radar-view-header">
                    <div class="title-group">
                        <div style="width:36px;height:36px;border-radius:10px;background:#fff7ed;border:1px solid #fed7aa;color:#f58220;display:flex;align-items:center;justify-content:center;flex-shrink:0;">
                            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" stroke-width="2"></circle><circle cx="12" cy="12" r="4" stroke-width="2"></circle><circle cx="12" cy="12" r="1" fill="currentColor"></circle></svg>
                        </div>
                        <div>
                            <h2>Dual Microphone Spatial Map</h2>
                            <p>Configure seat zones and camera presets across both ceiling microphones</p>
                        </div>
                    </div>
                    <div class="view-toggle-group">
                        <button class="view-toggle-btn" data-view="mixed">Mixed Overview</button>
                        <button class="view-toggle-btn active" data-view="both">Side by Side</button>
                        <button class="view-toggle-btn" data-view="primary">TCC2 Only</button>
                        <button class="view-toggle-btn" data-view="secondary">TCCM Only</button>
                    </div>
                </div>
                <section class="mic-team-panel"><div class="mic-team-heading"><div><h3>Microphone Status &amp; Room Lock</h3><p>Live audio levels and active seat tracking state</p></div><span data-team-lock style="font-size:12px;font-weight:600;color:#475569;background:#f8fafc;border:1px solid #e2e8f0;padding:5px 12px;border-radius:999px;"></span></div><div data-team-cards class="mic-team-cards"></div><div data-mixed-map hidden><p style="font-size:12px;color:#64748b;margin:10px 0;">Each radar uses its microphone's angular reference. Click any seat pill below to jump directly to its editor.</p><div data-mixed-grid class="mic-team-maps"></div></div></section>
                <div class="dual-radar-grid layout-both">
                    <div id="radar-slot-primary" class="radar-card-container primary-card"></div>
                    <div id="radar-slot-secondary" class="radar-card-container secondary-card"></div>
                </div>
            `;
        }

        bindViewToggles() {
            const btns = this.container.querySelectorAll('.view-toggle-btn[data-view]');
            const slotPrimary = this.container.querySelector('#radar-slot-primary');
            const slotSecondary = this.container.querySelector('#radar-slot-secondary');
            const grid = this.container.querySelector('.dual-radar-grid');

            btns.forEach(btn => {
                btn.onclick = () => {
                    btns.forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    const v = btn.dataset.view;
                    this.mode = v;

                    const mixed = this.container.querySelector('[data-mixed-map]');
                    if (mixed) mixed.hidden = v !== 'mixed';
                    if (v === 'mixed') {
                        if (slotPrimary) slotPrimary.style.display = 'none';
                        if (slotSecondary) slotSecondary.style.display = 'none';
                        if (grid) grid.classList.remove('layout-both');
                        if (this.lastState) this.updateTeam(this.lastState);
                    } else if (v === 'both') {
                        if (slotPrimary) slotPrimary.style.display = 'flex';
                        if (slotSecondary) slotSecondary.style.display = 'flex';
                        if (grid) grid.classList.add('layout-both');
                    } else if (v === 'primary') {
                        if (slotPrimary) slotPrimary.style.display = 'flex';
                        if (slotSecondary) slotSecondary.style.display = 'none';
                        if (grid) grid.classList.remove('layout-both');
                    } else if (v === 'secondary') {
                        if (slotPrimary) slotPrimary.style.display = 'none';
                        if (slotSecondary) slotSecondary.style.display = 'flex';
                        if (grid) grid.classList.remove('layout-both');
                    }

                    if (this.primaryRadar) this.primaryRadar.paint();
                    if (this.secondaryRadar) this.secondaryRadar.paint();
                };
            });
        }

        updateTeam(state) {
            this.lastState = state;
            const cards = this.container.querySelector('[data-team-cards]');
            if (!cards) return;
            const configs = [{...state.settings.mic, id:'primary'}, ...(state.settings.microphones || [])];
            const rows = configs.map((m,i) => { const live = m.id==='primary' ? state.mic : (state.microphones || []).find(x=>x.id===m.id); const level=live?.position?.level; return {m,i,live,level:live?.age!=null && live.age<1 && Number.isFinite(level) ? level : null}; });
            const eligible=rows.filter(r=>r.level!==null && r.level>=state.settings.tracking.level_threshold);
            const loudest=eligible.length?eligible.reduce((a,b)=>a.level>=b.level?a:b).m.id:null;
            cards.innerHTML=rows.map(({m,i,live,level})=>`<div class="mic-team-card ${m.id===loudest?'loud':''}"><span class="mic-team-icon">◉</span><span><strong>${i===0?'TCC2':'TCCM'} · ${esc(m.ip)}</strong><small>${esc(live?.state||'Disconnected')} · ${level===null?'No fresh level':level.toFixed(0)+' dB'}${m.id===loudest?' · loudest':''}</small></span></div>`).join('');
            const locked=state.settings.tracking.zones.find(z=>z.id===state.tracking.current);
            this.container.querySelector('[data-team-lock]').textContent=`Room lock: ${locked?.name||'Waiting'} · ${state.tracking.reason||'Waiting for microphone'}`;
            if (this.mode!=='mixed') return;
            const grid=this.container.querySelector('[data-mixed-grid]');
            const zones=[...(this.primaryRadar?.zones()||[]),...(this.secondaryRadar?.zones()||[])];
            const signature=JSON.stringify([rows.map(r=>[r.m.id,r.m.ip]),zones.map(z=>[z.id,z.name,z.mic_id,z.azimuth,z.elevation]),state.tracking.current]);
            if(grid.dataset.signature!==signature) {
                grid.innerHTML=rows.map(({m,i})=>`<div class="mic-team-map"><strong>${i===0?'TCC2':'TCCM'} · ${esc(m.ip)}</strong><canvas width="260" height="260" data-mixed-canvas="${esc(m.id)}"></canvas><div class="mic-team-seats">${zones.filter(z=>(z.mic_id||'primary')===m.id).map(z=>`<button type="button" data-mixed-source="${esc(m.id)}" data-mixed-seat="${esc(z.id)}">${esc(z.name)}${z.id===state.tracking.current?' · locked':''}</button>`).join('')||'<span>No seats assigned</span>'}</div></div>`).join('');
                grid.dataset.signature=signature;
                grid.onclick=e=>{const target=e.target.closest('[data-mixed-source]');if(!target)return;const primary=target.dataset.mixedSource==='primary';const radar=primary?this.primaryRadar:this.secondaryRadar;if(!radar)return;this.container.querySelector(`[data-view="${primary?'primary':'secondary'}"]`).click();radar.selected=target.dataset.mixedSeat;radar.fillSeatsDropdown();radar.fill();radar.paint();};
            }
            for(const {m,live} of rows){const canvas=[...grid.querySelectorAll('canvas')].find(c=>c.dataset.mixedCanvas===m.id),ctx=canvas.getContext('2d');ctx.setTransform(.5,0,0,.5,0,0);ctx.clearRect(0,0,520,520);ctx.strokeStyle='#cbd5e1';for(const e of [0,30,60,90]){ctx.beginPath();ctx.arc(260,260,35+e*2,0,Math.PI*2);ctx.stroke();}for(const z of zones.filter(z=>(z.mic_id||'primary')===m.id)){const xy=point(z.azimuth,z.elevation);ctx.fillStyle=z.id===state.tracking.current?'#f58220':'#475569';ctx.beginPath();ctx.arc(...xy,10,0,Math.PI*2);ctx.fill();ctx.fillStyle='#0f172a';ctx.font='18px Inter, sans-serif';ctx.fillText(z.name,xy[0]+13,xy[1]);}const pos=live?.age!=null&&live.age<1?live.position:null;if(pos&&Number.isFinite(pos.azimuth)&&Number.isFinite(pos.elevation)){ctx.fillStyle=m.id==='primary'?'#f58220':'#059669';ctx.beginPath();ctx.arc(...point(pos.azimuth,pos.elevation),7,0,Math.PI*2);ctx.fill();}}
        }

        syncZones(allZones) {
            if (this.primaryRadar?.draft) {
                const mine = this.primaryRadar.revision !== this.primaryRadar.saved ? this.primaryRadar.zones() : allZones.filter(z=>(z.mic_id||'primary')==='primary');
                this.primaryRadar.draft.zones = structuredClone([...allZones.filter(z=>(z.mic_id||'primary')!=='primary'), ...mine]);
                this.primaryRadar.fillSeatsDropdown();
                this.primaryRadar.paint();
            }
            if (this.secondaryRadar?.draft) {
                const source = this.secondaryRadar.source;
                const mine = this.secondaryRadar.revision !== this.secondaryRadar.saved ? this.secondaryRadar.zones() : allZones.filter(z=>(z.mic_id||'primary')===source);
                this.secondaryRadar.draft.zones = structuredClone([...allZones.filter(z=>(z.mic_id||'primary')!==source), ...mine]);
                this.secondaryRadar.fillSeatsDropdown();
                this.secondaryRadar.paint();
            }
        }

        stop() {
            if (this.primaryRadar) this.primaryRadar.stop();
            if (this.secondaryRadar) this.secondaryRadar.stop();
        }
    }

    // Auto-mount into Microphone Mapping page (#page-mapping)
    function mountSennheiserRadar() {
        const pageMapping = document.getElementById('page-mapping');
        if (!pageMapping) return;

        let container = document.getElementById('sennheiser-mapping-panel');
        if (!container) {
            container = document.createElement('div');
            container.id = 'sennheiser-mapping-panel';
            const scrollArea = pageMapping.querySelector('.scroll-area');
            if (scrollArea) {
                scrollArea.insertBefore(container, scrollArea.firstChild);
            }
        }

        if (!window._dualSennheiserManager) {
            window._dualSennheiserManager = new DualSennheiserManager(container);
        }
    }

    // Floating Auto-Tracking Pill Controller
    class SennheiserFloatingTracker {
        constructor() {
            this.pill = null;
            this.toast = null;
            this.isTracking = false;
            this.isConfigured = false;
            this.isConnected = false;
            this.isBusy = false;
            this.pollTimer = null;
            if (document.body) {
                this.init();
            } else {
                document.addEventListener('DOMContentLoaded', () => this.init());
            }
        }

        init() {
            this.createElements();
            this.bindEvents();
            this.syncPosition();
            this.checkStatus();
            this.startPolling();
        }

        createElements() {
            if (document.getElementById('sennheiser-tracking-floating-pill')) return;

            // Toast element
            this.toast = document.createElement('div');
            this.toast.id = 'senn-floating-toast';
            this.toast.innerHTML = '<span style="font-size:16px;">🎯</span> <span><strong>Auto-Tracking</strong></span>';
            document.body.appendChild(this.toast);

            // Pill element
            this.pill = document.createElement('div');
            this.pill.id = 'sennheiser-tracking-floating-pill';
            this.pill.className = 'senn-pill-off';
            this.pill.style.display = 'none'; // Hidden until verified
            this.pill.innerHTML = `
                <div class="senn-pill-icon-wrap">
                    <div class="senn-pill-pulse-ring"></div>
                    <svg class="senn-pill-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="9"></circle>
                        <circle cx="12" cy="12" r="3"></circle>
                        <line x1="12" y1="1" x2="12" y2="4"></line>
                        <line x1="12" y1="20" x2="12" y2="23"></line>
                        <line x1="1" y1="12" x2="4" y2="12"></line>
                        <line x1="20" y1="12" x2="23" y2="12"></line>
                    </svg>
                </div>
                <div class="senn-pill-content">
                    <div class="senn-pill-title">AUTO-TRACKING</div>
                    <div class="senn-pill-status" id="senn-pill-status-text">TRACKING OFF</div>
                </div>
                <div class="senn-pill-toggle">
                    <div class="senn-pill-switch">
                        <span class="senn-pill-knob"></span>
                    </div>
                </div>
            `;
            document.body.appendChild(this.pill);
        }

        bindEvents() {
            if (!this.pill) return;
            this.pill.addEventListener('click', () => this.toggleTracking());

            // Watch sidebar expand / collapse
            const sidebar = document.getElementById('main-sidebar');
            const toggleBtn = document.getElementById('sidebar-toggle');
            if (toggleBtn) {
                toggleBtn.addEventListener('click', () => {
                    setTimeout(() => this.syncPosition(), 50);
                    setTimeout(() => this.syncPosition(), 250);
                });
            }
            if (sidebar && window.MutationObserver) {
                const obs = new MutationObserver(() => this.syncPosition());
                obs.observe(sidebar, { attributes: true, attributeFilter: ['class'] });
            }
            window.addEventListener('resize', () => this.syncPosition());
        }

        syncPosition() {
            const sidebar = document.getElementById('main-sidebar');
            const isExpanded = sidebar && sidebar.classList.contains('sidebar-expanded');
            const leftOffset = isExpanded ? '240px' : '88px';
            if (this.pill) this.pill.style.left = leftOffset;
            if (this.toast) this.toast.style.left = leftOffset;
        }

        showToast(title, msg, icon = '🎯') {
            if (!this.toast) return;
            this.toast.innerHTML = `<span style="font-size:16px;">${icon}</span> <span><strong>${title}</strong>${msg ? ' &bull; ' + msg : ''}</span>`;
            this.toast.classList.add('show');
            clearTimeout(this._toastTimeout);
            this._toastTimeout = setTimeout(() => {
                if (this.toast) this.toast.classList.remove('show');
            }, 3200);
        }

        async checkStatus() {
            if (this.isBusy || this.statusBusy) return;
            this.statusBusy = true;
            const epoch = this.modeEpoch || 0;
            try {
                // 1. Check if Sennheiser mic is connected
                const ctrlType = document.getElementById('ctrl-type');
                const isSennType = !ctrlType || ctrlType.value === 'Sennheiser';

                const stateRes = await fetch('/api/sennheiser/state').then(r => r.ok ? r.json() : null).catch(() => null);
                const micConnected = !!(stateRes && stateRes.mic?.state === 'Connected');

                const homeBeam = document.getElementById('home-ctrl-beam');
                if (homeBeam) {
                    const p = stateRes?.mic?.position;
                    const b = (stateRes?.tracking?.smoothed && stateRes.tracking.smoothed[0] != null) 
                        ? stateRes.tracking.smoothed 
                        : (p ? [p.azimuth, p.elevation] : null);
                    if (b && b[0] != null) {
                        const ageSuffix = (stateRes?.mic?.age > 3) ? ' (stale)' : '';
                        homeBeam.textContent = `🎯 ${b[0].toFixed(0)}° Az / ${b[1].toFixed(0)}° El${ageSuffix}`;
                    } else if (micConnected) {
                        homeBeam.textContent = 'Waiting for beam…';
                    } else {
                        homeBeam.textContent = 'Disconnected';
                    }
                }

                const dcernoRes = await fetch('/api/check_dcerno_status').then(r => r.ok ? r.json() : null).catch(() => null);
                const isDcernoConnected = !!(dcernoRes && dcernoRes.connected);

                if (epoch !== (this.modeEpoch || 0)) return;
                this.isConnected = isSennType && micConnected && isDcernoConnected;

                if (!this.isConnected) {
                    if (this.pill) this.pill.style.display = 'none';
                    return;
                }

                // 2. Check if everything is configured (mappings with cameras and presets)
                const mapRes = await fetch('/api/get_mapping').then(r => r.ok ? r.json() : null).catch(() => ({ mapping: [] }));
                const mappings = mapRes?.mapping || [];
                const hasSeatsConfigured = mappings.length > 0 && mappings.some(m => m.camera && m.camera !== '0' && m.preset);

                this.isConfigured = hasSeatsConfigured;

                // Sennheiser is connected -> show pill
                if (this.pill) {
                    this.pill.style.display = 'flex';
                }

                // 3. Query current layout mode
                const modeRes = await fetch('/api/get_layout_mode').then(r => r.ok ? r.json() : null).catch(() => null);
                if (epoch !== (this.modeEpoch || 0) || !modeRes) return;
                const currentMode = modeRes.mode;
                this.isTracking = (currentMode === 'auto');

                this.render();
            } catch (e) {
                // Ignore transient network errors
            } finally {
                this.statusBusy = false;
            }
        }

        render() {
            if (!this.pill) return;
            const statusText = document.getElementById('senn-pill-status-text');

            if (!this.isConfigured) {
                this.pill.className = 'senn-pill-unconfigured';
                this.pill.title = 'Tracking seats & camera presets are not yet configured in Mapping';
                if (statusText) statusText.innerHTML = 'NOT CONFIGURED';
                return;
            }

            if (this.isTracking) {
                this.pill.className = 'senn-pill-on';
                this.pill.title = 'Auto-Tracking is ACTIVE. Click to Pause/Turn OFF.';
                if (statusText) statusText.innerHTML = 'LIVE &bull; ACTIVE';
            } else {
                this.pill.className = 'senn-pill-off';
                this.pill.title = 'Auto-Tracking is STANDBY. Click to Turn ON.';
                if (statusText) statusText.innerHTML = 'STANDBY &bull; OFF';
            }
        }

        async toggleTracking() {
            if (this.isBusy) return;

            if (!this.isConfigured && !this.isTracking) {
                this.showToast('Configuration Needed', 'Map seats to camera presets in Mapping first', '⚠️');
                const navMapping = document.querySelector('[data-page="page-mapping"]') || document.getElementById('nav-mapping');
                if (navMapping) navMapping.click();
                return;
            }

            this.isBusy = true;
            this.modeEpoch = (this.modeEpoch || 0) + 1;
            const targetMode = this.isTracking ? 'manual' : 'auto';
            const statusText = document.getElementById('senn-pill-status-text');
            if (statusText) statusText.innerHTML = targetMode === 'auto' ? 'STARTING...' : 'STOPPING...';

            try {
                if (targetMode === 'auto') {
                    // Set single auto sub-mode
                    await fetch('/api/set_auto_sub_mode', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ mode: 'single' })
                    }).catch(() => null);

                    const res = await fetch('/api/set_layout_mode', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ mode: 'auto', sub_mode: 'single' })
                    });
                    const data = await res.json();
                    if (data.success) {
                        this.isTracking = true;
                        this.render();
                        this.showToast('Auto-Tracking ON', 'Live speaker camera tracking active', '🎯');
                        if (typeof switchLayoutTab === 'function') {
                            try { switchLayoutTab('tab-layout-auto'); } catch (_) {}
                        }
                    } else {
                        throw new Error(data.error || 'Failed to start auto-tracking');
                    }
                } else {
                    const res = await fetch('/api/set_layout_mode', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ mode: 'manual' })
                    });
                    const data = await res.json();
                    if (data.success) {
                        this.isTracking = false;
                        this.render();
                        this.showToast('Auto-Tracking OFF', 'Manual camera control restored', '⏸️');
                        if (typeof switchLayoutTab === 'function') {
                            try { switchLayoutTab('tab-layout-manual'); } catch (_) {}
                        }
                    } else {
                        throw new Error(data.error || 'Failed to stop auto-tracking');
                    }
                }
            } catch (err) {
                this.showToast('Toggle Failed', err.message || 'Server error', '❌');
                await this.checkStatus();
            } finally {
                this.isBusy = false;
            }
        }

        startPolling() {
            if (this.pollTimer) clearInterval(this.pollTimer);
            this.pollTimer = setInterval(() => {
                this.checkStatus();
            }, 2500);
        }
    }

    // Keep the chosen editor stable during transient network/age changes.
    let sennPanelActivated = false;
    let modeCheckInFlight = false;
    function renderMicrophoneMode(selected, connected) {
        const visible = Boolean(selected);
        if (visible) mountSennheiserRadar();
        const container = document.getElementById('sennheiser-mapping-panel');
        if (container) container.style.display = visible ? 'block' : 'none';
        const genTable = document.getElementById('generic-mic-table-container') || document.getElementById('mic-table')?.parentElement;
        if (genTable) genTable.style.display = visible ? 'none' : 'block';
        const saveBtn = document.getElementById('table-save');
        if (saveBtn) saveBtn.style.display = visible ? 'none' : 'block';
        document.dispatchEvent(new CustomEvent('microphone-mode', {detail:{sennheiser:visible, selected, connected}}));
    }
    async function checkSennheiserStatus() {
        const selected = document.getElementById('ctrl-type')?.value === 'Sennheiser';
        if (!selected) {
            renderMicrophoneMode(false, false);
            return;
        }
        if (modeCheckInFlight) return;
        modeCheckInFlight = true;
        try {
            const [micState, controller] = await Promise.all([
                fetch('/api/sennheiser/state').then(r=>r.ok?r.json():null).catch(()=>null),
                fetch('/api/check_dcerno_status').then(r=>r.ok?r.json():null).catch(()=>null)
            ]);
            if ((document.getElementById('ctrl-type')?.value === 'Sennheiser') !== selected) return;
            const anyMicConnected = (micState?.mic?.state === 'Connected') || 
                (Array.isArray(micState?.microphones) && micState.microphones.some(m => m.state === 'Connected' || m.position));
            const connected = Boolean(selected && (controller?.connected || anyMicConnected));
            sennPanelActivated = true;
            renderMicrophoneMode(selected, connected);
        } finally { modeCheckInFlight = false; }
    }
    function resetMicrophoneMode() {
        sennPanelActivated = false;
        renderMicrophoneMode(document.getElementById('ctrl-type')?.value === 'Sennheiser', false);
    }
    window.addEventListener('microphone-disconnected', resetMicrophoneMode);
    window.addEventListener('configuration-imported', resetMicrophoneMode);

    // Hook into tab switches and DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        if (!window._sennheiserTracker) {
            window._sennheiserTracker = new SennheiserFloatingTracker();
        }
        checkSennheiserStatus();

        // When switching to mapping page, ensure radar is visible and painting
        const navMapping = document.querySelector('[data-page="page-mapping"]') || document.getElementById('nav-mapping');
        if (navMapping) {
            navMapping.addEventListener('click', () => {
                setTimeout(checkSennheiserStatus, 150);
            });
        }

        const ctrlSelect = document.getElementById('ctrl-type');
        if (ctrlSelect) {
            ctrlSelect.addEventListener('change', checkSennheiserStatus);
        }
    });

    setInterval(() => { if (!document.hidden) checkSennheiserStatus(); }, 3000);

    // Also run immediately
    if (!window._sennheiserTracker) {
        window._sennheiserTracker = new SennheiserFloatingTracker();
    }
    checkSennheiserStatus();
})();

