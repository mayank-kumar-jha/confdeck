import urllib.request
import json
import time

print("Sending POST /api/system/reboot...")
req = urllib.request.Request(
    'http://192.168.1.84:8765/api/system/reboot',
    data=json.dumps({"hardware": False}).encode(),
    headers={'content-type': 'application/json', 'x-confdeck': '1'}
)

try:
    resp = urllib.request.urlopen(req, timeout=5)
    print("Reboot response:", resp.status, resp.read().decode())
except Exception as e:
    print("Reboot request error (may be socket reset if restart immediate):", e)

print("Waiting for reboot and service restart...")
for i in range(1, 15):
    time.sleep(1)
    try:
        check_req = urllib.request.Request('http://192.168.1.84/api/state', headers={'x-confdeck': '1'})
        data = json.loads(urllib.request.urlopen(check_req, timeout=2).read().decode())
        online_cams = sum(1 for c in data.get('cameras', []) if c.get('state') == 'Online')
        mic_state = data.get('mic', {}).get('state')
        print(f"t={i}s: Server is UP! Online cameras: {online_cams}/6, Mic: {mic_state}")
        if online_cams == 6 and mic_state == 'Connected':
            print("SUCCESS! System fully rebooted and all 6 cameras + mic are re-connected!")
            break
    except Exception as e:
        print(f"t={i}s: waiting for server... ({e})")

