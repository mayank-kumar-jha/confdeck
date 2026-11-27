# ConfDeck

Local camera, room-display and ceiling-microphone tracking desk.

## Start
Double-click **Start ConfDeck.cmd**, or run `.\confdeck.cmd start`.
Open `http://127.0.0.1:8765`. `Start Demo.cmd` simulates only the microphone; camera video requires real devices.
Double-click **Check Setup.cmd** for installation checks. See the [small/large room installation guide](docs/ROOM_INSTALLATION.md) and [accuracy/latency audit](docs/MULTIMIC_AUDIT.md).

Multiple microphones: use **Add microphone** in Seats and Mic, then select each mic and place its seats using the same radar. Up to eight TCC Medium microphones and 16 network cameras can be configured; each mic has its own seat map. The selector is hidden for the normal single-mic setup. Mic Tuning follows the selected microphone.

## Workspace
- **Overview**: all camera previews, ordered display output, live seat radar, status, Follow/Stop, TV pairing, layout and HDMI.
- **Cameras**: discovery, credentials, video/control diagnosis, joystick and named scenes.
- **Seats and Mic**: connect the TeamConnect Ceiling Medium, unlock seat setup, place seats and assign scenes.
- **Mic Tuning**: Fast/Balanced/Safe starting profiles, tolerance multiplier, lock sensitivity, orientation, exclusions and gate settings. Preview app settings before applying.
- **Advanced**: detailed filters, evaluation, recorder/replay, exports, health and preferences.

Device changes require confirmation and verified readback. Revert restores the durable microphone snapshot. App-side Revert is available within the current tuning view.
What-if replays up to 60 seconds / 1,200 events through an isolated engine without moving cameras. The engine starts empty; device-side previews are not exact. After applying device changes, keep the view open for the 30-second comparison.

## Camera video
MediaMTX owns upstream connections. Preview and diagnostic clients read its relay. Canon fallback tries enabled alternatives without changing camera encoder settings. AVer paths retain their configured stream.
Use **Diagnose video** to see the first failure and remedy. For a camera with only H.265 available, enable its `transcode_h264` setting through the local camera PATCH endpoint. The PC converts its video to H.264 at 720p and 25 fps for the existing preview and shared relay.
H.264 is required for playback; a missing keyframe suggests checking enabled RTP output and a 1–2 second I-frame interval on an unused stream.
**Check control** is independent of video. It uses read-only inquiries and never moves a camera.
Enable network display in Overview to share `rtsp://THIS_PC:8554/CAMERA_ID` with other systems on the selected /24. Shared viewers should use the relay instead of opening another camera connection. API and publishing permissions remain local-only.

## Development and offline bundle
Python 3.11+ is required. Install with `pip install -e ".[dev]"` on a preparation machine.
Run `python scripts/fetch_vendor.py` and `python scripts/build_bundle.py --zip` to prepare an offline bundle. Existing bundles must be rebuilt to include source changes.
The runtime uses local bundled assets and private-network device connections. It does not download tools or change camera encoders automatically.

Run `.venv/Scripts/python -m pytest -q` for regression tests.
Run `.venv/Scripts/python -m scripts.cleanup_smoke` for isolated Edge UI verification; all hardware writes are mocked.
See [codebase map](docs/CODEBASE_MAP.md), [cleanup audit](docs/CLEANUP_AUDIT.md), and the device/preset guides in `docs` for implementation details.
