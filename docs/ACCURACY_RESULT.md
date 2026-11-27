Initial audit: DONE 0 / PARTIAL 10 / MISSING 14; implementation follow-up: DONE 23 / PARTIAL 1 / MISSING 0.
Implemented evaluation recording/protocol/replay/metrics/sweep, spherical vMF/HMM tracking, calibration, lab UI, device assistants and optional ROI vision.
Legacy baseline saved before engine changes: docs/reports/legacy-baseline.html, .json and .csv.
Synthetic comparison: legacy correct 72.5%, wrong 24.1%; new correct 83.7%, wrong 6.0%; median latency 1.3 seconds.
Synthetic quiet spurious switches: 0/min; echo trials with new false commitments: 0%; echo time retaining an earlier seat: 10.8%.
Opposite-direction error time remains 6.0%; room accuracy targets and near-zero echo occupancy are not established.
Event cost on this host: median 0.126 ms, p95 0.461 ms; occasional scheduling spikes exceed 1 ms.
Run: .venv\Scripts\python -m confdeck start, then Accuracy Lab; legacy is default and new engine starts in dry-run.
Tests: .venv\Scripts\python -m pytest tests/test_accuracy_lab.py tests/test_spherical_accuracy.py tests/test_tracking.py -q
153 tests covered without failures: full run completed 133 then stalled; the stalled test and remaining 20 passed separately. Final focused run: 19 passed. Browser smoke and targeted lint passed.
API: level GET supports negotiated subscription with bounded stale/rejected fallback; beam/noiseGate and exclusionZones GET/PUT verified against official OpenAPI 1.9.
Per-resource settings/zone subscription acceptance remains unverified; assistants use GET/read-back, confirmation and snapshots.
Vision defaults Off. Optional mouth adapter awaits an operator-supplied licensed local model/package and end-to-end validation; frame/decode cost and parked signature require manual checks.
Operator guide: docs/ACCURACY_LAB.md. No physical camera movement or microphone settings changes were performed during implementation.
