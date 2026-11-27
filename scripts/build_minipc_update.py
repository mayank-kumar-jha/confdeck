"""Build a configuration-free staged update for 192.168.1.84 only."""
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    'omnitrack/start-kiosk.sh': 'scripts/start-kiosk.sh',
    'omnitrack/kiosk_watch.py': 'scripts/kiosk_watch.py',
    'sehnizer/confdeck/speech.py': 'confdeck/speech.py',
    'sehnizer/confdeck/speech_audio.py': 'confdeck/speech_audio.py',
    'sehnizer/confdeck/aes67.py': 'confdeck/aes67.py',
    'sehnizer/confdeck/evaluation/speech.py': 'confdeck/evaluation/speech.py',
    'sehnizer/confdeck/evaluation/session.py': 'confdeck/evaluation/session.py',
    'sehnizer/confdeck/evaluation/lab.py': 'confdeck/evaluation/lab.py',
    'sehnizer/confdeck/tracking_math/quality.py': 'confdeck/tracking_math/quality.py',
    'sehnizer/confdeck/tracking_math/engine.py': 'confdeck/tracking_math/engine.py',
    'docs/SPEECH_GATE.md': 'docs/SPEECH_GATE.md',
    'docs/SPEECH_AUDIT.md': 'docs/SPEECH_AUDIT.md',
    'README.md': 'docs/MINIPC_PENDING_UPDATE.md',
}


def build(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = dict(target='192.168.1.84', status='staged-not-deployed',
                    automatic_install=False, speech_live_enabled=False, files={})
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for target, source in FILES.items():
            data = (ROOT/source).read_bytes()
            # Linux scripts must use LF even when checked out on Windows.
            if source.endswith(('.sh', '.py', '.md')):
                data = data.replace(b'\r\n', b'\n')
            archive.writestr(target, data)
            manifest['files'][target] = hashlib.sha256(data).hexdigest()
        archive.writestr('manifest.json', json.dumps(manifest, indent=2))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix+'.sha256').write_text(digest+'  '+output.name+'\n', encoding='utf-8')
    return digest


if __name__ == '__main__':
    output = ROOT/'artifacts'/'minipc-update-pending.zip'
    print(output)
    print(build(output))
