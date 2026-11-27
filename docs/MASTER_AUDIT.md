# Master audit — initial code state, 2026-11-03
DONE requires an exercised implementation; hardware results are reported separately.
C1 PARTIAL confdeck/media.py:video_status; codec discovery claims Online without decoded-frame proof; separate control chip absent.
C2 PARTIAL confdeck/media.py:probe; ffprobe/candidates/relay/player exist; staged authenticated DESCRIBE, UDP fallback, counters and browser-stage report absent.
C3 PARTIAL confdeck/static/app.js:cameraForm; Canon paths exist; detailed settings guidance and distinct failure causes absent.
C4 PARTIAL confdeck/visca.py:_exchange; Sony framing exists; Canon local-port retry, preset offset and separate probe report absent.
C5 MISSING confdeck/visca.py:check; currently uses unsupported AVer IP power inquiry.
C6 MISSING confdeck/app.py:camera routes; per-camera redacted diagnostic export and cause mocks absent.
L1 PARTIAL confdeck/follow.py:recall; command events exist; last-20 timing medians/worst and settle panel absent.
L2 PARTIAL confdeck/tracking_math/engine.py:feed; posterior exists but fixed confirm timer remains; evidence floor UI absent.
L3 PARTIAL confdeck/tracking_math/engine.py:feed; opposite-seat multiplier exists; separate first/switch posterior barriers absent.
L4 MISSING confdeck/tracking_math/engine.py:feed; closed-level-gate events discarded.
L5 PARTIAL confdeck/follow.py:dispatch; decisions asynchronous and spherical path has no legacy smoothing; first-lock cooldown exemption absent.
L6 PARTIAL confdeck/evaluation/sweep.py:sweep; bounded sweep exists; baseline-relative guard, fastest objective and saved profiles absent.
L7 MISSING confdeck/follow.py:dispatch; optional speculation intentionally deferred until accuracy/hardware acceptance.
S1 PARTIAL confdeck/static/studio.js:send; joystick speed reaches drive only; Canon XC follow has separate speed argument; VISCA recall has no speed.
S2 MISSING confdeck/visca.py; position inquiry, reserved operator-confirmed speed experiment and travel measurement absent.
S3 PARTIAL confdeck/visca.py:recall; normal recall exists; capability-qualified absolute approach/readback absent; slider label misleading.
S4 MISSING confdeck/follow.py:snapshot; no position/frame-based measured settling.
T1 PARTIAL tests/test_spherical_accuracy.py:test_new_engine_accuracy_regression; seeded replay exists; exact +0.5pp guard absent.
T2 MISSING tests; speed-dependent camera and staged RTSP failure mocks absent.
Initial counts: 0 DONE / 13 PARTIAL / 6 MISSING (19 items).
S1 speed trace: Studio range -> WebSocket speed -> ManualControl -> ViscaIpDriver.drive.
S1 speed trace: scene capture stores only camera preset token/name, no speed or position.
S1 speed trace: VISCA recall sends 81 01 04 3F 02 pp FF, without speed bytes.
S1 speed trace: Canon XC uses tracking.preset_speed; Canon VISCA recall requires camera-side speed.
Baseline: standard seeded Accuracy Lab sessions 7/19/41; real room recordings are not supplied.
