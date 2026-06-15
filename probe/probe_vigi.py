import sys, json, subprocess, hashlib, base64, urllib.parse, os
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

if len(sys.argv) < 4:
    print("Usage: python3 probe_vigi.py <ip> <username> <password>")
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

def curl_get_bytes(url):
    r = subprocess.run(["curl", "-sk", "--connect-timeout", "5", url], capture_output=True)
    return r.stdout

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
print(f"✓ {IP} — authenticated (stok: {stok[:16]}...)\n")
DS = f"{BASE}/stok={stok}/ds"

def api(payload, label):
    print(f"--- {label} ---")
    try:
        r = curl_post(DS, payload)
        print(json.dumps(r, indent=2))
    except Exception as e:
        print(f"  ✗ {e}")
    print()

# ── SNAPSHOT ──────────────────────────────────────────────────────────────────
print("=" * 60)
print("SNAPSHOT PROBING")
print("=" * 60)

# Try DS endpoint with various snapshot payloads
snap_payloads = [
    ({"method": "do", "video_clip": {"get_thumbnail": {"channel": 0}}},       "video_clip.get_thumbnail"),
    ({"method": "do", "snapshot": {"get_snapshot": {"channel": 0}}},          "snapshot.get_snapshot"),
    ({"method": "do", "image": {"get_snapshot": None}},                       "image.get_snapshot"),
    ({"method": "get", "snapshot": {"name": ["info"]}},                       "snapshot.info GET"),
]
for payload, label in snap_payloads:
    print(f"  Trying {label}:")
    try:
        r = curl_post(DS, payload)
        print(f"  {json.dumps(r, indent=4)}")
    except Exception as e:
        print(f"  ✗ {e}")
print()

# Try direct HTTP paths for JPEG
print("Trying HTTP snapshot paths:")
snap_paths = [
    f"/stok={stok}/stream/img.jpg",
    f"/stok={stok}/snapshot.jpg",
    f"/snapshot.jpg",
    f"/stream/snapshot",
    f"/cgi-bin/snapshot",
    f"/image/jpeg.cgi",
    f"/stok={stok}/ds?method=do",   # some cameras return JPEG here
    f"/onvif/snapshot",
    f"/tmpfs/snap.jpg",
    f"/streaming/channels/1/picture",
]
for path in snap_paths:
    data = curl_get_bytes(BASE + path)
    if data[:3] == b'\xff\xd8\xff':
        out = f"/tmp/vigi_{IP.replace('.','_')}_snapshot.jpg"
        open(out, "wb").write(data)
        print(f"  ✓ JPEG at {path} — saved to {out} ({len(data)} bytes)")
    else:
        snippet = data[:40].decode("utf-8", errors="replace").replace("\n", " ")
        print(f"  ✗ {path} ({len(data)}B) → {snippet}")
print()

# ── AUDIO / TWO-WAY TALK ─────────────────────────────────────────────────────
print("=" * 60)
print("AUDIO ENDPOINTS")
print("=" * 60)
api({"method": "get", "audio":       {"name": ["microphone", "speaker"]}},     "Audio Config")
api({"method": "get", "audio_config":{"name": ["speaker", "microphone"]}},     "Audio Config (alt key)")
api({"method": "get", "chn1_audio":  {"name": ["audio_info"]}},                "Channel Audio Info")
api({"method": "do", "audio_stream": {"start": {"channel": 0}}},               "Audio Stream Start")
api({"method": "do", "talk":         {"start": None}},                          "Talk Start")
api({"method": "get", "two_way_audio":{"name": ["config"]}},                   "Two-Way Audio Config")

# ── SYSTEM STATUS ─────────────────────────────────────────────────────────────
print("=" * 60)
print("SYSTEM STATUS")
print("=" * 60)
api({"method": "get", "device_info":       {"name": ["basic_info", "system_info"]}}, "Device + System Info")
api({"method": "get", "system":            {"name": ["clock_status", "basic", "device_status"]}}, "System Status")
api({"method": "get", "system_info":       {"name": ["cpu_usage", "mem_usage", "temperature"]}},  "CPU/Mem/Temp")
api({"method": "get", "diagnose":          {"name": ["device_info"]}},               "Diagnose Info")
api({"method": "get", "harddisk_manage":   {"table": "hd_info"}},                    "Harddisk Info (table)")
api({"method": "get", "storage_management":{"name": ["hd_info", "sd_status"]}},      "Storage Management")
api({"method": "get", "sd_card":           {"name": ["info", "status", "sd_status"]}}, "SD Card Status")
api({"method": "get", "storage":           {"name": ["sd_card_info"]}},               "Storage (sd_card_info)")
api({"method": "get", "performance":       {"name": ["cpu", "memory", "temperature"]}}, "Performance")
api({"method": "get", "log":               {"name": ["system_log"]}},                 "System Log")

# ── NETWORK / WIFI STATUS ─────────────────────────────────────────────────────
print("=" * 60)
print("NETWORK & MISC")
print("=" * 60)
api({"method": "get", "network": {"name": ["lan", "wan", "wlan"]}},            "Network (LAN/WAN/WLAN)")
api({"method": "get", "firmware": {"name": ["upgrade_info"]}},                 "Firmware Info")
api({"method": "get", "clock":    {"name": ["clock_status"]}},                 "Clock/Uptime")
