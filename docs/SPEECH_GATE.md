> Deployment update: enabled on .84 in manual camera mode; see `LIVE_SPEECH_PTZ_UPDATE.md` for current verification and limitations.

# Live integration update (16 November 2026)

The local checkout now supports **opt-in, single-primary-microphone speech-assisted seat locking** with the spherical engine. This update supersedes historical statements below that live code is disconnected. It has not been deployed or enabled on `.84`. The prior recording of AES67/model output was diagnostic and did not establish room accuracy.

Set these environment variables for the existing microphone runtime after verifying the deployed files, audio stream and dependencies:

```sh
CONFDECK_SPEECH_ENABLED=1
CONFDECK_SPEECH_MODEL=/absolute/path/to/silero_vad.onnx
CONFDECK_SPEECH_SDP=/absolute/path/to/TCCM-LocalOut.sdp
CONFDECK_SPEECH_INTERFACE=192.168.1.84
CONFDECK_SPEECH_OFFSET=0
```

Alternatively omit `CONFDECK_SPEECH_SDP` and set `CONFDECK_SPEECH_DEVICE` and `CONFDECK_SPEECH_CHANNEL` for a local 16 kHz input. ONNX Runtime, NumPy, a valid model and an AES67 LocalOut route (or sounddevice/PortAudio for the local source) are required in the **application's** Python environment, not only the separate diagnostic environment. Disable by removing `CONFDECK_SPEECH_ENABLED` and restarting the identified microphone service. Do not use the far-end mix or assume the sample SDP/IP is correct without readback. Use an independently measured position/audio offset; 0 is only a starting value.

In enabled mode, fresh scalar speech reliability accompanies valid mic direction samples; non-speech, clipping, unavailable/stale audio, incompatible engine and multiple-microphone configurations inhibit new locks. Missing audio clears the logical active seat and queued decisions; the downstream port-4000 adapter holds the last camera view. A valid speech frame can qualify a direction even if the telemetry level is below the level-only threshold. Upstream device gating can still suppress quiet speech before audio reaches the model; separate hardware calibration is necessary. Speech probability does not distinguish room occupants from loudspeaker speech and is not a guarantee against music, applause or all noise.

Local tests: the real bundled Silero model received synthetic quiet computer-generated speech, repeated synthetic clap-like bursts and steady synthetic noise. The complete model/gate/spherical pipeline selected the simulated correct seat at 0.58 s for the voice and made no seat decisions during either six-second noise sample. These are narrow synthetic checks, not labelled acoustic room acceptance, and the position was simulated. `tests/test_speech_live.py` also exercises fresh evidence, sustained loud non-speech and missing-frame behavior without cameras. Clock synchronization, actual quiet speakers, sustained real applause and real microphone/camera end-to-end testing remain necessary before using this feature in a meeting.

Existing `minipc-update-pending.zip` predates this integration and is **not** an installable live-speech update. Do not deploy it for this feature. Compare deployed source to the local checkout and build a reviewed release; the .84 administrative SSH attempt returned permission denied in this session.

---

# Speech gate implementation status

## Mini-PC diagnostic preparation (2026-11-16)

On .84, `/home/ordain/speech-diagnostics-venv` now contains NumPy 2.5.5,
sounddevice 0.5.6 and ONNX Runtime 1.32.0, separate from the running app.
PortAudio was installed without service restarts. Standalone diagnostic code
and the Silero model/license/commit-and-hash manifest are under
`/home/ordain/speech-diagnostics`. The real local ONNX model passed a zero-frame
inference check (-120 dBFS, P approximately .00167); this is not a speech test.
The source commit is `066409f3d9db45a30d21dfcbf9147826ffdd7052`.
Only Realtek ALC256 analog capture is exposed; no USB audio device is present.
The operator confirmed no audio cable is connected. PortAudio enumeration in
the SSH session also fails because the PulseAudio host cannot connect to a
server. No capture process or live speech gate was left enabled. Network audio
must be routed through a compatible receiver before acoustic validation.

Read-only network inspection from .84 subsequently discovered the microphone
`TCCM-2aaee6` at .93 advertising Dante `LocalOut` and `FarEndOut` channels at
48 kHz, and `iSound44D` at .12. These advertisements establish discoverability,
not receipt of audio samples. The existing MediaMTX ready paths exposed only
H264 video tracks. A 12-second SAP observation on .84 found no SDP announcements;
this short observation does not rule out a configured stream. The colleague's
working recording computer is currently powered off, and its receiver/app/input
configuration is unknown. No Dante routes or device IP settings were changed.

The first implementation is available only in the offline evaluator. Live
microphone capture and Mic Tuning controls are not connected. Production calls
keep the default `speech=False`; they load no speech module, audio device, model,
or inference worker. Existing level-only behavior remains the live behavior.

After the diagnostic check below, the user confirmed that the .84 AES67 receive
path produced live model outputs across speech, silence and noise. This verifies
audio delivery and inference only; the printed classes are not ground-truth
labels, so no live gate was enabled.

Implemented and exercised by `tests/test_speech_gate.py`:
- 32 ms scalar frame processing; two-frame onset, 300 ms release, stale-frame
  fallback, probability hysteresis, sustained near-threshold low-voice mode.
- Separate audio dBFS noise floor and microphone telemetry levels; bounded
  scalar history; reliability from audio level margin multiplied by probability.
- Offline spherical HMM fusion, spatial/far-end safeguards, existing 400 ms
  direction pre-roll with conservative speech reliability weighting.
- Calibration threshold search for silence/normal/quiet/noise metadata; no
  automatic apply; paired-clap offset estimation with consistency checks.
- Local-file-only Silero ONNX adapter, one CPU inference thread, recurrent
  context/state, clipping/no-signal measurements. Its tensor interface is tested
  with a mock session; a real model and acoustic validation are still required.

Silero interface reference: [upstream ONNX wrapper](https://github.com/snakers4/silero-vad/blob/master/src/silero_vad/utils_vad.py).
No dependencies or model files are downloaded by this implementation.

## Offline comparison

Use an Accuracy Lab **test** session with normal `mic` rows and aligned
`speech_frame` rows containing only `t`, `level` (PCM dBFS), and `probability`.
Sort rows by timestamp, placing the audio frame before a position row at the
same timestamp. `--offset` adds seconds to audio timestamps to obtain position
timestamps. Position labels use existing `soft` for quiet speech and noise
conditions from the Accuracy Lab protocol. The optional diagnostic JSONL stores
only timestamps, dBFS, probability and capture health; it never stores PCM.

For a real-room A/B, start an Accuracy Lab **test** session, then run the
AES67 diagnostic concurrently on the same mini PC with a new scalar output path.
Stop the diagnostic normally before stopping/exporting the Lab session. The
Lab session records its monotonic clock origin so the sidecar can be aligned:

```sh
python -m confdeck.speech_audio --enable --model /path/to/silero_vad.onnx \
  --aes67-sdp /path/to/TCCM-LocalOut.sdp --interface 192.168.1.84 \
  --seconds 900 --scalar-output ./room-session-vad.jsonl
python -m confdeck.evaluation.speech --session session.0000.jsonl.gz \
  --settings tracking-settings.json --scalar-frames ./room-session-vad.jsonl
```

Use a fresh output filename each run. The merge rejects missing clock metadata,
incomplete/dropped scalar captures, and frames from outside the Lab time window.
It rejects the comparison if VAD evidence does not cover every mic event.

The comparison uses the same spherical engine and settings in both arms. It
requires complete audio coverage, no wrong-seat regression, strictly fewer
non-speech switches, strictly more quiet trials locked, and at most 100 ms
additional median lock latency. Missing metrics fail. It never changes live
settings. A pass on invented probabilities is only a software check.

The seed-7 existing fixture still measures 4.375% wrong-seat time, 0 false
switches/10 minutes, 3/3 soft locks and 650 ms median latency in both arms with
audio absent. Its guard correctly fails (no demonstrated improvement).
The calibration unit fixture detects quiet voice with zero noise triggers;
these are supplied probabilities, not measured room speech/noise.

`speech_audio.py:AudioCapture` now provides an explicit opt-in diagnostic input:
device/channel selection, 16 kHz/512-sample frames, bounded in-memory buffering,
ADC-to-monotonic timestamps, missing-model/device status, stale/no-signal
fallback, clipping warning, and reconnect. It is separate from live tracking.
Tests use a mock source and prohibit file access during capture. See
[sounddevice's stream API](https://python-sounddevice.readthedocs.io/en/latest/_modules/sounddevice.html)
for the capture interface. `python -m confdeck.speech_audio` without `--enable`
opens nothing; `--help` describes explicit diagnostics.

For a Linux mini PC without a Dante sound-card driver, the opt-in
`Aes67Receiver` accepts one AES67 SDP flow and decodes 48 kHz L24/L16 RTP into
16 kHz mono frames in memory. It joins only the multicast group and port named
in the SDP. The diagnostic command is:

```sh
python -m confdeck.speech_audio --enable --model /path/to/silero_vad.onnx \
  --aes67-sdp /path/to/TCCM-LocalOut.sdp --interface 192.168.1.84 --seconds 60
```

The SDP must describe the TCC M `LocalOut` stream. The receiver handles packet
loss by inserting silence and resetting the VAD evidence, and reconnects when
the stream stops. It does not create or store an audio recording. This remains
a diagnostic input; the live tracker and tuning UI are not connected yet.

Remaining: room verification of capture-to-position clock mapping,
real model/audio tests including language and noise mixtures, live
status/fallback UI, guided calibration/apply/revert, and deployment acceptance.
Noise suppression remains absent until a separate evaluation proves a gain.
