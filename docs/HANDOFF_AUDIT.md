# Seat-locked handoff audit
Initial audit, before implementation. DONE requires an exercised end-to-end path.
- T1 PARTIAL tracking_api.py:native_set supports one HTTP dialect with readback. Studio exposes manual ON/OFF.
- T2 PARTIAL follow.py:execute recalls and settles. It previously had no tracker arming lifecycle.
- T3 MISSING visca.py:settled provides a measurement primitive only.
- T4 PARTIAL manual.py:Manual.accept and follow.py:_recall provide manual exclusion and command locking.
- T5 PARTIAL tracking.py:TrackingFilter.tick handles room silence and same-seat hysteresis.
- T6 PARTIAL visca.py:_exchange correlates framed replies. follow.py:execute records recall timings.
- T7 PARTIAL follow.py:execute covers moves with wide and holds previous output.
- T8 PARTIAL static/studio.js provides manual tracking controls only.

## After implementation
- T1 DONE handoff.py:Handoffs.send and handoff_api.py:configure_handoff. Studio supports none, custom VISCA, command presets and HTTP. Templates use SecretStore. Tests exercise physical-observation gating.
- T2 PARTIAL handoff.py:Handoffs.run. Tested loss, ordering, recovery and supersession. A recall already on the wire can finish before the latest target takes over.
- T3 PARTIAL handoff_api.py:configure_handoff records observed lock and travel, behavior during recall and shortest successful tested OFF gap. Distance requires position readback. Room measurements pending.
- T4 PARTIAL runtime.py:Runtime.start/monitor/close, manual.py:Manual.accept, visca.py:save and ptz.py:execute. Recovery uses a camera-specific wide when assigned. Physical reconnect recovery untested.
- T5 PARTIAL handoff.py:Handoffs.tick and follow.py:execute. Room silence selects program wide; per-camera timers manage idle trackers and optional re-anchoring. Physical verification pending.
- T6 PARTIAL visca.py:connection/wait_for_completion and follow.py:execute. Framed VISCA retains a socket; raw replies use isolated sockets. Advanced shows timings. Wire latency unmeasured.
- T7 PARTIAL follow.py:dispatch prefers eligible opted-in off-air cameras; the output waits for completion or fallback settling time. Physical cut verification pending.
- T8 DONE static/studio.js:handoffPanel and static/app.js:update. Browser verified Studio panel and Advanced waterfall. All four live cameras set to Off.
- Source: https://clarilus.com/product-details.html?product=cla-cam12 and its linked CLA-CAM12_Manual.pdf. Sony VISCA 52381 is documented. Exact tracking ON/OFF bytes remain unverified.
- Synthetic wrong-seat rate before/after: legacy 24.0625%, spherical 4.375%.
- Mock: 100 alternating turns, 1 ms simulated command awaits and 3 ms simulated travel. Median 46.5 ms, p90 63.0 ms on this Windows host.
- Physical .108 test, 2026-11-06: ON 81 0A 11 54 02 FF and OFF 81 0A 11 54 03 FF each returned 90 42 FF then 90 52 FF. Operator confirmed following during ON and stopping during OFF. Saved verified pair, cleared previous failed-test flag, mode remains Off. Query 81 09 11 54 FF was rejected. This verification covers .108 only; full seat handoff timing remains unmeasured.
