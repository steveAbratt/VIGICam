# VIGI / InSight Camera — Local API Research Notes

Findings from probing two cameras via the local network:
- **192.168.1.88** — VIGI C540V (PTZ, outdoor)
- **192.168.1.180** — InSight S245 (indoor, fisheye-style)

---

## Authentication

The local API uses a two-step RSA+MD5 auth flow (not standard Tapo):

```
POST https://<ip>/
Body: {"user_management": {"get_encrypt_info": null}, "method": "do"}
→ Response: {"data": {"nonce": "...", "key": "<URL-encoded RSA PEM>"}}

MD5 hash: hashlib.md5("TPCQ75NF2Y:" + password).hexdigest().upper()
Payload:  f"{md5hash}:{nonce}".encode()
Encrypt:  base64(RSA_PKCS1v15(payload, pub_key))

POST https://<ip>/
Body: {"method": "do", "login": {"username": "admin", "password": "<encrypted>",
       "passwdType": "md5", "encrypt_type": "2"}}
→ Response: {"stok": "<session_token>", ...}

All subsequent calls: POST https://<ip>/stok=<token>/ds
```

The `TPCQ75NF2Y:` prefix is in `const.py` as `TAPO_PASSWORD_PREFIX`. Note this differs from the Tapo consumer devices which use `TP-Link_Vivek`.

---

## JSON Local API (`/stok=.../ds`)

### What works

| Module | Method | Notes |
|--------|--------|-------|
| `device_info` | `get` with `name: ["basic_info"]` | Returns model, firmware, MAC, etc. |
| `network` | `get` | IP config, MAC |
| `system` | `get` | System status |
| `led` | `get`/`set` | Status LED control |
| `image` | `get`/`set` | Image settings (brightness, contrast, WDR, etc.) |
| `audio` | `get`/`set` | Audio alarm, pre-recorded clips |
| `motion_detection` | `get`/`set` | Motion detection settings |
| `tamper_detection` | `get`/`set` | Tamper detection |
| `smart_detection` | `get`/`set` | TPSmartEvent detection settings |
| `usr_def_audio_alarm` | `do` | Trigger pre-recorded audio clip |

### What does NOT exist (returns -40106)

Everything related to image capture, snapshots, or event history:

```
snap_shot, snapshot, image_capture, event_log, event_search,
smart_frame, vigi_smart_frame, search_event_log, alarm_pic,
latest_detection, detection_image, video_capsule, record,
playback, sd_card, storage_management, media_file, isp
```

**Conclusion:** The local JSON API has no snapshot or event-image endpoint. The camera does not expose a pull API for captured images.

---

## HTTP Snapshot Paths

```
http://<ip>/snapshot.jpg         → HTTP 302 redirect → HTTPS → HTML (web UI)
http://<ip>/cgi-bin/snapshot.cgi → same
http://<ip>/image/jpeg.cgi       → same
http://<ip>/tmpfs/snap.jpg       → same
https://<ip>/snapshot.jpg        → 404
https://<ip>/stok=<token>/snapshot.jpg → 404
```

All HTTP paths redirect to the web UI. There is no unauthenticated or token-authenticated snapshot URL.

---

## ONVIF

### Confirmed working

| Service | Endpoint | Notes |
|---------|----------|-------|
| Events (pull-point) | `/onvif/service` | ✓ Used by integration for motion/person/smart events |
| Recording | `/onvif/Recording` | ✓ One recording token: `OnvifRecordingToken_1` |
| SearchRecording | `/onvif/SearchRecording` | ✓ FindRecordings + GetRecordingSearchResults work |
| Replay | `/onvif/Replay` | ✓ See below |

### Not supported

```
GetSnapshotUri → ter:ActionNotSupported ("NVT does not support the function")
GetEventSearchResults → ter:InvalidArgVal (token accepted but results fail)
```

---

## ONVIF Replay Stream (SD Card)

**URI:** `rtsp://admin:<password>@<ip>:554/onvifreplay`

### Capabilities confirmed via probe

- Plain connect → streams from earliest recording
- **Clock-based seeking works:** send RTSP `PLAY` with `Range: clock=YYYYMMDDTHHMMSSz-` and the camera seeks to that exact UTC timestamp
- Concurrent stream limit: **1** — returns `HTTP 429 Stream Up To Limit` if a second client connects while one is active
- ffmpeg NPT seek (`-ss <seconds>`) does NOT work for large offsets; use clock-based seeking via the Python RTSP client in `probe_snapshot.py`

### SD Card recording format

The camera records in **keyframe-only (I-frame only) mode** — approximately one frame every 6–10 seconds. There are no P-frames between keyframes. This means:

- Only ~4–6 frames per minute are stored
- Seeking to an exact timestamp gives the nearest stored keyframe
- The "30 seconds of data" contains only 3–4 decodable frames
- ffmpeg `-vf fps=1` and seek filters do not work on this stream because there are no PTS values between keyframes

### Practical use

To grab a frame at a specific event timestamp `T`:

```python
# Clock-based RTSP seek — see probe_snapshot.py for full implementation
PLAY <url> RTSP/1.0
Range: clock=20260616T095700Z-

# Then read RTP/TCP interleaved stream, reassemble H264 (prepend SPS/PPS from SDP),
# pipe through ffmpeg to decode the first I-frame.
```

Key gotcha: the camera strips SPS/PPS from the stream after a seek. They must be prepended manually from the SDP `a=fmtp:96 ... sprop-parameter-sets=<base64>,<base64>` field. Without this, ffmpeg reports `sps_id 0 out of range` and decoding fails. The camera also inserts TP-Link proprietary SEI NAL units (type 6 with vendor subtype 226) and type-0 NAL units; strip these before passing to ffmpeg.

### ONVIF event timestamps vs frame availability

- The ONVIF detection event fires ~3 seconds **before** the subject is fully in frame
- The first available I-frame at the event timestamp shows an empty scene
- The **next** I-frame (~7s later) typically shows the detected object

For a "last detection" image entity, grabbing from the **live stream** at event time is more reliable than replay (no SD card dependency, no keyframe lottery).

---

## Smart Frame / Smart Capture

The camera has a "Smart Frame" / "Smart Capture" feature (enabled in camera settings under Event → Smart Frame). When triggered, the camera:

1. Crops the image to the detected object (person, vehicle, etc.)
2. Saves the cropped image to a **dedicated partition on the SD card**
3. Optionally uploads to FTP server

### Confirmed API: `get_media_list`

The web GUI uses the following chain to enumerate Smart Frame images:

```
# Step 1 — list years with recordings
POST /stok=<token>/ds
{"method": "do", "playback": {"search_year_utility": {
    "channel": [0], "start_date": "20210101", "end_date": "20261231"
}}}
→ {"playback": {"search_results": [{"search_results_1": {"date": "20260616"}}]}}

# Step 2 (optional) — list video segments on a date
POST /stok=<token>/ds
{"method": "do", "playback": {"search_video_utility": {
    "id": 1, "date": "20260616", "start_index": 0, "end_index": 49, "channel": 0, "all_event": 1
}}}

# Step 3 — list Smart Frame images for a time window
POST /stok=<token>/ds
{"method": "do", "media": {"get_media_list": {
    "channel": [0],
    "media_type": [2],        ← 1=video, 2=Smart Frame image
    "all_event": 1,
    "user_id": 1,
    "start_time": "<unix_ts>",
    "end_time":   "<unix_ts>",
    "event_type": [1,2,3,4,5,6,7,8,12,13,14,16,18,26,27,28,73],
}}}
```

Response structure (parallel arrays, one entry per image):

```json
{
  "media": {
    "total_num": "24",
    "selected_num": "24",
    "start_time":  ["1781622451", ...],    ← unix timestamps
    "end_time":    ["1781622451", ...],
    "file_id":     ["00010000000000", "00010000000001", ...],   ← sequential IDs
    "size":        [55956, 57472, ...],    ← bytes on disk (HEVC frame)
    "event_type":  ["134217728", ...],     ← bitmask; 134217728 = 0x08000000
    "media_type":  [2, 2, 2, ...],
    "channel":     [0, 0, 0, ...]
  },
  "error_code": 0
}
```

**Important:** `get_picture_list` returns items oldest-first per page. To get the newest items, use `get_media_list` with `media_type=[2]` instead — it returns all items in one response (no pagination) and is the preferred method for listing Smart Frames.

When using `get_media_list(media_type=[2])`, `event_type` values are small integers (not bitmasks):

| event_type | label | confirmed on S245 |
|-----------|-------|-------------------|
| 2 | Person | ✓ (14% of frames) |
| 26 | Smart Detection | ✓ (rare) |
| 27 | Smart Detection | ✓ (86% of frames) |

`event_type=27` corresponds to `IsTPSmartEvent` (catch-all smart detection). Integrations should map small-int event_type to human-readable labels from `SMART_FRAME_EVENT_LABELS` in `smart_frame.py`.

### Image encoding

Smart Frame images are stored as **raw HEVC (H.265) still frames** — *not* JPEG. The web GUI ships a WebAssembly HEVC decoder (`libffmpeg_single.wasm`, Emscripten build, exports `_hevc_decoder_init` and `_hevc_to_yuv`) to decode them client-side. Files are 55–86 KB each.

### Download mechanism — WebSocket `wss://<ip>:8443/stream`

#### Connection

```
URL:      wss://<ip>:8443/stream
Protocol: Sec-WebSocket-Protocol must be included — value is:
          encodeURIComponent(JSON.stringify({
              "Content-Type": "multipart/mixed;boundary=--client-stream-boundary--",
              "X-SECURE-HASH-1": ""
          }))
          = %7B%22Content-Type%22%3A%22multipart%2Fmixed...%22%7D
          (the server echoes this back in its 101 response)
```

#### Auth (Digest, WebSocket-specific)

On each fresh connection (no previous session ID), the server sends:
```json
{"type":"notification","params":{"event_type":"websocket_authenticate","realm":"TP-LINK IP-Camera","algorithm":"MD5","qop":"auth","nonce":"<hex>","opaque":"<hex>"}}
```

Client responds with a plain JSON WebSocket text message (**not** multipart):
```json
{"type":"request","seq":0,"params":{"method":"do","authenticate":{
    "username":"admin","realm":"TP-LINK IP-Camera","nonce":"<from challenge>",
    "uri":"/stream","algorithm":"MD5","response":"<hash>",
    "opaque":"<from challenge>","qop":"auth","nc":"00000001","cnonce":"<random>"
}}}
```

Digest hash computation (**important — uses SHA-256 of raw password, not raw password**):
```python
secureH = hashlib.sha256(raw_password.encode()).hexdigest().upper()
HA1     = hashlib.md5(f"{username}:{realm}:{secureH}".encode()).hexdigest()
HA2     = hashlib.md5(f"GET:/stream".encode()).hexdigest()
response = hashlib.md5(f"{HA1}:{nonce}:{nc}:{cnonce}:auth:{HA2}".encode()).hexdigest()
```

Server responds with:
```json
{"type":"response","seq":0,"params":{"error_code":0,"session_id":"34"}}
```
(wrapped in `----device-stream-boundary--` multipart frame)

#### Download request

Sent as a WebSocket text message in **multipart format**:
```
----client-stream-boundary--\r\n
Content-Type: application/json\r\n
Content-Length: <N>\r\n
\r\n
{"type":"request","seq":0,"params":{"method":"get","download":{
    "client_id":0,"channels":[0],"start_time":"<unix_ts string>",
    "media_type":2,"file_id":"<from get_picture_list>","event_type":[]
}}}
```

#### Response

Three multipart parts arrive (each preceded by `----device-stream-boundary--\r\n`):

1. `Content-Type: application/json` — download OK response with session_id
2. `Content-Type: image/avc` — the image binary (`Content-Length` bytes), with headers:
   - `X-Session-Id: <id>`, `File-Id: <hex>`, `Media-Type: 2`, `Timestamp: <unix>`
3. `Content-Type: application/json` — stream_status finished notification
   (contains `X-Session-Id` for session continuity on reconnect)

#### media_type=1 keyframe notes

- Frames arrive with **24 leading null bytes** before the H.264 NAL start code (`\x00\x00\x00\x01`); strip them before passing to ffmpeg.
- The `event_type` field from `get_media_list` **must** be passed in the download request — the camera returns -52415 if `event_type: []` is sent.
- **event_type=27 is not downloadable** — camera always returns -52415. On the S245 this is ~82% of keyframes. Only event_type values 2 (Person) and 14 have been confirmed downloadable.
- For the HA integration, use Smart Frames (media_type=2) instead — they work for all event types and are AI-cropped.

#### Image format

The image binary is **H.264 (AVC) Annex B**, *not* HEVC, despite the web app shipping a HEVC WASM decoder. SPS → PPS → IDR frame. Decode to JPEG:

```python
import subprocess, io
proc = subprocess.run(
    ["ffmpeg", "-i", "pipe:0", "-frames:v", "1", "-update", "1",
     "-f", "image2", "-vcodec", "mjpeg", "pipe:1"],
    input=h264_bytes, capture_output=True
)
jpeg_bytes = proc.stdout  # valid JPEG
```

Or via file:
```bash
ffmpeg -i smartframe.h264 -frames:v 1 -update 1 smartframe.jpg
```

#### Session continuity (optional)

To skip re-auth on reconnect, pass the raw binary WebSocket frame containing the `stream_status` notification as a second Sec-WebSocket-Protocol value:
```python
session_msg_b64 = base64.b64encode(raw_device_message_bytes).decode()
# Send both protocols in the Upgrade request:
# Sec-WebSocket-Protocol: <headers_json_encoded>, <session_msg_b64>
```

---

## ONVIF Event Topics

Topics confirmed from `GetEventProperties` on both cameras:

| HA sensor | ONVIF topic | Notes |
|-----------|-------------|-------|
| Motion | `RuleEngine/MotionRegionDetector/Motion` | Basic pixel-change motion |
| Person | `RuleEngine/TPSmartEvent/IsPersonDetection` | Person AI |
| Smart Detection | `RuleEngine/TPSmartEvent/IsTPSmartEvent` | Catch-all: vehicle, sound, loitering, abandoned object, scene change — **cannot be split** at ONVIF level |
| Tamper | `RuleEngine/TamperDetector/Tamper` | Camera cover/position change |
| Intrusion | `RuleEngine/TPSmartEvent/IsIntrusionDetection` | Zone intrusion |
| Line Crossing | `RuleEngine/TPSmartEvent/IsLineCrossingDetection` | Virtual tripwire |

`IsTPSmartEvent` is a TP-Link umbrella event — all vehicle, sound, loitering etc. fire the same topic with no sub-type. The only way to distinguish them is through Smart Frame image metadata (if exposed after SD format).

---

## OpenAPI (port 20443)

The cameras expose a second, richer API on port 20443 (HTTPS). This must be enabled
in the camera's web UI: **Settings → Network → OpenAPI → Enable**.

Full specification: `tmp/tplinkopenapi.pdf` (gitignored — local only).
See `docs/IMPLEMENTATION_PLAN.md` for the complete feature map and build plan.

### Authentication

Two-step JSON POST to the root URL (not a path endpoint):

```python
# Step 1 — GET challenge (new TCP connection)
POST https://<ip>:20443/
{"method": "doAuth", "params": null}
→ {"authenticate": {"realm": "TP-LINK IP-Camera", "nonce": "...", "algorithm": "SHA-256",
                    "uri": "doAuth", "method": "POST"}, "errCode": -10020}

# Step 2 — compute response and authenticate (new TCP connection)
A1       = SHA256(f"{username}:{realm}:{password}")
A2       = SHA256(f"{method}:{uri}")          # "POST:doAuth"
response = SHA256(f"{A1}:{nonce}:{A2}")

POST https://<ip>:20443/
{"method": "doAuth", "params": {"nonce": "<from step 1>", "response": "<computed>"}}
→ {"stok": "...", "errCode": 0}

# All subsequent calls
POST https://<ip>:20443/stok=<stok>
{"method": "<method_name>", "params": {...}}
```

**Critical:** The camera closes the TCP connection after every response. Each call must
use a fresh connection (`force_close=True` in aiohttp, or a new session per call).
Stok expires after 30 minutes — cache with a 25-minute TTL and re-auth proactively.

### Confirmed supported modules — both cameras

`system`, `dateTime`, `video`, `dayNightMode`, `motionDetection`, `tamperDetection`,
`StreamPort`, `msgPush`, `sdCard`, `recordSchedule`, `playback`, `download`,
`audio_speaker`, `audio_microphone`, `sound_alarm_enabled`, `light_alarm_enabled`,
`CrossLineDetection`, `InvasionDetection`, `AreaEntryDetection`, `AreaLeaveDetection`,
`PeopleDetection`, `VehicleDetection`, `DropAndTakeDetection`, `AudioAnomalyDetection`

**InSight S245 only:** `LoiterDetection`, `SceneChangeDetection`
**VIGI C540V only:** `ptz`, `ptz_zoom`

### subscribeMsg — event push

Sends named events over a persistent HTTP connection:

```python
POST https://<ip>:20443/stok=<stok>
{"method": "subscribeMsg", "params": {"event_type": ["all"], "heartbeat": 15}}

# Response: multipart/mixed stream
# Heartbeat every 15s: {"Heartbeat":"30"}
# On detection:        {"event_type":"VehicleDetection","time":1723175020}
#                      {"event_type":"PeopleDetection","time":...}
#                      {"event_type":"MotionDetection","time":...}
```

Event types: `MotionDetection`, `TamperDetection`, `CrossLineDetection`,
`InvasionDetection`, `AreaEntryDetection`, `AreaLeaveDetection`, `PeopleDetection`,
`VehicleDetection`, `DropAndTakeDetection`, `LoiterDetection`, `SceneChangeDetection`,
`AudioAnomalyDetection`

**Requirement:** each detection type's `msg_push_enabled` must be `"on"` (check with
`getMotionDetectionSwitch` etc.; set with the matching `set*` method if off).

### Key differences from Smart Detection catch-all (ONVIF)

ONVIF fires `IsTPSmartEvent` for: vehicle, sound anomaly, loitering, scene change,
drop/take, area entry, area exit — all merged. `subscribeMsg` fires them separately.
This is the primary reason to implement the OpenAPI layer.

### getSdCardStatus — extra fields vs JSON API

The OpenAPI version returns additional fields not available via port 443:
`total_space`, `free_space`, `video_total_space`, `video_free_space`,
`picture_total_space`, `picture_free_space`, `record_duration` (total seconds stored),
`record_free_duration` (seconds of remaining capacity), `record_start_time` (unix ts
of oldest recording), `loop_record_status`.

### PTZ via OpenAPI (C540V only)

- `motorMove` — absolute position: `x_coord`, `y_coord`, `z_coord` all in [-1, 1]
- `cruiseMove` — continuous: `coord` in `{x, y, -x, -y, z, -z}` + optional `coord_speed`
- `stopMove` — stop
- `setPresetPoint` — save current position as preset (id 1–8, name string)
- `removePresetPoint` — delete preset by id
- `gotoPresetPoint` — go to preset by id (integer string "1"–"8")
- `getPresetPoint` — returns id[], name[], position_pan[], position_tilt[], position_zoom[]
- `getPTZCapability` — speed_x_max, speed_y_max, speed_z_max

### searchVideoList

Returns recording segments for a date with individual `video_type` per segment:
`MotionDetection`, `PeopleDetection`, `VehicleDetection`, `Timing`, etc.
Use this to find a specific clip to download.

### Video download (stream interface, port 554, MULTITRANS protocol)

NOT standard RTSP. Uses `MULTITRANS rtsp://<ip>/multitrans RTSP/1.0` verb.
Auth is SHA-256 Digest (same algorithm as OpenAPI control, different realm).
Data transmitted as RTP over TCP (`$` framing: 1B `$`, 1B channel, 2B length, RTP).
Requires `searchVideoList` to get `file_id` first.

---

## Probe Scripts

`probe_snapshot.py` — tests snapshot endpoints and Smart Frame WebSocket. Gitignored.
`probe_openapi.py` — tests OpenAPI auth and all control endpoints. Gitignored.

Run with:
```bash
python3 probe_snapshot.py 180   # InSight S245
python3 probe_openapi.py 180    # InSight S245
python3 probe_openapi.py 88     # VIGI C540V
```

Credentials stored in `.credentials.json` (gitignored).
