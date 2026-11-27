# Remote audio and room-speaker tracking

## Required behavior

Remote conference audio played in the room must not initiate camera preset
recalls. A local participant must still be able to trigger tracking while the
remote participant is speaking. Keep the current shot when there is not enough
evidence to distinguish the two; do not mistake a missing reference for proof
of local speech.

## Room inspection, 17 November 2026

- The Voice Track mini PC is `192.168.1.30` on `enp2s0`.
- The TCC M control connection is `192.168.1.92`; its Dante identity is
  `TCCM-2aaee6` at `192.168.1.93`. Its `LocalOut` currently feeds the speech
  model directly via AES67, without DSP echo cancellation.
- The Dante network advertises a device called `iSound44D` at `192.168.1.12`.
  Dante Controller exposes its transmit channels as `OUT5` through `OUT8`.
- Dante Controller shows no subscription to the TCC M `FarEndRef` receiver.
  During a short observation the microphone reference meter read -120 dBFS.
  The user confirmed the DSP's far-end reference is not yet configured. A quiet
  meter on its own would not prove a route is absent.
- The current speech model detects speech but cannot distinguish remote speech
  from local speech. The spherical quality gate rejects the TCC M beam-freeze
  flag, so freeze is not a solution for tracking a new local speaker during
  far-end/near-end overlap.

## Integration to prepare when the conferencing route exists

1. In the DSP's configuration software, identify the remote playback signal
   actually sent to the room speakers. Feed that signal to the DSP's acoustic
   echo canceller as its reference. Route the ceiling mic into the AEC's
   microphone input; do not feed the mic output back as its own reference.
2. Expose a separate **AEC-processed local microphone output**, without the
   remote mix, for Voice Track. Verify the specific DSP, its processing block,
   output format, and AEC behavior; the visible `OUT5`–`OUT8` names alone do
   not identify the signal. If using the existing AES67 receiver, prepare an
   advertised, compatible mono 48 kHz L16/L24 AES67 output and verify its
   timing. Native Dante routing to the mini PC requires a Dante receiver.
3. Add a named and persistently bound speech source in the admin connection
   flow so the speech model receives that AEC output rather than TCC M
   `LocalOut`. Confirm the chosen output and microphone belong to the same
   room; do not silently infer identity from IP addresses or select an
   unrelated source when multiple streams exist. Reconnect safely after loss.
4. Validate that the microphone's directional telemetry continues to identify
   a real local speaker while remote audio is playing. The DSP's AEC output
   alone does not correct a wrong beam direction. If the mic parks its beam on
   `FarEndRef`, the current tracker intentionally rejects that position; do
   not enable a blanket far-end hold as a substitute for overlap tracking.
   Evaluate appropriate microphone beam, loudspeaker placement, and device
   settings, and use further independent local-direction evidence if needed.
5. Calibrate the delay between processed audio and the beam telemetry and
   include capture freshness, stream loss, source changes, and restarts in the
   fail-closed behavior. The stored zero offset is not a measured alignment.

## Room acceptance

Observe with camera output in dry-run first. Collect labelled trials from
every intended seat for: local speech only, remote speech only at normal and
loud playback levels, simultaneous local/remote speech, remote playback with
local silence, and network/reference disconnect and recovery. Measure wrong
presets and missed local speakers, including overlap. Review real camera
motions only after the dry-run result is acceptable. Synthetic or unlabelled
model outputs do not establish the false-trigger rate.

References: [INFOBIT iSound manual](https://www.infobitav.com/wp-content/uploads/2024/09/iSound-44D-88D-1616D-User-Manual-V2.0-1.pdf),
[Sennheiser AEC and beam behavior](https://help.sennheiser.com/hc/en-us/articles/29958524525586-TCC-2-TCC-M-Acoustic-Echo-Cancellation).
