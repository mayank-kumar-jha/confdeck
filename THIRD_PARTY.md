# Third-party software

ConfDeck code is MIT licensed. Redistributed components retain their own licenses.

- **MediaMTX 1.21.3**: MIT, https://github.com/bluenviron/mediamtx. License accompanies the binary in `vendor/bin/MEDIAMTX-LICENSE`.
- **FFmpeg / ffprobe**: Windows essentials build from https://www.gyan.dev/ffmpeg/builds/. This build includes GPL components such as libx264. License accompanies the executables as `vendor/bin/FFMPEG-LICENSE`. FFmpeg is executed as a separate program. The exact executable version is recorded in the validation report. Source and build information: https://www.gyan.dev/ffmpeg/builds/ and https://ffmpeg.org/download.html.
- **hls.js 1.6.13**: Apache-2.0, https://github.com/video-dev/hls.js. License at `confdeck/static/vendor/HLS-LICENSE`.
- **Inter**: SIL Open Font License, https://github.com/rsms/inter. License at `confdeck/static/vendor/FONT-LICENSE`.
- **Python**: Python Software Foundation license, included in the Windows embedded runtime.
- Python dependencies retain the license files embedded in their wheels and installed `.dist-info` directories. The bundle includes the wheelhouse, for the installed runtime.

`vendor/manifest.json` records build-time asset sources and SHA-256 checksums. `SHA256SUMS.json` in each offline bundle records the delivered files. These checksums detect accidental damage; they are not a vendor signature.

Do not remove license files when redistributing a bundle. Review the applicable FFmpeg/GPL source-distribution requirements for your distribution context. No third-party assets are fetched during runtime.
