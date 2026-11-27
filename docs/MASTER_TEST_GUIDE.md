# Testing latency, speed and conversation display

Restart ConfDeck to load the updated backend. Keep Follow in dry-run initially.

1. Add the camera ending in .119 using its full IP in Cameras; it was not among the three configured cameras found during this work (.106/.108/.109). Enable its VISCA control and use Test control.
2. In Studio, frame and save a named scene for each seat. For two-camera conversation views, save each seat's scene on both cameras where possible. Link each seat to its scene in Tune & Follow. Each simultaneous view requires a distinct physical camera.
3. In Accuracy Lab select Spherical HMM, first-lock posterior .78, switch posterior .90 and minimum evidence 250 ms; apply in dry-run. Legacy tracking remains available and unchanged.
4. Record a labelled Test session with normal/soft speech, alternating seats, silence and noise. Replay and inspect wrong-seat rate, missed trials, spurious switches and latency. The saved synthetic baseline is not a room acceptance result. Accept room tuning only within +0.5 percentage points wrong-seat rate and no extra spurious switches.
5. Run Guarded latency sweep on that session, review Fast/Balanced/Safe, and apply the chosen profile. Validate a separate held-out room session before enabling Follow.
6. For speed testing, stop Follow and release the joystick. Save two visibly different reserved shots. Open Studio > Preset speed test, choose the two shots, and confirm movement. This does not overwrite camera presets. After a successful test assign a destination preset speed, recall it, and compare low/high settings. Newly saved shots capture position when support has been verified. Failed readback disables the software speed path and normal recall remains available.
7. In Accuracy Lab enable Conversation display and then enable Follow. Let A speak until locked, then B within six seconds. With two eligible camera assignments, the display shows two views after both commands succeed. Continue B: after six seconds without another confirmed turn the display returns to B alone. With one usable camera it stays single. Turn the option off for ordinary single-speaker following.
8. Observe Last 20 seat locks: evidence-to-decision, dispatch, acknowledgement and available position settle measurements. Acknowledgement is not proof of optical arrival. Frame-difference settling when position inquiries fail remains unavailable.

The current microphone adapter supplies one beam direction. Conversation display represents recent confirmed turns, not verified simultaneous speech; it cannot identify a second speaker who never wins the microphone beam. Actual overlap detection requires an additional independent speaker signal and room validation. No change to seat scoring was made to simulate overlap.

Synthetic standard sessions 7/19/41: median lock 1300 -> 650 ms (50% faster); wrong-seat time 6.04% -> 4.37%; spurious switches 0 -> 0. Hardware speed movement was not performed; the speed-dependent mock passed. Read-only version/position control probes succeeded on .106/.108/.109. Canon investigation is deferred per the revised request.
