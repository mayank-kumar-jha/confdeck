# Preview pause hold — deployed 16 November 2026

The port-4000 single-view layout previously selected Camera 1 immediately when no microphone seat was active. The spherical tracker can release a seat after a short silence, so that layout fallback caused the preview to leave a speaker during a normal pause even though its explicit wide-shot timer was longer.

`services/layout_service.py` now holds the last voice-tracked speaker in the single-view preview for five seconds **after** active-seat loss. A new confirmed seat or an explicit wide-shot seat takes priority immediately. After the hold expires the existing Camera 1 fallback resumes. The hold is limited to a connected voice controller and resets on mode/sub-mode changes. It does not change speech confidence, seat-lock timing, preset assignments, camera command timing or physical microphone behavior.

Tested: seven pause-hold tests, including the actual simulated layout loop, short pause, same-speaker resumption, immediate switch, explicit wide, disconnect/assignment loss and hold expiry; twelve existing tracking integration tests passed. All seven hold tests also passed on the .84 staged copy without camera access. Installed by atomic replacement after confirming the live source matched the reviewed baseline. Restarted only `tracking_backend.service`; viewer HTTP 200, microphone Connected, speech enabled/healthy, controller reconnected. The layout mode before and after was `manual`; it was not changed. No physical camera or person-based speech test was performed. Backup: `/home/ordain/integration-backups/preview-pause-hold-20261116/layout_service.py`.

---

# Deployment result — 16 November 2026

Deployed to `192.168.1.84` after source comparison and staging. Both `confdeck.service` and `tracking_backend.service` are active. Speech mode is enabled with the installed Silero ONNX model, TCCM LocalOut AES67 SDP, the .84 interface, 0 ms receiver timestamp estimate and zero clock offset. The Python application uses the existing diagnostic virtual environment for ONNX Runtime through `confdeck_speech_runtime.pth`; the app's existing NumPy remains in use. Speech configuration is the `/etc/systemd/system/confdeck.service.d/speech-live.conf` drop-in. The earlier import-offline drop-in is intact.

**Automatic camera tracking was changed from auto to manual and remains paused** because the room-speech calibration and physical camera STOP/preset test could not be witnessed. No PTZ motion or physical preset commands were issued during deployment. Re-enable using the port-4000 tracking control only after the operator checks camera framing and quiet speech/noise behavior. The actual capture/direction clock offset remains uncalibrated; do not interpret a zero offset as measured. The settings threshold and seat mappings were not modified.

The first atomic upload attempt failed before a source replacement because standard SFTP rename would not overwrite. Rollback commands restored the service configuration; the second attempt used atomic POSIX rename and installed ten source files. Exact original files are in `/home/ordain/integration-backups/speech-ptz-20261116-deploy2`, with the original pre-pause layout setting in `/home/ordain/integration-backups/speech-ptz-20261116-deploy`. Original setup HTML and Sennheiser UI JavaScript were separately backed up under `deploy2/public/`. No settings database, camera credentials or preset memories were modified.

Post-deployment: microphone Connected; speech enabled/healthy in 45/45 samples over 24 seconds (silence/noise model output; no labelled speech); viewer HTTP 200; microphone and backend services active; no sampled Python error/traceback; deployed Lumens timeout/preset logic passed the same mocked no-hardware simulation. In a 10-second preliminary timing diagnostic, the original 40 ms estimate had 54 stale samples of 177; changing it to 0 ms produced 197/197 fresh samples. A separate HTTP-polled direction shadow check matched 98/136 events, demonstrating that actual timing/clock alignment and real utterance testing still require attention. The result is not an accuracy measurement and cannot certify all long-duration noise rejection or correct physical preset motion.

The port-4000 Sennheiser mapping panel now reports speech validation readiness. Setup HTML cache versions were changed for the status module and camera preset module; reload the page. Original device and network configuration otherwise remains intact.

---

# Live speech and Lumens PTZ update — 16 November 2026

## Status

Local implementation and isolated tests only. `.84:4000` remains on the previously deployed version: SSH using the available non-interactive identity returned `Permission denied (publickey,password)`. No camera movement, service restart, hardware configuration or remote file was performed.

## Speech

See `SPEECH_GATE.md` for mode, variables, fallback, tests and acceptance limits. Speech validation is opt-in and defaults off. The normal microphone runtime and the saved port-4000 microphone-only runtime both call `Runtime.connect_mic`, which starts capture. The existing adapter remains the camera owner. Only the primary spherical engine is supported.

The real Silero ONNX model accepted synthetic computer speech attenuated to 12% amplitude, and the spherical tracker locked simulated seat A at 0.58 seconds using -68 dB telemetry. Repeated synthesized clap transients and stationary white noise (six seconds each) produced no simulated seat decisions. This is a small, artificial sample, not a measured accuracy rate, and proves neither field performance nor that claps, music or far-end voices can always be rejected. Capture-to-direction latency and drift are not physically calibrated. The computer speech and results are in `artifacts/product-review/`.

## Camera preset issue

The live `.84` read-only camera list identifies .108 and .109 as LUMENS. The saved port-4000 source dispatches continuous directional commands through multiple transports and launches asynchronous ONVIF motion separately. Stop is independently dispatched; a delayed movement request can arrive after a stop. Browser requests are also unsynchronized and lacked a backend deadman. These are credible causes of runaway movement, not proof of the specific observed incident.

In the saved integration source, Lumens movement now sends one framed VISCA command on UDP 52381, with a renewable 700 ms server-side stop timer. The browser serializes movement/stop, renews while held, and stops on blur, hidden page, page exit and camera change. Lumens preset save/recall uses the same one-path transport, validates range and reports sent rather than physical arrival. Other brands retain existing paths. The simulated test verifies timeout, stop cancellation, packet bytes, exactly one preset operation, and invalid slot rejection; it sends no network packets. Real camera firmware/protocol compatibility and physical STOP/recall are unverified.

The affected files under `integration/current84/` are ignored in this checkout and must be included explicitly in any release. Compare the actual deployed files first; `.84` may contain newer changes. Preserve credentials/configuration and existing physical preset memory. Back up exact deployed files, stage the patch, verify hashes, and use a controlled on-site test and rollback. Do not claim the issue is fixed on the running device until that test passes.
