# Smart preview

1. Save a named scene for each person from Camera Studio. Link each seat to that scene in Tune & Follow. Camera 1 can cover three audience seats and Camera 2 another three; optional Camera 3 can cover a presenter. To give a seat an alternate camera, save its same named scene on that camera too.
2. In Display > Smart preview enable Automatically show the conversation. If using a dedicated presenter camera, choose both the presenter seat and its camera. That camera must already have a saved framing in the presenter's scene. Save; this leaves Follow in dry-run.
3. Enable Follow in Tune & Follow. The Display live smart preview and room display now switch together.

The newest confirmed speaker uses one view. A presenter appears when their seat is active; the dedicated presenter camera is not used for audience seats.

When two people share one camera, it follows the newest confirmed speaker. A camera already showing the desired shot is not recalled again. Failed, disabled and manually operated cameras are excluded. New speaker decisions during a pending movement are coalesced to the newest request.

Short pauses keep the last shot stable. Once everyone is quiet, the last shot remains until the existing silence-to-wide rule runs, if a wide preset is configured. Each microphone reports one beam direction; level and direction alone do not establish that a person is speaking.

Test A on camera 1, then B on camera 2: expect the confirmed speaker's view. Test two people sharing camera 1: expect that camera to show the latest speaker. No physical camera movement was performed during automated validation.

The program excludes cameras while their recall is pending and waits for measured settling when returned by the driver; otherwise it waits the configured settle delay. Increase that delay for long moves. Stop Follow to change presets or presenter assignments. The existing microphone accuracy guard and standard replay baselines still apply to future inference changes.
