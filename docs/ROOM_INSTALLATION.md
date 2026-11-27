# Room installation

## Install and start

1. Extract the complete portable Windows ZIP into a local folder. Do not launch inside the ZIP. The portable package includes Python and the video tools; no Python installation is needed.
2. Connect the control PC, cameras and microphone control interfaces to the room network. Give each device a unique private IPv4 address. Use a wired connection for predictable video delivery.
3. Double-click **Check Setup.cmd** to check tools, writable settings and video ports. Ports already in use are expected while ConfDeck is running.
4. Double-click **Start ConfDeck.cmd**. The local desk opens at `http://127.0.0.1:8765`.
5. In **Cameras**, scan or add by IP, use **Diagnose video**, then **Check control**. Save your camera scenes before linking seats.
6. In **Seats and Mic**, connect the first microphone using its IP and third-party password. Unlock setup, speak from each seat, use the live beam position, and assign the camera scene.
7. Start in dry-run. Check every seat, quiet speech, ordinary speech, room noise and speaker changes. Enable Follow only after the proposed seats are right.

Settings and encrypted credentials stay in the existing per-user ConfDeck data folder. Updating the extracted application does not require re-entering room settings. Close the running application before replacing it; restart to load new backend code.

## Small rooms

Start with one microphone if it covers all seats clearly. Keep the existing Balanced profile, then compare Fast in Mic Tuning using recorded examples. Tune against soft speech and the room noise floor, rather than reducing every confirmation delay. A camera wide shot helps during silence or uncertainty.

## Large rooms and multiple microphones

Use **Add microphone** in Seats and Mic for each additional microphone. Select TCC Medium for SSCv2 or TCC 2 for SSCv1 TCP, normally port 45. TCC 2 uses an unencrypted legacy protocol without a third-party password; use a trusted room network. Open **Show mixed map** to see both angular maps and select an existing seat.

Select a microphone before placing its seats. Each mic has a separate angular map because the same angle means a different physical seat from another ceiling location. Where coverage overlaps, create the seat in both maps and assign the same saved camera scene. Keep device gain/sensitivity comparable; absolute speech levels are used for arbitration. A persistent level advantage caused by different gain settings can bias selection.

Mic Tuning follows the microphone selected in Seats and Mic. Orientation, seat tolerance and physical device changes apply to that microphone; timing and app speech gates apply to the whole room. Device writes still require review and readback, with a separate snapshot for each device.

The system independently checks level and seat direction for each microphone. A still-confirming candidate can block a conflicting weaker confirmed seat. Conflicting fresh seats need a 6 dB advantage to change the shot; otherwise the current shot is retained. A short 200 ms arbitration interval prevents a first-arriving microphone from winning immediately. Old votes expire after 600 ms. Camera hold times, move limits and silence-to-wide remain room-wide. Level and direction alone do not prove a sound is speech, and raw levels from different models may not be calibrated to each other. Test both microphones with the room's actual gains and acoustics.

Follow Sennheiser's placement guidance for the actual hardware. For TCC Medium, the published planning guide gives a minimum mounting distance of 2.5 m above the floor and a recommended maximum speaker distance of 3–3.5 m. Room acoustics and obstructions still need an on-site coverage test. See [planning the installation](https://docs.cloud.sennheiser.com/en-us/tc-ceiling-medium/planning-installation.html).

## Camera scale and latency

Up to 16 configured cameras can participate in scenes; output compositions support up to four cameras. Existing Canon, ONVIF and VISCA control adapters are retained. Use an existing H.264 stream for browser playback and WebRTC when available. HLS is a fallback with more buffering. The PC's preview limit now also applies to Overview, avoiding unnecessary simultaneous decoders. Relay sources remain available for all configured cameras.

Keep all viewers on the shared relay rather than opening extra connections to each camera. Microphone level and direction requests run concurrently with bounded timeouts, and a slow microphone cannot block another microphone's polling. This reduces avoidable software delay; it does not remove microphone DSP, network jitter, camera travel or encoder buffering.

Measure speech-to-correct-seat and speech-to-settled-camera separately. Record at least 20 turns per seat, soft/normal speech, interruptions and simultaneous talk. Report median and 95th percentile delays plus wrong-seat changes. Use a filmed clock for glass-to-glass video latency. The software tests do not establish a universal real-room accuracy percentage or sub-500 ms end-to-end result.

## Compatibility boundaries

- Microphone positioning: TCC Medium over authenticated SSCv2 HTTPS/SSE; initialize third-party access in Control Cockpit. TCC 2 uses read-only SSCv1 TCP meter queries. Configure TCC 2 exclusion zones in Control Cockpit; the app's device-settings editor supports TCC Medium. See [Sennheiser's protocol compatibility](https://docs.cloud.sennheiser.com/en-us/control-cockpit/control-cockpit/ssc-protocols.html). Test actual firmware and metering in the room.
- Cameras: local RTSP with an H.264 stream, with supported Canon/ONVIF/VISCA preset control for automatic movement. H.265 conversion is available through the existing camera API setting but costs CPU. USB-only cameras are not supported by this network-camera path.
- Audio conferencing: ConfDeck consumes beam direction and level, not microphone PCM audio. Dante/analog audio mixing, echo cancellation and the conferencing app's audio-device selection remain in the room DSP/conferencing system. Generic USB microphones without direction telemetry cannot drive these seat maps.
- Network: private IPv4; discovery scans the selected /24. For another routed private subnet, add devices by IP and verify firewall reachability.

Disconnecting a microphone retains its seat map. Remove its seats before removing the microphone. Recheck calibration whenever a microphone is moved or replaced.

## Display and speech gate

Open **Display** for the live output and smart-following settings. Enable smart following there and start Follow to switch the displayed camera after a confirmed seat. Camera settling, available framings and connection health affect when the picture changes.

The optional speech audio gate has one configured audio source and cannot validate two microphones independently. Multi-microphone Follow refuses to start while that option is enabled; see [speech gate setup](SPEECH_GATE.md) for its configuration.
