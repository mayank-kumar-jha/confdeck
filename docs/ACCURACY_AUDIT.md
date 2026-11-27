# Accuracy audit — before implementation
E1 | PARTIAL | confdeck/follow.py:Follow.feed | Missing: gzip rotation, raw payloads, session tags, decisions and commands.
E2 | MISSING | confdeck/static/tune.js:guide | Missing: labelled randomized standard protocol and self-paced trials.
E3 | PARTIAL | confdeck/follow.py:Follow.replay | Missing: engine/profile selection and non-wall-clock batch replay.
E4 | MISSING | confdeck/evaluation/metrics.py (planned) | Missing: time-weighted metrics and HTML/JSON/CSV reports.
E5 | MISSING | confdeck/evaluation/sweep.py (planned) | Missing: constrained sweep and confirmation-only suggestion.
E6 | PARTIAL | confdeck/microphone.py:tracking_scenario | Missing: spherical/ghost/parked/level scenarios and accuracy thresholds.
M1 | PARTIAL | confdeck/tracking.py:distance | Missing: great-circle geometry, conventions and nadir verification.
M2 | MISSING | confdeck/tracking_math/model.py (planned) | Missing: robust vMF mixtures, clutter, tolerance and separability.
M3 | PARTIAL | confdeck/runtime.py:poll_tracking_position | Missing: adaptive floor, sustained energy and transient gate.
M4 | PARTIAL | confdeck/tracking.py:TrackingFilter.feed | Missing: configurable parked signature and frozen high-level detection.
M5 | MISSING | confdeck/tracking_math/engine.py (planned) | Missing: jump reliability and opposite confirmation.
M6 | MISSING | confdeck/tracking_math/engine.py (planned) | Missing: HMM, none state, posterior hysteresis.
M7 | PARTIAL | confdeck/follow.py:Follow.learn_zone | Missing: 20-second robust calibration, split data and offset estimation.
M8 | PARTIAL | confdeck/device_settings.py:DeviceSettings.apply | Missing: log-derived proposals; existing snapshot/confirm/read-back retained.
M9 | MISSING | confdeck/evaluation/assistants.py (planned) | Missing: settings A/B workflow with revert.
U1 | PARTIAL | confdeck/static/tune.js:tuning | Missing: echo guidance and live quality indicators.
U2 | PARTIAL | confdeck/static/tune.js:live | Missing: posterior bars and radar rejection overlay.
U3 | MISSING | confdeck/static/accuracy-lab.js (planned) | Missing: Accuracy Lab page.
V1 | MISSING | confdeck/vision (planned) | Missing: lazy off/advisory/active modes and budgets.
V2 | MISSING | confdeck/vision (planned) | Missing: ROI motion energy, lighting normalization and PTZ mask.
V3 | MISSING | confdeck/vision (planned) | Missing: optional offline mouth-motion adapter.
V4 | MISSING | confdeck/static/accuracy-lab.js (planned) | Missing: camera/preset ROI setup.
V5 | MISSING | confdeck/vision (planned) | Missing: calibrated LLR soft evidence and replay switches.
V6 | MISSING | confdeck/vision (planned) | Missing: phase-correlation movement self-test.

Counts: DONE 0; PARTIAL 10; MISSING 14. Planned pointers indicate absent code.

## Implementation follow-up (code plus test/UI/CLI entry)
E1 | DONE | evaluation/session.py:Recorder; evaluation/lab.py:AccuracyLab | Bounded raw/decision/command gzip sessions; round-trip test and UI.
E2 | DONE | evaluation/protocol.py:protocol; static/accuracy-lab.js:guide | Seeded protocol, countdowns, self-paced labels.
E3 | DONE | evaluation/replay.py:replay | Timestamped mocked legacy/new replay; determinism tests and CLI.
E4 | DONE | evaluation/metrics.py:metrics,export | Duration weighting, metrics, timeline/heatmap exports and comparison UI.
E5 | DONE | evaluation/sweep.py:sweep | Constrained grid suggestion; explicit profile confirmation UI.
E6 | DONE | evaluation/synthetic.py:generate; tests/test_spherical_accuracy.py:test_new_engine_accuracy_regression | Three seeded regression thresholds.
M1 | DONE | tracking_math/geometry.py | Spherical geometry, conventions, nadir and offset controls/tests.
M2 | DONE | tracking_math/model.py:fit,calibrate,SeatModel | Robust mixtures/clutter/tolerance and held-out separability.
M3 | DONE | tracking_math/quality.py:QualityGate; runtime.py:poll_tracking_position | Level subscription negotiation/fallback and energy/noise gates.
M4 | DONE | tracking_math/quality.py:QualityGate.feed | Configurable parked and frozen gates; hardware signature unverified.
M5 | DONE | tracking_math/engine.py:SphericalFilter.feed | Corroborated jumps and opposite-candidate confirmation.
M6 | DONE | tracking_math/engine.py:SphericalFilter | Forward posteriors, none decay and decision hysteresis; tests/UI.
M7 | DONE | tracking_math/model.py:calibrate; follow.py:Follow.learn_zone | 20-second learning, separate calibration and offset controls.
M8 | DONE | evaluation/assistants.py:propose_exclusions; evaluation/api.py:exclusions | Proposals plus existing confirmed snapshot/read-back path.
M9 | DONE | evaluation/assistants.py:settings_ab; evaluation/api.py:ab | Guided A/B with restoration; failure test and UI.
U1 | DONE | static/accuracy-lab.js:render,refresh | Echo guidance, quality indicators and reason counts.
U2 | DONE | static/accuracy-lab.js:refresh | Radar accept/reject history, posterior bars and confidence.
U3 | DONE | static/accuracy-lab.js:AccuracyLab | Admin page and intercepted browser smoke.
V1 | DONE | tracking_math/config.py:VisionSettings; evaluation/lab.py:frame | Global/per-camera modes, lazy imports and budgets; off-import test.
V2 | DONE | vision/motion.py:MotionEnergy; static/accuracy-lab.js:capture | ROI motion, baseline, masks, light rejection and PTZ suppression.
V3 | PARTIAL | vision/mouth.py:MouthMotion; evaluation/lab.py:frame | Optional adapter/fallback and UI entry exist; local licensed model/package and end-to-end mouth validation remain operator-supplied.
V4 | DONE | static/accuracy-lab.js:render | Head/mask drawing per camera/preset and persisted ROI configuration.
V5 | DONE | vision/motion.py:learn_llr,llr; tracking_math/engine.py:SphericalFilter.feed | Calibration-only soft evidence, advisory/active replay and veto setting.
V6 | DONE | vision/motion.py:phase_correlation | Transient before/after self-test UI and shifted-frame test.
Follow-up counts: DONE 23; PARTIAL 1; MISSING 0. Hardware acceptance remains manual; these statuses describe implemented and exercised capabilities.
