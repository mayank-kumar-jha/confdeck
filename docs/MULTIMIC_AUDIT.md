# Accuracy, latency and room-scale audit — 2026-11-12

## Findings and implemented changes

| Finding | Change | Practical effect |
| --- | --- | --- |
| One microphone object and one angular coordinate space | Independent adapters, credentials, certificate pins, lifecycle and seat maps; up to eight configured mics | Multiple coverage areas without averaging unrelated directions |
| No arbitration for simultaneous mic reports | Per-mic engines, fresh votes, 6 dB conflict margin and 200 ms nomination interval | Holds the shot when sources disagree; shared movement limits remain enforced |
| Serial level/direction HTTP reads and serial polling | Parallel reads per mic, independent polling loops, 600 ms request bounds and 200 ms target cadence | A slow mic no longer stalls other microphones; avoids additive level/direction round-trip delay |
| In-flight polling could overwrite newer SSE | Direction/level revision counters, including Windows equal-clock-tick cases | Newer push telemetry wins |
| Duplicate/old spherical samples altered the posterior | Reject before posterior advancement | Repeated data does not weaken confidence or count as new evidence |
| Old packets could update level before timestamp rejection | Validate packet timestamp before telemetry mutation | Stale levels cannot influence fresh direction |
| Custom field mappings detected only default field names | Recognize configured direction field names | Custom SSCv2 resource mappings work as advertised |
| Overview ignored the existing preview limit | Apply the existing preview limit to the camera overview | Avoids starting a decoder for every configured camera |
| Pending camera decisions waited for a 100 ms timer | Wake the scheduler as soon as a command completes | Removes up to one scheduler tick of avoidable queue delay |
| Setup exposed only the primary microphone | Reuse existing form/radar; show a selector only for multiple microphones | No extra tuning fields or room-size form |
| Learning/calibration could mix source coordinates | Filter training data and separability checks by microphone identity | Calibration and replay keep independent coordinate spaces |
| Camera codec retries probed a reconnecting relay and could lose the chosen path after restart | Wait for upstream readiness and persist successful stream selection | More reliable recovery of existing H.264 alternatives |
| Decoder warnings contaminated ffprobe JSON | Capture stderr separately from structured probe output | Valid decoded streams no longer fail JSON parsing |

The single-primary-mic configuration remains compatible with old settings. The existing engine and tuned thresholds are preserved; no global accuracy claim or unmeasured threshold reduction is applied. Camera presets, native tracking and physical microphone settings are not changed by the test suite.

## Validation and limits

The pre-existing 225-test regression suite passed during implementation. New tests cover source separation in both engines, conflicting speech, dominant-source selection, failover, global camera budgets, frozen/quiet/unknown input, stale polling races, API persistence, identity and secret handling, and isolated reconnects. Browser acceptance runs use a temporary room and simulated devices; they verify adding microphones, separate seat maps, selected-device tuning, decoder limits and mobile width.

The expanded full suite passed **236 tests**. The isolated multi-mic browser and existing cleanup browser checks both passed; the output handoff JavaScript checks passed. Additional selected-device and unmapped-source tests are included in the focused multi-mic suite.

Synthetic scaling processed 6,400 events from eight microphones for each engine, with eight expected seat transitions and no unexpected transitions. Measured per-event processing p95 was approximately 0.04 ms (legacy) and 0.48 ms (spherical) on this PC. With 40 ms mocked network requests, median level/direction polling fell from 97.4 ms serial to 49.9 ms parallel. Synthetic speech-to-decision was 500–600 ms for the test's legacy configuration and 800–900 ms for the spherical configuration. These figures exclude network transport, physical camera motion and real room acoustics; they are not a reason to override the room's saved thresholds.

See `tests/test_multimic.py`, `scripts/multimic_smoke.py`, and `docs/reports/multimic-benchmark.json` for reproducible checks. Re-run with `.venv/Scripts/python -m pytest -q` and `.venv/Scripts/python -m scripts.multimic_smoke`.

Software regression and synthetic timing tests are not physical room measurements. Universal hardware support, end-to-end accuracy and a fixed latency guarantee are not established. Remaining acceptance work is real multi-mic speech trials, matched microphone gains, overlapping coverage calibration, camera firmware/protocol validation and measured video/physical PTZ timing. Installation steps and exact supported boundaries are in [ROOM_INSTALLATION.md](ROOM_INSTALLATION.md).
