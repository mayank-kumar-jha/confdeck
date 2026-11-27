"""Opt-in, in-memory audio diagnostics. Never imported by the live tracker.

InputStream callback timestamps use PortAudio's ADC clock mapped to monotonic
time. Only scalar level/probability records leave the capture worker.
"""
import argparse
import json
import queue
import threading
import time
from collections import deque


class AudioCapture:
    def __init__(self, model_path, device=None, channel=1, config=None):
        if type(channel) is not int or not 1 <= channel <= 64:
            raise ValueError("Choose an input channel from 1 to 64")
        self.model_path, self.device, self.channel, self.config = model_path, device, channel, config
        self.frames = deque(maxlen=938)
        self.scalar_frames = None
        self.scalar_dropped = 0
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.status = "Off"
        self.latest = None
        self.latest_received = 0.

    def start(self, record_scalars=False):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.scalar_frames = queue.Queue(maxsize=4096) if record_scalars else None
        self.scalar_dropped = 0
        self.thread = threading.Thread(target=self._worker, name="speech-audio", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        with self.lock:
            self.latest = None
            self.frames.clear()
            self.status = "Stopping" if self.thread and self.thread.is_alive() else "Off"

    def snapshot(self):
        with self.lock:
            frame = dict(self.latest) if self.latest else None
            status = self.status
            received = self.latest_received
        capture_time = frame['time'] - (self.config.offset if self.config else 0) if frame else None
        if frame and (frame.get('no_signal') or frame.get('clipping') or time.monotonic()-received > .096
                      or time.monotonic()-capture_time > .096 or capture_time > time.monotonic()):
            frame = None
            status = "No fresh audio signal; using existing level-only gate"
        return dict(status=status, frame=frame)

    def _record_scalar(self, frame):
        # Optional evaluation sidecar: timestamps and VAD scalars only, never PCM.
        if self.scalar_frames is None:
            return
        row = {key: frame[key] for key in ("time", "level", "probability")}
        try:
            self.scalar_frames.put_nowait(row)
        except queue.Full:
            self.scalar_dropped += 1

    def drain_scalar_frames(self):
        if self.scalar_frames is None:
            return []
        rows = []
        while True:
            try:
                rows.append(self.scalar_frames.get_nowait())
            except queue.Empty:
                return rows

    def _worker(self):
        from .speech import SileroFrames, SpeechGate
        try:
            model = SileroFrames(self.model_path)
            import sounddevice as sd
        except Exception as error:
            self.status = f"Audio unavailable ({type(error).__name__}: {error}); using existing level-only gate"
            return
        while not self.stop_event.is_set():
            buffers = queue.Queue(maxsize=16)  # At most 512 ms PCM, memory only.
            gate = SpeechGate(self.config)
            model.reset()
            previous = None
            def callback(data, count, timing, flags):
                if self.stop_event.is_set():
                    return
                # Callback copies one channel; ONNX never runs on PortAudio's thread.
                stamp = time.monotonic()+float(timing.inputBufferAdcTime-timing.currentTime)
                if count != 512:
                    return
                item = (stamp, data[:, self.channel-1].copy(), bool(flags))
                try:
                    buffers.put_nowait(item)
                except queue.Full:
                    # Drop the new frame; the subsequent timestamp gap resets recurrence.
                    pass
            try:
                sd.check_input_settings(device=self.device, channels=self.channel,
                                        dtype='float32', samplerate=16000)
                with sd.InputStream(device=self.device, channels=self.channel, samplerate=16000,
                                    blocksize=512, dtype='float32', callback=callback):
                    self.status = "Waiting for audio signal"
                    last_arrival = time.monotonic()
                    while not self.stop_event.is_set():
                        try:
                            stamp, samples, overflow = buffers.get(timeout=.1)
                        except queue.Empty:
                            if time.monotonic()-last_arrival > 1:
                                raise OSError("No audio frames; reconnecting input")
                            continue
                        last_arrival = time.monotonic()
                        if overflow or time.monotonic()-stamp > .096:
                            with self.lock:
                                self.latest = None
                                self.frames.clear()
                            previous = None
                            model.reset()
                            gate = SpeechGate(self.config)
                            continue
                        if previous is not None and (stamp <= previous or stamp-previous > .048):
                            model.reset()
                            gate = SpeechGate(self.config)
                        previous = stamp
                        info = model.process(samples)
                        frame = gate.push(stamp, info['level'], info['probability'])
                        frame.update(clipping=info['clipping'], no_signal=info['no_signal'])
                        self._record_scalar(frame)
                        with self.lock:
                            self.latest = frame
                            self.latest_received = time.monotonic()
                            self.frames.append(frame)
                            self.status = ("No signal; check the selected input/channel" if info['no_signal']
                                           else "Clipping; reduce input gain" if info['clipping'] else gate.status)
            except Exception as error:
                with self.lock:
                    self.latest = None
                    self.status = f"Input unavailable ({type(error).__name__}); retrying; using existing level-only gate"
                self.stop_event.wait(2)


class Aes67Capture(AudioCapture):
    """Explicit AES67 diagnostic source; no sound card or audio file is used."""

    def __init__(self, model_path, sdp_path, interface="0.0.0.0", latency_ms=40, config=None):
        super().__init__(model_path, device=None, channel=1, config=config)
        self.sdp_path = sdp_path
        self.interface = interface
        self.latency_ms = latency_ms

    def _worker(self):
        from .speech import SileroFrames, SpeechGate
        try:
            from .aes67 import Aes67Receiver, Aes67Stream
            with open(self.sdp_path, encoding="utf-8") as source:
                stream = Aes67Stream.from_sdp(source.read())
            model = SileroFrames(self.model_path)
        except Exception as error:
            self.status = f"AES67 unavailable ({type(error).__name__}: {error}); using existing level-only gate"
            return
        while not self.stop_event.is_set():
            gate = SpeechGate(self.config)
            model.reset()
            previous = None
            try:
                with Aes67Receiver(stream, self.interface, self.latency_ms) as receiver:
                    self.status = f"Waiting for AES67 audio on {stream.group}:{stream.port}"
                    while not self.stop_event.is_set():
                        result = receiver.read_frame(self.stop_event)
                        if result is None:
                            break
                        stamp, samples, discontinuity = result
                        if time.monotonic()-stamp > .096:
                            model.reset()
                            gate = SpeechGate(self.config)
                            previous = None
                            with self.lock:
                                self.latest = None
                                self.frames.clear()
                            continue
                        if previous is not None and (stamp <= previous or stamp-previous > .048):
                            model.reset()
                            gate = SpeechGate(self.config)
                            with self.lock:
                                self.latest = None
                                self.frames.clear()
                        if discontinuity:
                            model.reset()
                            gate = SpeechGate(self.config)
                            with self.lock:
                                self.latest = None
                                self.frames.clear()
                        previous = stamp
                        info = model.process(samples)
                        frame = gate.push(stamp, info["level"], info["probability"])
                        frame.update(clipping=info["clipping"], no_signal=info["no_signal"])
                        self._record_scalar(frame)
                        with self.lock:
                            self.latest = frame
                            self.latest_received = time.monotonic()
                            self.frames.append(frame)
                            self.status = (
                                "No signal on AES67 stream" if info["no_signal"]
                                else "Clipping; check source level" if info["clipping"]
                                else gate.status
                            )
            except Exception as error:
                with self.lock:
                    self.latest = None
                    self.status = (
                        f"AES67 input unavailable ({type(error).__name__}); retrying; "
                        "using existing level-only gate"
                    )
                self.stop_event.wait(2)


def main():
    parser = argparse.ArgumentParser(description="Opt-in speech diagnostics; records no audio and sends no camera commands")
    parser.add_argument('--list-devices', action='store_true')
    parser.add_argument('--enable', action='store_true', help='Explicitly open the audio input')
    parser.add_argument('--model', help='Local Silero ONNX model path')
    parser.add_argument('--device', type=int)
    parser.add_argument('--channel', type=int, default=1, help='One-based channel')
    parser.add_argument('--aes67-sdp', help='Local SDP file for one AES67 multicast audio flow')
    parser.add_argument('--interface', default='0.0.0.0', help='Local IPv4 interface for multicast membership')
    parser.add_argument('--latency-ms', type=int, default=40, help='Initial multicast buffer, 0 to 250 ms')
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--scalar-output', help='Optional new JSONL file for timestamped level/probability only; no audio is saved')
    args = parser.parse_args()
    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return
    if not args.enable:
        print('Speech diagnostics OFF; no audio device or model opened')
        return
    if not args.model or not 1 <= args.seconds <= 3600:
        parser.error('Supply --model and --seconds between 1 and 3600')
    if not 0 <= args.latency_ms <= 250:
        parser.error('--latency-ms must be from 0 to 250')
    capture = (
        Aes67Capture(args.model, args.aes67_sdp, args.interface, args.latency_ms)
        if args.aes67_sdp else AudioCapture(args.model, args.device, args.channel)
    )
    scalar_file = None
    if args.scalar_output:
        try:
            scalar_file = open(args.scalar_output, 'x', encoding='utf-8')
        except FileExistsError:
            parser.error('--scalar-output already exists; choose a new file name')
        scalar_file.write(json.dumps({"kind": "speech_frames", "version": 1, "clock": "monotonic",
                                      "started_monotonic": time.monotonic()}) + "\n")
        scalar_file.flush()

    def write_scalar_frames():
        if scalar_file:
            for frame in capture.drain_scalar_frames():
                scalar_file.write(json.dumps({"kind": "speech_frame", **frame}, allow_nan=False) + "\n")
            scalar_file.flush()

    capture.start(record_scalars=bool(scalar_file))
    try:
        deadline = time.monotonic() + args.seconds
        next_report = time.monotonic() + 1
        while time.monotonic() < deadline:
            capture.stop_event.wait(min(.1, max(0., deadline-time.monotonic())))
            write_scalar_frames()
            if time.monotonic() >= next_report:
                state = capture.snapshot()
                frame = state['frame']
                print(state['status'] + (f" | {frame['level']:.1f} dBFS | P={frame['probability']:.3f}" if frame else ''))
                next_report += 1
            if capture.thread and not capture.thread.is_alive():
                break
    except KeyboardInterrupt:
        pass
    finally:
        capture.stop()
        write_scalar_frames()
        if scalar_file:
            scalar_file.write(json.dumps({"kind": "summary", "dropped": capture.scalar_dropped}) + "\n")
            scalar_file.close()


if __name__ == '__main__':
    main()
