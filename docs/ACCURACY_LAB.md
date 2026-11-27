# Accuracy Lab

Start `.venv\Scripts\python -m confdeck start`, then select **Accuracy Lab** in the local administrator UI. The display listener does not expose the lab. The tracking engine defaults to **legacy**; choose **Spherical HMM** to evaluate the new engine in dry-run. Enable Follow separately when ready. No hardware settings were changed during implementation.

## Shared seats from direction samples

Connect both microphones. In Accuracy Lab, choose **All connected microphones — one shared seat**, enter a name and run the 20-second sample with one person seated and the room quiet. Each microphone collects its own direction over the same period. Review both sets of readings and warnings, then save one shared seat. The radar preview uses the first microphone's angles; the text lists all microphones. Assign a camera preset to the resulting seat in Seats. Run separate, labelled validation with other voices and actual room noise before trusting it.

This is not a physical floor plan or voice identity. Independent speech evidence is shown where configured; without it, a stationary noise source or a reflection could produce a consistent preview. The level below the configured threshold is rejected unless the sample has positive speech evidence. If even one chosen microphone lacks sufficient usable readings, the sample asks for a retry. Two microphones observing the same false direction can still be wrong. No audio is stored by this flow.

### Requirements for live shared-seat tracking

Shared-seat camera control is **blocked** unless Spherical HMM is selected and verified independent audio captures exist for all microphones in the seat map. The existing single-microphone speech gate (`CONFDECK_SPEECH_ENABLED`) cannot be combined with this multi-microphone mode; configure the independent inputs instead. The shared-seat tracker requires fresh confirming votes from all microphones associated with that seat. It holds on disagreement or missing data, including when one is much louder. This is a conservative first implementation; a validated single-microphone degraded mode is not implemented. Existing non-shared multi-microphone behavior is unchanged.

Set `CONFDECK_MULTI_AUDIO_CONFIG` to the path of a local JSON file containing `model` and `audio`. The shape below is illustrative and **invalid** until real paths, microphone IDs, channels and measured offsets are filled in; `null` offsets intentionally fail validation:

```json
{
  "model": "/path/to/silero_vad.onnx",
  "audio": {
    "primary": {"kind": "aes67", "sdp": "/verified/first.sdp", "interface": "0.0.0.0", "channel": 1, "offset": null},
    "rear": {"kind": "aes67", "sdp": "/verified/second.sdp", "interface": "0.0.0.0", "channel": 1, "offset": null}
  }
}
```

Offsets are position timestamp minus audio timestamp in seconds and must be independently measured. Distinct streams or channel numbers do not by themselves prove independent microphone signals; verify the actual DSP routing, echo cancellation, source identity, alignment and channel content in the room. Device audio bindings can instead use `{"kind":"device", "device":0, "channel":1, "offset":0.0}` after measurement. The Accuracy Lab displays independent audio status. PCM is processed in memory and is not stored by the sampling flow.

The level-based spherical quality gate can now raise its noise estimate from known non-speech readings; when a device supplies no activity or speech evidence, it cannot reliably separate sustained noise from a person, and estimates remain limited. Spherical model scores and direction consistency are not calibrated probabilities. Neither the shared tracker nor the noise settings have been validated on the mini PC or in this room.

## Offline scenario checks and transfer

Run `python -m pytest tests/test_shared_seat_scenarios.py tests/test_seat_sample.py tests/test_multimic.py -q` and `python -m scripts.seat_sample_smoke` to exercise generated scalar microphone/speech events and the mocked browser. These tests cover the code's response to simulated agreement, conflicting directions and gains, missing/stale data, freeze, overlap flags, speech absence, quiet/normal speech evidence, noise-floor updates, and stale queued decisions. The VAD results are supplied to these tests; they do not establish that the real speech model correctly classifies actual fans, music, echo or overlapping voices. The tests also explicitly demonstrate that two identically wrong observations cannot be distinguished from a real speaker at the wrong seat using these scalars alone.

No update has been deployed. Existing archives, including `minipc-transfer.zip` and the older allowlisted mini-PC update builder, predate this shared-seat change and are not a complete transfer package for it. The mini PC may run a different ConfDeck/integration version: compare its installed source and bridge before transferring, preserve settings, and do not overwrite that installation with a partial set of files. The new seat profile is a settings-schema change, so older software may reject settings saved with it. The separate experimental `seat_fusion` shadow model does not yet accept these shared seat records; it fails closed if asked to load them.

## Manual room test
1. Confirm the coordinate convention in Geometry. Stand directly under the mic, record and label a short calibration trial, stop it, and select **Verify under-mic elevation**. Horizontal-reference elevation should be about 90 degrees downward. Existing microphone inversion/offset is applied before tracking; avoid duplicating it.
2. Configure seats and camera scenes. Select Calibration and run **20+ seconds per seat**: eight seconds each of normal, soft and loud speech. Stop, export, fit calibration, review separability warnings, and apply. The existing Learn Seat control also now takes 20 seconds and fits robust clusters.
3. Select Test and collect a separate session. Guided protocol uses seeded randomization and spoken countdowns; self-paced Start/Stop is also available. Follow the actual labelled condition, including ambient noise, loudspeaker echo, transients, moving speech, overlap, A-B-A and pauses. Overlap is recorded but excluded from single-speaker accuracy.
4. Stop and export. **Compare legacy / new** uses identical event times and mocked commands. HTML contains a timeline and confusion heatmap; JSON/CSV contain metrics. Review missed trials and no-decision time alongside accuracy.
5. Sweep suggestions require explicit confirmation. Validate the suggestion on a separate held-out test session before claiming room accuracy.

## Recording and replay
Raw microphone JSON, level readings, processed directions, reasons, seat/confidence, command attempts/results and optional vision scores are recorded. Calibration sessions cannot be scored or swept. Gzip JSONL chunks live under the profile's `accuracy-sessions` directory: approximately 2 MB uncompressed per chunk, 12 chunks retained. The background queue is capped at 2,048 events; drops invalidate scoring. The live buffer is capped at 50,000 rows / 30 minutes. Export before rotation removes older recordings. Frames and audio are never stored.

```powershell
.venv\Scripts\python -m confdeck.evaluation --engine compare --output docs/reports/my-comparison
.venv\Scripts\python -m confdeck.evaluation --session session.0000.jsonl.gz --settings tracking-settings.json --engine compare --output reports/room
.venv\Scripts\python -m pytest tests/test_accuracy_lab.py tests/test_spherical_accuracy.py tests/test_tracking.py -q
.venv\Scripts\python -m scripts.accuracy_lab_smoke
```

The CLI defaults to a seeded synthetic fixture. Supply all session chunks and a TrackingSettings JSON export for real replay. Tune & Follow replay supports accelerated playback and uses the selected engine; lab batch replay runs without sleeping. Runtime reports overwrite the previous comparison in `accuracy-reports` to bound storage.

## Metrics and acceptance
Rates are weighted by labelled time. Confusion cells are seconds; none includes no decision and wide view. Angular errors compare predicted and labelled seat centers under the configured elevation convention, conditional on a seat prediction. No-decision time is reported separately. Opposite error is speech time with a predicted seat over 120 degrees from truth. Latency begins at each labelled speech interval; missed trials are reported separately. Spurious switches count new seat commands during quiet/noise/echo labels. Echo false-trigger rate measures echo time with an active seat; `echo_trigger_trial_rate` separately measures echo trials with a new false commitment. A previous seat can persist briefly during release hysteresis.

`docs/reports/legacy-baseline.*` was saved before engine work. It and the comparison reports are synthetic measurements, not hardware baselines. Room targets require real held-out recordings. Opposite errors and echo occupancy during release are reduced, not eliminated. `event-benchmark.json` reports measured costs on this host; occasional scheduling spikes exceed 1 ms, so a strict maximum is not claimed.

## Optional vision
Off is the default: no vision imports, workers, preview or frame requests. Advisory logs scores; Active applies calibrated, bounded log-likelihood evidence. Both global and camera modes must permit processing. The lab supplies 320x180 grayscale frames at at most 5 fps from the existing preview. Closing the page ends frame supply; evidence expires after 600 ms. Draw head ROIs per camera/seat, optionally per preset, and screen masks, then save. For close-ups, select the actual preset context; a fixed observer is preferable.

Frame processing runs on a worker thread, retaining only transient frames and bounded baselines. It removes global brightness changes and masks PTZ motion through settling plus 500 ms. Processing disables above a 25 ms rolling average, 200 ms end-to-end frame lag, or a two-second feed gap. Re-enable to retry. Browser decoding cost is outside the Python measurement: configure a low-resolution stream where available and check CPU on the target machine.

Capture-before / check-after uses phase correlation. Recall the preset separately in Studio; the self-test sends no movement command. Textureless frames return inconclusive. The transient before frame survives page navigation within this application load.

For mouth evidence, supply a local Face Landmarker `.task` path in Advanced vision settings and an offline-installed MediaPipe package. Nothing downloads automatically. Only the candidate crop is processed. Missing dependency/model falls back to motion; excessive processing latency disables vision. No model or MediaPipe binary is bundled. Review the licenses supplied with your chosen package and model. Recalibrate likelihoods after changing score source.

## Microphone resources and assistants
Verified against the repository copy of the official [TCC M OpenAPI 1.9](https://docs.cloud.sennheiser.com/en-us/api-docs/api-docs/open-api-tc-ceiling-medium.html):

- Level: `GET /api/audio/inputs/microphone/level`, integer peak from -90 to 0 dB, tagged FastResource. The adapter separately attempts subscription and uses bounded polling if rejected or stale. Direction polling remains at most 5 Hz for stationary-speaker confirmation.
- Settings: `GET/PUT /api/audio/inputs/microphone/beam` and `/api/audio/noiseGate`.
- Exclusions: `GET /api/audio/inputs/microphone/exclusionZones`, `GET/PUT .../exclusionZones/{id}`, IDs 0–4, five-degree boundaries.
- Subscription mechanism: `GET /api/ssc/state/subscriptions`, `PUT .../{uuid}/add`. Per-resource acceptance for settings/zones is not separately advertised or hardware-verified; the assistant uses explicit GET/read-back.

Exclusion proposals use labelled noise/echo in original device coordinates and ignore the parked signature. Review real seat overlap before confirming Apply. Existing durable snapshot, confirmation and read-back are reused. A/B accepts two or three setting sets: run the same standard protocol for each, then Finish this setting set. The immediate pre-test settings are restored on completion, failure, timeout or cancellation. A hard OS/process crash cannot execute an async revert: use the retained durable snapshot and verify the device before continuing.

Sennheiser documents Dante AEC reference routing and a downward 90-degree beam in far-end mode: [official AEC guidance](https://help.sennheiser.com/hc/en-us/articles/29958524525586-TCC-2-TCC-M-Acoustic-Echo-Cancellation). The exact 315-degree azimuth remains a configurable hypothesis to verify on this unit.

## Code and verification
`tracking.py` preserves legacy decisions. `tracking_math/` contains geometry, vMF fitting, quality and HMM inference; this name avoids colliding with the existing module. `evaluation/` contains recording, protocols, replay, reports, sweep, live API and device assistants. `vision/` contains lazy optional processors. Focused tests cover geometry, ghosts, calibration isolation, gates, HMM decay, deterministic replay, three regression seeds, duration weighting, vision-off imports, motion/phase correlation, settings restoration and admin routes. Browser smoke intercepts every request and does not touch hardware.
