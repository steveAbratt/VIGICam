import sys, json, subprocess, hashlib, base64, urllib.parse, socket
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

if len(sys.argv) < 4:
    print("Usage: python3 probe2_vigi.py <ip> <username> <password>")
    sys.exit(1)

IP   = sys.argv[1]
USER = sys.argv[2]
PASS = sys.argv[3]
BASE = f"https://{IP}"

def curl_post(url, body):
    r = subprocess.run(
        ["curl", "-sk", "--connect-timeout", "5", "-X", "POST",
         "-H", "Content-Type: application/json",
         "-d", json.dumps(body), url],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return json.loads(r.stdout)

# --- Auth ---
enc = curl_post(BASE + "/", {"user_management": {"get_encrypt_info": None}, "method": "do"})
nonce = enc["data"]["nonce"]
key_b64 = urllib.parse.unquote(enc["data"]["key"])
if "BEGIN" not in key_b64:
    key_b64 = f"-----BEGIN PUBLIC KEY-----\n{key_b64}\n-----END PUBLIC KEY-----"
rsa_key = RSA.import_key(key_b64)
encrypted = base64.b64encode(
    PKCS1_v1_5.new(rsa_key).encrypt(
        f"{hashlib.md5(('TPCQ75NF2Y:' + PASS).encode()).hexdigest().upper()}:{nonce}".encode()
    )
).decode()
login = curl_post(BASE + "/", {
    "method": "do",
    "login": {"username": USER, "password": encrypted, "passwdType": "md5", "encrypt_type": "2"}
})
stok = login.get("stok") or login.get("result", {}).get("stok")
if not stok:
    print(f"Login failed: {login}"); sys.exit(1)
print(f"✓ {IP} — authenticated\n")
DS = f"{BASE}/stok={stok}/ds"

def api(payload, label):
    print(f"--- {label} ---")
    try:
        r = curl_post(DS, payload)
        ec = r.get("error_code", "?")
        if ec == 0:
            print(json.dumps(r, indent=2))
        else:
            print(f"  ✗ error_code {ec}")
    except Exception as e:
        print(f"  ✗ {e}")
    print()

# ── WHITE LED / SPOTLIGHT ─────────────────────────────────────────────────────
print("=" * 60)
print("WHITE LED / SPOTLIGHT")
print("=" * 60)
api({"method": "get", "image": {"name": ["switch", "common"]}},                "Image Switch (full)")
api({"method": "get", "whiteLamp": {"name": ["config"]}},                       "whiteLamp config")
api({"method": "get", "white_led": {"name": ["config"]}},                       "white_led config")
api({"method": "get", "led": {"name": ["config", "status"]}},                   "LED config+status")
api({"method": "get", "illuminator": {"name": ["config"]}},                     "illuminator config")

# Test spotlight ON (wtl = white LED always on)
print("--- Test: spotlight ON (wtl_night_vision) ---")
r = curl_post(DS, {"method": "set", "image": {"switch": {"night_vision_mode": "wtl_night_vision"}}})
print(f"  Result: error_code={r.get('error_code')}")
import time; time.sleep(1)
# Restore
r2 = curl_post(DS, {"method": "set", "image": {"switch": {"night_vision_mode": "md_night_vision"}}})
print(f"  Restored: error_code={r2.get('error_code')}")
print()

# ── ALERTS & EVENTS ────────────────────────────────────────────────────────────
print("=" * 60)
print("ALERTS & EVENTS")
print("=" * 60)
api({"method": "get", "msg_alarm":         {"name": ["chn1_msg_alarm_info"]}},  "Alarm Info")
api({"method": "get", "msg_alarm_trigger": {"name": ["chn1_msg_alarm_trigger_info"]}}, "Alarm Trigger")
api({"method": "get", "alert":             {"name": ["alert_config"]}},          "Alert Config")
api({"method": "do",  "search_detection_list": {"channel": 0, "type": "all",
     "start_time": 1781431200, "end_time": 1781517600,
     "start_index": 0, "count": 10}},                                            "Detection List (last 24h)")
api({"method": "do",  "playback":          {"search_video_with_utc": {
     "channel": 0, "start_time": 1781431200, "end_time": 1781517600}}},          "Playback/Recordings Search")
api({"method": "get", "event":             {"name": ["event_config"]}},          "Event Config")

# ── CONNECTION / WIFI ──────────────────────────────────────────────────────────
print("=" * 60)
print("CONNECTION / WIFI / SIGNAL")
print("=" * 60)
api({"method": "get", "network":     {"name": ["lan", "wan", "wlan", "wireless"]}}, "Network full")
api({"method": "get", "wireless":    {"name": ["wlan"]}},                           "Wireless WLAN")
api({"method": "get", "connection":  {"name": ["connection_type"]}},                "Connection Type")
api({"method": "get", "wifi":        {"name": ["config", "status"]}},               "WiFi Config")

# ── FIRMWARE / UPDATE ──────────────────────────────────────────────────────────
print("=" * 60)
print("FIRMWARE & UPDATES")
print("=" * 60)
api({"method": "get", "cloud_config": {"name": ["configure"]}},                 "Cloud Config")
api({"method": "do",  "cloud_config": {"check_fw_version_by_cloud": None}},     "Check Firmware Version")
api({"method": "get", "fw_download":  {"name": ["fw_info"]}},                   "Firmware Download Info")

# ── ONVIF PORT CHECK ───────────────────────────────────────────────────────────
print("=" * 60)
print("ONVIF PORT DISCOVERY")
print("=" * 60)
for port in [80, 443, 554, 2020, 8080, 8443, 8554]:
    try:
        s = socket.create_connection((IP, port), timeout=2)
        s.close()
        print(f"  ✓ Port {port} OPEN")
    except:
        print(f"  ✗ Port {port} closed")
print()

# Try ONVIF device info on open ports
print("--- ONVIF GetDeviceInformation (port 8080) ---")
onvif_body = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  <s:Body>
    <tds:GetDeviceInformation xmlns:tds="http://www.onvif.org/ver10/device/wsdl"/>
  </s:Body>
</s:Envelope>"""
for port in [8080, 2020, 80]:
    r = subprocess.run(
        ["curl", "-sk", "--connect-timeout", "3", "-X", "POST",
         f"http://{IP}:{port}/onvif/device_service",
         "-H", "Content-Type: application/soap+xml",
         "-d", onvif_body],
        capture_output=True, text=True)
    if r.returncode == 0 and "Manufacturer" in r.stdout:
        print(f"  ✓ ONVIF responded on port {port}:")
        # Extract key fields
        for tag in ["Manufacturer", "Model", "FirmwareVersion", "SerialNumber"]:
            import re
            m = re.search(f"<[^>]*{tag}[^>]*>([^<]+)<", r.stdout)
            if m: print(f"    {tag}: {m.group(1)}")
        break
    else:
        print(f"  ✗ port {port}: {r.stdout[:80].strip() or 'no response'}")
print()

# Try ONVIF GetCapabilities to find event service URL
print("--- ONVIF GetCapabilities ---")
cap_body = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  <s:Body>
    <tds:GetCapabilities xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
      <tds:Category>All</tds:Category>
    </tds:GetCapabilities>
  </s:Body>
</s:Envelope>"""
for port in [8080, 2020]:
    r = subprocess.run(
        ["curl", "-sk", "--connect-timeout", "3", "-X", "POST",
         f"http://{IP}:{port}/onvif/device_service",
         "-H", "Content-Type: application/soap+xml",
         "-d", cap_body],
        capture_output=True, text=True)
    if r.returncode == 0 and len(r.stdout) > 100:
        print(f"  Port {port} responded ({len(r.stdout)} bytes):")
        # Pull out XAddr URLs
        for m in re.finditer(r"<[^>]*XAddr[^>]*>([^<]+)<", r.stdout):
            print(f"    {m.group(1)}")
        break
    else:
        print(f"  ✗ port {port}")
