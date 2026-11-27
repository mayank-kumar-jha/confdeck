# Independent-audio seat fusion: shadow candidate

This is an experimental candidate for the room at 192.168.1.31. On 21 November
2026 it was enabled in shadow mode, which does not command cameras. The active
controller is implemented but is not enabled or validated with room speech.

The operator enabled Delegate AES67 and created flow 16. Both microphone audio
streams have reached the mini PC, and the service reports both sources running.
The selected Delegate channel 2 and zero timing offsets are provisional and
must be verified with real speech and timing measurements. The shadow scorer is
uncalibrated; no trained model or room accuracy result is available. The camera
was reporting automatic mode at deployment and that mode was preserved. The
original AES67 receiver was backed up at
`/home/ordain/integration-backups/fusion-isolation-20261121-175951`.
During a short operator speech check, both Delegate channels reported the same
status counts: speech on 6 of 40 half-second polls, noise on 10, and silence on
24. This confirms detectable speech occurred during the comparison, but does
not identify channel processing or establish accuracy.

## Audio-network inspection, 21 November 2026

- The tracking service currently uses a single speech input bound to the TCC M
  `LocalOut` SDP. It cannot independently identify speech for each mic. The
  inspected diagnostics directory has no second SDP, and the mini PC only
  exposes its onboard HDA audio device in `/proc/asound/cards`. A separate,
  short-lived receiver using the staged code received 90 frames from the
  existing TCC M multicast in three seconds while the existing capture remained
  healthy. This checks transport only, not alignment or speech accuracy.
- Dante Controller shows `Delegate` (TCC 2) and `TCCM-2aaee6` (TCC M).
- `xilica-9bcb65` identifies itself as a Xilica Solaro Module. It has four
  receive subscriptions: two from `Delegate` and two from the TCC M. All four
  show green status and signal indicators. The internal DSP processing and
  the identity/content of its output channels are not known. Its RTP mode
  currently reads `None`; no change or reboot was made.
- `Delegate`'s RTP mode also reads `None`. Its two Dante audio channels feed
  the Xilica device, so a microphone reboot or transport change could interrupt
  the other project even though the microphone itself is in scope.
- The mini PC also has a running ALSA capture for its enabled far-end monitor.
  The source feeding that analog input has not been verified. It is already in
  use and is not evidence of an available independent microphone feed.
- `iSound44D` identifies as DSP-44D and has AES67 mode enabled. Its IN5 and
  IN6 subscriptions to the old TCC M `FarEndOut` and `LocalOut` names show
  warning icons. The currently advertised TCC M channels display as `Dante 1`
  and `Dante 2`. This is consistent with a changed channel name, but the exact
  warning reason and DSP signal routing have not been established. No routes
  were changed. The iSound transmit page currently lists OUT5–OUT8 and no
  multicast transmit flows.
- Do not assume `Dante 1`, `Dante 2`, `OUT5`–`OUT8`, or the Xilica outputs are
  independent echo-cancelled local-mic signals based on their names. Verify
  the signal path and far-end reference in the relevant DSP configuration.

### Operator-provided Xilica project backup

The operator downloaded `yashxilica.pjxml` from the Solaro QR1; it was parsed
locally without changing the DSP. The saved design connects four Dante input
channels into the audio input block, whose corresponding labels are `TM 1 M`,
`TM 1 F`, `TM 2 F`, and `TM 2 M`. Each passes through its own filters,
equalizer, and automatic gain control before entering the matrix mixer.

In the saved design, the matrix's outputs named `Tile REF 1` through `Tile REF
4` connect to the four Dante transmit channels. These names and connections do
not establish whether any output is an isolated microphone, a far-end reference,
or a mixture. The internal matrix crosspoints require interpretation in Xilica
Designer and live verification. The exported project is the originally loaded
design and may not reflect later runtime parameter changes. Do not route these
outputs to seat detection or alter matrix routing solely from their names.

The [INFOBIT iSound manual](https://www.infobitav.com/wp-content/uploads/2024/09/iSound-44D-88D-1616D-User-Manual-V2.0-2.pdf)
describes an AEC module using local and remote mixers; it does not establish
that this installation provides two separate processed outputs. Dante's
[RTP configuration documentation](https://dev.audinate.com/GA/dante-controller/userguide/webhelp/content/rtp_config_tab.htm)
explains that compatibility and mode must be checked per device.

The operator clarified that the Xilica is used for this project's voice lift,
while other projects must remain unaffected. No DSP, microphone, or Dante
configuration has been modified during this investigation.
The Solaro control interface advertises `192.168.1.88`; its separate Dante
interface is `192.168.1.89` and is the current Dante clock leader. Rebooting
or changing its network-audio mode could affect other Dante devices.

## What is implemented

- `confdeck/seat_fusion/audio.py`: explicit, distinct source-to-microphone
  bindings; one existing Silero capture and recurrent state per mic. It rejects
  missing timing offsets, duplicate channel bindings, stale frames, clipping,
  and missing signals. Mono and selected stereo AES67 channels are supported;
  stereo channels are decoded separately and never downmixed. It does not persist PCM.
- `engine.py`: an inspectable experimental spatial/speech scorer, optional
  trained model, uncertainty/hold behavior, and a shadow-only recommendation.
  Its initial heuristic scores are not calibrated probabilities. It does not
  claim to resolve simultaneous ambiguous speakers.
- `model.py` and `train.py`: a small supervised fusion model trained only on
  explicitly labeled calibration sessions from at least two distinct session
  IDs. It needs examples for every mapped seat and the no-speaker class, and
  fingerprints the microphone/seat geometry and angle convention. Reconfigure
  or recalibrate after the room map changes.
- `replay.py`: tests a recorded baseline and candidate against the same labeled
  test session. Missing audio produces no recommendation. Its trial report
  counts misses and reports noise-only switches separately. The report compares
  retained baseline and shadow views and also reports shadow speaker detection
  separately, where missing and ambiguous evidence count as no decision. Calibration and
  evaluation sessions must be separated; no measured 98% claim exists.
- `integration/mini31-fusion-candidate/confdeck/` holds opt-in hooks for the
  currently inspected mini-PC version. Set `CONFDECK_FUSION_SHADOW_CONFIG` to
  a reviewed JSON config only after the two audio bindings are verified. With
  no variable the hooks are off. Errors in shadow inference are isolated from
  the existing tracker. Shadow recommendations are included in tracking status
  and labeled sessions as scalar telemetry; they do not reach camera dispatch.

`python integration/mini31-fusion-candidate/stage_check.py` creates a temporary
copy of the live package, checks the live baseline hashes, adds the candidate,
loads two independent copies of the installed speech model, exercises synthetic
fusion and calibration, and checks that a shadow fault does not block the
existing tracker. This is not a physical audio or seat-accuracy test.

`install_disabled.py` installed the candidate code with no shadow configuration
and restarted only the mini PC service. It verified both microphones, audio,
seat settings, viewer, manual layout mode, and shadow-off status after restart.
The previous source files are under
`/home/ordain/integration-backups/seat-fusion-20261121-171713`.
The stereo-channel addition was installed with shadow still disabled, without
restarting the service. Its previous audio binding file is under
`/home/ordain/integration-backups/seat-fusion-channel-20261121-172109`.
The focused microphone, evaluation, capture, AES67, and fusion suite passed
67 tests. This is software verification rather than a real-room accuracy result.

## Configuration shape

The following is illustrative only and intentionally invalid until the actual
audio routes and offsets are measured. The IDs must match the configured seat
map, not device names guessed from the network. Use a separate mono AES67 stream
per mic, or bind each mic to its own verified channel in a stereo AES67 stream.
The independent-channel capture in this candidate does not downmix them.

```json
{
  "model": "/home/ordain/speech-diagnostics/model/silero_vad.onnx",
  "audio": {
    "primary": {"kind": "aes67", "sdp": "/verified/mic1.sdp", "interface": "192.168.1.31", "offset": null},
    "0cde3151f884": {"kind": "aes67", "sdp": "/verified/mic2.sdp", "interface": "192.168.1.31", "offset": null}
  },
  "seat_links": {}
}
```

`offset` is the independently measured position timestamp minus audio
timestamp, in seconds. Null fails validation. Alternatively, a binding can use
`{"kind":"device", "device":0, "channel":1, "offset":0.0}` when the
specific physical device/channel and timing have been verified. Two bindings
may not use the same endpoint. Different stream addresses alone do not prove
different speech content.
For a verified stereo stream, supply the same SDP path to both bindings and
set `"channel": 1` and `"channel": 2` respectively. This is permitted only
when the two channels have actually been confirmed as separate microphone feeds.

If both microphones have independently calibrated angles for one physical
seat, `seat_links` explicitly maps each additional zone ID to the chosen
canonical zone ID. Do not infer these links from a similar name or beam angle.
The current room maps have separate seats per microphone; overlapping
cross-microphone seat calibration has not been collected.

## Room validation and training

1. Back up and inspect the DSP configuration and Dante routes with the AV
   operator. Identify both microphone signals, remote playback reference,
   AEC/noise-suppression processing, and two separate outputs. If the DSP mixes
   the microphones, it cannot provide the separate inputs this design requires.
2. Verify stream arrival, source identity, timing offsets, level, clipping,
   and audio/beam alignment for each mic. Do not reuse the single shared feed
   under two names. Configuring AES67 or routes may interrupt conference audio.
3. Configure and start the installed opt-in shadow candidate after verifying
   its two independent feeds. Record labeled calibration sessions for each seat, room
   noise, transient sounds, and silence. The baseline tracker continues to own
   the camera; training and recording are local and store scalar evidence only.
4. Export at least two distinct calibration sessions. Example (repeat each
   `--session` for a separate session; include all its chunks):

   ```sh
   python -m confdeck.seat_fusion.train --session calibration1.0000.jsonl.gz --session calibration2.0000.jsonl.gz --settings tracking.json --output room-model.json
   ```

5. Collect separate held-out labeled **test** sessions, then compare:

   ```sh
   python -m confdeck.seat_fusion.replay --session test.0000.jsonl.gz --settings tracking.json --fusion-model room-model.json --output comparison.json
   ```

The trained model needs labeled calibration examples for every configured
seat and for no speaker. Its scores can be wrong or overconfident. Real-room
accuracy, latency, false switches, walking, speaker overlap, noise, echo,
occlusion, and loss/recovery need held-out testing. The optional active camera
bridge is implemented but is not enabled; a visual model is not implemented.
