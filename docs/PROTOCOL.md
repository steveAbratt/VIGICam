<!--
  Reverse-engineered protocol notes for TP-Link VIGI cameras.

  Contributed by @AdrianEddy (github.com/AdrianEddy) in
  https://github.com/steveAbratt/VIGICam/issues/82, released under the WTFPL.
  Committed here so the research survives independently of that attachment.

  Written against a VIGI C540S. Other models differ in places — treat the
  specifics as a starting point, not a guarantee. Host addresses have been
  replaced with <camera-ip> / <phone-ip> placeholders.
-->

# TP-Link VIGI C540S — web API, reverse-engineered

Everything below was recovered from the camera's own web bundle (`index-C7umv8cc.js`
plus its lazy chunks, mirrored into `www/` and beautified into `src/`) and verified
against the live camera at `<camera-ip>`.

## 1. Login

### 1.1 Fetch the encryption parameters

```http
POST / HTTP/1.1
Content-Type: text/plain;charset=UTF-8

{"method":"do","user_management":{"get_encrypt_info":null}}
```

Answers HTTP 200 with a **deliberately non-zero** `error_code` — the payload is the point:

```json
{
  "data": {
    "code": -40407,
    "encrypt_type": ["1", "2"],
    "key":   "MIGfMA0GCSqGSIb3DQEB…",   // 1024-bit RSA public key, SPKI base64, URL-encoded
    "key_2": "MIIBIjANBgkqhkiG9w0BAQ…",  // 2048-bit RSA public key, same encoding
    "nonce": "z4ngBG5T",
    "passwdType": "md5"
  },
  "error_code": -40401
}
```

Fields that also show up on some models/firmwares: `current_encrypt_type` (overrides the
choice below) and `sec_left` / `code == ESYSLOCKED` when the account is locked out.

### 1.2 Derive the password field

```
encrypt_type = current_encrypt_type
             ?? max(encrypt_type[])        // numerically; "2" wins over "1"

md5pw     = MD5("TPCQ75NF2Y:" + plaintextPassword)  ->  uppercase hex, 32 chars
plaintext = md5pw + ":" + nonce                     ->  e.g. "A1B2…D6:z4ngBG5T"
cipher    = base64( RSAES-PKCS1-v1_5( plaintext, key_2 ?? key ) )
password  = encodeURIComponent(cipher)
```

`TPCQ75NF2Y:` is a fixed salt baked into the firmware. With the 2048-bit `key_2` the
base64 is always **344 characters** — which is exactly the length of the value captured
from the browser, confirming the derivation.

> The stock UI wraps the RSA call in a `for (let r = 0; r < 50 && …; r++)` loop that
> retries until `cipher.length % 64 === 0`. That condition can never hold for a 2048-bit
> key (344 % 64 = 24), so it simply encrypts 50 times and uses the last result. PKCS#1
> v1.5 padding is randomised, so every attempt is a valid, different ciphertext. One
> encryption is enough.

### 1.3 Log in

```http
POST /
Content-Type: text/plain;charset=UTF-8

{"method":"do","login":{
  "encrypt_type":"2",
  "passwdType":"md5",
  "password":"<url-encoded base64 from above>",
  "username":"admin",
  "keyType":"1"          // present only when key_2 was used
}}
```

```json
{"user_group":"root","stok":"WoH0HeQtEhx91H2RHFG*5C9icfNvC3cd","error_code":0}
```

### 1.4 Authenticated calls

Every later request goes to `POST /stok=<stok>/ds` with a body of
`{"method":"get"|"set"|"do"|"add"|"delete", …}`. `error_code: 0` means success;
`-40401` means the stok expired (log in again), `-40107` means permissions changed.

Two extra password derivations the UI keeps in `sessionStorage`, both needed later:

| key        | value                                        |
|------------|----------------------------------------------|
| `lgUser`   | the login username                           |
| `secureH`  | `SHA256(plaintextPassword)` uppercase hex — **this is the media daemon's digest password** |

## 2. Recording catalogue

### 2.1 Which days have footage

```json
{"method":"do","playback":{"search_year_utility":{
  "channel":[0],"start_date":"20260101","end_date":"20261231"}}}
```

Reply shape (index-suffixed keys, one per hit):

```json
{"playback":{"search_results":[
  {"search_results_1":{"date":"20260810"}},
  {"search_results_2":{"date":"20260811"}}
]}}
```

### 2.2 Segments within one day

```json
{"method":"do","playback":{"search_video_utility":{
  "id": <client_id>, "date":"20260812",
  "start_index":0, "end_index":49,
  "channel":0, "all_event":1}}}
```

`id` is the `client_id` from `{"method":"do","system":{"get_user_id":null}}`. The API
returns at most 50 rows, so page with `start_index = 50*n`, `end_index = 50*(n+1)-1`
until a short page comes back. Rows carry `startTime` / `endTime` (unix seconds) and
`vedio_type` (sic) — `1` is scheduled/continuous recording, anything else is
event-triggered.

### 2.3 Clock and DST

```json
{"method":"get","system":{"name":["basic","clock_status","dst","dst_manual"]}}
```

gives `system.basic.zone_id` (URL-encoded), `system.clock_status.seconds_from_1970`,
and the DST rules the timeline needs to place segments correctly.

## 3. Media stream

A separate daemon (`Server: Streamd`) listens on **port 8443**, path **`/stream`**.
The web UI reads the port from `network.port` config and falls back to 8443.

### 3.1 Authentication

Plain HTTP Digest, but the password is *not* the account password:

```
username = login username
password = SHA256(plaintextPassword) uppercase hex
HA1      = H(username : realm : password)
HA2      = H("POST" : "/stream")
response = H(HA1 : nonce : nc : cnonce : qop : HA2)
```

`H` is MD5 or SHA-256 depending on the `algorithm` the challenge advertises (the camera
currently offers `algorithm="MD5", qop="auth", realm="TP-LINK IP-Camera"`). Note `uri`
in the Authorization header is the **path only**.

### 3.2 Request

```http
POST /stream HTTP/1.1
Content-Type: multipart/mixed;boundary=--client-stream-boundary--
X-SECURE-HASH-1:
Authorization: Digest …

----client-stream-boundary--
Content-Type: application/json
Content-Length: <n>
X-Data-Window-Size: 100

{"type":"request","seq":0,"params":{"method":"get","playback":{
  "client_id":<id>,"start_time":"<unix seconds>","scale":"1/1",
  "channels":[0],"event_type":[1],"event_filter":0}}}
```

For live view, swap the `playback` object for
`{"preview":{"channels":[0],"resolutions":["VGA"]}}` with `seq: 1`.

`scale` is a fraction string: `"1/4"`, `"1/2"`, `"1/1"`, `"2/1"`, `"4/1"`, `"8/1"`.

### 3.3 Response framing

A stream of parts, each:

```
----device-stream-boundary--
Content-Type: video            (or audio, image/jpeg, application/json)
Content-Length: 1880
X-Data-Sequence: 42
X-Timestamp: 1786…
X-Frame-Type: 0                (0 = IDR, 1 = P)
X-Timeleft: …
X-Session-Id: 7

<Content-Length raw bytes>
```

Other boundaries the client understands: `--data-boundary--`, `--video-boundary--`,
`--audio-boundary--`.

- `application/json` parts are control messages. The first one carries
  `params.session_id`; a `{"type":"notification","params":{"status":"finished"}}`
  marks the end of available footage.
- Everything else is **plain MPEG-2 TS**, which is why mpegts.js can play it once the
  framing is stripped.

Inside the TS: video is H.264 (stream type `0x1B`) or H.265 (`0x24`); audio uses
TP-Link-specific stream types — `0x90` G.711A, `0x91` G.711U, `0x92` WAV, `0x03` MPEG.
Standard players ignore the audio PIDs.

Measured on this camera (C540S, 2560x1440):

| | |
|---|---|
| PMT PID | `0x12` |
| video | stream type `0x1B` (H.264, `avc1.640032`) on PID `0x44`, stream_id `0xE0` |
| audio | stream type `0x90` (G.711 A-law, 8 kHz mono) on PID `0x45`, stream_id `0xC0` |
| audio framing | 1024 bytes = 1024 samples per PES, dPTS 11520 ticks = 128 ms |

Both tracks carry PTS on the same 90 kHz clock, so audio can be aligned to the video
timeline as `(pts - firstVideoPts) / 90000` seconds.

Wall-clock time rides in the null packets (PID `0x1FFF`): payload byte 0 is `0x01`,
bytes 7..10 are a big-endian unix timestamp, and `"XFIT"` sits at byte 11. Byte 15's
high bit flags a synthesised keyframe.

### 3.4 Flow control — required

The daemon stops sending once the un-acked window (`X-Data-Window-Size`, 100) fills.
Every 2 seconds the client must post, on the **normal JSON API**:

```json
{"method":"do","playback":{"web_playback_cmd":{"command":{
  "header":{"X-Session-Id":<id>,"X-Data-Received":<last X-Data-Sequence>},
  "content":{"type":"notification","params":{"event_type":"stream_sequence"}}}}}}
```

Same envelope drives the session:

| action | `content.params` |
|---|---|
| seek  | `{"method":"do","play":{"start_time":"<unix>"}}` |
| speed | `{"method":"do","play":{"scale":"4/1","start_time":"<unix>"}}` |
| stop  | `{"method":"do","stop":"null"}` |

`error_code == INVALID_SESSION` means the session died — reopen the stream instead.

## 4. CORS

Both services answer with `Access-Control-Allow-Origin: *`, so a browser page on any
origin can drive the camera directly. Two wrinkles:

- `OPTIONS /` on the web port returns **405**, so the JSON API must be called as a CORS
  *simple request*: `Content-Type: text/plain;charset=UTF-8` (the camera ignores the
  request content type and parses the body as JSON regardless).
- The stream port handles preflight properly and exposes `WWW-Authenticate`,
  `X-Session-Id` and friends via `Access-Control-Expose-Headers`, which is what makes
  the digest handshake possible from JavaScript.

The certificate is self-signed, so the browser has to have accepted it once.

## 5. Odds and ends

- `system.get_user_id` → `client_id` used by playback search and the stream request.
- Password strength rules the firmware enforces: 9–32 printable ASCII, upper + lower +
  digit + symbol, no 3 repeated or 3 consecutive characters, must not contain the
  username.
- Other user-management calls reuse `Foe`/`E` (the same RSA+nonce construction) for
  `old_passwd`, plus AES-CBC with an HKDF-SHA256 key derived from
  `SHA256(password)` and a per-request nonce/salt for security answers.

## 6. Concurrency, and the app's parallel thumbnails

Measured against firmware 2.0.1 on the C540S, from an mitmproxy capture of the Android app
(2.7.100) plus direct probing.

### 6.1 One viewer, device-wide

The stream daemon serves **exactly one playback session at a time for the whole device**.
Every additional connection gets `session_id` allocated and then immediately
`error_code -52405` ("viewers reached the limit") with zero media bytes.

This is not per client. Six simultaneous connections, each with its own TCP socket, its own
digest exchange, a distinct `client_id` *and* a distinct `X-Client-UUID`, still yield
**1 of 6** delivering media (`tools/probe-parallel.mjs`). Parallelism is actively harmful:
a pool of 6 takes 11.3 s with half failing, against 3.2 s serial.

### 6.2 One request part per body

The request body carries exactly one `----client-stream-boundary--` part. Everything else
was tried and rejected:

| Body shape                                    | Result                                   |
|-----------------------------------------------|------------------------------------------|
| One part, exact `Content-Length`               | works — session opens, media flows       |
| N parts concatenated, exact `Content-Length`   | 200, response ends immediately, 0 parts  |
| `Transfer-Encoding: chunked`, parts over time  | 200, response ends immediately, 0 parts  |
| Over-declared `Content-Length`, written slowly | 200, response ends immediately, 0 parts  |
| Empty body (`Content-Length: 0`)               | 200, response **held open** indefinitely |

So the daemon will not process a body it has not fully received, and it does not accept
more than one request per connection. Request-side multiplexing is out.

### 6.3 `X-SECURE-HASH-1` is mandatory

Omitting the (empty-valued) `X-SECURE-HASH-1` request header makes the daemon re-issue the
digest challenge forever, even when the digest response is correct — it reads as an auth
failure but is not one. This cost an afternoon; it is the first thing to check if the
stream 401s in a loop.

### 6.4 Extracting the firmware (the source of truth)

Guesswork ended once the firmware was unpacked. `C540S_..._up_boot-signed.bin` carries a
squashfs at offset **0x21ae98** (v4.0, xz, 1415 inodes):

```bash
# carve from the offset for `bytes_used` in the superblock, then unpack
node -e "…"          # see tools/ — reads the hsqs superblock and slices the image
7z x -orootfs rootfs.squashfs
```

`rootfs/bin/main` is the daemon that serves both the JSON API and :8443/stream.
`rootfs/www/web-static/language/error.js` holds the error table.

### 6.5 Error codes (from `error.js`)

| Code     | Name               | Meaning                        |
|----------|--------------------|--------------------------------|
| `-71101` | `EIDREACHLIMIT`    | client id limit reached        |
| `-71102` | `EIDOCCUPIED`      | client id in use               |
| `-71103` | `EIDINVALID`       | client id invalid              |
| `-71104` | `ENOEVENTS`        | no events                      |
| `-71105` | `ESEARCHFAILED`    | search failed                  |
| `-71107` | `ETIMEINVALID`     | bad time                       |
| `-71108` | `ECHNINVALID`      | bad channel                    |
| `-71110` | `ECLIPLAYING`      | client already playing         |
| `-71111` | `ECLINOTCONNECTED` | client not connected           |
| `-71112` | `EARGSILLEGAL`     | **illegal arguments**          |
| `-71113` | `EOPENFILEFAILED`  | open failed                    |
| `-40106` | —                  | method not supported           |

`-71112` is an argument error, not a semantic refusal — the earlier reading was wrong.

### 6.6 No thumbnail endpoint exists

`bin/main` contains the stream-type table verbatim:

```
preview
playback
talk
unknow
```

plus `download` and the two `usr_def_audio` types (see 6.9). That is
the complete set the :8443 daemon will dispatch. There is no frame, photo, snapshot or
thumbnail stream type, so no amount of body-shape guessing was ever going to find one, and
`[HTTP]Max playback stream count is %d` is the limit measured in 6.1.

Conclusion: decoding a keyframe client-side (`vigi-frame.js`) is not a workaround, it is the
only mechanism available on this firmware — which is exactly what the app does too, via the
libavcodec/libswscale it ships.

### 6.7 The `media` module: reachable, but rows are never returned

Argument shape recovered from `bin/main`'s validation order (channel → user id → event_type
→ media_type → times → start index/max count):

```json
{"method":"do","media":{"get_media_list":{
  "search_id":"media_<user_id>",       // required
  "id":<user_id>,                      // required
  "start_time":"<unix>","end_time":"<unix>",
  "channel":[0],                       // exactly one, must be 0
  "event_type":[0,1,…],                // non-empty
  "media_type":[0],                    // non-empty; only 0,1,2 are legal
  "start_index":0,"max_num":100        // max_num without start_index is EARGSILLEGAL
}}}
```

This returns `error_code: 0` and a populated **`total_num`** (e.g. 54 for `media_type:[0]`,
35 for `[1]`, 0 for `[2]`) with the full row schema — `start_time`, `end_time`, `size`,
`file_id`, `event_type`, `media_type`, `channel` — but **every row array comes back empty**,
at any `start_index`/`max_num`, on every day tried.

The companion `get_media_cnt`, which the firmware logs as a separate step, answers
`-71112` for every argument set tried (with/without `search_id`, `id`, `media_type`,
`event_type`, numeric vs string times). The module looks half-wired on this model, so no
`file_id` is obtainable.

### 6.9 The full `/stream` request field set

`bin/main`'s parser accepts, across the stream types: `resolutions`, `start_time`,
`end_time`, `scale`, `client_id`, `event_filter`, `media_type`, `file_id`, `half_duplex`,
`audio_file_id`. Request keys are `preview`, `playback`, `talk`, `download`,
`usr_def_audio_upload`, `usr_def_audio_download`.

**`download` does work** — earlier attempts returned `finished` with zero bytes because
they were missing `event_type` **and** `event_filter`. This shape pulls 506 KB of a 26 s
clip:

```json
{"method":"get","download":{
  "start_time":"…","end_time":"…","channels":[0],
  "media_type":[0],"event_type":[1],"event_filter":0}}
```

It needs no `file_id`, and unlike `playback` it carries an `end_time`, so the camera
terminates the session itself. It is capped at one concurrent session exactly like
`playback` (`-52405` on the rest).

`resolutions` is parsed but has **no effect on playback** — `VGA`, `HD` and `QVGA` all
return 2560×1440. Recordings exist only at the recorded resolution, so there is no cheap
small variant to fetch for thumbnails.

### 6.10 `scale` sweeps a whole day in seconds

`scale` values of `4/1`, `8/1` and `16/1` make the daemon replay footage at roughly
**4000× realtime** — 33,600 s of footage arrives in 8 s. `1/1` and `2/1` run near realtime,
and `32/1` falls back to slow. A single `4/1` session over one day finishes in 20.5 s,
delivering 14.8 MB and 56 keyframes at ~52 distinct timestamps.

That is not a better thumbnail source than serial grabs, though: it yields whichever frames
it likes rather than one per segment, and works out to ~4 s per usefully-covered segment
against ~530 ms for a targeted grab.

### 6.11 Grab cost is session setup, not the handshake

The digest nonce is replayable (6.3 note: across connections, and with a repeated `nc` —
`tools/probe-nonce.mjs`), so the unauthenticated probe *can* be skipped. Measured, it saves
only **68 ms of a 576 ms grab (12%)**, because the cost is the camera's own session setup:
the first keyframe itself arrives 134–156 ms after the request. Worse, the tighter cadence
races session teardown and grabs begin failing `-52405` (2 of 8). The probe request is
therefore kept — it doubles as backpressure. Benchmark: `tools/bench-grab.mjs`.

### 6.12 Why interception can't capture the stream

Under mitmproxy the app sends only the `Content-Length: 0` challenge probe, gets its normal
401, and never follows up with `Authorization` — it loops instead. The REST API on :443 is
captured fine. `X-SECURE-HASH-1` is the likely reason: it reads as a channel binding over
the server certificate, so with an intercepting cert the app declines to send credentials.
Nothing in mitmproxy's configuration changes this; it would need hooking on the device.

If you do intercept, set `--set stream_large_bodies=64k` so the endless multipart responses
are streamed rather than buffered.

## 7. Correction: which firmware this describes

Sections 6.4–6.7 were derived from `C540S_1.0_en_2.0.1_Build_240207` — the only image that
still unpacks. The live cameras run **newer, encrypted firmware**:

| Host | Model | Firmware |
|------|-------|----------|
| .190 | VIGI C540S 1.0 | 3.1.0 Build 250625 |
| .73  | VIGI C440-W 2.0 | 2.1.1 Build 250717 |

Only the 2.0.1 image contains a plain squashfs (`hsqs` at `0x21ae98`). In 2.1.0 (Dec 2024)
and later — including 2.5.1, 3.1.0 and the C440-W 2.1.1 — the payload is uniformly high
entropy with **no filesystem magic at all**; the sole squashfs in 3.1.0 (`0xed540c`) is the
AI-model partition (`face_landmark.bin`, `head_detection.bin`, …). TP-Link began encrypting
the rootfs between Feb and Dec 2024.

So "the daemon implements only preview/playback/talk" is true **of 2.0.1 only**. The proof
that the API moved on: both live cameras advertise `MULTITRANS` in their RTSP `Public:`
list, and that string appears nowhere in the 2.0.1 rootfs.

The live measurements in 6.1–6.3 and 6.9–6.11 were taken against the running cameras and
stand regardless.

## 8. MULTITRANS — the multiplexing transport (unfinished)

`libIPCAppContextJNI.so` (unencrypted, from the APK) holds the client side:

```
MULTITRANS %s RTSP/1.0
CSeq: %d
Content-Type: %s
Content-Length: %d
X-Data-Received: %lld
```

with, nearby, `/stream/photo?channel=%d&path=%s`, `multipart/mixed`, and
`IPCNetService:: %p, get session multitrans get multitrans info error`. Because the request
carries `Content-Type`/`Content-Length`, MULTITRANS tunnels ordinary `/stream` requests over
one connection — which is how the app can fetch many thumbnails at once even though :8443
serves one session at a time.

`/stream/photo` is a **URI, not a JSON request key**, which is why every earlier `photo`
probe inside the multipart body got no reply. Over plain HTTPS it is unreachable: every
`GET /stream/*` 302-redirects, and `POST /stream/photo` is simply routed to the normal
stream handler.

### 8.1 What works on port 554

RTSP is **plaintext** and uses **Basic** auth with the *account* password (not the
SHA-256 stream password):

```
DESCRIBE rtsp://<host>/stream1   Authorization: Basic base64(user:password)   -> 200 + SDP
SETUP    rtsp://<host>/stream1/track1  Transport: RTP/AVP/TCP;…              -> 200, Session: 0C3A6AA8
```

SDP: H.264 `profile-level-id=640032` on `track1`, PCMA/8000 on `track2`.

### 8.2 What does not work yet

`MULTITRANS` returns `400 Bad Request` for every request-line tried — `/stream`,
`rtsp://host/stream`, `rtsp://host:554/stream`, `/stream/photo?channel=0&path=…`, `*`,
bare host, `/stream1` — both standalone and inside an established session. The 400 comes
back before the `CSeq` is parsed (the server echoes the previous CSeq), so the **request
line** is being rejected, not the body.

### 8.3 How to finish it

Port 554 is unencrypted, so this needs no TLS interception and none of the certificate
problems that stop mitmproxy: capture the phone's traffic with Wireshark while the app
loads a thumbnail grid, with the camera in mitmproxy's `ignore_hosts` (or no proxy at all)
so the app works normally. The MULTITRANS request line and body will be in plaintext.

## 9. The camera stores JPEG snapshots — this is the thumbnail source

Captured from the phone (mitmproxy, `--ignore-hosts` on :8443 so the app kept working), the
app's own `get_media_list` call, which **returns rows** where every earlier attempt returned
an empty array:

```json
{"method":"do","media":{"get_media_list":{
  "search_id":"media_ffffffff-c696-ae3a-c696-ae3a00000000",
  "start_time":"1785967200","end_time":"1786053600",
  "start_index":0,"max_num":100,"channel":[0],
  "event_type":[1,2,3,…,77],
  "media_type":[1],
  "all_event":1}}}
```

Three differences from everything tried before: `event_type` runs **1..77** (not 0..31),
`all_event: 1` is present, and there is **no `id`**. `VIGI Security Manager.exe` confirms the
signature:

```
get_media_list ( user_id start_time end_time start_index max_num channel event_type media_type all_event )
```

The reply is 102 rows for one day:

| field | example |
|---|---|
| `file_id` | `00010000300940` (sequential) |
| `start_time` / `end_time` | **equal** — these are instants, not ranges |
| `size` | 78 000 – 101 000 bytes |
| `media_type` | `1` |
| `event_type` | `2` |

Equal timestamps at ~90 KB means these are **stored JPEG snapshots**, one per event — not
clips. That is what fills the app's grid, and it explains the behaviour that contradicted
every measurement here: fetching a stored file is not a video session, so it is not subject
to the one-viewer cap, and dozens can be fetched at once.

`media_type` 0 and 1 are legal (2 returns nothing); values ≥3 are `-71112`.

### 9.1 Fetching the bytes — still open

Not solved. The best result is `download` with picture arguments, which now returns a
**distinct** error, `-52415` (not the `-52405` viewer limit), i.e. the picture path is
reached and rejects the arguments:

```json
{"method":"get","download":{"client_id":<id>,"start_time":"<t>","media_type":1,"channels":[0]}}
```

Ruled out along the way:

- **554 / MULTITRANS.** The capture shows the app making **zero** connections to 554 while
  the grid filled. Everything went to :8443. MULTITRANS is real (`rtsp://%s:%d/multitrans`,
  `MULTITRANS %s RTSP/1.0`) but is not what the app uses locally.
- **The URL API on :8443.** `VIGI Security Manager.exe` formats
  `/stream/playback?client_id=%d&type=%d&scale=%s&start_time=%lld&channels=`,
  `/stream/download?…&media_type=%d`, `/stream/audio?client_id=%d` and
  `/stream/photo?channel=%d&path=%s` — but these belong to the **P2P/relay pipe** (they sit
  beside the STUN/relay code). Locally :8443 serves only `POST /stream` with multipart:
  `GET` 302-redirects to `https://host:443`, and RTSP verbs answer
  `HTTP/1.0 400 Bad Request` with a `CSeq:` header.
- **`photo` as a JSON request key.** `400 Bad Request` — but so does `playback` when its
  arguments are wrong, so 400 means "bad arguments", not "unknown key". There is no cheap
  oracle for enumerating request keys.

### 9.2 `download` is the right request key — proven

With an argument set that is valid for snapshots, the daemon distinguishes cleanly between
a recognised key (opens a session) and an unrecognised one (`400 Bad Request`). Sweeping 25
candidate names against that argument set:

```
download         RECOGNISED  session=39 err=0
playback preview photo picture image snapshot thumbnail media file jpeg frame still
get_photo photo_download picture_download image_download media_download file_download
snap capture event_image alarm_picture media_picture pic      -> all 400
```

So `download` + `media_type: 1` + `file_id` is the snapshot path. It opens a session with
`error_code 0`, then reports `finished` having sent **zero bytes**. With slightly different
arguments it returns `-52415`, a data-transfer error distinct from the `-52405` viewer
limit — so the handler is reached and is rejecting the request, not refusing a session.

Tried without success: `file_id` as string / number / array of either; with and without
`start_time`/`end_time`/`event_type`/`event_filter`; `media_type` scalar and array; and the
separate `play` command (`{"type":"request","seq":1,"params":{"method":"do"}}` with an
`X-Session-Id` header, taken from the web client) sent on a second connection — no reply and
no data.

### 9.3 Ports

`class.js` sets `WEB_STREAM_REPLAY_PORT = 6443` and builds playback URLs as
`wss://host:6443/stream` — a WebSocket, which would allow many parts on one connection.
**6443 is closed on this camera** (ECONNREFUSED); it appears to be an NVR-only path. Open
ports here are 80, 443, 554, 2020, 8443, 8800.

### 9.4 Second capture, with :8443 intercepted

Confirms the app opens a **pool of parallel connections** — four within one second, eight
more later, matching the tile count — but every one is a `Content-Length: 0` probe answered
`401` with a fresh nonce, and none is ever followed by an authorized request. The app
withholds credentials whenever the certificate is not the camera's own. Capturing the
working request therefore needs Frida or a system-level CA on the phone; no proxy
configuration reaches it.

## 10. Frida capture: the app uses a UDP P2P pipe, not local HTTPS

Captured the running Android app with a Frida gadget (objection-patched APK, non-rooted
phone; frida 17 client in a py3.12 venv to match the gadget). Tooling: `tools/vigi-hook.js`,
`tools/frida-capture.py`.

### 10.1 The decisive finding

While the app streamed live video and loaded thumbnail grids, `/proc/<pid>/net` showed
**zero TCP connections to the camera** and **8 UDP flows to it** (`camera_tcp=0
camera_udp=8`, sustained). The phone was on a different subnet (`<phone-ip>`, a PC
hotspot) from the camera (`<camera-ip>`), reaching it only by routing.

So the app does **not** use the local `POST /stream` HTTPS API our card uses. It uses
TP-Link's **P2P pipe over UDP** (the `IPCNetworkPipeManagerTask` / relay / STUN machinery),
with the media payload encrypted by the app's own AES layer. Consequently:

- Every `SSL_write` / `SSL_write_ex` hook (libssl, libjavacrypto/Conscrypt,
  libIPCAppContextJNI, libTPMediaKit) and every TCP egress hook (`send`/`write`/`sendto`/
  `writev`) fired **zero times** for camera traffic — it is neither TLS-over-TCP nor a
  plain socket we hooked; it is the UDP pipe (`sendmsg`, app-encrypted).
- This is also why thumbnails fail under mitmproxy: the pipe pins/encrypts independently of
  the system CA.

### 10.2 What the capture did yield — the download schema

Hooking the `snprintf` family (request strings are formatted in plaintext before the pipe
encrypts them) captured the player-protocol schema for a stored-photo fetch:

```
download ( search_id channels start_time media_type file_id event_type event_filter )
```

So a snapshot is fetched with the **`download`** request key, carrying **`search_id`** (the
same `media_<uuid>` used for `get_media_list`) and the row's **`file_id`**, with
`media_type` as an array.

### 10.3 Still no local delivery

Replaying that exact shape against the local `:8443` daemon does not return the JPEG: the
request is rejected `400 Bad Request` (or `-52415` when `media_type` is a scalar). The
stored-photo `download` appears to be served only over the P2P pipe transport on this
firmware, not through the local HTTP-multipart channel — the local daemon serves `preview`,
`playback` (video), `talk` and audio, but not photo-by-`file_id` delivery.

### 10.4 Net conclusion

The camera **does** store per-event JPEG snapshots, and `get_media_list` exposes their
`file_id`, `size`, `event_type` and exact time locally (section 9). But the byte delivery
the app uses is the encrypted UDP pipe, which a browser-based Home Assistant card cannot
speak. For a pure local card, decoding a keyframe with WebCodecs (`vigi-frame.js`) remains
the only workable path; `get_media_list` can still drive an accurate event timeline/grid.
Extracting the JPEGs would require either decoding the P2P pipe protocol or a Python HA
integration that speaks it — a substantially larger effort than the card.

## 11. Superseded thumbnail theory (kept for experiment history)

> **Correction:** this section traced a real but unrelated playback-frame API. The recording
> grid uses the stored-resource path documented in section 12.

### 11.0 Earlier Java-layer observations

The socket/TLS hooks never fired because the app's HTTP rides **Cronet** (`libcrypto_httpengine.so`, Chromium's stack) — stripped, own BoringSSL, own I/O. The Java bridge (via the `frida` CLI, not the plain Python API) exposed the truth without touching the transport.

### 11.1 Thumbnails are decoded frames, keyed by time

`com.tplink.ipc.feature.video.model.PlaybackImageResult` carries:
- `resId = <deviceId>_<channel>_<unixTime>_<HH:MM:SS>` — keyed by **timestamp**, not file_id
- `filePath = .../files/filecache/<md5>` — cached to a local file

The image loader `IPCImageDownloader.i(String)` (HTTP) fired **zero** times; the frames come from
`IPCAppContext.downloaderReqFrame(deviceId, channel, listType, time, TPAVFrame)` →
`downloaderReqFrameNative(...)`, filled by decode, then
`downloaderGetCachedPlaybackImage(deviceId, channel, time, listType)` returns the cached path.
So a thumbnail is a **decoded frame at a timestamp** — exactly what `vigi-frame.js` does.

A pulled cache file is a **704×576 JPEG, ~25 KB**. The camera's encoders (from `video.get`):
main **2688×1520** / minor **704×576**. The thumbnail matches the minor stream.

### 11.2 Why the app is fast and we can't match it locally

- Local `:8443` playback is **main-stream only** — `type`, `resolutions`, `stream_type`, `sub`,
  `channels` are all ignored, always 2688×1520. The camera records only the main stream, so
  the 704×576 is a camera-side downscale delivered through the frame API, not a recorded
  sub-stream we can play.
- The camera serves **one playback viewer at a time**, device-wide — and the phone app's pipe
  session occupies that same slot (7–8 UDP flows to the camera → local `:8443` gets `-52405`).
- The app's parallelism comes from `downloaderReqFrame` being a **one-shot frame request over
  the P2P/MULTITRANS pipe**, which multiplexes many concurrent requests. The local stream API
  has no concurrent frame request — only the one-viewer playback stream.

### 11.3 The local optimum

Measured: opening **one** playback session and `seek`-ing across segments grabs a keyframe in
~250–570 ms each (avg ~490 ms), one per segment, exactly on target — cleaner and slightly
faster than a session per grab, and it holds only one viewer. The camera's seek→IDR latency
(~300 ms) is the floor; local generation is inherently serial at ~0.5 s/frame.

Fast *perceived* thumbnails therefore come from **pre-generating and caching** (background, one
session, seek-based), not from matching the app's on-demand parallel pipe fetch. True parity
would require implementing the encrypted P2P pipe + frame protocol in a Python HA integration.

## 12. Android recording-grid path and exact parallelism

Static decompilation and a fresh uncached live run identify a different path from section 11.
The grid helper is `com.tplink.ipc.feature.video.q4` (`PlaybackImageDownHelper`). For each
visible recording tile it first calls:

```
downloaderGetCachedMessageImage(deviceId, channel, fileId, timestamp, resourceType)
```

On a cache miss, the local-device branch calls:

```
msgDownloadPlaybackResource(
    deviceId, site, channel, timestamp, resourceType,
    fileId, new int[] { eventType }, 1)
```

The native chain is:

```
IPCAppContext.msgDownloadPlaybackResourceNative
  -> IPCAPPCONTEXT::DevicePlaybackDownloadResource
  -> IPCDeviceAlert::DownloadPlaybackResource
  -> IPCDownloadImageTaskContext::RegisterDownloadResourceTask
  -> IPCDeviceAlertDownloadResourceTask::Execute
  -> IPCAPPCONTEXT::DownloaderReqLoadMessageImage
  -> IPCAPPDownloadExecutorTask::LoadMessageImage
  -> TPDOWNLOADER::SubmitTasks
```

### 12.1 Concurrency limits

`IPCDownloadImageTaskContext::SubmitDownloadImageTask` counts entries whose state is 1.
Its submission loop compares that count with 5 and exits when it is greater, so at most
**six resource tasks** are active. Before submitting each message image,
`DownloaderReqLoadMessageImage` calls `SetMaxParallelNum(20)` on the lower downloader.

This is a two-level design: a six-request application queue over a downloader capable of
20 transfers. `downloaderReqFrame`, the API previously mistaken for the grid path, explicitly
calls `SetMaxParallelNum(1)` and is therefore serial.

### 12.2 Live confirmation

When an uncached recording day was opened, native hooks showed six message-image media jobs
submitted within tens of milliseconds, followed by further jobs as slots freed. At the same
time, files appeared in `/sdcard/Android/data/com.tplink.vigi/files/filecache` in batches.
Fresh examples were 80–95 KB baseline JPEGs at **704×576** (`file(1)` verified the format and
dimensions), consistent with the stored snapshot sizes in `get_media_list`.

Therefore Android does not create this grid by opening parallel playback streams or by
decoding `downloaderReqFrame` results. It downloads stored JPEG resources by `file_id`, with
a six-worker queue, and caches them by the UI's device/channel/time key.

### 12.3 Implementation consequence

Matching Android locally means implementing the same device/P2P message-image resource
transport, then scheduling visible `get_media_list` rows through a six-worker bounded queue.
Do not parallelize local `:8443` playback grabs: that daemon has a device-wide one-viewer
limit and the additional requests fail with `-52405`. Until the resource transport is
decoded, the existing serial playback-frame builder remains the correct local fallback.

Instrumentation for the corrected path is in `tools/java-resource-trace.js`.

## 13. SOLVED — fast local thumbnails via native-mode `download` (2026-08-13)

Frida capture of the app's own BoringSSL (`tools/pipe-tls.js`, hooking `SSL_write`/`SSL_read`
exported by `libIPCAppContextJNI.so`) revealed the exact mechanism and corrects the guesses in
§11–12. Reproduced locally at 9.3 thumbs/sec, decoded to PNG with ffmpeg. Reference impl:
`tools/probe-native.mjs`.

### 13.1 The thumbnail is an H.264 keyframe fetched by `download`

Per visible tile the app sends, over the `:8443` media daemon, one `download` request keyed by
the `file_id` from `get_media_list`:

```
----client-stream-boundary--
Content-Type: application/json
X-Data-Window-Size: 50
X-Key-Exchange: 1
Content-Length: <n>

{"type":"request","seq":<N>,"params":{"method":"get","download":{
  "search_id":"download_<uuid>","channels":[0],"start_time":"<unix>",
  "media_type":1,"file_id":"<id from get_media_list>",
  "event_type":["<evt>"],"event_filter":2}}}
```

The device replies (interleaved by seq) with three parts:

```
----device-stream-boundary--  Content-Type: application/json   {"type":"response","seq":N,"params":{"error_code":0,"session_id":"NNN"}}
----device-stream-boundary--  Content-Type: image/avc  X-If-Encrypt: 0  Content-Length: <len>  X-Session-Id: NNN   <payload>
----device-stream-boundary--  Content-Type: application/json   {"type":"notification","params":{"event_type":"stream_status","status":"finished"}}
```

The `image/avc` payload = **24-byte header + H.264 Annex-B (SPS `67` + PPS `68` + IDR `65`)**,
resolution **704×576** (minor stream). `X-If-Encrypt: 0` → **plaintext, no per-frame crypto**.
Strip to the first `00 00 00 01` and hand to WebCodecs / ffmpeg. Earlier notes calling these
JPEGs (§12.2) were wrong — they are H.264 keyframes.

### 13.2 Two daemon modes — only native mode pipelines

The `:8443/stream` daemon behaves differently based on the `X-SECURE-HASH-1` header:

| | Web mode | Native mode (the app) |
|---|---|---|
| `X-SECURE-HASH-1: ` header | present (empty) | **absent** |
| Digest password | `SHA256(admin_pw).upper` | **P2P SharePwd** (see 13.3) |
| Request delivery | one request in the POST body | POST `Content-Length: 0`, then request parts written onto the still-open socket |
| Lifetime | `Connection: close`, one request | persistent, many requests |
| Concurrency | ~1 connection device-wide; parallel connections fail 50% | one persistent connection |
| Speed | ~440 ms/thumb (fresh TLS+digest each) | **~100 ms/thumb** |

Native mode is a hijacked bidirectional connection: open with an empty body, do the
`401 → digest` dance (no `X-SECURE-HASH-1`), get `200 OK` (which stays open despite
`Connection: close`), then stream request parts onto the socket and read responses.

**Pacing (critical):** the daemon processes **one download at a time per connection**. Send the
next request only after the previous one's `finished` notification (window = 1). Firing several
at once yields exactly one image then a stall. Window=1 on a single hot connection already gives
~9–10 thumbs/sec — the app's apparent "parallel" load is really this, not concurrency.

### 13.3 The credential: P2P SharePwd

Native-mode digest (realm `TP-LINK IP-Camera`, MD5) uses neither the admin password nor a hash
of it, but the device's **P2P SharePwd**. Recovered via Frida (`tools/pw-hook.js`, generic hook
over `DigestCalcHA1`/`MD5Init/Update/Final` — the app's own RFC-2617 MD5, which is why BoringSSL
`MD5_Update` hooks saw nothing): the HA1 input is literally `admin:TP-LINK IP-Camera:<sharepwd>`.

Facts established:
- It is a **camera-local stored credential**, not a cloud/account credential. Fetch it after
  local admin login with `get {"user_management":{"name":"root"}}`, then read
  `user_management.root.passwd`.
- Both tested cameras (`.190` and `.73`) currently return `410Uwg30xlH50yK`. That shows their
  local provisioning installed or preserved the same value; it does not require or imply a
  TP-Link account.
- The same root record separates the credentials clearly: `passwd` is the native-stream
  password; `passwdMd5` is `MD5("TPCQ75NF2Y:" + admin_pw)`; and `SecureH` is
  `SHA256(admin_pw).upper()`.
- `do user_management.get_p2p_sharepwd` returns a 32-hex **verifier**, not AES ciphertext:
  `MD5(root.passwd + ":" + decodeURIComponent(root.ciphertext))`. The differing results on
  `.190` and `.73` are explained by their differing RSA ciphertext fields.
- Firmware's `set_password` path stores these fields during local provisioning. The precise
  historical source of the original `410Uwg30xlH50yK` value (generated, supplied, or copied)
  has not yet been observed, but it is not derived at runtime from the current admin password.

### 13.4 Recipe for the card / HA backend

1. Login with the admin password (RSA, encrypt_type 2) — existing path.
2. Fetch the camera's SharePwd with
   `get {"user_management":{"name":"root"}}` → `user_management.root.passwd`. An explicit
   configured value may remain as an override, but is not normally needed.
3. `get_media_list` → `file_id`s for the day.
4. Open ONE native-mode `/stream` connection (no `X-SECURE-HASH-1`, digest pw = SharePwd).
5. Issue `download` requests window=1 (next after `finished`); collect `image/avc` parts.
6. Strip the 24-byte header, decode the H.264 keyframe (WebCodecs / ffmpeg). ~9–10 thumbs/sec.

No cloud call, TP-Link account, AES decryption, or manual SharePwd is required for this recipe.
