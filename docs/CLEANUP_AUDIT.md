# Cleanup audit — 2026-11-05 (initial state)
Visual refresh: reference calls for light mint/white cards and compact navigation; implemented in tracker.css with existing controls preserved.
R1 MISSING — confdeck/audio.py, runtime.py:Runtime, app.py:/api/audio*, models.py:AudioSettings, static/app.js:audioPage, style.css:.meters, tests/test_audio_and_supervision.py; sounddevice dependency.
R2 MISSING — audio.py worker/model/export; app.py:/api/captions.*, /api/audio/models; models.py:audio/display.captions; display.html/js overlay; scripts/build_bundle.py extras; pyproject.toml:sherpa extra.
R3 MISSING — static/tune.js:console/draw chart; static/app.js:drawHistory; static/style.css chart rules.
R4 MISSING — static/tune.js:console Session Lab; preserve requested recorder/replay capabilities only in Advanced.
T1 PARTIAL — static/tune.js:filters exposes all controls; static/accuracy-lab.js profiles and sweeps; no compact main tuning flow.
T2 PARTIAL — app.js:nav home/tune/accuracy/cameras/display/microphone/audio/health/settings plus setup; single index.html shell and separate paired display.html; duplicate radar/preview/settings views; studio.js opens second /api/ws; seat-radar and accuracy-lab refetch shared state.
C MISSING — static/tracker.css and style.css opaque theme; common panel markup exists; no unified glass accessibility overrides.
D1 PARTIAL — seat-radar.js shared angular geometry, live trail, seats/tolerances and draggable exclusions exist; lacks hover effects and rejected-event reasons.
D2 MISSING — no bounded app-side what-if endpoint or ghost preview; follow.py engine/recording available.
D3 PARTIAL — seat-radar.js device read/apply confirmation/readback/snapshot/revert exists; missing basic/more split, practical profile and 30-second comparison.
K1 PARTIAL — media.py persistent relay source exists; _fallback_codec_probe bypasses relay; diagnostics.py direct probes; discovery parallel probes.
K2 PARTIAL — errors.py busy mapping incomplete; Canon alternatives exist but sharing hint and active path absent.
K3 PARTIAL — media.py TCP source/restart backoff exists; player.js lacks first-frame diagnostic watchdog; UDP fallback/keyframe reporting incomplete.
K4 PARTIAL — camera_diagnostics.py staged diagnosis exists but removed from camera UI; still probes upstream while relay active.
K5 PARTIAL — camera_diagnostics.py separate control exists; visca.py read-only version inquiry present; camera cards no longer show both statuses.
TEST PARTIAL — existing regression suites; removal migration and new what-if/relay mock coverage needed.
Initial counts: 0 DONE / 10 PARTIAL / 6 MISSING.
Scope: preserve tracking, mic device API resources, numpy, seats, scenes, presets, joystick and paired display. No camera encoder or device-setting writes during validation.

## Implementation / verification
R1 DONE — capture module, endpoints, settings, panel, styles, tests and sound-device dependency removed; runtime.py:Runtime, app.py:create_app.
R2 DONE — worker/model/export/overlay code and dependencies removed; store.py:_load_settings strips obsolete keys and persists migrated backup.
R3 DONE — chart markup, draw code and styles removed; live radar trail retained as requested; static/tune.js, app.js, tracker.css.
R4 DONE — old panel removed; evaluation recorder/replay/export remains under Advanced; static/app.js:route.
T1 DONE — essential profile/tolerance/lock controls; detailed filter, sweeps and evaluation live in Advanced; mic-tuning.js:MicTuning.
T2 DONE — five destinations, master Overview, shared room state and one command/state WebSocket; static/common.js:room, app.js:homePage, studio.js:send.
C DONE — shared glass tokens, gradient, high-contrast text, unblurred media/radar and accessibility fallbacks; tracker.css.
D1 DONE — shared geometry/trail/events, seat tolerance, elevation rings, orientation, device sectors with draggable edges and priority overlay; seat-radar.js:draw/down/move.
D2 DONE — bounded 60-second/1,200-event camera-free replay with reasons, stale-preview guard and ghost points; tuning_preview.py:preview, tracking_api.py:preview_settings.
D3 PARTIAL — confirmed Apply/readback/snapshot/Revert and practical drafts implemented; 30-second comparison UI implemented but no physical mic write or before/after acceptance performed.
K1 DONE — serialized relay-only startup/discovery/retry/fallback probes; direct diagnostic DESCRIBE only without relay; media.py:Media.probe.
K2 DONE — busy mapping, ordered alternatives/backoff, active path and read-only LAN relay sharing hint; errors.py:classify_camera, media.py:write_config.
K3 DONE — TCP source, Canon UDP timeout fallback, bounded probes, keyframe state and browser first-frame watchdog; media.py:probe, player.js:start.
K4 PARTIAL — fail-first diagnosis and fixes restored; running-relay TCP/DESCRIBE stages use relay evidence rather than extra upstream sockets; absent RTP versus absent keyframe may require camera-side inspection. No configured Canon available for physical verification.
K5 DONE — independent Video/Control statuses, explicit control check; existing version/position inquiries preserved; camera_diagnostics.py:control, visca.py:check.
TEST DONE — full regression run passed 209 tests, including mock RTSP and relay-only diagnosis coverage. Isolated Edge verifies tabs, one socket, joystick release, replay, Advanced profiles, responsive width and screenshots.
Final audit: 14 DONE / 2 PARTIAL / 0 MISSING. Partial items are explicitly noted above, not claimed as hardware acceptance.
Grep exceptions: obsolete-key migration and regression fixtures/audit provenance; required mic device /audio/ resources; MediaMTX srt:false disables an unrelated transport.
Deployment: operator stopped the old backend; updated backend started and verified on port 8765 (home, tuning module and tracking profiles). Existing offline bundles are not rebuilt.
