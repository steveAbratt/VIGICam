"""VIGI NVR OpenAPI client: login hash, token handling, encoding, event parsing.

The fake NVR below follows TP-Link's "VIGI NVR OpenAPI Document" v1.4 and the
"How to Use VIGI NVR OpenAPI Function" guide: a digest challenge on GET /openapi/token,
SHA-256 A1/A2 response, percent-encoded token and string values, Bearer on every call.
"""
import asyncio
import datetime
import hashlib
import json
import ssl
import urllib.parse

import pytest
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from vigicam_pure import nvr_api

USER, PASSWORD = "admin", "Pa ss/w%rd!"
REALM, NONCE = "TP-LINK NVR", "5f3c9a1b"


# ── Pure helpers ──────────────────────────────────────────────────────────────

def test_digest_matches_tplink_formula():
    def sha(s):
        return hashlib.sha256(s.encode()).hexdigest()

    a1 = sha(f"{USER}:{REALM}:{PASSWORD}")
    a2 = sha("GET:/openapi/token")
    assert nvr_api.digest_response(USER, PASSWORD, REALM, NONCE, "/openapi/token") == sha(
        f"{a1}:{NONCE}:{a2}"
    )


def test_parse_challenge_quoted_and_bare():
    got = nvr_api.parse_digest_challenge(
        'Digest realm="TP-LINK NVR", nonce="abc", algorithm=SHA-256, url="/openapi/token"'
    )
    assert got == {
        "realm": "TP-LINK NVR", "nonce": "abc", "algorithm": "SHA-256", "url": "/openapi/token",
    }


def test_encode_decode_values_only():
    body = {"name": "IPC OSD", "port": 80, "list": ["a b"], "url": "/api/webhook/x"}
    enc = nvr_api.encode_values(body)
    assert enc == {"name": "IPC%20OSD", "port": 80, "list": ["a%20b"], "url": "/api/webhook/x"}
    assert nvr_api.decode_values(enc) == body


def test_error_code_variants():
    assert nvr_api.error_code({"error_code": "-40403"}) == -40403
    assert nvr_api.error_code({"errCode": 0}) == 0
    assert nvr_api.error_code({"devices": []}) == 0


def test_rtsp_url_quotes_credentials():
    api = nvr_api.VIGINVR("192.168.0.240", USER, PASSWORD)
    assert api.rtsp_url(3, 1) == (
        "rtsp://admin:Pa%20ss%2Fw%25rd%21@192.168.0.240:554/live/3/1/avm"
    )


# ── Event push parsing ────────────────────────────────────────────────────────

def _multipart(boundary: str, event: dict) -> bytes:
    return (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="event"\r\n'
        "Content-Type: application/json\r\n\r\n"
        f"{json.dumps(event)}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="picture"; filename="1.jpg"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode() + b"\xff\xd8\xff{not json}\xff\xd9\r\n" + f"--{boundary}--\r\n".encode()


EVENT = {
    "device_id": "abc", "device_name": "Home%20NVR",
    "messages": [
        {"type": 1, "sub_type": [4, 101], "channel": "2", "channel_name": "Front%20Door",
         "localtime": "2026-11-17 07:40:00"},
        {"type": 1, "sub_type": [2], "channel": 5},
        {"type": 2, "sub_type": [4]},
        {"type": 7, "sub_type": [2]},
        {"type": 1, "sub_type": ["22"], "channel": 1},
        {"type": 1, "sub_type": [99], "channel": 1},
    ],
}


@pytest.mark.parametrize("boundary", ["--VIGI-OPENAPI-MSG-PUSH", "__VIGI-OPENAPI_MSG_PUSH"])
def test_extract_event_from_multipart_either_boundary(boundary):
    body = _multipart(boundary, EVENT)
    got = nvr_api.extract_event_json(body, f"multipart/form-data; boundary={boundary}")
    assert got["device_name"] == "Home NVR"
    assert len(got["messages"]) == 6


def test_extract_event_without_content_type_or_as_plain_json():
    assert nvr_api.extract_event_json(_multipart("x", EVENT))["device_id"] == "abc"
    assert nvr_api.extract_event_json(json.dumps(EVENT).encode(), "application/json")
    assert nvr_api.extract_event_json(b"garbage", "") is None


def test_parse_event_maps_codes():
    msgs = nvr_api.parse_event(nvr_api.decode_values(EVENT))
    assert msgs[0]["channel"] == 2 and msgs[0]["keys"] == ["line_cross", "person"]
    assert msgs[0]["raw"]["channel_name"] == "Front Door"
    assert msgs[1]["channel"] == 5 and msgs[1]["keys"] == ["motion"]
    assert msgs[2]["exception"] == "disk_full" and msgs[2]["channel"] is None
    assert msgs[3]["exception"] == "raid_degraded"
    assert msgs[4]["keys"] == ["vehicle"]
    assert msgs[5]["keys"] == [] and msgs[5]["sub_types"] == [99]


def test_parse_event_tolerates_slash_subtypes_and_junk():
    msgs = nvr_api.parse_event({"messages": [
        {"type": "1", "sub_type": "5/102", "channel": "3"},
        {"type": "bad"},
        "nonsense",
    ]})
    assert len(msgs) == 1 and msgs[0]["keys"] == ["intrusion", "vehicle"]


# ── Fake NVR ──────────────────────────────────────────────────────────────────

def _cert(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TP-LINK NVR")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(1)
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "c.pem", tmp_path / "k.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    return ctx


class FakeNVR:
    def __init__(self):
        self.tokens: set[str] = set()
        self.logins = 0
        self.expire_next = False
        self.posted: list[dict] = []
        self.seq = 0

    def _new_token(self) -> dict:
        self.seq += 1
        token = f"tok/{self.seq}=="  # characters that must survive percent-encoding
        self.tokens.add(token)
        return {
            "token_type": "bearer", "expires_in": 1800,
            "access_token": urllib.parse.quote(token, safe=""),
            "refresh_token": urllib.parse.quote(f"r{self.seq}", safe=""),
            "error_code": 0,
        }

    def _authed(self, request) -> bool:
        auth = request.headers.get("Authorization", "")
        return auth.startswith("Bearer ") and auth[7:] in self.tokens

    async def token(self, request):
        if request.query.get("grant_type") == "refresh_token":
            if not self._authed(request):
                return web.json_response({"error_code": -40401}, status=401)
            return web.json_response(self._new_token())
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Digest"):
            return web.json_response(
                {"error_code": -40401}, status=401,
                headers={"WWW-Authenticate":
                         f'Digest realm="{REALM}", nonce="{NONCE}", algorithm="SHA-256", '
                         'url="/openapi/token"'},
            )
        fields = nvr_api.parse_digest_challenge(auth)
        expected = nvr_api.digest_response(USER, PASSWORD, REALM, NONCE, "/openapi/token")
        if fields.get("username") != USER or fields.get("response") != expected:
            return web.json_response({"error_code": -40401}, status=401)
        self.logins += 1
        return web.json_response(self._new_token())

    async def devices(self, request):
        if self.expire_next:
            self.expire_next = False
            self.tokens.clear()
            return web.json_response({"error_code": -40403})
        if not self._authed(request):
            return web.json_response({"error_code": -40401}, status=401)
        return web.json_response({"devices": [
            {"id": 1, "name": "Front%20Door", "alias": "IPC1", "online": "1",
             "ip": "192.168.0.2", "mac": "aa-bb"},
            {"id": 2, "name": "Yard", "online": "0", "ip": "192.168.0.3"},
        ], "error_code": 0})

    async def snapshot(self, request):
        if not self._authed(request):
            return web.json_response({"error_code": -40401}, status=401)
        if request.query.get("channel") == "2":
            return web.json_response({"error_code": -52405})  # channel offline
        return web.Response(body=b"\xff\xd8jpeg", content_type="image/jpeg")

    async def arm(self, request):
        if not self._authed(request):
            return web.json_response({"error_code": -40401}, status=401)
        self.posted.append(await request.json())
        return web.json_response({"error_code": 0})


def _run(tmp_path, scenario):
    async def main():
        nvr = FakeNVR()
        app = web.Application()
        app.router.add_get("/openapi/token", nvr.token)
        app.router.add_get("/openapi/added_devices", nvr.devices)
        app.router.add_get("/openapi/snapshot", nvr.snapshot)
        app.router.add_post("/openapi/event_server", nvr.arm)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=_cert(tmp_path))
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            return await scenario(nvr, port)
        finally:
            await runner.cleanup()

    return asyncio.run(main())


def test_login_list_and_reauth_on_session_timeout(tmp_path):
    async def scenario(nvr, port):
        api = nvr_api.VIGINVR("127.0.0.1", USER, PASSWORD, port=port)
        try:
            devices = await api.added_devices()
            assert [d["name"] for d in devices] == ["Front Door", "Yard"]
            assert nvr.logins == 1
            nvr.expire_next = True  # -40403 session timeout on the next call
            assert len(await api.added_devices()) == 2
            assert nvr.logins == 2
        finally:
            await api.close()

    _run(tmp_path, scenario)


def test_snapshot_bytes_and_offline_channel(tmp_path):
    async def scenario(nvr, port):
        api = nvr_api.VIGINVR("127.0.0.1", USER, PASSWORD, port=port)
        try:
            assert await api.snapshot(1) == b"\xff\xd8jpeg"
            with pytest.raises(nvr_api.VIGINVRCallError) as err:
                await api.snapshot(2)
            assert err.value.error_code == -52405
        finally:
            await api.close()

    _run(tmp_path, scenario)


def test_wrong_password_is_auth_error(tmp_path):
    async def scenario(nvr, port):
        api = nvr_api.VIGINVR("127.0.0.1", USER, "wrong", port=port)
        try:
            with pytest.raises(nvr_api.VIGINVRAuthError):
                await api.login()
        finally:
            await api.close()

    _run(tmp_path, scenario)


def test_post_body_is_percent_encoded(tmp_path):
    async def scenario(nvr, port):
        api = nvr_api.VIGINVR("127.0.0.1", USER, PASSWORD, port=port)
        try:
            await api.set_event_server(
                2, "192.168.1.10", 8123, "/api/webhook/abc", https=False, pictures=False
            )
        finally:
            await api.close()
        assert nvr.posted == [{
            "id": 2, "ip_or_domain": "192.168.1.10", "port": 8123, "url": "/api/webhook/abc",
            "protocol": "HTTP", "picture_switch": "off",
        }]

    _run(tmp_path, scenario)


def test_refresh_used_when_token_expires(tmp_path):
    async def scenario(nvr, port):
        api = nvr_api.VIGINVR("127.0.0.1", USER, PASSWORD, port=port)
        try:
            await api.added_devices()
            api._expiry = 0  # pretend the access token aged out
            await api.added_devices()
            assert nvr.logins == 1  # refreshed, not a second digest login
        finally:
            await api.close()

    _run(tmp_path, scenario)
