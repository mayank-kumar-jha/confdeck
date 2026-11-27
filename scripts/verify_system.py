import urllib.request, json

for port_str, url in [('Direct Port 8765', 'http://192.168.1.84:8765/api/state'), ('Standard Web Port 80', 'http://192.168.1.84/api/state')]:
    try:
        req = urllib.request.Request(url, headers={'x-confdeck': '1'})
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode())
        print(f"=== {port_str} Success (HTTP {resp.status}) ===")
        print("Cameras:")
        for c in data.get('cameras', []):
            print(f"  - {c.get('name')} ({c.get('ip')}): state={c.get('state')} online={c.get('online')}")
        mic = data.get('mic', {})
        print(f"Mic: state={mic.get('state')} rate={mic.get('rate')} age={mic.get('age')}")
        settings = data.get('settings', {})
        zones = settings.get('tracking', {}).get('zones', [])
        print(f"Calibrated Seats/Zones: {len(zones)}")
        for z in zones:
            print(f"  - Seat: {z.get('name')} (Camera Preset: {z.get('presets')})")
        print(f"Wide Presets: {settings.get('tracking', {}).get('wide_presets')}")
        print(f"Tracking mode: {data.get('tracking', {}).get('mode')}")
    except Exception as e:
        print(f"=== {port_str} Error: {e} ===")
