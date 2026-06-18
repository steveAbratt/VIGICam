"""WebSocket event image download for VIGI/InSight cameras.

Downloads the event-captured still from wss://<ip>:8443/stream.
All I/O is non-blocking; ffmpeg runs via asyncio subprocess.

Protocol summary
----------------
1. Open raw TLS connection to <ip>:8443.
2. HTTP upgrade to WebSocket — Sec-WebSocket-Protocol header is required;
   the camera echoes it back, which breaks the standard websockets library.
3. Camera sends Digest auth challenge as a JSON WebSocket text frame.
4. Client responds with auth (SHA-256 of raw password as the Digest password).
5. Client sends download request as a multipart WebSocket text frame.
6. Camera replies with three multipart binary frames:
     - JSON download-ok
     - image/avc  (H.264 AVC Annex B, ~56-90 KB)
     - JSON stream_status
7. Pipe H.264 bytes through ffmpeg to produce JPEG.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import ssl
import struct
import urllib.parse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .api import VIGICamera

# event_type integers returned by get_media_list for media_type=2 (event images).
# Confirmed on S245 firmware 3.1.1: 2=Person, 27=Smart Detection (catch-all).
# Values 1/3/4/8/16/18 follow TP-Link's standard AI event enumeration.
EVENT_IMAGE_LABELS: dict[int, str] = {
    1: "Motion",
    2: "Person",
    3: "Vehicle",
    4: "Pet",
    8: "Sound",
    16: "Intrusion",
    18: "Line Crossing",
    26: "Smart Detection",
    27: "Smart Detection",
    28: "Smart Detection",
}

_PORT = 8443
_PATH = "/stream"
_CLIENT_BOUNDARY = "----client-stream-boundary--\r\n"
_DEVICE_BOUNDARY = b"----device-stream-boundary--\r\n"
_PROTO_HEADERS = {
    "Content-Type": "multipart/mixed;boundary=--client-stream-boundary--",
    "X-SECURE-HASH-1": "",
}


def _make_ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _subprotocol_value() -> str:
    return urllib.parse.quote(json.dumps(_PROTO_HEADERS, separators=(",", ":")))


def _ws_encode(payload: str | bytes, opcode: int = 1) -> bytes:
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    n = len(payload)
    if n < 126:
        header = bytes([0x80 | opcode, 0x80 | n])
    elif n < 65536:
        header = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack(">H", n)
    else:
        header = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack(">Q", n)
    mask_key = os.urandom(4)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return header + mask_key + masked


async def _ws_recv(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    hdr = await reader.readexactly(2)
    opcode = hdr[0] & 0x0F
    masked = (hdr[1] >> 7) & 1
    length = hdr[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", await reader.readexactly(8))[0]
    mask_key = await reader.readexactly(4) if masked else b""
    data = await reader.readexactly(length)
    if masked:
        data = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
    return opcode, data


def _digest_response(
    username: str, realm: str, password: str,
    nonce: str, nc: str, cnonce: str, qop: str,
) -> str:
    secure_h = hashlib.sha256(password.encode()).hexdigest().upper()
    ha1 = hashlib.md5(f"{username}:{realm}:{secure_h}".encode()).hexdigest()
    ha2 = hashlib.md5(f"GET:{_PATH}".encode()).hexdigest()
    return hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode()).hexdigest()


def _multipart_payload(obj: dict) -> str:
    body = json.dumps(obj, separators=(",", ":"))
    return (
        _CLIENT_BOUNDARY
        + f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
        + body
    )


def _parse_multipart(data: bytes) -> list[tuple[str, dict, bytes]]:
    parts: list[tuple[str, dict, bytes]] = []
    start = 0
    while True:
        idx = data.find(_DEVICE_BOUNDARY, start)
        if idx < 0:
            break
        hdr_start = idx + len(_DEVICE_BOUNDARY)
        hdr_end = data.find(b"\r\n\r\n", hdr_start)
        if hdr_end < 0:
            break
        headers: dict[str, str] = {}
        for line in data[hdr_start:hdr_end].decode("utf-8", errors="replace").split("\r\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip()] = v.strip()
        body_start = hdr_end + 4
        clen = headers.get("Content-Length", "")
        if clen.isdigit():
            body = data[body_start : body_start + int(clen)]
            start = body_start + int(clen)
        else:
            next_b = data.find(_DEVICE_BOUNDARY, hdr_start)
            body = data[body_start : next_b - 2] if next_b > 0 else data[body_start:]
            start = next_b if next_b > 0 else len(data)
        parts.append((headers.get("Content-Type", ""), headers, body))
    return parts


async def _ws_download_h264(
    ip: str,
    username: str,
    password: str,
    ssl_ctx: ssl.SSLContext,
    file_id: str,
    start_time: str,
) -> bytes | None:
    """Authenticate via WebSocket Digest and download one event image.

    Returns raw H.264 AVC Annex B bytes, or None on any failure.
    """
    proto = _subprotocol_value()
    ws_key = base64.b64encode(os.urandom(16)).decode()

    try:
        reader, writer = await asyncio.open_connection(ip, _PORT, ssl=ssl_ctx)
    except Exception:
        return None

    try:
        writer.write((
            f"GET {_PATH} HTTP/1.1\r\n"
            f"Host: {ip}:{_PORT}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Protocol: {proto}\r\n"
            f"Origin: https://{ip}\r\n"
            f"\r\n"
        ).encode())
        await writer.drain()

        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            if line == b"\r\n":
                break

        chunks: list[bytes] = []
        auth_done = dl_sent = False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 15

        while loop.time() < deadline:
            try:
                opcode, data = await asyncio.wait_for(_ws_recv(reader), timeout=4)
            except asyncio.TimeoutError:
                if dl_sent and chunks:
                    break
                continue
            except Exception:
                break

            if opcode == 8:  # close frame
                break

            if opcode == 1:  # text frame
                msg = json.loads(data.decode("utf-8"))
                params = msg.get("params", {})
                if not auth_done and params.get("event_type") == "websocket_authenticate":
                    nc = "00000001"
                    cnonce = hashlib.md5(os.urandom(16)).hexdigest()[:16]
                    auth_msg = json.dumps({
                        "type": "request", "seq": 0,
                        "params": {"method": "do", "authenticate": {
                            "username": username,
                            "realm": params["realm"],
                            "nonce": params["nonce"],
                            "uri": _PATH,
                            "algorithm": "MD5",
                            "response": _digest_response(
                                username, params["realm"], password,
                                params["nonce"], nc, cnonce, params["qop"],
                            ),
                            "opaque": params.get("opaque", ""),
                            "qop": params["qop"],
                            "nc": nc,
                            "cnonce": cnonce,
                        }},
                    }, separators=(",", ":"))
                    writer.write(_ws_encode(auth_msg))
                    await writer.drain()
                elif (
                    not auth_done
                    and msg.get("type") == "response"
                    and params.get("error_code") == 0
                ):
                    auth_done = True

            elif opcode == 2:  # binary frame
                chunks.append(data)

            if auth_done and not dl_sent:
                writer.write(_ws_encode(_multipart_payload({
                    "type": "request", "seq": 0,
                    "params": {"method": "get", "download": {
                        "client_id": 0, "channels": [0],
                        "start_time": start_time,
                        "media_type": 2,
                        "file_id": file_id,
                        "event_type": [],
                    }},
                })))
                await writer.drain()
                dl_sent = True

    finally:
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), timeout=2)
        except Exception:
            pass

    if not chunks:
        return None

    for ctype, _hdrs, body in _parse_multipart(b"".join(chunks)):
        if "avc" in ctype.lower() or "hevc" in ctype.lower():
            nal_start = b"\x00\x00\x00\x01"
            if body[:4] != nal_start:
                idx = body.find(nal_start)
                if idx > 0:
                    body = body[idx:]
            return body

    return None


async def _h264_to_jpeg(h264: bytes, ffmpeg_bin: str) -> bytes | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg_bin,
            "-loglevel", "error",
            "-i", "pipe:0",
            "-frames:v", "1", "-update", "1",
            "-f", "image2", "-vcodec", "mjpeg",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(input=h264), timeout=10)
        return stdout if proc.returncode == 0 and stdout else None
    except Exception:
        return None


async def fetch_latest_event_image(
    ip: str,
    username: str,
    password: str,
    camera: VIGICamera,
    ffmpeg_bin: str = "ffmpeg",
) -> dict | None:
    """Fetch the most recent event image and return a result dict, or None.

    Returns:
        {"jpeg": bytes, "event_type": int, "label": str, "file_id": str, "timestamp": str}
    Returns None if event image capture is disabled, the SD card is absent,
    or any network/decode step fails.
    """
    frames = await camera.get_event_images(days_back=1, max_items=5)
    if not frames:
        return None

    frame = frames[0]
    ssl_ctx = _make_ssl_ctx()
    h264 = await _ws_download_h264(
        ip, username, password, ssl_ctx,
        frame["file_id"], frame["start_time"],
    )
    if not h264:
        return None

    jpeg = await _h264_to_jpeg(h264, ffmpeg_bin)
    if not jpeg:
        return None

    et = int(frame.get("event_type", 0))
    return {
        "jpeg": jpeg,
        "event_type": et,
        "label": EVENT_IMAGE_LABELS.get(et, f"Detection ({et})"),
        "file_id": frame["file_id"],
        "timestamp": frame["start_time"],
    }
