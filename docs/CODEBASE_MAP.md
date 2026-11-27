# ConfDeck codebase map
Updated 2026-11-16. Start here; use targeted searches rather than a repository scan.

- Current product review: `docs/PRODUCT_RELEASE_REVIEW.md` covers live read-only .84 observations, speech/threshold defects, production readiness and the port-4000 implementation plan. This pass changes only UI presentation and documentation; backend work remains planned. Full suite: 275 passed; saved integration tracking checks: 12 passed.

## Run and verify
- Pending .84 deployment: `docs/MINIPC_PENDING_UPDATE.md`; `scripts/build_minipc_update.py` builds an allowlisted archive without configuration/credentials. `confdeck/aes67.py` adds opt-in SDP/RTP reception for in-memory speech diagnostics; the live tracker remains disconnected. `scripts/kiosk_watch.py` + `scripts/start-kiosk.sh` provide the port-4000 `/viewer` HDMI supervisor. Production source reference is `integration/current84/PRODUCTION_AUDIT.md` (newer than `integration/STATUS.md`).
- Entry: `confdeck/cli.py` → `confdeck/__main__.py`; Windows launcher: `Start ConfDeck.cmd`.
- Run: `.venv/Scripts/python -m confdeck start`; isolated microphone simulator: add `--demo`.
- Tests: `.venv/Scripts/python -m pytest -q`; isolated browser check: `python -m scripts.cleanup_smoke`.
- Latest full suite: 209 passed. Isolated browser validation passes (including joystick release and confirmed mic writes).
- Cleanup audit and remaining acceptance limits: `docs/CLEANUP_AUDIT.md`.
- Browser screenshots: `artifacts/cleanup/overview.png`, `artifacts/cleanup/tuning.png` (mock sources).

## Startup compatibility repair (2026-11-05)
- `app.js:boot` now loads `mic-tuning.js` only after the backend-version check. An older running backend cannot serve that new module; a top-level import previously prevented even the restart notice from rendering.
- Verified against the real old backend: restart notice and Stop button visible, no page errors. `test_frontend_boot.py` now traverses dynamic imports as well as static imports.

## State, security and navigation
- `app.py:create_app`: localhost admin API/static allowlist and separate paired display listener. Device credentials never enter browser snapshots.
- `runtime.py:Runtime`: mic connection, 100 ms tracking tick, relay supervision, health, shared snapshots, shutdown.
- `store.py:Store`: SQLite settings and backup migration; obsolete top-level settings are stripped while preserving cameras, seats and device secrets.
- Default real-room storage: `%LOCALAPPDATA%/ConfDeck`; `--data-dir` overrides it. Demo uses separate storage.
- `static/common.js:room/liveSocket/sendManual`: one shared admin state and WebSocket, including manual commands.
- `static/app.js`: five hash destinations only: home (Overview), cameras, seats, tuning, advanced. Old hashes fall back to Overview.
- Overview owns camera previews, ordered program output, shared read-only radar, connection status, Follow/Stop and TV/layout/HDMI controls.
- Cameras owns discovery/edit, diagnostics, joystick, named scenes, and collapsed smart-follow configuration.
- Seats and Mic owns connection settings and the visual seat/scene editor.
- Mic Tuning owns essential profiles, tolerance/lock controls, device settings, spatial drafts and isolated replay.
- Advanced owns detailed filters, seat assignments, device schema, evaluation/record/replay/exports, health and preferences.
- `static/tracker.css`: port-4000-aligned white/slate palette, blue controls and orange apply/save actions; original navigation/layout, dark theme, unblurred media/radar panels and reduced-motion support retained.

## Tracking, inference and scenes
- Four-seat recovery: original kashif/neha/Mayank/asad geometry restored from `.runtime/small-room-connected-baseline.json`, relinked by name and camera IP to current .108 slots 21/22 and .109 slots 21/22; disk/readback verified. Pre-restore settings: `.runtime/before-four-seat-restore.json`. Replaced five generic seats, preserved current tuning and new scenes.
- `tracking.py`: validated settings/zones, legacy filter, confirmation/hold/move limits and silence return.
- `tracking_math/`: spherical geometry, fitted seats, quality gate and HMM. Default engine remains unchanged.
- `follow.py`: orchestration, dry-run/live safeguards, scene dispatch, bounded recent events and isolated recording/replay.
- `tuning_preview.py:preview`: camera-free replay of at most 1,200 buffered events / 60 seconds; reasons and accepted/rejected samples.
- `tracking_api.py`: local-admin settings/mode/scene/manual interfaces, `/api/tracking/preview`, device read/apply/revert.
- `evaluation/`: labelled capture, metrics, sweeps and offline evaluation; status uses the shared WebSocket.
- `conversation.py` assigns camera coverage. Smart Preview shows one confirmed seat and uses wide for shared-camera moves.
- `handoff.py:Handoffs` orders optional tracking OFF, preset recall, settle and tracking ON downstream of seat recognition. `handoff_api.py:add_handoff_routes` handles camera setup and physical timing tests. Camera Preset Studio owns configuration; Advanced shows the timing waterfall. See `HANDOFF_AUDIT.md`.
- `manual.py`: socket-owned joystick leases, validation, 10 Hz cap and independent 500 ms deadman.
- `static/studio.js`: joystick/keyboard/gamepad, release/blur/navigation STOP through the main socket; scene capture stays acknowledgement-gated.
- Preset Studio camera selector reconciles added/removed cameras on live state updates, preserving current selection and unsaved scene name; removing the selected camera releases movement and clears its preview.
- Saved scenes include Remove scene with confirmation and linked-seat protection; removal deletes the app scene, retaining camera memory. Camera recovery baseline: `.runtime/before-camera-recovery.json`.
- Scene repair: DELETE `/api/scenes/{id}?confirm_unlink=true` removes one scene atomically despite unrelated stale camera IDs, stops follow and clears only its seat assignments. Recall allows available saved camera mappings even when another mapping is stale; scenes without any valid saved framing remain disabled. Regression tests cover stale removal and partial recall.
- `static/seat-radar.js`: shared angular geometry, tolerance shapes, trail, event reasons, exclusions with boundary handles, priority sectors and snapshots.
- `static/mic-tuning.js`: app drafts/replay, profile and lock controls, orientation preview, confirmed apply/readback/revert, 30-second device comparison.

## Microphone
- Offline speech implementation (2026-11-16): `speech.py` contains scalar gate, local Silero adapter, calibration/sync helpers; `speech_audio.py --scalar-output` emits timestamped scalar VAD JSONL without PCM. `AccuracyLab` saves a monotonic clock origin, and `evaluation/speech.py:merge_scalar_frames` aligns the sidecar for guarded replay. The live gate remains disconnected/default off with no speech imports. Details and the real-room workflow: `docs/SPEECH_GATE.md`.
- Speech-gate audit (2026-11-16): `docs/SPEECH_AUDIT.md`; seed-7 synthetic baseline has zero false switches and 3/3 soft locks, so its strict improvement guard cannot pass. A real AES67 stream and VAD outputs were observed on .84, but aligned labelled room measurements are still needed before live enablement.
- Small-room tuning (2026-11-05): baselines in `.runtime/small-room-connected-baseline.json` and `.runtime/before-ambient-tuning.json`; priority disabled, noise gate -55 dB/350 ms, NormalRoom retained, all read back. Inference evidence 400 ms, switch threshold .93, margin .25, conversation retention 8000 ms, minimum hold 2500 ms, move cap 20/min. Seats unchanged; Follow off. Unlabelled 30-sample observation stayed connected (levels -58 to -21 dB, estimated noise floor -63 dB); accuracy and physical latency are unmeasured. User declined controlled speech test.
- `microphone.py`: SSCv2 HTTPS/SSE control adapter and test simulator; mic level is tracking evidence, not captured sound.
- `device_settings.py` + `protocols/tcc_medium.yaml`: published device resource schemas, confirmed writes, durable pre-change snapshot and verified readback.
- Hardware API resource paths containing `/audio/` are required for beam direction, level, gate and zones; retain them.
- `models.py:MicSettings`: fixed api username, private IP/443, certificate fingerprint, resource paths, coordinate conventions.
- TOFU pinning and legacy mic migration preserve prior device identity; no device writes occur during tests.

## Video and control
- Device compatibility repair: camera-create API retains VISCA configuration; native tracking OFF verifies VISCA and restores protocol/control, with immediate Studio refresh and explicit tracking-lock message. .106/.108/.109 use H264 `/stream2`, .120 `/live/av0`; all four decoded frames and passed control inquiries. Settings snapshot `.runtime/verified-four-camera-settings.json`; disk state verified against live state before restart.
- Native monocular tracking: .120 firmware MCU V3.2.3 / camera V1.0.4 (2022-05-27), exact model not exposed. Camera-owned live.js documents `/ajaxcom` GetEnv/SetEnv MonoTracking (bEnable, nTrackMode). Studio exposes status/start presenter/stop; backend verifies readback, stops mic follow, persists manual-control interlock and fails closed on uncertain writes. Region definitions remain on camera. Test: native tracking interlock in test_scenes.py.
- Controls restored for .108/.109/.106/.120: VISCA enabled on UDP 52381 after successful position inquiries; all four API control tests OK. Built-in tracking integration for .120 pending exact model/firmware and supported commands; standard VISCA response does not establish tracking capability.
- Camera .120 recovery: replaced incorrect `/rtpstream/config1` with Minrray `/live/av0`; relay probe decoded 196 H264 frames at 1920x1080 / 60 fps.
- Recovery: .108/.109 `/stream1` advertised H265 despite relay-ready status; switched to H264 `/stream2`. `media.py:refresh` now rejects H265-only tracks before marking Online (source fix takes effect on next backend restart).
- Status repair: browser playback advancing within four seconds overrides stale keyframe probe warnings in the camera count/cards. Backend probing reads four seconds instead of a single packet; backend change requires next restart.
- `media.py:Media`: persistent MediaMTX sources; ffprobe reads the relay path. Global serialization covers startup/discovery/retry probes.
- Canon-like paths try the current stream then config2/config3/config1, with TCP first and bounded UDP fallback after timeout; AVer paths are not changed.
- `camera_diagnostics.py`: fail-first diagnostics; running relay is the sole upstream observer; direct DESCRIBE/probe is allowed only without a relay process.
- `errors.py`: busy/limit/reset/refusal/timeout, auth and path messages. Keyframe waiting is distinct from codec failure.
- `static/player.js`: retained players, WHEP/HLS, first-frame watchdog, reconnect backoff and local metrics.
- `player.js:syncOutput` freezes outgoing composition during display handoffs, including transient empty selections, until destination tiles have fresh decoded playback; explicit blackout clears it. Shared by Overview and paired display. `node scripts/test_output_handoff.cjs` covers hold/reveal/blackout transitions. Failed destinations retain the last frame; no first-frame freeze exists on initial startup.
- `display.html/js`: paired TV, ordered layouts and blackout; separate listener never exposes admin tools.
- LAN relay sharing follows explicit network-display enablement; read-only access is limited to the selected IPv4 /24, API/publish remain loopback.
- `ptz.py`, `visca.py`: separate Canon/ONVIF/VISCA controls; VISCA read-only checks use version/position inquiries.
- `preset_speed.py`: explicit operator speed experiments; named scene capture reserves app-owned memories and waits for acknowledgement.

## Verification limits
- The operator stopped the old backend; the updated backend is now running on port 8765. Home and the new tuning module return HTTP 200, and the state exposes tracking profiles.
- No Canon camera is currently configured in the inspected running profile; physical Canon video and device before/after measurements remain on-site acceptance.
- Engine replay starts with empty state; it is a bounded counterfactual, not a prediction of microphone firmware behavior.
- App-side Revert snapshot lives in the current Mic Tuning view; device snapshots are durable in SQLite.
