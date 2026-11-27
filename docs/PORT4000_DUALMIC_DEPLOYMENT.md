# Dual-microphone update on the mini PC

Deployed to `192.168.1.31`, 20 November 2026. The operator interface remains on port 4000 and OmniTrack remains the camera-command owner. The installed source was read first; the deployed candidate was built on that source to preserve the newer room-specific integration.

The TCC 2 now refreshes its beam and level meter every 200 ms. Both microphone seat maps participate in the shared arbitration. A still-confirming candidate can hold back a conflicting seat. When both microphones have enough recent non-speech samples, the arbiter compares levels relative to each microphone's own background rather than relying on their uncalibrated absolute readings. The existing confirmation and movement limits remain in force.

The enabled speech-audio capture is shared and did not reliably recognize tests from either microphone. Each microphone therefore has a bounded fallback: after a short quiet calibration period, repeated signals at least 7 dB above its own room background can enter normal seat confirmation and microphone arbitration. The old fixed TCC 2 level threshold could trigger on the room's background. This fallback detects sound rather than proving it is speech, so music or loud local noise can also trigger a mapped seat. Very quiet speech close to the background level can still be missed; verify every seat in the actual room.

The existing dual editor now has a mixed-map overview, live microphone levels, a shared-lock indicator and per-microphone editing. Each plot uses its own microphone's angular coordinates. The display preview uses the existing `/viewer` output and has a refresh control. The shared relay accepts OmniTrack's camera publishers, and Camera 1 automatically uses its verified H.264 substream for preview when the configured main stream is H.265. The stored camera source is not changed.

The TCC 2 exclusion controls are managed in Sennheiser Control Cockpit. Exclusions were not changed automatically. Review them against the actual microphone angles and ensure they do not cover a valid seat.

## Verification

- The mini PC's installed engine passed staged speech-mode arbitration, duplicate-sample, TCC 2 metering, room-noise normalization and shared-speech-gate checks using simulated input.
- The port-4000 camera bridge passed 14 local integration tests; the dual-map editor passed a browser smoke test against the installed-source candidate. The standalone workspace suite passed 296 tests before the final mini-PC-specific changes.
- On the mini PC, both microphones reconnected and the speech capture was healthy after restart. The seat and preset settings matched their pre-deployment hash, with eight saved seat entries.
- During operator tests, the TCC 2 room lock switched from asad to mayank to neha. The automatic controller logged neha's saved Solvision preset 2. A direct recall of that same preset received both an acknowledgement and a completion reply from the camera. The operator subsequently reported that the TCC Medium seats were working. A brief incorrect seat transition was also observed after sound stopped; a proposed tightening did not pass staged tests and was not deployed. A full per-seat accuracy and latency measurement was not performed.
- Both camera publishers reported ready H.264 tracks; both WebRTC endpoints returned successful answers. The Display page's embedded viewer decoded live video with its startup overlay dismissed.
- Automatic tracking was restored to its original `auto / single` mode.

Source backups are under `/home/ordain/integration-backups/dualmic-20261120-174121`, `dualmic-20261120-174434`, `dualmic-20261120-174910`, `dualmic-20261120-175352`, `dualmic-20261120-180314`, and `dualmic-20261120-180855`. The reviewed mini-PC candidate is in `artifacts/mini31-candidate`. No camera preset memories, seat coordinates, or room microphone settings were intentionally changed.
