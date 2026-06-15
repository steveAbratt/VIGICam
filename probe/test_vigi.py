import sys, json, subprocess, hashlib, base64, urllib.parse, os
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

if len(sys.argv) < 4:
    print("Usage: python3 test_vigi.py <ip> <username> <password>")
    sys.exit(1)

IP = sys.argv[1]
USER = sys.argv[2]
PASS = sys.argv[3]
BASE = f"https://{IP}"

def curl_post(url, body):
    result = subprocess.run(
        ["curl", "-sk", "--connect-timeout", "5", "-X", "POST",
         "-H", "Content-Type: application/json",
         "-d", json.dumps(body), url],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl error: {result.stderr}")
    return json.loads(result.stdout)

def curl_get_bytes(url):
    result = subprocess.run(
        ["curl", "-sk", "--connect-timeout", "5", url],
        capture_output=True
    )
    return result.stdout

# --- Auth ---
enc = curl_post(BASE + "/", {"user_management": {"get_encrypt_info": None}, "method": "do"})
nonce = enc["data"]["nonce"]
key_pem_b64 = urllib.parse.unquote(enc["data"]["key"])
if "BEGIN" not in key_pem_b64:
    key_pem = f"-----BEGIN PUBLIC KEY-----\n{key_pem_b64}\n-----END PUBLIC KEY-----"
else:
    key_pem = key_pem_b64

rsa_key = RSA.import_key(key_pem)
cipher_rsa = PKCS1_v1_5.new(rsa_key)
pwd_hash = hashlib.md5(("TPCQ75NF2Y:" + PASS).encode()).hexdigest().upper()
encrypted = base64.b64encode(cipher_rsa.encrypt(f"{pwd_hash}:{nonce}".encode())).decode()

login_resp = curl_post(BASE + "/", {
    "method": "do",
    "login": {"username": USER, "password": encrypted, "passwdType": "md5", "encrypt_type": "2"}
})
stok = login_resp.get("stok") or login_resp.get("result", {}).get("stok")
if not stok:
    print(f"Login failed: {login_resp}")
    sys.exit(1)

print(f"✓ Authenticated as {USER} (stok: {stok[:16]}...)\n")
DS = f"{BASE}/stok={stok}/ds"

def api(payload, label):
    print(f"--- {label} ---")
    try:
        r = curl_post(DS, payload)
        print(json.dumps(r, indent=2))
    except Exception as e:
        print(f"  ✗ {e}")
    print()

# --- PTZ ---
api({"method": "get", "motor": {"name": ["info"]}},            "PTZ Motor Info")
api({"method": "get", "preset": {"name": ["preset"]}},         "PTZ Presets")
api({"method": "get", "patrol": {"name": ["patrol"]}},         "PTZ Patrol/Cruise")

# --- Detection & Events ---
api({"method": "get", "people_detection": {"name": ["people_detection_info"]}}, "Person Detection Config")
api({"method": "get", "vehicle_detection": {"name": ["vehicle_detection_info"]}}, "Vehicle Detection Config")
api({"method": "get", "tamper_detection": {"name": ["tamper_det"]}},             "Tamper Detection")
api({"method": "get", "linecrossing_detection": {"name": ["cross_plan_info"]}},  "Line Crossing")
api({"method": "get", "intrusion_detection": {"name": ["intrusion_plan_info"]}}, "Intrusion Detection")

# --- Events / recordings ---
api({"method": "do", "search_detection_list": {"channel": 0, "type": "all", "start_index": 0, "end_index": 10}}, "Recent Detection Events")
api({"method": "get", "record_plan": {"name": ["chn1_record_plan_info"]}},  "Recording Plan")
api({"method": "get", "sd_card": {"name": ["info"]}},                       "SD Card")

# --- Snapshot ---
print("--- Snapshot (direct HTTP) ---")
snap_url = f"{BASE}/stok={stok}/ds"
snap_payload = {"method": "do", "video_clip": {"get_thumbnail": {"channel": 0, "start_time": 0}}}
snap_bytes = curl_get_bytes(snap_url)  # try via ds first
# Also try common snapshot paths
for path in ["/stream/img.jpg", "/snapshot", "/cgi-bin/snapshot.cgi", "/snap.jpg"]:
    url = f"{BASE}/stok={stok}{path}"
    data = curl_get_bytes(url)
    if data[:3] == b'\xff\xd8\xff':  # JPEG magic bytes
        out = "/tmp/vigi_snapshot.jpg"
        with open(out, "wb") as f:
            f.write(data)
        print(f"  ✓ JPEG snapshot saved to {out} ({len(data)} bytes)")
        break
    else:
        print(f"  ✗ {path} — not a JPEG ({len(data)} bytes, starts: {data[:20]})")
print()

# --- Toggle person detection ON then report ---
print("--- Enable Person Detection ---")
r = curl_post(DS, {"method": "set", "motion_detection": {"motion_det": {"people_enabled": "on"}}})
print(json.dumps(r, indent=2))
print()

# --- Verify it took ---
api({"method": "get", "motion_detection": {"name": ["motion_det"]}}, "Motion Detection (after enabling person)")

# --- Restore person detection to off ---
curl_post(DS, {"method": "set", "motion_detection": {"motion_det": {"people_enabled": "off"}}})
print("(person detection restored to off)\n")

# --- OSD ---
api({"method": "get", "OSD": {"name": ["date", "font"]}}, "OSD Config")

# --- Audio ---
api({"method": "get", "audio": {"name": ["microphone"]}}, "Microphone Config")
