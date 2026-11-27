# Voice Tracker Pro: Tune & Follow

Camera previews, counts, selectors and seat assignments show only real configured
camera connections. Old simulated camera entries are removed when a profile loads.

The frontend uses the supplied ORDAIN logo and charcoal/orange palette, with blue
signal accents. All fonts, scripts, images and configuration are local. The
existing ConfDeck camera, display, microphone pages remain available.

## Run

From the project directory in PowerShell:

```powershell
.venv\Scripts\python -m confdeck start
# Isolated microphone demonstration (cameras still need real connections):
.venv\Scripts\python -m confdeck start --demo --data-dir .runtime\demo
```

Open the local admin URL printed by the launcher, then **Tune & Follow**.
Tracking always starts in **Dry-run**, including after restart. Saving tuning,
changing camera/microphone settings, and starting replay stop live following.
The Stop following button is also available in the global header while following.
Stopping prevents further recalls; a movement already accepted by a camera may
finish. It does not claim to physically halt that movement.

## Live voice test in the room

1. Connect the TCC Medium on the Microphone page: SSCv2, HTTPS port 443, username
   `api` (built in), and the third-party password set in Control Cockpit. Complete
   the staged connection test and verify the certificate fingerprint.
2. Add the cameras with their existing video/control credentials. Under **Seats &
   presets → Camera control**, select Canon XC, ONVIF, or Sony VISCA over IP and
   enable the control switch for each intended camera. VISCA uses UDP 52381 by
   default; adapt this to the camera. This checkout did not contain a
   `ViscaIpDriver`; the new adapter is recall-only. It cannot save/delete presets
   or overwrite a reserved save range. VISCA preset numbering is 0–127; verify
   the camera's own numbering and protocol. Canon is 1–100, ONVIF uses tokens.
3. Read **Microphone settings**. Review current values and adjust noise gate,
   sensitivity, mounting or device zones if needed. Apply asks for confirmation,
   snapshots all exposed controls first, then reads each write back. A failed or
   incomplete initial snapshot blocks writes. Partial write failures identify
   completed controls; the original snapshot remains available for **Revert to my
   snapshot**. Snapshots are keyed by microphone address, port and certificate.
4. Choose Calm, Balanced or Responsive under **Tracking filter**. These are
   starting values, not calibrated room profiles. For TCC Medium, enable **Require
   speech above the level threshold**, then tune the threshold against room
   noise. Do not require a talking flag unless your microphone mapping actually
   supplies one. TCC Medium exposes level and far-end beam freeze, not a direct
   near-end talking boolean. Missing required signals pause tracking.
5. Stay in dry-run. Speak at seat 1 and choose **Learn a seat · 3 seconds**. Assign
   an existing preset for each camera, then save. Repeat at seat 2. Learning only
   stores angular centers/radii; it never saves a camera preset. Overlapping zones
   produce a warning. Assign a wide preset for every camera that should return
   after silence. Use **Test recall** to verify individual shot framing; it asks
   before moving cameras even in dry-run.
6. Watch raw/smoothed history, candidate progress and the decision log. Increase
   confirmation, smoothing or hold time if the camera would switch too often.
   Zone margins must be smaller than their zone radii. Multi-camera recalls count
   individually toward the moves-per-minute limit.
7. Choose **Test with my voice**: speak at seat 1, then seat 2, then pause. Dry-run
   PASS verifies a filter decision, not camera framing. Save the tuning first.
   Choose **Follow live** and confirm to repeat with real cameras. Verify the
   physical shots yourself. Three camera errors stop live following automatically.

Canon supports the configurable recall speed through the existing driver.
VISCA and ONVIF keep their existing camera speed; no undocumented speed command
is sent. The displayed angular radar is illustrative, not a floor measurement.
Tune zone widths using speech at the actual seats; the displayed beam cone is illustrative.

## Record/replay and persistence

- **Advanced → Accuracy → Record**, then **Stop recording → Export session**, saves JSON
  containing raw input angles, level/activity when available, and relative time.
  Recording is bounded to 10 minutes or 10,000 samples. Export before closing the
  app: the in-memory recording is not automatically saved.
- Load that JSON at 0.25–8× speed. Replay uses a separate filter and stops live
  following. It never calls a camera. Signal charts show replay while it runs;
  replay decisions are shown in its log. Sample jitter/flapping/noise scenarios
  use the first two saved seat centers when available.
- Tuning, seat mappings, wide presets and camera-control switches persist through
  the existing validated SQLite settings store. Export/import tuning as JSON.
  Following mode itself is never persisted as enabled.
- Decision reasons and device changes go to the normal application logs. Live
  history and log lists are bounded. The 60-second chart is sampled at most 20 Hz.

## Confirmed device API resources

Source: [Sennheiser TCC Medium Swagger page](https://docs.cloud.sennheiser.com/en-us/api-docs/open-api-tc-ceiling-medium.html).
Its `dist-tc-ceiling-medium/swagger-initializer.js` points to
[SSCv2 schema 1.9](https://assets.sennheiser.com/download/assets/api_1.9.0.json/18b31510118311f186a21e752e7f194a).
The original schema is saved as `docs/reference/tcc-medium-1.9.0.json`.

| Control | Resource and fields |
| --- | --- |
| Noise gate | `/api/audio/noiseGate`: `threshold` −90…−40 dB; `holdTime` 50…1000 ms (UI enforces 50 ms steps) |
| Sensitivity/mount | `/api/audio/inputs/microphone/beam`: `sourceDetectionThreshold` QuietRoom/NormalRoom/LoudRoom; `installationType` FlushMounted/SurfaceMounted/Suspended; `offset` |
| Exclusion zones | `/api/audio/inputs/microphone/exclusionZones/{id}`, ids 0–4: enabled, azimuth, elevation |
| Priority zone | `/api/audio/inputs/microphone/priorityZones/0`: enabled, weight, azimuth, elevation |
| LEDs | `/api/device/leds/ring`: brightness, showFarendActivity, micOn, micMute, micCustom |
| Direction | `/api/audio/inputs/microphone/beam/direction`: azimuth, elevation, beamFreezeActive |
| Mic level | `/api/audio/inputs/microphone/level`: peak (−90…0 dB) |

Resource paths and write schemas live in `confdeck/protocols/tcc_medium.yaml`.
An optional `tcc_medium.yaml` in the active data directory overrides the bundled
mapping at startup, so paths can be corrected without editing Python. Reuse the
same schema layout; these are operator-controlled configuration files.
Device controls use the existing pinned/authenticated SSCv2 client. Change-only
SSE remains intact; periodic direction/level reads provide fresh samples for a
stationary speaker. Public mic WebSocket events retain their duplicate suppression.

**Unconfirmed/unavailable:** no audio factory-reset endpoint was found in schema
1.9. The button is disabled, with a note to use Control Cockpit. No invented reset
or static-beam/yellow LED command is sent. Only the documented LED fields are
exposed. Device firmware may reject documented controls; errors are shown plainly.

## Verification

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check confdeck tests
node --check confdeck/static/app.js
node --check confdeck/static/tune.js
```

Focused tests: `tests/test_tracking.py` and `tests/test_device_settings.py` cover
wrap, median/EMA, flapping, noise bursts, margin/deadband, cooldown/settling/rate,
silence, duplicate/missing input, persistence, replay isolation, automatic error
stop, a real loopback UDP VISCA exchange, and snapshot/revert through a local
certificate-pinned HTTPS SSCv2 mock. Tests prohibit public networking.

Browser review screenshots are under `artifacts/tune-review/`. Hardware testing
still requires the actual microphone/cameras and an on-site speaker; software
tests and protocol acknowledgements do not prove physical framing or audio quality.

## Changed files

- Frontend: `confdeck/static/app.js`, `index.html`, `tune.js`, `tracker.css`, and
  the two supplied ORDAIN logo assets.
- Filter/orchestration: `confdeck/tracking.py`, `follow.py`, `tracking_api.py`.
- Device/control: `confdeck/device_settings.py`, `protocols/tcc_medium.yaml`,
  `visca.py`, with integration updates to `microphone.py`, `runtime.py`,
  `models.py`, `app.py`, and a VISCA guard in `ptz.py`.
- Tests and documentation: the focused test modules, this guide,
  `docs/CODEBASE_MAP.md`, `README.md`, and the downloaded reference sources.
