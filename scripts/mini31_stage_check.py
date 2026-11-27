"""Stage and test the candidate against the mini PC's installed Python code."""
import ast
import re
from pathlib import Path

import paramiko


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'artifacts' / 'mini31-candidate'
CREDS = ast.parse((ROOT / 'scripts/deploy_update_and_autostart.py').read_text())
VALUES = {n.targets[0].id: ast.literal_eval(n.value) for n in CREDS.body
          if isinstance(n, ast.Assign) and len(n.targets) == 1
          and isinstance(n.targets[0], ast.Name) and n.targets[0].id in {'USER', 'PASSWORD'}}

TEST = '''
from confdeck.multimic import MultiMicFilter
from confdeck.tracking import TrackingSettings, Zone
from confdeck.microphone import TCC2Adapter
from confdeck.models import MicSettings

def event(t, mic, level):
    return {'azimuth':45+(t%0.1), 'elevation':45, 'sample_time':t, 'mic_id':mic,
            'level':level, 'activity':True, 'speech':{'reliability':1.0}}
for kind in ('legacy', 'new'):
    cfg=TrackingSettings(engine=kind, confirm_ms=100, min_hold_ms=500, settle_ms=0,
        ema_alpha=1, level_gate=True,
        zones=[Zone(id='front',name='Front',azimuth=45,elevation=45,presets={'a':'1'}),
               Zone(id='back',name='Back',mic_id='rear',azimuth=45,elevation=45,presets={'b':'2'})])
    engine=MultiMicFilter(cfg,kind,speech=(kind=='new'))
    if kind=='legacy': engine.sources['rear'].settings.confirm_ms=2000
    else: engine.sources['rear'].cfg.evidence_floor_ms=2000
    commands=[]
    for i in range(30):
        t=i/10
        for mic,level in (('rear',-25),('primary',-40)):
            decision=engine.feed(event(t,mic,level),t)
            if decision: commands.append(decision['zone'])
    assert commands==['back'], (kind,commands,engine.reason)
    engine.feed(event(2.9,'rear',-25),3.0)
    assert 'rear' in engine.votes

adapter=TCC2Adapter(MicSettings(ip='127.0.0.1',port=45))
seen=[]
adapter.parser.on_sample=seen.append
adapter._handle_line(b'{"m":{"beam":{"azimuth":40,"elevation":30},"in1":{"peak":-32}}}')
assert len(seen)==1 and seen[0]['level']==-32 and seen[0]['azimuth']==40
from confdeck.follow import Follow
from types import SimpleNamespace
from collections import deque
f=Follow.__new__(Follow)
f.runtime=SimpleNamespace(speech_enabled=True, settings=SimpleNamespace(tracking=SimpleNamespace(engine='new',vision=SimpleNamespace(mode='off'))), speech_evidence=lambda t: None)
captured=[]
f.engine=SimpleNamespace(sources={}, reason='accepted', last_sample=None, feed=lambda event, now: captured.append(event))
f.far_end_blocked=lambda: False
f.recording=False; f.preview_events=deque(); f.preview_outcomes=deque(); f.lab=SimpleNamespace(mic=lambda *args:None)
f.dispatch=lambda decision:None; f.last_reason='accepted'; f.audit=lambda message:None; f.learn=None
f.feed({'mic_id':'primary','azimuth':40,'elevation':30,'level':-40})
assert captured[-1]['speech']['reliability']==0
assert captured[-1]['speech']['unavailable']
f.runtime.speech_evidence=lambda t: {'reliability':0}
f.feed({'mic_id':'primary','azimuth':40,'elevation':30,'level':-40})
assert captured[-1]['speech']['reliability']==0

cfg=TrackingSettings(engine='new', confirm_ms=100, min_hold_ms=500, settle_ms=0, ema_alpha=1,
    zones=[Zone(id='front',name='Front',azimuth=45,elevation=45,presets={'a':'1'}),
           Zone(id='back',name='Back',mic_id='rear',azimuth=45,elevation=45,presets={'b':'2'})])
engine=MultiMicFilter(cfg,'new',speech=True)
for i in range(12):
    t=i/10
    for mic,level in (('primary',-45),('rear',-70)):
        row=event(t,mic,level);row['speech']={'reliability':0};engine.feed(row,t)
assert engine.current is None
decisions=[]
for i in range(12,40):
    t=i/10
    for mic,level in (('primary',-43),('rear',-48)):
        decision=engine.feed(event(t,mic,level),t)
        if decision: decisions.append(decision['zone'])
assert decisions==['back'], (decisions,engine.reason)
engine=MultiMicFilter(cfg,'new',speech=True)
for i in range(15):
    t=i/5
    for mic,level in (('primary',-45),('rear',-70)):
        row=event(t,mic,level);row['speech']={'reliability':0};engine.feed(row,t)
assert engine.current is None
decisions=[]
for i in range(15,30):
    t=i/5
    for mic,level in (('primary',-30),('rear',-68)):
        row=event(t,mic,level);row['speech']={'reliability':0}
        decision=engine.feed(row,t)
        if decision: decisions.append(decision['zone'])
assert decisions==['front'], (decisions,engine.reason)
engine=MultiMicFilter(cfg,'new',speech=True)
for i in range(15):
    t=i/5
    for mic,level in (('primary',-45),('rear',-70)):
        row=event(t,mic,level);row['speech']={'reliability':0};engine.feed(row,t)
assert engine.current is None
decisions=[]
for i in range(15,35):
    t=i/5
    for mic,level in (('primary',-45),('rear',-54 if i%3!=0 else -68)):
        row=event(t,mic,level);row['speech']={'reliability':0}
        decision=engine.feed(row,t)
        if decision: decisions.append(decision['zone'])
assert decisions==['back'], (decisions,engine.reason)
engine=MultiMicFilter(cfg,'new',speech=True)
for i in range(15):
    t=i/5
    for mic,level in (('primary',-45),('rear',-70)):
        row=event(t,mic,level);row['speech']={'reliability':0};engine.feed(row,t)
decisions=[]
for i in range(15,35):
    t=i/5
    for mic,level in (('primary',-33),('rear',-44)):
        row=event(t,mic,level);row['speech']={'reliability':0}
        decision=engine.feed(row,t)
        if decision: decisions.append(decision['zone'])
assert decisions==['back'], (decisions,engine.reason)
engine=MultiMicFilter(cfg,'new',speech=True)
for i in range(15):
    t=i/5
    row=event(t,'rear',-70);row['speech']={'reliability':0};engine.feed(row,t)
for i in range(15,18):
    t=i/5
    row=event(t,'rear',-53);row['speech']={'reliability':0};engine.feed(row,t)
for i in range(18,24):
    t=i/5
    row=event(t,'rear',-70);row['azimuth']=190;row['speech']={'reliability':0}
    assert engine.feed(row,t) is None
    assert engine.sources['rear'].last_sample != t
import json, urllib.request
live=json.load(urllib.request.urlopen('http://127.0.0.1:4000/api/sennheiser/state', timeout=4))
saved=TrackingSettings.model_validate(live['settings']['tracking'])
neha=next(z for z in saved.zones if z.name.lower()=='neha' and z.mic_id=='primary')
engine=MultiMicFilter(saved, 'new', speech=True)
decisions=[]
for i in range(30):
    t=i/5
    row={'mic_id':'primary','azimuth':293+(i%2)*.1,'elevation':39,
         'level':-42 if i<10 else -30,'sample_time':t,'speech':{'reliability':0}}
    decision=engine.feed(row,t)
    if decision: decisions.append(decision['zone'])
assert decisions==[neha.id], (decisions,engine.reason)
for seat_name, observed in (('seat 3', (3.4,37.5)), ('seat 4', (320.,45.))):
    target=next(z for z in saved.zones if z.name.lower()==seat_name and z.mic_id!='primary')
    engine=MultiMicFilter(saved,'new',speech=True)
    decisions=[]
    for i in range(35):
        t=i/5
        for mic,level,az,el in (('primary',-43,293,39),
                                (target.mic_id,-67 if i<10 else (-52 if i%3 else -65),*observed)):
            row={'mic_id':mic,'azimuth':az+(i%2)*.1,'elevation':el,'level':level,
                 'sample_time':t,'speech':{'reliability':0}}
            decision=engine.feed(row,t)
            if decision: decisions.append(decision['zone'])
    assert decisions==[target.id], (seat_name, decisions,engine.reason)
import os, tempfile, yaml
from pathlib import Path
from confdeck.media import Media
with tempfile.TemporaryDirectory() as folder:
    media=Media.__new__(Media);media.root=Path(folder);media.display_host='192.168.1.31';media.settings=SimpleNamespace(cameras=[])
    os.environ['CONFDECK_OMNITRACK_CAMERA_CONFIG']='test'
    media.write_config()
    cfg=yaml.safe_load((Path(folder)/'mediamtx.yml').read_text())
    assert cfg['paths']['all_others']['source']=='publisher'
    assert '192.168.1.31' in cfg['webrtcAdditionalHosts']
print('PASS: installed engine, speech-mode arbitration, stale packet and TCC 2 metering')
'''


def main():
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect('192.168.1.31', username=VALUES['USER'], password=VALUES['PASSWORD'],
                   timeout=5, look_for_keys=False, allow_agent=False)
    try:
        _, out, _ = client.exec_command('mktemp -d /tmp/omnitrack-dualmic-XXXXXXXX', timeout=5)
        stage = out.read().decode().strip()
        if not re.fullmatch(r'/tmp/omnitrack-dualmic-[A-Za-z0-9]{8}', stage):
            raise RuntimeError('Invalid staging directory')
        _, out, err = client.exec_command('cp -a /home/ordain/sehnizer/confdeck ' + stage + '/confdeck', timeout=20)
        if out.channel.recv_exit_status():
            raise RuntimeError(err.read().decode())
        sftp = client.open_sftp()
        for filename in ('microphone.py', 'multimic.py', 'follow.py', 'media.py'):
            sftp.put(str(BASE / 'sehnizer/confdeck' / filename), stage + '/confdeck/' + filename)
        with sftp.open(stage + '/check.py', 'w') as target:
            target.write(TEST)
        sftp.close()
        command = 'cd ' + stage + ' && /home/ordain/sehnizer/.venv/bin/python check.py'
        _, out, err = client.exec_command(command, timeout=30)
        output, errors = out.read().decode(), err.read().decode()
        code = out.channel.recv_exit_status()
        print(output, end='')
        if errors:
            print(errors, end='')
        if code:
            raise RuntimeError('Staged candidate checks failed')
        (ROOT / 'artifacts/mini31-stage-path.txt').write_text(stage, encoding='utf8')
        print('Staged candidate:', stage)
    finally:
        client.close()


if __name__ == '__main__':
    main()
