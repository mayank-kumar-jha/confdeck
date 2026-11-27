"""Reviewable per-microphone direction samples; no audio is stored."""
import math
import statistics
import time
import uuid

import numpy as np

from confdeck.tracking_math.geometry import angles, distance, vector
from confdeck.tracking_math.model import fit


class SeatSample:
    duration = 20.0

    def __init__(self, runtime):
        self.runtime = runtime
        self.state = None

    def configuration(self, source):
        settings = self.runtime.settings
        mic = settings.mic if source == 'primary' else next(
            (m for m in settings.microphones if m.id == source), None)
        if mic is None:
            return None
        tracking = settings.tracking
        return (mic.model_dump(), tracking.inference.model_dump(),
                tracking.level_threshold, tracking.zone_margin_deg)

    def configuration_matches(self):
        s = self.state
        return all(self.configuration(source) == config for source, config in s['configuration'].items())

    def start(self, name, source):
        if self.state and self.state['status'] == 'recording':
            raise ValueError('A seat sample is already running')
        name = name.strip() if isinstance(name, str) else ''
        if not name or len(name) > 80:
            raise ValueError('Enter a seat name (up to 80 characters)')
        if not isinstance(source, str):
            raise ValueError('Choose a microphone or all connected microphones')
        if source == '__all__':
            sources = sorted(self.runtime.microphones, key=lambda x: (x != 'primary', x))
            if len(sources) < 2:
                raise ValueError('Connect at least two microphones for a shared seat')
        else:
            sources = [source]
        if any(s not in self.runtime.microphones or self.configuration(s) is None for s in sources):
            raise ValueError('Connect and configure the selected microphone first')
        if self.runtime.follow.lab.recorder:
            raise ValueError('Stop the Accuracy Lab session before sampling a seat')
        self.runtime.follow.stop('Seat sample in dry-run')
        self.state = {'status': 'recording', 'name': name, 'source': sources[0], 'sources': sources,
                      'configuration': {s: self.configuration(s) for s in sources},
                      'start': time.monotonic(), 'samples': {s: [] for s in sources}, 'last_stamp': {}}
        return self.snapshot()

    def feed(self, event, now):
        s = self.state
        source = event.get('mic_id', 'primary')
        if not s or s['status'] != 'recording' or source not in s['sources']:
            return
        if self.runtime.follow.live or not self.configuration_matches():
            s.pop('samples')
            s.update(status='retry', message='Follow or microphone settings changed during the sample. Try again in dry-run.')
            return
        if now >= s['start'] + self.duration or now < s['start']:
            return
        stamp = event.get('sample_time', now)
        if (type(stamp) not in (int, float) or not math.isfinite(stamp)
                or not 0 <= now-stamp <= .6 or stamp <= s['last_stamp'].get(source, -math.inf)):
            return
        s['last_stamp'][source] = stamp
        a, e = event.get('azimuth'), event.get('elevation')
        if not all(type(v) in (int, float) and math.isfinite(v) for v in (a, e)) or not (0 <= a <= 360 and 0 <= e <= 90):
            return
        cfg = self.runtime.settings.tracking.inference
        if (event.get('beam_freeze') or event.get('overlap') or event.get('activity') is False or
                (cfg.parked_enabled and abs((a-cfg.parked_azimuth+180)%360-180) <= cfg.parked_tolerance
                 and abs(e-cfg.parked_elevation) <= cfg.parked_tolerance)):
            return
        level = event.get('level')
        if type(level) not in (int, float) or not math.isfinite(level) or not -90 <= level <= 0:
            level = None
        speech = event.get('speech')
        speech_ok = (isinstance(speech, dict) and not speech.get('clipping') and not speech.get('no_signal')
                     and type(speech.get('reliability')) in (float, int) and speech['reliability'] > 0)
        if isinstance(speech, dict) and not speech_ok:
            return
        if level is not None and level < self.runtime.settings.tracking.level_threshold and not speech_ok:
            return
        if len(s['samples'][source]) < 4000:
            s['samples'][source].append((now-s['start'], a % 360, e, level, speech_ok))

    def finish(self):
        s = self.state
        samples_by_source = s.pop('samples')
        conv = self.runtime.settings.tracking.inference.convention
        settings = self.runtime.settings.tracking
        warnings, profiles, qualities = [], {}, {}
        for source, samples in samples_by_source.items():
            bins = len({int(row[0]) for row in samples})
            if len(samples) < 20 or bins < 10:
                s.update(status='retry', message=f'{source}: not enough fresh direction readings. Check the microphone and try again.')
                return
            vectors = [vector(row[1], row[2], conv) for row in samples]
            try:
                components = fit(vectors)
                center = components[0]['mean']
                errors = [distance(center, v) for v in vectors]
                support = sum(error <= 25 for error in errors) / len(errors)
                if support < .7:
                    raise ValueError('Directions varied too much. Ask one person to stay seated and try again.')
                a, e = angles(center, conv)
            except ValueError as exc:
                s.update(status='retry', message=f'{source}: {exc}')
                return
            levels = [row[3] for row in samples if row[3] is not None]
            speech_bins = len({int(row[0]) for row in samples if row[4]})
            profiles[source] = {'azimuth': a, 'elevation': e, 'radius': max(25, settings.zone_margin_deg + 1),
                                'components': [{**components[0], 'weight': 1.0}]}
            qualities[source] = {'readings': len(samples), 'seconds_with_readings': bins,
                                 'direction_support': round(support, 2),
                                 'spread_90_degrees': round(float(np.percentile(errors, 90)), 1),
                                 'median_level': round(statistics.median(levels), 1) if levels else None,
                                 'seconds_with_speech_evidence': speech_bins}
            for zone in settings.zones:
                prior = zone if zone.mic_id == source else zone.microphone_profiles.get(source)
                if prior and distance(center, vector(prior.azimuth, prior.elevation, conv)) < 25:
                    warnings.append(f'{source}: another seat has a similar direction; test whether they can be distinguished.')
                    break
            if speech_bins < 10:
                warnings.append(f'{source}: independent speech evidence was not available for most of the sample.')
            if len(levels) < len(samples) / 2:
                warnings.append(f'{source}: microphone level was unavailable for much of the sample.')
        warnings.insert(0, 'Direction consistency does not prove speech or physical seat accuracy. Verify with a separate test.')
        main = s['source']
        proposal = {'id': uuid.uuid4().hex[:12], 'name': s['name'], 'mic_id': main, **profiles[main]}
        if len(profiles) > 1:
            proposal['microphone_profiles'] = {source: value for source, value in profiles.items() if source != main}
        s.update(status='ready', proposal=proposal, quality=qualities[main], per_microphone=qualities, warnings=warnings)

    def snapshot(self):
        if not self.state:
            return {'status': 'idle'}
        s = self.state
        elapsed = max(0, time.monotonic()-s['start'])
        if s['status'] == 'recording' and elapsed >= self.duration:
            if self.runtime.follow.live or not self.configuration_matches():
                s.pop('samples')
                s.update(status='retry', message='Follow or microphone settings changed during the sample. Try again in dry-run.')
            else:
                self.finish()
        return {k: v for k, v in s.items() if k not in ('samples', 'start', 'configuration', 'last_stamp')} | {'elapsed': min(elapsed, self.duration)}

    def cancel(self):
        self.state = None
        return self.snapshot()
