# Pending mini-PC update — 192.168.1.84 only

This archive is staged locally, not deployed. No network settings, camera IPs,
microphone IPs, credentials, room configuration, service units, or model files
are included. Port 4000 and the existing `/viewer` endpoint are retained.
Do not copy this update to any other device.

Local validation for the speech-evaluation update: 19 focused speech and
Accuracy Lab tests passed; the diagnostic CLI confirmed OFF without opening an
audio device or model. No remote services were accessed or restarted for this
build.

## HDMI / Smart Preview

`omnitrack/start-kiosk.sh` and `omnitrack/kiosk_watch.py` replace the kiosk
launcher together in the existing OmniTrack installation. They run within its
existing X11 session, use the installed Chromium-compatible browser, wait for
the local viewer, start fullscreen when a display connects, and restart the
browser after a crash. Display mode changes happen only on connection changes.
The launcher disables screen blanking and does not launch an overlay video.
It does not change layouts, camera selection, routing, or IP settings.

Tested with mocked connected/disconnected outputs, browser crash, backend
unavailability and X server failure. HDMI, X authorization, actual browser
decoding, boot services and power-cycle recovery require the mini PC.

## Speech gate

The `sehnizer/` files extend the existing ConfDeck installation. The live
tracker retains the old gate. Diagnostics require optional locally installed
`sounddevice` (with PortAudio), `onnxruntime`, and a compatible local Silero
ONNX model. The archive does not install or download them.

```sh
python -m confdeck.speech_audio                       # OFF, opens nothing
python -m confdeck.speech_audio --list-devices         # explicit enumeration
python -m confdeck.speech_audio --enable --model /path/to/silero_vad.onnx --device 2 --channel 1 --seconds 60
```

Diagnostics read 16 kHz audio in memory only and print scalar status. The
optional `--scalar-output` sidecar stores timestamped level/probability values
and a dropped-frame count, never PCM. Accuracy Lab sessions now include the
mini PC's monotonic clock origin, allowing the scalar sidecar to be aligned for
offline comparison. It does not issue camera commands or change room settings.
See `docs/SPEECH_GATE.md` for the workflow and strict guard. The guided
calibration UI and live speech-gate controls are not included; production
tracking keeps its current gate.

## Deployment when the device is reachable

1. Connect only to .84. Identify its current OmniTrack root, ConfDeck root,
   graphical session and kiosk service; do not assume paths or restart services
   from older deployment scripts. Port 4000 must be restored before HDMI QA.
2. Upload into a new staging directory. Verify the archive SHA-256 sidecar and
   every payload hash in `manifest.json`. Review deployed source differences;
   this archive is not permission to overwrite unrelated newer work.
3. Back up the exact destination files with permissions. Deploy the two kiosk
   files together and make `start-kiosk.sh` executable. Keep the existing launch
   entry and X authority. Restart only the identified kiosk session/service.
4. Verify `/viewer`, HDMI unplug/replug, fullscreen live video and browser
   restart. Do not restart ConfDeck/MediaMTX blindly: existing service scripts
   may kill shared media ports. Roll back the two kiosk files if verification
   fails and restart the same kiosk service.
5. Stage the ConfDeck files separately. Confirm source compatibility, run the
   offline tests, and prepare audio dependencies/model for this Linux host.
   No live speech activation until a representative held-out comparison passes.

The archive intentionally contains no automatic installer or service restart.
It cannot certify real-room speech accuracy or repair an unreachable host.
