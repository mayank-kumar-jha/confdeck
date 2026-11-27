import hashlib
import json
from zipfile import ZipFile

from scripts.build_minipc_update import FILES, build


def test_package_allowlist_hashes_and_no_configuration(tmp_path):
    output = tmp_path/'update.zip'
    digest = build(output)
    assert digest == hashlib.sha256(output.read_bytes()).hexdigest()
    with ZipFile(output) as archive:
        assert set(archive.namelist()) == set(FILES)|{'manifest.json'}
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['target'] == '192.168.1.84'
        assert not manifest['automatic_install']
        assert not manifest['speech_live_enabled']
        for name, expected in manifest['files'].items():
            data = archive.read(name)
            assert hashlib.sha256(data).hexdigest() == expected
            assert b'\r\n' not in data
            assert not any(word in name for word in ('credentials', 'settings.db', 'private_key', 'camera_config', '.wav'))
