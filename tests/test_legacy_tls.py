"""Cameras that offer only static-RSA cipher suites need the legacy TLS context.

Older firmware (seen on a VIGI C340, 3.0.0 Build 231229) does this, with a 1024-bit RSA
certificate. OpenSSL 3's default cipher list leaves those suites out, so the handshake
fails ("sslv3 alert handshake failure") and the camera cannot be added at all ("Cannot
connect to camera"). The test runs a real TLS 1.2 server that behaves like that camera.
"""
import asyncio
import datetime
import ssl

import aiohttp
import pytest
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from vigicam_pure.api import VIGICamera, is_tls_handshake_error, make_ssl_context


def _weak_cert(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TPRI-IPC")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    cert_file = tmp_path / "cert.pem"
    key_file = tmp_path / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


async def _get(port: int, legacy_tls: bool) -> int:
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=make_ssl_context(legacy_tls)),
        timeout=aiohttp.ClientTimeout(total=5),
    ) as session:
        async with session.get(f"https://127.0.0.1:{port}/") as resp:
            return resp.status


async def _with_weak_server(tmp_path, fn):
    cert_file, key_file = _weak_cert(tmp_path)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    # like the camera: static-RSA suites only (@SECLEVEL=0 just lets the server load the weak key)
    server_ctx.set_ciphers("AES256-GCM-SHA384:@SECLEVEL=0")
    server_ctx.load_cert_chain(cert_file, key_file)

    async def ok(_request):
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", ok)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_ctx)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        return await fn(port)
    finally:
        await runner.cleanup()


def test_strict_context_fails_and_legacy_connects(tmp_path):
    async def scenario(port):
        try:
            strict = await _get(port, legacy_tls=False)
        except aiohttp.ClientConnectorError as exc:  # the server drops or rejects the handshake
            strict = exc
        return strict, await _get(port, legacy_tls=True)

    strict, legacy = asyncio.run(_with_weak_server(tmp_path, scenario))
    if not isinstance(strict, BaseException):
        pytest.skip("this OpenSSL offers static-RSA suites by default")
    # the strict context cannot connect ... and the legacy context gets through
    assert legacy == 200


def test_contexts_never_verify_certificates():
    for legacy in (False, True):
        ctx = make_ssl_context(legacy)
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False


def test_certificate_errors_are_not_handshake_errors():
    # An untrusted certificate is fixed by turning verification off, not by legacy TLS.
    class _Key:
        ssl = None
        host = "h"
        port = 443

    cert_exc = aiohttp.ClientConnectorCertificateError(_Key(), ssl.SSLCertVerificationError())
    assert not is_tls_handshake_error(cert_exc)
    assert not is_tls_handshake_error(ValueError("x"))
    assert not is_tls_handshake_error(aiohttp.ClientConnectionError())


def test_handshake_failure_is_recognised():
    # What the camera produces: the server's "handshake failure" alert, wrapped by aiohttp.
    class _Key:
        ssl = None
        host = "h"
        port = 443

    assert is_tls_handshake_error(aiohttp.ClientConnectorSSLError(_Key(), ssl.SSLError(1, "handshake failure")))


def test_legacy_camera_ignores_the_shared_ha_session():
    # HA's shared session can't be given a cipher list, so a legacy camera makes its own.
    async def scenario():
        shared = aiohttp.ClientSession()
        try:
            normal = VIGICamera("192.0.2.1", "admin", "pw", session=shared)
            legacy = VIGICamera("192.0.2.1", "admin", "pw", session=shared, legacy_tls=True)
            same = normal._get_session() is shared
            own = legacy._get_session()
            distinct = own is not shared
            await legacy.close()
            return same, distinct, legacy.legacy_tls, normal.legacy_tls
        finally:
            await shared.close()

    assert asyncio.run(scenario()) == (True, True, True, False)
