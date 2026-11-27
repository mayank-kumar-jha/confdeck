# Speech gate audit (2026-11-16)

DONE requires both implementation and a test, UI, or CLI exercise of the requested item.
- S1 PARTIAL — `speech_audio.py:AudioCapture`, `Aes67Capture`; `aes67.py:Aes67Receiver`: opt-in local device capture or SDP-selected AES67 multicast into bounded in-memory 16 kHz frames; mocked PCM/SDP/RTP decoding tested and the user's TCCM stream produced real speech/noise/silence frames on .84; live integration pending.
- S2 PARTIAL — `speech.py:SileroFrames`, `SpeechGate.push`: local ONNX adapter and timed transitions, mock-interface/state tests; the real mini-PC model processed the room stream, but no labelled acoustic accuracy set exists.
- S3 PARTIAL — `speech.py:SpeechGate`, `tracking_math/quality.py:QualityGate.feed`: offline dual-gate/reliability/low-voice implementation tested; no live PCM connection.
- S4 PARTIAL — `tracking_math/engine.py:SphericalFilter._feed`: offline speech pre-roll bounded/corroborated, tested; physical latency unmeasured.
- S5 PARTIAL — `speech.py:suggest_calibration`: tested scalar threshold suggestions; guided UI/apply/revert pending.
- S6 PARTIAL — `speech.py:estimate_sync`: tested paired-clap clock estimation; live capture/UI pending.
- S7 PARTIAL — `evaluation/speech.py:compare`: same-session scalar comparison and strict guard tested; real room frames are confirmed, with aligned labelled acoustic comparison pending.
- S8 PARTIAL — `speech_audio.py:main`, `evaluation/lab.py:AccuracyLab`, `evaluation/speech.py:merge_scalar_frames`: opt-in timestamped scalar VAD capture can be aligned with Accuracy Lab without saving PCM; real comparison still pending.
- S9 PARTIAL — `static/mic-tuning.js:MicTuning`: level gate controls exist; no speech-gate card, audio status, or wizard.

Baseline: seed 7, `python -m confdeck.evaluation --engine compare --output artifacts/speech-baseline`; rechecked 2026-11-16 with same metrics.
Legacy: wrong 24.0625%, false switches 0/10 min, soft locks 3/3, median 1.8 s; spherical: 4.375%, 0/10 min, 3/3, 0.65 s.
The fixture synthesizes direction and level, not audio; its soft and normal levels use the same distribution. A real AES67/VAD diagnostic ran on .84, but its frame labels are model output, not operator ground truth, so the diagnostic is not an accuracy comparison.
Offline implementation and usage: `docs/SPEECH_GATE.md`. Live speech gate remains disconnected/default off. Audit: 0 DONE, 8 PARTIAL, 1 MISSING.
Local Python environment lacks sounddevice, onnxruntime, sherpa-onnx, and silero_vad; no local speech model was identified in the inspected dependency paths.
