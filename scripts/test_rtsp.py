import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.1.84', username='ordain', password='0208')

cameras = [
    ("109 (VISCA)", "rtsp://admin:admin@192.168.1.109:554/stream2"),
    ("108 (VISCA)", "rtsp://admin:admin123@192.168.1.108:554/stream2"),
    ("106 (VISCA)", "rtsp://192.168.1.106:554/stream2"),
    ("113 (Canon)", "rtsp://admin:admin123@192.168.1.113:554/rtpstream/config2"),
    ("112 (Canon)", "rtsp://admin:admin123@192.168.1.112:554/rtpstream/config2"),
    ("111 (Canon)", "rtsp://admin:admin123@192.168.1.111:554/rtpstream/config2"),
]

for label, url in cameras:
    cmd = f'ffprobe -v error -rtsp_transport tcp -rw_timeout 5000000 -select_streams v:0 -show_entries stream=codec_name,width,height -of csv=p=0 "{url}"'
    stdin, stdout, stderr = ssh.exec_command(cmd)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    print(f"Camera {label} -> {out or 'FAILED'} {err}")

ssh.close()

